"""Shared prompt context for the phase 2 research agents (pure data shaping, no LLM calls)."""
from __future__ import annotations

import json
import os

ARCHETYPES = ["Small leader in a large, growing market", "Policy tailwind beneficiary", "Turnaround",
              "Cyclical at the bottom", "Formalisation and premiumisation", "Platform or financialisation"]

GROUND_RULES = """You are a research analyst for an educational Indian small/mid-cap (NSE/BSE) multibagger
research system. A human reviews everything you write and places any trades themselves.
Rules: use only the evidence provided plus widely known public facts; never invent numbers. Every claim
that moves a score must cite its source as one of: "fund:<field>", "filing:<id>", "fact:<id>",
"theme:<id>" or "general knowledge". Prefer saying "unknown" over guessing. Tone (management
optimism, TV interviews) may move management or visibility by at most 3 points."""


def compact(obj, limit: int = 60000) -> str:
    """JSON for a prompt. Large inputs are reduced by dropping the oldest list items, never cut mid-item."""
    text = json.dumps(obj, default=str, ensure_ascii=False)
    while len(text) > limit and isinstance(obj, dict):
        lists = [k for k, v in obj.items() if isinstance(v, list) and len(v) > 5]
        if not lists:
            break
        k = max(lists, key=lambda k: len(json.dumps(obj[k], default=str)))
        obj = {**obj, k: obj[k][: max(5, len(obj[k]) * 3 // 4)], f"{k}_note": "older items omitted for length"}
        text = json.dumps(obj, default=str, ensure_ascii=False)
    return text


def company_record(store, symbol: str, max_filings: int = 60) -> dict:
    f = store.get("fundamentals", symbol) or {}
    filings = store.list("filings", where=[("symbol", "==", symbol)], order_by="ingested", limit=max_filings)
    facts = store.list("facts", where=[("symbol", "==", symbol)], order_by="extracted", limit=80)
    alerts = store.list("alerts", where=[("symbol", "==", symbol)])
    return {
        "symbol": symbol,
        "fundamentals": {k: v for k, v in f.items() if v is not None and k not in ("id",)},
        "screen": (store.get("candidates", symbol) or {}).get("rules"),
        "red_flags": [{k: a.get(k) for k in ("rule", "severity", "detail", "status", "source")} for a in alerts],
        "filings": [{"id": x["id"], "date": x.get("date"), "category": x.get("category"),
                     "subject": (x.get("subject") or "")[:600]} for x in filings],
        "extracted_facts": [{k: v for k, v in x.items() if k not in ("symbol",)} for x in facts],
    }


def strength(c: dict) -> tuple:
    """Sort key for watchlist candidates: most Pass 1 rules met first, then fewest rules failed."""
    met, known = c.get("met") or 0, c.get("known") or 0
    return (-met, known - met, c.get("id") or "")


def eligible(store) -> list[dict]:
    """Watchlist stocks worth the research agents' time: passed the screen and met at least the Settings
    "minimum Pass 1 rules met" (default 5 of 7), strongest first."""
    from .. import llm
    need = int(llm.get_settings(store).get("min_rules_met") or 5)
    return sorted((c for c in store.list("candidates", where=[("passed", "==", True)]) if (c.get("met") or 0) >= need),
                  key=strength)


def per_run(store, agent: str, default: int = 50) -> int:
    """Stocks an agent handles per run: the Settings page value; MB_<AGENT>_MAX on the job overrides it."""
    from .. import llm
    return int(os.getenv(f"MB_{agent.upper()}_MAX") or llm.get_settings(store).get(f"{agent}_per_run") or default)
