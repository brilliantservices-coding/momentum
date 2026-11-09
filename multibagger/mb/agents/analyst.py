"""Company analyst: thesis memo, bull/base/bear values and the seven-family conviction score.

The model proposes points per signal family with evidence; the score, caps, agreement bonus,
red-flag overrides and tier are computed here by the strategy's rules, not by the model.
"""
from __future__ import annotations

from .. import inputs, llm, requests
from ..scoring import score_memo
from ..playbooks import VERSION as PLAYBOOK_VERSION
from ..store import now_iso
from ._context import ARCHETYPES, GROUND_RULES, company_record, compact, eligible, per_run, strength



PROMPT = """Analyse {symbol} as a potential multibagger (strategy v2). The thesis to test:
structural opportunity -> the company takes disproportionate share -> incremental economics improve ->
management reinvests into the advantage -> leading indicators convert into earnings -> earnings compound ->
valuation may re-rate. Look for a company entering this sequence early, and separate genuine inflections from
cyclical peaks, accounting artefacts, speculative capex and credit-risk build-up.

Sector playbook (decides which facts matter, never qualifies the stock): {playbook}
Score each of its critical signals; for lenders do not use cash conversion or net debt/EBITDA (use loan
growth, asset quality, credit cost, capital adequacy, ROA); for cyclicals normalise peak margins and returns
before awarding financial or valuation points.

Signal families and their maximum points for this playbook: {weights}. Score each family linearly from 0 to
its weight (the weights differ by sector: e.g. order-book businesses weigh visibility, lenders governance).
- theme: structural runway, penetration, capital-cycle stage, theme valuation (active theme 24+/30 = full).
- financial: 60% of its weight for sector-adjusted quality, balance sheet and cash economics + 40% from the
  Business Inflection Score economics (incremental returns, margins, asset turns, reinvestment runway).
- management: guidance accuracy, capital allocation, execution, alignment.
- visibility: 60% of its weight for the playbook's leading indicators + 40% for leading signals already
  converting into reported revenue and earnings.
- ecosystem: independent customer/supplier/regulator/trade corroboration (not the company's own words).
- valuation: scenario returns on normalised earnings; full marks needs a base-case 3-year value 2.5x+.
- governance: universal and sector-specific forensic checks.
The record has fundamentals (including annual and quarterly change variables and reinvestment: reinvestment
rate, capex, CWIP to net block, growth funded at the incremental return), the screen lane, the stock's
percentiles within its industry, a numbers-only BIS, positive filing triggers, red flags, filing headlines and
extracted facts (from filings, and from annual reports, presentations and concall transcripts where
documents_read lists them). Judge financial quality against the industry percentiles, not fixed bars. Items
in sector_critical_data_missing lower evidence confidence and belong in open_questions. A family with no usable evidence at all: set
"unverified": true (it gets half its weight and never counts as strong) instead of guessing. Weak or negative
evidence is not the same as no evidence: score it low. An unknown but critical fact lowers evidence
confidence; it is not a negative fact. Do not count correlated evidence twice (a presentation and an
interview repeating the same figure are one source group).

Business Inflection Score (diagnostic, 0-100): demand_share (20, growth accelerating or above the sector),
incremental_economics (25), reinvestment_runway (20), competitive_reinforcement (20, scale strengthens the
moat), second_engine (15, credible adjacent engine).

Sector signals: for each critical signal give status CONFIRMED / PARTIAL / UNTESTED / FAILED, the source
grade (P1-P6) and a source_group naming the underlying evidence (e.g. "Q2 FY27 results filing").

Inflection-to-Earnings tracker: turn the 2-4 main leading signals into falsifiable expectations. Previous
tracker (update statuses with any new evidence; keep the same signal wording; set timeline_revised only if
the company itself revised the timeline): {previous_tracker}

Evidence confidence: High = 80%+ of score-driving evidence verified, sector-critical fields verified, 2+
independent source groups behind the core inflection, no unresolved contradiction, primary-source support;
Moderate = 60-79%, one important non-fatal field unverified; Low = below 60%, a critical sector metric
missing, or the thesis rests mainly on management assertion.

Archetypes: {archetypes}.
Active themes: {themes}

Company record:
{record}

Return {{"archetype": "...", "theme_id": "... or null", "primary_playbook": "...", "summary": "2-3 sentences",
"one_sentence_thesis": "why it can compound earnings for 5-10 years",
"drivers": [{{"driver": "...", "proof_metric": "e.g. Q3 FY28 utilisation above 75%"}}] (exactly 3),
"bis": {{"demand_share": n, "incremental_economics": n, "reinvestment_runway": n, "competitive_reinforcement": n,
"second_engine": n, "notes": "..."}},
"sector_signals": [{{"signal": "...", "status": "CONFIRMED|PARTIAL|UNTESTED|FAILED", "source_grade": "P1-P6",
"source_group": "...", "evidence": "source: claim"}}],
"inflection_tracker": [{{"signal": "...", "first_observed": "e.g. Q2 FY27", "expected_consequence": "...",
"expected_lag_quarters": n, "target_metric": "...", "target": "...", "actual": "... or null",
"status": "UNTESTED|CONFIRMED|PARTIAL|FAILED", "timeline_revised": false}}],
"valuation": {{"current_price": n or null, "bull_3y": n, "base_3y": n, "bear_3y": n, "method": "...",
"assumed_eps_cagr": "...", "terminal_multiple": "...", "growth_duration": "..."}},
"families": {{"theme": {{"points": n, "unverified": true|false, "evidence": ["source: claim"]}}, "financial": {{...}},
"management": {{...}}, "visibility": {{...}}, "ecosystem": {{...}}, "valuation": {{...}}, "governance": {{...}}}},
"evidence_confidence": "High|Moderate|Low", "extraordinary_claims": ["claims needing more corroboration"],
"key_risks": ["..."], "kill_criteria": ["observable condition that forces a sell"],
"add_trim_exit": {{"add": "...", "trim": "...", "exit": "..."}},
"open_questions": ["what to read next"], "guidance_track_record": "met/missed summary or unknown"}}"""


def run(store, log) -> dict:
    """Analyses watchlist names whose inputs changed since their last memo, never-analysed first.

    Inputs are the company record (fundamentals, screen, red flags, filings, facts), the active themes
    and this prompt. Unchanged names are skipped; a price move of 15%+ since the memo counts as a change.
    """
    only = inputs.only_symbols()
    asked = only or set(requests.queued(store, "analyst"))
    cands = eligible(store)
    if asked:  # stocks requested on the stock page: first, even if unchanged or not health-qualified
        known = {c["id"]: c for c in store.list("candidates") if c["id"] in asked}
        cands = [known.get(s) or {"id": s} for s in sorted(asked)] + ([] if only else [c for c in cands if c["id"] not in asked])
    scores = {s["id"]: s for s in store.list("scores")}
    themes = [{"id": t["id"], "name": t.get("name"), "total": t.get("total")}
              for t in store.list("themes", where=[("active", "==", True)])]
    todo, unchanged = [], 0
    for c in cands:
        sym, prev = c["id"], scores.get(c["id"]) or {}
        rec = company_record(store, sym)
        key = inputs.digest(rec, themes, PROMPT)
        price = rec["fundamentals"].get("price")
        if (sym not in asked and prev and not inputs.redo_due(prev.get("analysed")) and prev.get("inputs_hash") == key
                and not inputs.price_moved(prev.get("price_at_analysis"), price)):
            unchanged += 1
            continue
        todo.append((prev.get("analysed", ""), sym, c, rec, key, price))
    # Never-analysed first, then oldest memo; within that, the strongest Pass 1 screens go first.
    todo.sort(key=lambda t: (t[1] not in asked, t[0], strength(t[2])))
    picked = todo[:max(per_run(store, "analyst"), len(asked))]
    results, errors = {}, {}
    stopped = None
    for _, sym, c, rec, key, price in picked:
        if inputs.out_of_time():
            stopped = "time"
            log.warning("near the job timeout: stopping here, the next run continues")
            break
        prev_memo = store.get("memos", sym) or {}
        try:
            out = llm.complete_json(GROUND_RULES, PROMPT.format(
                symbol=sym, weights=rec["family_weights"], archetypes="; ".join(ARCHETYPES), themes=themes or "(none scored yet)",
                playbook=compact(rec.get("playbook"), 3000),
                previous_tracker=compact(prev_memo.get("inflection_tracker") or [], 6000) or "[]",
                record=compact(rec)), agent="analyst", store=store)
        except llm.BudgetExceeded as exc:
            stopped = "daily cap"
            log.warning("%s", exc)
            break
        except Exception as exc:
            errors[sym] = str(exc)[:300]
            log.error("%s: %s", sym, exc)
            continue
        v2 = score_memo(out, rec["red_flags"], prev_memo, price, weights=rec["family_weights"])
        score = v2["score"]
        stamp = now_iso()
        prev = store.get("scores", sym) or {}
        store.set("scores", sym, {"symbol": sym, "name": c.get("name"), **score, "archetype": out.get("archetype"),
                                  "theme_id": out.get("theme_id"), "previous_score": prev.get("score"),
                                  "analysed": stamp, "redteam_needed": score["tier"] in ("A", "B"),
                                  "inputs_hash": key, "price_at_analysis": price,
                                  "playbook": (rec.get("playbook") or {}).get("id"),
                                  "evidence_confidence": v2["evidence_confidence"], "bis": v2["bis"]["total"],
                                  "convergence_passed": v2["convergence"]["passed"], "gates": v2["gates"],
                                  "base_multiple": v2["base_multiple"], "rules_version": v2["rules_version"],
                                  "model_version": llm.get_settings(store).get("model"),
                                  "playbook_version": PLAYBOOK_VERSION})
        memo = {"symbol": sym, **out, "inflection_tracker": v2["inflection_tracker"], "score": score,
                "bis_scored": v2["bis"], "convergence": v2["convergence"],
                "evidence_confidence_final": v2["evidence_confidence"], "gates": v2["gates"], "written": stamp}
        store.set("memos", sym, memo)
        store.set("memo_history", f"{sym}-{stamp}", memo)
        results[sym] = f"{score['score']} ({score['tier']})"
        requests.finish(store, "analyst", [sym], results[sym])
        log.info("%s: %s tier %s", sym, score["score"], score["tier"])
    left = max(0, len(todo) - len(results) - len(errors))
    return {"analysed": len(results), "scores": results, "errors": errors, "eligible": len(cands),
            "unchanged_skipped": unchanged, "still_to_do": left, "stopped_by": stopped,
            "more_to_do": bool(left and results and stopped != "daily cap" and not only),
            "remaining_unanalysed": max(0, sum(1 for c in cands if c["id"] not in scores) - len(results))}
