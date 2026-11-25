"""Company analyst: thesis memo, bull/base/bear values and the seven-family conviction score.

The model proposes points per signal family with evidence; the score, caps, agreement bonus,
red-flag overrides and tier are computed here by the strategy's rules, not by the model.
"""
from __future__ import annotations

from .. import inputs, llm, requests
from ..scoring import score_memo
from ..playbooks import VERSION as PLAYBOOK_VERSION
from ..store import now_iso
from ._context import (ARCHETYPES, GROUND_RULES, PROMOTE_AT, analyst_bands, company_record, compact, eligible,
                       per_run, strength)



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


TRIAGE_PROMPT = """Quick triage of {symbol} ({name}) for a multibagger research queue: numbers only, no filings.
Sector playbook: {playbook}. Judge it against its industry (percentiles given), not fixed bars, and look for an
early inflection (accelerating growth, rising margins and incremental returns, reinvestment runway) rather than
static quality alone. Score each signal family from 0 to its weight {weights} on what these numbers suggest; with
no evidence on management, ecosystem or governance, give about half the weight. Be brief.

Screen and numbers:
{record}

Return {{"archetype": "...", "summary": "two lines at most", "families": {{"theme": n, "financial": n,
"management": n, "visibility": n, "ecosystem": n, "valuation": n, "governance": n}},
"worth_full_analysis": true|false, "why": "one line"}}"""
TRIAGE_FIELDS = ("name", "sector", "industry", "mcap", "pe", "price", "sales_cagr_3y", "profit_cagr_3y", "qtr_sales_yoy",
                 "roce", "roce_3y_avg", "debt_to_equity", "promoter", "pledge", "promoter_change", "debtor_days",
                 "revenue_growth_1y", "revenue_acceleration", "qtr_acceleration", "revenue_growth_ttm",
                 "relative_growth_gap", "ebitda_margin", "margin_delta", "qtr_margin_delta", "incremental_roce",
                 "sales_to_capital_delta", "reinvestment_rate", "cwip_to_net_block", "reinvestment_growth")
TRIAGE_REDO_DAYS = 30


def _days_since(stamp: str | None) -> float:
    from datetime import datetime, timezone
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds() / 86400
    except (TypeError, ValueError):
        return 1e9


def _flags(rec: dict) -> str:
    """Open red flags as evidence: a new or newly dismissed flag always earns a fresh memo."""
    return ",".join(sorted(f"{a.get('rule')}:{a.get('severity')}" for a in rec.get("red_flags") or []
                           if a.get("status") != "dismissed"))


def _triage(store, c: dict, model: str) -> dict:
    """One short call on the numbers alone. Stored as depth "triage" with tier "Triage": it never earns a
    tier, a red-team review or a buy ticket; a score of 62+ moves the stock to the full analysis."""
    from ..playbooks import brief, weights_for
    from ..rules import conviction
    sym = c["id"]
    f = store.get("fundamentals", sym) or {}
    pb = c.get("playbook") or "general"
    w = weights_for(pb)
    rec = {"numbers": {k: f.get(k) for k in TRIAGE_FIELDS if f.get(k) is not None},
           "screen": {k: c.get(k) for k in ("rules", "lane", "trigger_events", "signal_groups", "peer_pct",
                                             "quant_bis", "research_priority", "missing_sector_data")}}
    out = llm.complete_json(GROUND_RULES, TRIAGE_PROMPT.format(
        symbol=sym, name=c.get("name") or f.get("name") or sym, playbook=brief(pb)["name"], weights=w,
        record=compact(rec, 8000)), agent="analyst-triage", store=store, model=model, effort="low")
    fam = out.get("families") or {}
    sc = conviction({k: (v.get("points") if isinstance(v, dict) else v) for k, v in fam.items()}, weights=w)
    return {"symbol": sym, "name": c.get("name"), "depth": "triage", "tier": "Triage", "score": sc["score"],
            "triage_score": sc["score"], "families": sc["families"], "weights": w, "playbook": pb,
            "archetype": out.get("archetype"), "triage": {"summary": out.get("summary"), "why": out.get("why"),
                                                         "worth_full_analysis": out.get("worth_full_analysis")},
            "analysed": now_iso(), "redteam_needed": False, "model_version": model,
            "notes": ["quick triage on the numbers only: no tier until a full analysis"]}


def run(store, log) -> dict:
    """Full memos for the deep band only, a quick triage for the light band, nothing for the tail
    (see _context.analyst_bands). Deep stocks are redone when their inputs changed, but at most every
    `analyst_redo_days` unless new evidence (facts, documents) arrived or the price moved 15%+.
    """
    only = inputs.only_symbols()
    asked = only or set(requests.queued(store, "analyst"))
    cfg = llm.get_settings(store)
    cands = eligible(store)
    if asked:  # stocks requested on the stock page: first, even if unchanged or not health-qualified
        known = {c["id"]: c for c in store.list("candidates") if c["id"] in asked}
        cands = [known.get(s) or {"id": s} for s in sorted(asked)] + ([] if only else [c for c in cands if c["id"] not in asked])
    scores = {s["id"]: s for s in store.list("scores")}
    bands = analyst_bands(store, [c for c in cands if c["id"] not in asked], scores, asked)
    counts = {b: sum(1 for v in bands.values() if v["band"] == b) for b in ("deep", "light", "tail")}
    store.set("control", "analyst_bands", {"updated": now_iso(), "counts": counts,
                                           "bands": {k: v["band"] for k, v in bands.items()}})
    themes = [{"id": t["id"], "name": t.get("name"), "total": t.get("total")}
              for t in store.list("themes", where=[("active", "==", True)])]
    redo_days = float(cfg.get("analyst_redo_days") or 14)
    facts_n: dict[str, int] = {}
    for x in store.list("facts"):
        facts_n[x.get("symbol")] = facts_n.get(x.get("symbol"), 0) + 1
    docs_n: dict[str, int] = {}
    for d in store.list("documents"):
        docs_n[d.get("symbol")] = docs_n.get(d.get("symbol"), 0) + 1
    todo, unchanged, recent = [], 0, 0
    light = []
    for c in cands:
        sym, prev = c["id"], scores.get(c["id"]) or {}
        band = (bands.get(sym) or {}).get("band", "tail")
        if band == "light":
            if sym not in asked and prev and (prev.get("depth") != "triage" or
                                              _days_since(prev.get("analysed")) < TRIAGE_REDO_DAYS):
                continue  # keeps its full memo, or a triage under 30 days old
            light.append(c)
            continue
        if band != "deep":
            continue
        rec = company_record(store, sym)
        key = inputs.digest(rec, themes, PROMPT)
        price = rec["fundamentals"].get("price")
        full = prev and prev.get("depth") != "triage"
        if sym not in asked and full and not inputs.redo_due(prev.get("analysed")):
            moved = inputs.price_moved(prev.get("price_at_analysis"), price)
            if prev.get("inputs_hash") == key and not moved:
                unchanged += 1
                continue
            new_evidence = (facts_n.get(sym, 0) > (prev.get("facts_count") or 0)
                            or docs_n.get(sym, 0) > (prev.get("docs_count") or 0)
                            or _flags(rec) != prev.get("flags_key"))
            if not moved and not new_evidence and _days_since(prev.get("analysed")) < redo_days:
                recent += 1  # inputs changed a little (a new filing headline), but the memo is recent
                continue
        todo.append((prev.get("analysed", "") if full else "", sym, c, rec, key, price))
    # Requested first, then never fully analysed, then the oldest memo; ties by research priority.
    todo.sort(key=lambda t: (t[1] not in asked, t[0], strength(t[2])))
    light.sort(key=lambda c: (c["id"] not in asked, strength(c)))
    picked = todo[:max(per_run(store, "analyst"), len(asked))]
    results, errors, triaged = {}, {}, {}
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
                                  "inputs_hash": key, "price_at_analysis": price, "depth": "deep",
                                  "facts_count": facts_n.get(sym, 0), "docs_count": docs_n.get(sym, 0),
                                  "flags_key": _flags(rec),
                                  "playbook": (rec.get("playbook") or {}).get("id"),
                                  "evidence_confidence": v2["evidence_confidence"], "bis": v2["bis"]["total"],
                                  "convergence_passed": v2["convergence"]["passed"], "gates": v2["gates"],
                                  "base_multiple": v2["base_multiple"], "rules_version": v2["rules_version"],
                                  "model_version": cfg.get("model"),
                                  "playbook_version": PLAYBOOK_VERSION})
        memo = {"symbol": sym, **out, "inflection_tracker": v2["inflection_tracker"], "score": score,
                "bis_scored": v2["bis"], "convergence": v2["convergence"],
                "evidence_confidence_final": v2["evidence_confidence"], "gates": v2["gates"], "written": stamp}
        store.set("memos", sym, memo)
        store.set("memo_history", f"{sym}-{stamp}", memo)
        results[sym] = f"{score['score']} ({score['tier']})"
        requests.finish(store, "analyst", [sym], results[sym])
        log.info("%s: %s tier %s", sym, score["score"], score["tier"])
    # Quick triage for the light band, in what is left of this run's per-run count.
    room = max(0, per_run(store, "analyst") - len(results) - len(errors))
    tmodel = cfg.get("triage_model") or cfg.get("model")
    for c in light[:room] if not stopped else []:
        if inputs.out_of_time():
            stopped = "time"
            break
        try:
            doc = _triage(store, c, tmodel)
        except llm.BudgetExceeded as exc:
            stopped = "daily cap"
            log.warning("%s", exc)
            break
        except Exception as exc:
            errors[c["id"]] = str(exc)[:300]
            log.error("%s triage: %s", c["id"], exc)
            continue
        store.set("scores", c["id"], doc)
        triaged[c["id"]] = doc["triage_score"]
        log.info("%s: triage %s%s", c["id"], doc["triage_score"],
                 " (moves to full analysis)" if doc["triage_score"] >= PROMOTE_AT else "")
    light_ids = {c["id"] for c in light}
    deep_err = sum(1 for e in errors if e not in light_ids)
    left = max(0, len(todo) - len(results) - deep_err) + max(0, len(light) - len(triaged) - (len(errors) - deep_err))
    log.info("bands: %d deep, %d triage, %d not analysed; this run %d full memos, %d triages", counts["deep"],
             counts["light"], counts["tail"], len(results), len(triaged))
    return {"analysed": len(results), "triaged": len(triaged), "scores": results, "triage_scores": triaged,
            "errors": errors, "eligible": len(cands), "bands": counts, "tail_skipped": counts["tail"],
            "unchanged_skipped": unchanged, "recent_skipped": recent, "still_to_do": left, "stopped_by": stopped,
            "more_to_do": bool(left and (results or triaged) and stopped != "daily cap" and not only)}
