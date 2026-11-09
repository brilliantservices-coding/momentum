"""One-time re-scoring of existing memos under the revised conviction rules (8 Nov 2026), no LLM calls.

Each memo already holds the analyst's points and evidence per family. A family whose evidence is empty or
only says the data is missing is treated as unverified; the score, tier and red-team adjustment are then
recomputed with today's red flags. Stocks whose inputs haven't changed since their memo get their
fingerprint moved to the revised prompt, so the analyst doesn't re-run them just because the prompt changed.
"""
from __future__ import annotations

from . import inputs
from .rules import tier_for
from .store import now_iso

MISSING_WORDS = ("unknown", "no data", "not available", "no evidence", "not provided", "no information",
                 "not in the record", "none in the record", "no usable", "not disclosed", "insufficient data",
                 "no concall", "no annual report", "cannot assess", "can't assess", "unable to assess")

# The analyst prompt before the 8 Nov revision: only used to check whether a memo's inputs are unchanged.
PROMPT_BEFORE_REVISION = """Analyse {symbol} as a potential multibagger (20%+ earnings growth for 5+ years plus re-rating).

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


def unverified_families(families: dict) -> list[str]:
    out = []
    for k, v in (families or {}).items():
        if (v or {}).get("unverified") is True:
            out.append(k)
            continue
        ev = [str(e).lower() for e in ((v or {}).get("evidence") or []) if str(e).strip()]
        if not ev or all(any(w in e for w in MISSING_WORDS) for e in ev):
            out.append(k)
    return out


def run(store, log=None) -> dict:
    from .agents._context import company_record
    from .agents.analyst import PROMPT
    from .scoring import score_memo
    themes = [{"id": t["id"], "name": t.get("name"), "total": t.get("total")}
              for t in store.list("themes", where=[("active", "==", True)])]
    stamp, changed, kept_hash = now_iso(), {}, 0
    for s in store.list("scores"):
        sym = s["id"]
        memo = store.get("memos", sym) or {}
        fam = memo.get("families")
        if not fam:
            continue
        rec = company_record(store, sym)
        unv = unverified_families(fam)
        out = {**memo, "families": {k: {**(v or {}), "unverified": k in unv} for k, v in fam.items()}}
        v2 = score_memo(out, rec["red_flags"], memo, (rec.get("fundamentals") or {}).get("price"))
        base = v2["score"]
        doc = {**{k: base[k] for k in ("families", "strong_families", "unverified", "notes")},
               "score_before_rescore": s.get("score"), "tier_before_rescore": s.get("tier"), "rescored": stamp,
               "evidence_confidence": v2["evidence_confidence"], "bis": v2["bis"]["total"],
               "convergence_passed": v2["convergence"]["passed"], "gates": v2["gates"],
               "base_multiple": v2["base_multiple"], "rules_version": v2["rules_version"]}
        score, tier = base["score"], base["tier"]
        if s.get("redteamed"):
            rt = store.get("redteam", sym) or {}
            if rt.get("verdict") == "reject":
                score, tier = 0.0, "Reject"
            else:
                adj = max(-15.0, min(0.0, float(rt.get("score_adjustment") or 0)))
                score = max(0.0, round(score + adj, 1))
                tier = tier_for(score, len(base["unverified"]), v2["gates"])
                doc["redteam_adjustment"] = adj
        doc.update({"score": score, "tier": tier, "redteam_needed": tier in ("A", "B") and not s.get("redteamed")})
        if s.get("inputs_hash") == inputs.digest(rec, themes, PROMPT_BEFORE_REVISION):
            doc["inputs_hash"] = inputs.digest(rec, themes, PROMPT)
            kept_hash += 1
        store.set("scores", sym, doc, merge=True)
        store.set("memos", sym, {"score": base, "rescored": stamp, "convergence": v2["convergence"],
                                 "gates": v2["gates"], "evidence_confidence_final": v2["evidence_confidence"]},
                  merge=True)
        changed[sym] = f"{s.get('score')} ({s.get('tier')}) -> {score} ({tier})"
    store.set("control", "rescore", {"at": stamp, "rescored": len(changed), "unchanged_inputs": kept_hash})
    if log:
        log.info("rescored %d memos (%d keep their analysis)", len(changed), kept_hash)
    return {"rescored": len(changed), "unchanged_inputs": kept_hash, "changes": changed}
