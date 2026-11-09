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
optimism, TV interviews) may move management or visibility by at most 3 points.
Source grades: P1 audited filing / exchange disclosure / regulator / government data; P2 concall or investor
presentation; P3 rating agency or contemporaneous institutional research; P4 customer/supplier publication or
trade body; P5 reputable media or interview; P6 forum or social. P6 can raise a question but never earns
points. The more extraordinary the reported performance, the more independent corroboration it needs."""


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
    from ..playbooks import brief, critical_missing, weights_for
    cand = store.get("candidates", symbol) or {}
    pb = cand.get("playbook") or "general"
    docs = store.list("documents", where=[("symbol", "==", symbol)])
    return {
        "symbol": symbol,
        "fundamentals": {k: v for k, v in f.items() if v is not None and k not in ("id",)},
        "screen": cand.get("rules"),
        "playbook": brief(pb),
        "family_weights": weights_for(pb),
        "secondary_playbooks": cand.get("secondary_playbooks") or [],
        "screen_lane": cand.get("lane"),
        "industry_percentiles": cand.get("peer_pct") or {},
        "inflection_screen": {"candidate": cand.get("inflection_candidate"), "triggers": cand.get("trigger_events") or [],
                              "signal_groups": cand.get("signal_groups") or [],
                              "numbers_only_bis": cand.get("quant_bis"), "bis_parts": cand.get("quant_bis_parts")},
        "sector_critical_data_missing": critical_missing(pb, f, [x.get("type") for x in facts]),
        "documents_read": [{k: d.get(k) for k in ("file", "kind", "source_grade", "facts")} for d in docs],
        "red_flags": [{k: a.get(k) for k in ("rule", "severity", "detail", "status", "source")} for a in alerts],
        "filings": [{"id": x["id"], "date": x.get("date"), "category": x.get("category"),
                     "subject": (x.get("subject") or "")[:600]} for x in filings],
        "extracted_facts": [{k: v for k, v in x.items() if k not in ("symbol",)} for x in facts],
    }


def strength(c: dict) -> tuple:
    """Sort key for watchlist candidates: highest research priority first (strategy v2: rules met, rules
    failed, the inflection lane, the numbers-only Business Inflection Score and filing triggers), then most
    rules met."""
    met, known = c.get("met") or 0, c.get("known") or 0
    prio = c.get("research_priority")
    if prio is None:
        prio = 10 * met - 5 * (known - met)
    return (-prio, -met, known - met, c.get("id") or "")


def qualified(c: dict, need: int) -> bool:
    """Health-qualified for LLM work: on the watchlist (passed, no open hard flag) and either a Compounder
    meeting the Settings "minimum Pass 1 rules met" (default 5 of 7) or an Inflection-lane stock, which has
    passed its own health checks (no leverage or promoter failure, positive margin, pledge 20% or less)."""
    if not c.get("passed"):
        return False
    lanes = c.get("lanes")
    if lanes is None:  # screened before the two lanes existed
        return (c.get("met") or 0) >= need
    return bool(lanes.get("inflection")) or (bool(lanes.get("compounder")) and (c.get("met") or 0) >= need)


def eligible(store) -> list[dict]:
    """Watchlist stocks worth the research agents' (LLM) time, strongest first."""
    from .. import llm
    need = int(llm.get_settings(store).get("min_rules_met") or 5)
    return sorted((c for c in store.list("candidates", where=[("passed", "==", True)]) if qualified(c, need)),
                  key=strength)


def per_run(store, agent: str, default: int = 50) -> int:
    """Stocks an agent handles per run: the Settings page value; MB_<AGENT>_MAX on the job overrides it."""
    from .. import llm
    return int(os.getenv(f"MB_{agent.upper()}_MAX") or llm.get_settings(store).get(f"{agent}_per_run") or default)
