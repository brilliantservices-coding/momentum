"""LLM access for the phase 2 agents, switchable in the portal (Settings page).

provider "subscription": runs the Claude Code CLI headless (`claude -p`) with the long-lived
  token from `claude setup-token` (env CLAUDE_CODE_OAUTH_TOKEN, mounted from Secret Manager).
  Usage counts against the user's Claude plan limits, not the GCP bill.
provider "vertex": Claude on Vertex AI through the Anthropic SDK, billed per token to GCP.
provider "openai": OpenAI's Responses API with the key in env OPENAI_API_KEY (Secret Manager
  mb-openai-api-key), billed per token by OpenAI. The rate-limit headers of each reply are kept in
  control/llm_limits so the Settings page can show how much of the per-minute limit is left.

Every call is checked against a daily spend cap first. For Vertex the cost is estimated from token
usage at list prices; for the subscription it's the CLI's notional cost, used as a proxy for how
much of the plan's allowance the agents consume.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone

from .config import settings
from .store import get_store, now_iso

DEFAULTS = {"provider": "subscription", "model": "claude-opus-5-5", "effort": "medium",
            "daily_cap_usd": settings.daily_llm_cap_usd, "vertex_region": "global",
            "openai_price_in": 2.5, "openai_price_out": 15.0, "analyst_per_run": 50,
            "extraction_per_run": 50, "redteam_per_run": 50, "min_rules_met": 5}
MODELS = ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
# USD per million tokens (input, output), first-party list prices used as an estimate.
# Fable 5.2 uses the Fable 5.1 list price as its estimate (same tier); the subscription reports its own cost.
PRICES = {"claude-fable-5-2": (10.0, 50.0), "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
PROVIDERS = {"subscription": "Claude subscription", "vertex": "Vertex AI (Claude)", "openai": "OpenAI API"}
# Shown when the portal can't list the account's models; any model id the account has can be typed in.
OPENAI_MODELS = ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna", "gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,63}$")


def valid_model(provider: str, model: str) -> bool:
    if provider == "openai":
        return bool(MODEL_ID.match(model or "")) and not model.startswith("claude")
    return model in MODELS


def openai_models() -> list[str]:
    """The account's text models when a key is available (cached for an hour), else a fixed list."""
    import time
    if not os.getenv("OPENAI_API_KEY"):
        return OPENAI_MODELS
    if _models_cache.get("at", 0) > time.time() - 3600:
        return _models_cache["ids"]
    try:
        from openai import OpenAI
        ids = sorted({m.id for m in OpenAI(timeout=10).models.list()
                      if re.match(r"^(gpt-|o\d)", m.id) and not re.search(r"audio|realtime|image|tts|transcribe|search|embedding", m.id)},
                     reverse=True)
    except Exception:
        ids = []
    _models_cache.update(at=time.time(), ids=ids or OPENAI_MODELS)
    return _models_cache["ids"]


_models_cache: dict = {}


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


def get_settings(store=None) -> dict:
    return {**DEFAULTS, **((store or get_store()).get("control", "llm") or {})}


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def spend_today(store=None) -> dict:
    return (store or get_store()).get("llm_spend", today()) or {"usd": 0.0, "calls": 0}


def _record(store, cost: float, provider: str, model: str, agent: str | None) -> None:
    s = spend_today(store)
    store.set("llm_spend", today(), {"date": today(), "usd": round(s.get("usd", 0) + cost, 6),
                                     "calls": s.get("calls", 0) + 1, "updated": now_iso()})
    store.set("llm_calls", f"{now_iso()}-{os.urandom(3).hex()}",
              {"at": now_iso(), "provider": provider, "model": model, "usd": round(cost, 6), "agent": agent})


def extract_json(text: str):
    """Parses the first JSON object in a reply (tolerates ```json fences and prose around it)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(candidate)
    except Exception as exc:
        raise LLMError(f"reply was not valid JSON: {exc}; first 300 chars: {text[:300]!r}") from exc


def _via_cli(system: str, prompt: str, model: str, effort: str, timeout: int) -> tuple[str, float]:
    token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if token and (len(token) < 40 or not token.startswith("sk-ant-")):
        raise LLMError(f"CLAUDE_CODE_OAUTH_TOKEN doesn't look like a token ({len(token)} characters): store a new "
                       "`claude setup-token` value in Secret Manager (mb-claude-oauth-token)")
    if not token:
        raise LLMError("CLAUDE_CODE_OAUTH_TOKEN is not set: store the `claude setup-token` value in Secret "
                       "Manager (mb-claude-oauth-token) and redeploy, or switch the provider to Vertex in Settings")
    # No tools: a single text-in, JSON-out completion, nothing on disk or the network is touched.
    cmd = ["claude", "-p", "--output-format", "json", "--model", model, "--effort", effort, "--tools", "",
           "--no-session-persistence", "--append-system-prompt", system]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = None
    if proc.returncode != 0 or out is None:
        detail = (out or {}).get("result") if isinstance(out, dict) else None
        status = (out or {}).get("api_error_status") if isinstance(out, dict) else None
        raise LLMError(f"claude CLI exited {proc.returncode}"
                       + (f" (API status {status})" if status else "")
                       + f": {detail or (proc.stderr or proc.stdout)[-500:]}")
    if out.get("is_error"):
        raise LLMError(f"claude CLI error: {str(out.get('result'))[:500]}")
    return out.get("result") or "", float(out.get("total_cost_usd") or 0.0)


def _via_vertex(system: str, prompt: str, model: str, effort: str, region: str) -> tuple[str, float]:
    from anthropic import AnthropicVertex

    client = AnthropicVertex(project_id=settings.project, region=region)
    with client.messages.stream(model=model, max_tokens=32000, system=system,
                                thinking={"type": "adaptive"}, output_config={"effort": effort},
                                messages=[{"role": "user", "content": prompt}]) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise LLMError(f"model declined: {getattr(msg, 'stop_details', None)}")
    text = "".join(b.text for b in msg.content if b.type == "text")
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    cost = (msg.usage.input_tokens * pin + msg.usage.output_tokens * pout) / 1e6
    return text, cost


def _via_openai(system: str, prompt: str, model: str, effort: str, prices: tuple[float, float],
                timeout: int) -> tuple[str, float, dict]:
    if not os.getenv("OPENAI_API_KEY"):
        raise LLMError("OPENAI_API_KEY is not set: store the key in Secret Manager (mb-openai-api-key) and redeploy, "
                       "or switch the provider in Settings")
    from openai import OpenAI

    client = OpenAI(timeout=timeout)
    raw = client.responses.with_raw_response.create(
        model=model, instructions=system, input=prompt, reasoning={"effort": effort},
        text={"format": {"type": "json_object"}}, store=False)
    resp = raw.parse()
    if resp.status != "completed":
        raise LLMError(f"OpenAI reply {resp.status}: {resp.incomplete_details or resp.error}")
    u = resp.usage
    cost = ((u.input_tokens if u else 0) * prices[0] + (u.output_tokens if u else 0) * prices[1]) / 1e6
    limits = {k.removeprefix("x-ratelimit-").replace("-", "_"): v for k, v in raw.headers.items()
              if k.lower().startswith("x-ratelimit-")}
    return resp.output_text or "", cost, limits


def limits(store=None) -> dict:
    return (store or get_store()).get("control", "llm_limits") or {}


def month_usage(store=None) -> dict:
    """This month's spend and calls per provider, from llm_calls."""
    store = store or get_store()
    start = datetime.now(timezone.utc).strftime("%Y-%m-01")
    out: dict[str, dict] = {}
    for c in store.list("llm_calls", where=[("at", ">=", start)]):
        p = out.setdefault(c.get("provider") or "?", {"usd": 0.0, "calls": 0})
        p["usd"] = round(p["usd"] + (c.get("usd") or 0), 4)
        p["calls"] += 1
    return out


def complete_json(system: str, prompt: str, agent: str | None = None, store=None, timeout: int = 900):
    """One call, JSON reply. Raises BudgetExceeded before calling if today's cap is used up."""
    store = store or get_store()
    cfg = get_settings(store)
    spent = spend_today(store).get("usd", 0.0)
    if spent >= float(cfg["daily_cap_usd"]):
        raise BudgetExceeded(f"daily LLM cap reached: ${spent:.2f} of ${float(cfg['daily_cap_usd']):.2f}")
    system = system + "\n\nReply with a single JSON object only, no prose before or after it."
    if cfg["provider"] == "vertex":
        text, cost = _via_vertex(system, prompt, cfg["model"], cfg["effort"], cfg["vertex_region"])
    elif cfg["provider"] == "openai":
        prices = (float(cfg["openai_price_in"]), float(cfg["openai_price_out"]))
        text, cost, lim = _via_openai(system, prompt, cfg["model"], cfg["effort"], prices, timeout)
        if lim:
            store.set("control", "llm_limits", {"openai": {**lim, "at": now_iso(), "model": cfg["model"]}}, merge=True)
    else:
        text, cost = _via_cli(system, prompt, cfg["model"], cfg["effort"], timeout)
    _record(store, cost, cfg["provider"], cfg["model"], agent)
    return extract_json(text)
