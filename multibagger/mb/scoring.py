"""Strategy v2 scoring on top of the analyst's output: conviction score, Business Inflection Score, sector
signal convergence, Inflection-to-Earnings tracker upkeep, Evidence Confidence and tier gates.

The model proposes points and evidence; every number that decides a tier is computed here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .rules import (RULES_VERSION, conviction, convergence, evidence_confidence, tier_for, tier_gates)

BIS_WEIGHTS = {"demand_share": 20, "incremental_economics": 25, "reinvestment_runway": 20,
               "competitive_reinforcement": 20, "second_engine": 15}
TRACKER_STATUSES = ("UNTESTED", "CONFIRMED", "PARTIAL", "FAILED")
FAILED_TRACKER_COST = 2  # visibility points per failed inflection-to-earnings expectation (max 6)


def bis(raw: dict | None) -> dict:
    raw = raw or {}
    parts = {}
    for k, w in BIS_WEIGHTS.items():
        v = raw.get(k)
        v = (v or {}).get("points") if isinstance(v, dict) else v
        try:
            parts[k] = max(0.0, min(float(v), w))
        except (TypeError, ValueError):
            parts[k] = 0.0
    total = round(sum(parts.values()), 1)
    label = ("strong inflection" if total >= 80 else "credible inflection" if total >= 65
             else "early / uncertain" if total >= 50 else "weak or unproven")
    return {"components": parts, "total": total, "label": label}


def _key(text) -> str:
    return " ".join(str(text or "").lower().split())[:120]


def update_tracker(items: list | None, previous: list | None, now: datetime | None = None) -> list:
    """Keeps first_seen across memos and fails stale claims: an expectation still untested two quarters
    after its expected lag becomes FAILED unless the analyst revised the timeline."""
    now = now or datetime.now(timezone.utc)
    prev = {_key(p.get("signal")): p for p in previous or []}
    out = []
    for it in items or []:
        it = dict(it)
        status = str(it.get("status") or "UNTESTED").upper()
        it["status"] = status if status in TRACKER_STATUSES else "UNTESTED"
        old = prev.get(_key(it.get("signal"))) or {}
        it["first_seen"] = old.get("first_seen") or now.isoformat(timespec="seconds")
        try:
            lag = float(it.get("expected_lag_quarters") or old.get("expected_lag_quarters") or 4)
        except (TypeError, ValueError):
            lag = 4.0
        due = datetime.fromisoformat(it["first_seen"]) + timedelta(days=91 * (lag + 2))
        if it["status"] in ("UNTESTED", "PARTIAL") and now > due and not it.get("timeline_revised"):
            it["status"], it["stale"] = "FAILED", True
        out.append(it)
    return out


def score_memo(out: dict, red_flags: list, prev_memo: dict | None = None, current_price=None) -> dict:
    """Everything the strategy computes from one analyst answer. Returns the fields for scores/ and memos/."""
    fam = out.get("families") or {}
    hard = sum(1 for a in red_flags if a.get("severity") == "hard" and a.get("status") != "dismissed")
    soft = sum(1 for a in red_flags if a.get("severity") == "soft" and a.get("status") != "dismissed")
    unverified = [k for k, v in fam.items() if (v or {}).get("unverified") is True]
    tracker = update_tracker(out.get("inflection_tracker"), (prev_memo or {}).get("inflection_tracker"))
    failed_tracker = [t.get("signal") for t in tracker if t["status"] == "FAILED"]
    points = {k: (v or {}).get("points") for k, v in fam.items()}
    if failed_tracker and points.get("visibility") is not None:
        cost = min(6, FAILED_TRACKER_COST * len(failed_tracker))
        try:
            points["visibility"] = max(0.0, float(points["visibility"]) - cost)
        except (TypeError, ValueError):
            pass
    score = conviction(points, hard, soft, unverified)
    if failed_tracker:
        score["notes"].append(f"{len(failed_tracker)} failed inflection-to-earnings expectation(s): visibility "
                              f"-{min(6, FAILED_TRACKER_COST * len(failed_tracker))}")
    signals = list(out.get("sector_signals") or [])
    conv = convergence(signals + [{"signal": s, "status": "FAILED"} for s in failed_tracker])
    conf = evidence_confidence(out.get("evidence_confidence"), len(score["unverified"]), conv)
    val = out.get("valuation") or {}
    price = val.get("current_price") or current_price
    try:
        base_multiple = round(float(val.get("base_3y")) / float(price), 2) if price else None
    except (TypeError, ValueError, ZeroDivisionError):
        base_multiple = None
    gates = tier_gates(score, conf, conv, base_multiple, hard)
    before = score["tier"]
    score["tier"] = tier_for(score["score"], len(score["unverified"]), gates)
    if score["tier"] != before:
        score["notes"].append(f"tier {before} → {score['tier']}: " + "; ".join(
            gates["a_missing"] if before == "A" else gates["b_missing"] or gates["a_missing"]))
    return {"score": score, "bis": bis(out.get("bis")), "convergence": conv, "evidence_confidence": conf,
            "gates": gates, "base_multiple": base_multiple, "inflection_tracker": tracker,
            "rules_version": RULES_VERSION}
