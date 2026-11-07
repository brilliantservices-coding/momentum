"""Portfolio agent: turns scores into order tickets for human approval (`proposals/`). No LLM, no orders.

Entry: score 50+, red-teamed if tier A/B, no open hard flag, not already held or proposed.
Exit: held and (score below 50 or an open hard flag). Sizes follow the tier table (starter % of
portfolio at cost); holdings count is capped at 25 names.
"""
from __future__ import annotations

from ..rules import SIZING
from ..store import now_iso

MAX_NAMES = 25


def run(store, log) -> dict:
    held = {h["id"]: h for h in store.list("holdings")}
    pending = {p.get("symbol"): p for p in store.list("proposals", where=[("status", "==", "pending")])}
    hard = {a.get("symbol") for a in store.list("alerts", where=[("severity", "==", "hard"), ("status", "!=", "dismissed")])}
    stamp, new = now_iso(), {}
    for s in sorted(store.list("scores"), key=lambda s: -(s.get("score") or 0)):
        sym, tier, score = s["id"], s.get("tier"), s.get("score") or 0
        if sym in pending:
            continue
        if sym in held:
            if score < 50 or sym in hard:
                why = "open hard red flag: exit fully" if sym in hard else f"conviction {score} below 50: exit in stages"
                new[f"{sym}-exit-{stamp[:10]}"] = {"symbol": sym, "action": "exit", "tier": tier, "score": score,
                                                   "size_pct": held[sym].get("size_pct"), "reason": why}
            continue
        if tier not in SIZING or sym in hard or s.get("redteam_needed"):
            continue
        if len(held) + sum(1 for p in new.values() if p["action"] == "buy starter") >= MAX_NAMES:
            break
        starter, cap = SIZING[tier]
        new[f"{sym}-buy-{stamp[:10]}"] = {
            "symbol": sym, "action": "buy starter", "tier": tier, "score": score, "size_pct": starter,
            "max_pct": cap, "reason": f"Tier {tier} at {score}: starter {starter}% of portfolio at cost, build to "
                                      f"{cap}% only as drivers are confirmed. Buy in 2-4 tranches over 2-6 weeks "
                                      "if illiquid; avoid within 2 days of a 20%+ move.",
            "memo_link": f"/research/{sym}", "redteam_link": f"/research/{sym}#redteam"}
    store.set_many("proposals", {k: {**v, "status": "pending", "created": stamp} for k, v in new.items()})
    log.info("proposals: %d new", len(new))
    return {"new_proposals": len(new), "held": len(held)}
