import logging
from datetime import datetime, timedelta, timezone

LOG = logging.getLogger("t")


def test_playbooks_and_sector_aware_pass1():
    from mb.playbooks import classify
    from mb.rules import pass1
    assert classify({"sector": "Financial Services", "industry": "Credit Services"})["playbook"] == "lender"
    assert classify({"sector": "Basic Materials", "industry": "Steel"})["playbook"] == "cyclical"
    assert classify({"sector": "Technology", "industry": "Electronic Components"})["playbook"] == "manufacturing"
    assert classify({})["playbook"] == "general"
    base = {"sales_cagr_3y": 20, "roce": 30, "roce_3y_avg": 10, "debt": 500, "cash": 0, "ebitda": 100,
            "promoter": 60, "pe": 10, "profit_cagr_3y": 20, "mcap": 2000, "avg_traded_value_cr": 5}
    lender = pass1({**base, "sector": "Financial Services", "industry": "Credit Services"})
    assert lender["rules"]["balance_sheet"] is None and lender["playbook"] == "lender"  # no net debt/EBITDA
    cyc = pass1({**base, "industry": "Steel"})
    assert cyc["rules"]["roce"] is False and cyc["rules"]["valuation"] is None  # mid-cycle ROCE, no spot P/E
    inf = pass1({**base, "sales_cagr_3y": 5, "revenue_acceleration": 15, "margin_delta": 3})
    assert inf["inflection_candidate"] and inf["rules"]["growth"] is True and len(inf["trigger_events"]) == 2
    assert inf["research_priority"] > pass1({**base, "sales_cagr_3y": 5})["research_priority"]


def test_change_variables():
    from mb.yahoo import change_variables
    cv = change_variables(rev=[150, 110, 100], ebitda=[30, 15, 12], ebit=[25, 12, 10], ta=[300, 250, 240],
                          cl=[100, 90, 80])
    assert round(cv["revenue_growth_1y"], 1) == 36.4 and round(cv["revenue_acceleration"], 1) == 26.4
    assert round(cv["margin_delta"], 1) == 6.4 and round(cv["incremental_roce"], 1) == 24.4


def test_gates_convergence_and_confidence():
    from mb.scoring import score_memo
    fam = {k: {"points": v, "evidence": ["fund:x"]} for k, v in
           {"theme": 14, "financial": 18, "management": 17, "visibility": 13, "ecosystem": 8, "valuation": 8,
            "governance": 9}.items()}
    good = [{"signal": "a", "status": "CONFIRMED", "source_grade": "P1", "source_group": "results"},
            {"signal": "b", "status": "CONFIRMED", "source_grade": "P2", "source_group": "concall"},
            {"signal": "c", "status": "CONFIRMED", "source_grade": "P1", "source_group": "results"}]
    out = {"families": fam, "sector_signals": good, "evidence_confidence": "High",
           "valuation": {"current_price": 100, "base_3y": 260}}
    r = score_memo(out, [])
    assert r["score"]["tier"] == "A" and r["convergence"]["passed"] and r["evidence_confidence"] == "High"
    r = score_memo({**out, "evidence_confidence": "Moderate"}, [])
    assert r["score"]["tier"] == "B" and "evidence confidence Moderate" in r["gates"]["a_missing"]
    r = score_memo({**out, "evidence_confidence": "Low"}, [])
    assert r["score"]["tier"] == "C"  # B needs at least Moderate
    r = score_memo({**out, "sector_signals": good[:2]}, [])
    assert not r["convergence"]["passed"] and r["evidence_confidence"] == "Moderate" and r["score"]["tier"] == "B"
    r = score_memo({**out, "valuation": {"current_price": 100, "base_3y": 200}}, [])
    assert r["score"]["tier"] == "B" and r["base_multiple"] == 2.0
    low = {k: {"points": 0.51 * w, "evidence": ["x"]} for k, w in
           {"theme": 15, "financial": 20, "management": 20, "visibility": 15, "ecosystem": 10, "valuation": 10,
            "governance": 10}.items()}
    r = score_memo({"families": low}, [])
    assert r["score"]["score"] == 51.0 and r["score"]["tier"] == "Research"


def test_tracker_goes_stale_and_costs_visibility():
    from mb.scoring import score_memo, update_tracker
    old = (datetime.now(timezone.utc) - timedelta(days=91 * 7)).isoformat(timespec="seconds")
    items = update_tracker([{"signal": "Capacity commissioned", "status": "UNTESTED", "expected_lag_quarters": 4}],
                           [{"signal": "capacity  commissioned", "first_seen": old}])
    assert items[0]["status"] == "FAILED" and items[0]["stale"] and items[0]["first_seen"] == old
    fresh = update_tracker([{"signal": "x", "status": "UNTESTED", "expected_lag_quarters": 4}], [])
    assert fresh[0]["status"] == "UNTESTED"
    fam = {"visibility": {"points": 12, "evidence": ["x"]}}
    r = score_memo({"families": fam, "inflection_tracker": [{"signal": "y", "status": "FAILED"}]}, [])
    assert r["score"]["families"]["visibility"] == 10 and "y" in r["convergence"]["failed"]


def test_portal_shows_v2_fields(env):
    from fastapi.testclient import TestClient

    from portal.app import app
    store, _ = env
    store.set("scores", "ZED", {"symbol": "ZED", "score": 70, "tier": "B", "families": {"theme": 10},
                                "playbook": "chemicals", "evidence_confidence": "Moderate", "bis": 66.0,
                                "gates": {"a_missing": ["evidence confidence Moderate"], "b_missing": []}})
    store.set("memos", "ZED", {"symbol": "ZED", "summary": "s", "one_sentence_thesis": "t",
                               "bis_scored": {"components": {"demand_share": 15}, "total": 66.0, "label": "credible inflection"},
                               "sector_signals": [{"signal": "Customer qualification", "status": "CONFIRMED",
                                                   "source_grade": "P1", "source_group": "filing"}],
                               "convergence": {"passed": False, "reasons": ["1 confirmed signals (needs 3)"]},
                               "inflection_tracker": [{"signal": "Debottlenecking", "status": "UNTESTED",
                                                       "first_seen": "2026-11-08T00:00:00+00:00"}]})
    c = TestClient(app)
    page = c.get("/research/ZED").text
    for text in ("Specialty chemicals", "Business Inflection Score", "credible inflection", "Customer qualification",
                 "Inflection-to-earnings tracker", "Debottlenecking", "Tier gates not met"):
        assert text in page, text
    assert "Moderate" in c.get("/research").text
