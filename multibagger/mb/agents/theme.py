"""Theme analyst: scores Indian structural themes on the strategy's six dimensions (1-5 each)."""
from __future__ import annotations

from .. import inputs, llm
from ..store import now_iso
from ._context import GROUND_RULES

DIMENSIONS = ["demand_runway", "policy_support", "capital_cycle", "earnings_breadth", "narrative_stage",
              "valuation_headroom"]

PROMPT = """Score 25-40 investable Indian equity themes for the next 5 years. Start from structural drivers:
government capex and infrastructure, defence and railway indigenisation, China+1 and PLI manufacturing
(electronics, chemicals, pharma APIs), energy transition (solar, transmission, batteries, green hydrogen),
financialisation of savings, formalisation and premiumisation of consumption, healthcare and diagnostics,
data centres and digital infrastructure, tourism and hospitality, real estate; add others you see.

Score each on six dimensions, 1-5 (5 best): demand_runway (12%+ market growth for 5 years), policy_support,
capital_cycle (5 = under-investment turning to rising utilisation; 1 = everyone adding capacity),
earnings_breadth, narrative_stage (5 = early and doubted; 1 = euphoric, IPO rush), valuation_headroom
(sector valuation vs its own 10-year history). Mark the top 6-10 as active. A theme scoring 1 on
narrative_stage and 1 on valuation_headroom cannot be active.

Recent filings headlines from the watchlist, for context (may be empty):
{headlines}

Previous scores (may be empty), keep changes justified:
{previous}

Return {{"themes": [{{"id": "kebab-case-id", "name": "...", "scores": {{"demand_runway": n, ...}},
"active": true|false, "bottleneck": "where in the value chain profit pools", "evidence": ["..."],
"change_vs_previous": "..."}}]}}"""


def run(store, log) -> dict:
    prev = {t["id"]: {"name": t.get("name"), "total": t.get("total"), "active": t.get("active")}
            for t in store.list("themes")}
    heads = [f"{f.get('symbol')}: {(f.get('subject') or '')[:120]}"
             for f in store.list("filings", order_by="ingested", limit=80)]
    key = inputs.digest(heads, PROMPT)
    last = store.get("control", "theme_inputs") or {}
    if prev and last.get("inputs_hash") == key and not inputs.redo():
        log.info("themes: no new filings headlines since the last scoring; keeping the %d saved themes", len(prev))
        return {"themes": len(prev), "unchanged_skipped": True,
                "active": sorted(k for k, v in prev.items() if v.get("active"))}
    out = llm.complete_json(GROUND_RULES, PROMPT.format(headlines="\n".join(heads) or "(none)",
                                                       previous=prev or "(none)"), agent="theme", store=store)
    stamp, docs = now_iso(), {}
    for t in out.get("themes", []):
        scores = {d: max(1, min(5, int(t.get("scores", {}).get(d) or 1))) for d in DIMENSIONS}
        docs[t["id"]] = {"name": t.get("name"), "scores": scores, "total": sum(scores.values()),
                         "active": bool(t.get("active")), "bottleneck": t.get("bottleneck"),
                         "evidence": t.get("evidence", []), "change": t.get("change_vs_previous"), "scored": stamp}
    store.set_many("themes", docs)
    store.set("control", "theme_inputs", {"inputs_hash": key, "scored": stamp})
    active = sorted((k for k, v in docs.items() if v["active"]), key=lambda k: -docs[k]["total"])
    log.info("themes: %d scored, active: %s", len(docs), ", ".join(active))
    return {"themes": len(docs), "active": active}
