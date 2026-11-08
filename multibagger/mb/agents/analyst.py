"""Company analyst: thesis memo, bull/base/bear values and the seven-family conviction score.

The model proposes points per signal family with evidence; the score, caps, agreement bonus,
red-flag overrides and tier are computed here by the strategy's rules, not by the model.
"""
from __future__ import annotations

import os

from .. import inputs, llm
from ..rules import WEIGHTS, conviction
from ..store import now_iso
from ._context import ARCHETYPES, GROUND_RULES, company_record, compact

MAX_PER_RUN = int(os.getenv("MB_ANALYST_MAX", "5"))

PROMPT = """Analyse {symbol} as a potential multibagger (20%+ earnings growth for 5+ years plus re-rating).

Signal families and their maximum points: {weights}.
What earns full marks: theme = sits in an active theme scoring 24+/30; financial = Pass 1 fully met with
accelerating revenue and margins; management = 80%+ guidance hit rate, clean capital allocation, insider
buying; visibility = order book above 2x revenue, capacity on time, new customers qualified; ecosystem =
customer/supplier/hiring/clearance/trade data independently confirm the story; valuation = base-case
3-year value at least 2.5x the current price; governance = no hard flags and at most one soft flag.
Score what the evidence supports; missing evidence earns low points, not average points.

Archetypes: {archetypes}.
Active themes: {themes}

Company record:
{record}

Return {{"archetype": "...", "theme_id": "... or null", "summary": "2-3 sentences",
"drivers": [{{"driver": "...", "proof_metric": "e.g. Q3 FY28 utilisation above 75%"}}] (exactly 3),
"valuation": {{"current_price": n or null, "bull_3y": n, "base_3y": n, "bear_3y": n, "method": "..."}},
"families": {{"theme": {{"points": n, "evidence": ["source: claim"]}}, "financial": {{...}}, "management": {{...}},
"visibility": {{...}}, "ecosystem": {{...}}, "valuation": {{...}}, "governance": {{...}}}},
"key_risks": ["..."], "kill_criteria": ["observable condition that forces a sell"],
"open_questions": ["what to read next"], "guidance_track_record": "met/missed summary or unknown"}}"""


def run(store, log) -> dict:
    """Analyses watchlist names whose inputs changed since their last memo, never-analysed first.

    Inputs are the company record (fundamentals, screen, red flags, filings, facts), the active themes
    and this prompt. Unchanged names are skipped; a price move of 15%+ since the memo counts as a change.
    """
    cands = [c for c in store.list("candidates", where=[("passed", "==", True)])]
    scores = {s["id"]: s for s in store.list("scores")}
    themes = [{"id": t["id"], "name": t.get("name"), "total": t.get("total")}
              for t in store.list("themes", where=[("active", "==", True)])]
    todo, unchanged = [], 0
    for c in cands:
        sym, prev = c["id"], scores.get(c["id"]) or {}
        rec = company_record(store, sym)
        key = inputs.digest(rec, themes, PROMPT)
        price = rec["fundamentals"].get("price")
        if (prev and not inputs.redo() and prev.get("inputs_hash") == key
                and not inputs.price_moved(prev.get("price_at_analysis"), price)):
            unchanged += 1
            continue
        todo.append((prev.get("analysed", ""), sym, c, rec, key, price))
    # Never-analysed first, then oldest memo; within that, the strongest Pass 1 screens go first.
    todo.sort(key=lambda t: (t[0], -(t[2].get("met") or 0), (t[2].get("known") or 0) - (t[2].get("met") or 0), t[1]))
    picked = todo[:MAX_PER_RUN]
    results, errors = {}, {}
    for _, sym, c, rec, key, price in picked:
        try:
            out = llm.complete_json(GROUND_RULES, PROMPT.format(
                symbol=sym, weights=WEIGHTS, archetypes="; ".join(ARCHETYPES), themes=themes or "(none scored yet)",
                record=compact(rec)), agent="analyst", store=store)
        except llm.BudgetExceeded as exc:
            log.warning("%s", exc)
            break
        except Exception as exc:
            errors[sym] = str(exc)[:300]
            log.error("%s: %s", sym, exc)
            continue
        fam = out.get("families", {})
        hard = sum(1 for a in rec["red_flags"] if a["severity"] == "hard" and a.get("status") != "dismissed")
        soft = sum(1 for a in rec["red_flags"] if a["severity"] == "soft" and a.get("status") != "dismissed")
        score = conviction({k: (v or {}).get("points") for k, v in fam.items()}, hard, soft)
        stamp = now_iso()
        prev = store.get("scores", sym) or {}
        store.set("scores", sym, {"symbol": sym, "name": c.get("name"), **score, "archetype": out.get("archetype"),
                                  "theme_id": out.get("theme_id"), "previous_score": prev.get("score"),
                                  "analysed": stamp, "redteam_needed": score["tier"] in ("A", "B"),
                                  "inputs_hash": key, "price_at_analysis": price})
        memo = {"symbol": sym, **out, "score": score, "written": stamp}
        store.set("memos", sym, memo)
        store.set("memo_history", f"{sym}-{stamp}", memo)
        results[sym] = f"{score['score']} ({score['tier']})"
        log.info("%s: %s tier %s", sym, score["score"], score["tier"])
    return {"analysed": len(results), "scores": results, "errors": errors,
            "unchanged_skipped": unchanged, "still_to_do": max(0, len(todo) - len(results) - len(errors)),
            "remaining_unanalysed": max(0, sum(1 for c in cands if c["id"] not in scores) - len(results))}
