"""Strategy v2 round 2: two-lane screen, industry percentiles, sector weights, reinvestment, quarterly change
variables, positive filing triggers, company documents and per-stock stage runs."""
import logging

from test_phase2 import FAMILIES, V2, _seed_watchlist

LOG = logging.getLogger("t")
BASE = {"sales_cagr_3y": 20, "roce": 30, "roce_3y_avg": 25, "debt": 50, "cash": 40, "ebitda": 140,
        "promoter": 60, "pledge": 0, "pe": 20, "profit_cagr_3y": 20, "mcap": 2000, "avg_traded_value_cr": 5,
        "cfo_3y": 300, "ebitda_3y": 380, "debtor_days": 60, "debtor_days_3y_back": 62}


def test_two_lanes_and_health():
    from mb.rules import pass1
    weak = {**BASE, "sales_cagr_3y": 3, "roce": 8, "roce_3y_avg": 9, "pe": 60, "cfo_3y": 100}
    r = pass1(weak)
    assert not r["lanes"]["compounder"] and not r["passed"]
    turning = {**weak, "qtr_acceleration": 18, "qtr_margin_delta": 3.5, "opm_qtr": 12}
    r = pass1(turning)
    assert r["lanes"] == {"compounder": False, "inflection": True} and r["passed"] and r["lane"] == "inflection"
    assert r["signal_groups"] == ["demand", "margin"] and r["quant_bis"] > 0
    # two signals from the same group (annual and quarterly margin) are one piece of evidence
    assert not pass1({**weak, "margin_delta": 3, "qtr_margin_delta": 3})["inflection_candidate"]
    # health: a levered inflection does not qualify
    r = pass1({**turning, "debt": 2000})
    assert r["inflection_candidate"] and not r["lanes"]["inflection"] and not r["health"]["balance_sheet"]
    # a positive filing trigger counts as its own group
    r = pass1({**weak, "qtr_margin_delta": 3}, triggers=[{"rule": "order_win", "label": "order win", "date": "x"}])
    assert r["lanes"]["inflection"] and r["signal_groups"] == ["event", "margin"]


def test_industry_relative_rules():
    from mb.peers import percentiles
    from mb.rules import pass1
    funds = [{"id": f"C{i}", "industry": "Cement", "roce": 6 + i, "sales_cagr_3y": 5 + i} for i in range(10)]
    pct = percentiles(funds)
    assert pct["C9"]["roce"] == 100.0 and pct["C0"]["roce"] == 0.0 and pct["C9"]["peer_group"].startswith("industry")
    leader = {**BASE, "roce": 12, "roce_3y_avg": None, "industry": "Paper"}
    assert pass1(leader)["rules"]["roce"] is False
    r = pass1(leader, pct={"roce": 85})
    assert r["rules"]["roce"] is True and "ROCE top 30% of its industry" in r["relative_passes"]


def test_sector_weights_change_the_score():
    from mb.playbooks import WEIGHT_PROFILES, weights_for
    from mb.rules import conviction
    assert all(sum(w.values()) == 100 for w in WEIGHT_PROFILES.values())
    assert weights_for("capital_goods")["visibility"] == 25 and weights_for(None)["visibility"] == 15
    pts = {"theme": 10, "financial": 15, "management": 15, "visibility": 22, "ecosystem": 8, "valuation": 8,
           "governance": 8}
    assert conviction(pts, weights=weights_for("capital_goods"))["score"] > conviction(pts)["score"]


def test_yahoo_reinvestment_and_quarterly():
    from mb.yahoo import quarterly_changes, reinvestment
    r = reinvestment(ebit=[100], capex=[-90], dep=[-30], ppe=[400], cwip=[80], inc_roce=30)
    assert r["reinvestment_rate"] == 80.0 and r["cwip_to_net_block"] == 20.0 and r["reinvestment_growth"] == 24.0
    q = quarterly_changes(qrev=[130, 120, 110, 105, 100], qebitda=[19.5, 15, 13, 12, 12], growth_1y=10)
    assert round(q["qtr_margin_delta"], 1) == 3.0 and round(q["qtr_acceleration"], 1) == 20.0


def test_trendlyne_change_columns():
    from mb.fundamentals import parse
    csv = (b"Stock Name,NSE Code,Revenue Growth Qtr YoY %,Sector Revenue Growth Qtr YoY %,Operating Profit Margin Qtr %,"
           b"Operating Profit Margin Qtr 4Qtr ago %,Operating Revenue TTM,Operating Revenue TTM 1Yr Ago,"
           b"Revenue Annual 3Yr Growth %,Industry Name,sector_name\n"
           b"Alpha,ALPHA,30,12,18,14,1200,1000,15,Auto Parts,Automobiles\n")
    out, rep = parse(csv, "t.csv")
    a = out["ALPHA"]
    assert a["relative_growth_gap"] == 18 and a["qtr_margin_delta"] == 4 and a["revenue_growth_ttm"] == 20
    assert a["ttm_acceleration"] == 5 and a["industry"] == "Auto Parts" and a["sector"] == "Automobiles"
    assert "relative_growth_gap" in rep["recognised"]


def test_positive_triggers_flow_into_screen(env):
    from mb.agents import forensic, screener
    from mb.rules import positive_triggers
    assert positive_triggers("Receipt of order worth Rs 120 crore from NHAI")[0]["rule"] == "order_win"
    assert positive_triggers("Credit rating upgraded to AA- by CRISIL")[0]["rule"] == "rating_upgrade"
    assert positive_triggers("Credit rating downgraded") == []
    assert positive_triggers("Cancellation of order") == []
    store, blob = env
    _seed_watchlist(store, blob)
    store.set("filings", "f1", {"symbol": "ALPHA", "subject": "Commencement of commercial production at new plant",
                                "date": "08-Nov-2026", "ingested": "2026-11-08T10:00:00+00:00"})
    store.set("control", "triggers", {"backfilled": False})
    assert forensic.run(store=store, log=LOG)["positive_triggers"] >= 1
    screener.run(store=store, log=LOG)
    c = store.get("candidates", "ALPHA")
    assert any("capacity commissioned" in t for t in c["trigger_events"]) and "event" in c["signal_groups"]
    import json
    wanted = json.loads(blob.get("docs/wanted.json"))
    assert [s["symbol"] for s in wanted["stocks"]] == ["ALPHA"]


def test_documents_are_read_once(env, cli):
    from mb.agents import extraction
    store, blob = env
    _seed_watchlist(store, blob)
    blob.put("inbox/docs/ALPHA/concall-Q2FY27.txt", b"Management guided 25% volume growth in FY28.")
    fake = cli([{"facts": [{"type": "volume_growth", "statement": "Guided 25% volume growth", "period": "FY28"}]},
                {"facts": []}])
    out = extraction.run(store=store, log=LOG)
    assert out["documents"] == 1
    f = [x for x in store.list("facts") if x.get("source_type") == "concall transcript"]
    assert f and f[0]["source_grade"] == "P2" and f[0]["source_doc"] == "docs/ALPHA/concall-Q2FY27.txt"
    assert blob.list("inbox/docs/") == [] and store.get("documents", "doc-ALPHA-concall-Q2FY27")["facts"] == 1
    assert "volume growth" in fake.calls[0][1].lower()


def test_stock_page_and_single_stock_runs(env, cli, monkeypatch):
    from fastapi.testclient import TestClient

    from mb import control, requests, runner
    from mb.agents import analyst
    from portal.app import app
    store, blob = env
    _seed_watchlist(store, blob)
    c = TestClient(app)
    page = c.get("/stock/ALPHA")
    assert page.status_code == 200 and "Pipeline for this stock" in page.text and "Company analyst" in page.text
    assert c.get("/stock/NOPE").status_code == 404

    started = []
    monkeypatch.setattr(control, "run_now", lambda a, **k: started.append((a, k)) or "ok")
    r = c.post("/stock/ALPHA/run/analyst", follow_redirects=False)
    assert r.status_code == 303 and started[-1] == ("analyst", {"env": {"MB_SYMBOLS": "ALPHA"}})
    assert requests.queued(store, "analyst") == ["ALPHA"]
    assert c.post("/stock/ALPHA/run/portfolio").status_code == 400

    def busy(a, **k):
        raise control.AlreadyRunning(a)
    monkeypatch.setattr(control, "run_now", busy)
    r = c.post("/stock/BETA/run/analyst")
    assert "queued" in r.text and requests.queued(store, "analyst") == ["ALPHA", "BETA"]

    # The single-stock run: only ALPHA, even though BETA is queued and ALPHA was never health-checked here.
    cli([{**V2, "summary": "s", "families": FAMILIES}])
    monkeypatch.setenv("MB_SYMBOLS", "ALPHA")
    out = analyst.run(store=store, log=LOG)
    assert list(out["scores"]) == ["ALPHA"] and not out["more_to_do"]
    assert requests.queued(store, "analyst") == ["BETA"]
    # When a run ends, waiting requests get a run of their own.
    monkeypatch.setattr(control, "run_now", lambda a, **k: started.append((a, k)) or "ok")
    monkeypatch.setattr(control, "active_runs", lambda *a, **k: [])
    monkeypatch.delenv("MB_SYMBOLS")
    monkeypatch.setattr("mb.agents.analyst.run", lambda store, log: {"analysed": 0})
    runner.run("analyst", force=True)
    assert started[-1][1]["env"]["MB_SYMBOLS"] == "BETA"
    assert "analysed" in c.get("/candidates").text


def test_rescore_rescales_to_playbook_weights(env, cli):
    from mb import rescore
    from mb.agents import analyst
    store, blob = env
    _seed_watchlist(store, blob)
    cli([{**V2, "summary": "s", "families": FAMILIES}])
    analyst.run(store=store, log=LOG)
    before = store.get("scores", "ALPHA")["score"]
    store.set("candidates", "ALPHA", {"playbook": "capital_goods"}, merge=True)
    rescore.run(store)
    s = store.get("scores", "ALPHA")
    assert s["weights"]["visibility"] == 25 and s["playbook"] == "capital_goods" and s["score"] != before


def test_pages_after_analysis(env, cli):
    from fastapi.testclient import TestClient

    from mb.agents import analyst
    from portal.app import app
    store, blob = env
    _seed_watchlist(store, blob)
    cli([{**V2, "summary": "s", "families": FAMILIES}])
    analyst.run(store=store, log=LOG)
    blob.put("inbox/docs/ALPHA/annual-report-FY26.pdf", b"%PDF")
    c = TestClient(app)
    for url in ("/stock/ALPHA", "/candidates?lane=inflection", "/candidates?all=1", "/funnel", "/research/ALPHA",
                "/settings"):
        r = c.get(url)
        assert r.status_code == 200, url
    page = c.get("/stock/ALPHA").text
    assert "annual-report-FY26.pdf" in page and "tier" in page
