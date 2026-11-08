"""Red-team agent: argues the bear case for tier A/B names and anything held that is down 35%+."""
from __future__ import annotations

from .. import inputs, llm
from ..rules import TIERS
from ..store import now_iso
from ._context import GROUND_RULES, company_record, compact, per_run

PROMPT = """You are the red team. The analyst scored {symbol} at {score} (tier {tier}). Your job is to find what
the analyst missed: accounting quality, governance, customer concentration, competition, cyclicality
mistaken for growth, valuation already pricing the bull case, liquidity, story-stock signs (theme
attached to tiny revenue), and whether the kill criteria are observable.

Analyst memo:
{memo}

Company record:
{record}

Return {{"bear_case": "3-5 sentences", "missed_risks": [{{"risk": "...", "evidence": "source: claim",
"severity": "high|medium|low"}}], "weak_evidence": ["claims in the memo not supported by the record"],
"score_adjustment": n (0 to -15; negative lowers the score), "verdict": "hold score|lower score|reject"}}"""


def run(store, log) -> dict:
    todo = [s for s in store.list("scores", where=[("redteam_needed", "==", True)])]
    held = {h["id"] for h in store.list("holdings")}
    todo += [s for s in store.list("scores") if s["id"] in held and s.get("drawdown_pct", 0) <= -35
             and s not in todo]
    # Falling holdings first (risk to money already in), then the highest scores.
    todo.sort(key=lambda s: (s["id"] not in held, -(s.get("score") or 0)))
    limit, reviewed = per_run(store, "redteam"), 0
    done, unchanged, stopped = {}, 0, None
    for s in todo:
        if reviewed >= limit:
            break
        if inputs.out_of_time():
            stopped = "time"
            break
        sym = s["id"]
        memo = store.get("memos", sym) or {}
        rec = company_record(store, sym)
        key = inputs.digest(memo, rec, s.get("analysed"), PROMPT)
        prev = store.get("redteam", sym) or {}
        if prev.get("inputs_hash") == key and not inputs.redo_due(prev.get("written")):
            # Same memo and evidence as last review: keep that verdict instead of asking again.
            if s.get("redteam_needed"):
                store.set("scores", sym, {"redteam_needed": False}, merge=True)
            unchanged += 1
            continue
        reviewed += 1
        try:
            out = llm.complete_json(GROUND_RULES, PROMPT.format(symbol=sym, score=s.get("score"), tier=s.get("tier"),
                                    memo=compact(memo, 30000), record=compact(rec)),
                                    agent="redteam", store=store)
        except llm.BudgetExceeded as exc:
            stopped = "daily cap"
            log.warning("%s", exc)
            break
        adj = max(-15.0, min(0.0, float(out.get("score_adjustment") or 0)))
        new = 0.0 if out.get("verdict") == "reject" else max(0.0, round(float(s.get("score") or 0) + adj, 1))
        tier = next((t for cut, t in TIERS if new >= cut), "Reject")
        stamp = now_iso()
        store.set("redteam", sym, {"symbol": sym, **out, "score_before": s.get("score"), "score_after": new,
                                   "written": stamp, "inputs_hash": key})
        store.set("scores", sym, {"score": new, "tier": tier, "redteam_needed": False, "redteamed": stamp,
                                  "redteam_adjustment": new - float(s.get("score") or 0)}, merge=True)
        done[sym] = f"{s.get('score')} -> {new} ({tier})"
        log.info("%s: %s", sym, done[sym])
    return {"reviewed": len(done), "changes": done, "unchanged_skipped": unchanged,
            "still_to_do": max(0, len(todo) - unchanged - reviewed), "stopped_by": stopped,
            "more_to_do": bool(done and len(todo) - unchanged - reviewed > 0 and stopped != "daily cap")}
