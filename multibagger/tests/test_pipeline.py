import base64
import json
from datetime import date

import pytest

CSV = b"""Name,NSE Code,Current Price,Market Capitalization,Price to Earning,Sales growth 3Years,Profit growth 3Years,Return on capital employed,Operating cash flow 3Years,EBITDA 3Years,Net profit 3Years,EBITDA,Debt,Cash Equivalents,Promoter holding,Change in promoter holding,Pledged percentage,Debtor days,Debtor days 3Years back,Volume 1month average
Alpha Precision,ALPHA,500,3200,24,22,28,21,300,380,200,140,50,40,58,0.5,0,60,62,200000
Beta Pledged,BETA,100,1500,15,18,20,17,120,150,90,60,30,10,45,0,32,40,40,500000
Gamma Large,GAMMA,2000,90000,30,15,15,25,900,1000,700,400,0,100,60,0,0,30,30,900000
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import mb.blob
    import mb.store
    monkeypatch.setattr(mb.store, "_store", mb.store.LocalStore(tmp_path))
    monkeypatch.setattr(mb.blob, "_blob", mb.blob.LocalBlob(tmp_path))
    return mb.store._store, mb.blob._blob


def test_import_forensic_screen(env):
    import logging

    from mb.agents import forensic, screener
    from mb.fundamentals import import_file
    store, blob = env
    rep = import_file(store, blob, CSV, "screen.csv")
    assert rep["rows"] == 3 and "pledge" in rep["recognised"]
    assert store.get("fundamentals", "ALPHA")["avg_traded_value_cr"] == pytest.approx(10.0)

    log = logging.getLogger("t")
    out = forensic.run(store=store, log=log)
    assert out["hard"] == 1 and store.get("alerts", "BETA-pledge_above_20")["status"] == "open"
    assert forensic.run(store=store, log=log)["new_alerts"] == 0  # idempotent

    out = screener.run(store=store, log=log)
    assert out["passed"] == 1 and out["added"] == ["ALPHA"]
    assert store.get("candidates", "BETA")["blocked_by"] == "open hard red flag"
    assert not store.get("candidates", "GAMMA")["passed"]


def test_ingestion_fallback_and_dedupe(env):
    import logging

    from mb.agents import ingestion
    store, _ = env

    def nse(a, b):
        raise ConnectionError("blocked")

    def bse(a, b):
        return [{"id": "bse-1", "exchange": "BSE", "symbol": "500001", "company": "X", "category": "Company Update",
                 "subject": "Resignation of Statutory Auditor", "date": str(date.today()), "attachment": None}]

    out = ingestion.run(store=store, log=logging.getLogger("t"), fetchers=[("NSE", nse), ("BSE", bse)])
    assert out["source"] == "BSE" and out["new"] == 1 and "NSE" in out["source_errors"]
    out = ingestion.run(store=store, log=logging.getLogger("t"), fetchers=[("BSE", bse)])
    assert out["new"] == 0

    from mb.agents import forensic
    assert forensic.run(store=store, log=logging.getLogger("t"))["hard"] == 1


def test_runner_respects_pause_and_kill(env, monkeypatch):
    from mb import runner
    store, _ = env
    store.set("agents", "screener", {"enabled": False})
    assert runner.run("screener")["status"] == "skipped"
    store.set("agents", "screener", {"enabled": True})
    store.set("control", "global", {"killed": True})
    assert runner.run("screener")["reason"] == "budget kill switch active"
    with pytest.raises(SystemExit):  # forced run proceeds and fails cleanly: no fundamentals yet
        runner.run("screener", force=True)
    run = store.list("runs")[0]
    assert run["status"] == "failed" and "no fundamentals" in run["summary"]["error"]


def test_budget_kill_switch(env):
    from mb import control
    store, _ = env
    store.set("agents", "forensic", {"enabled": False})
    msg = {"costAmount": 1200.0, "budgetAmount": 2500.0, "currencyCode": "INR", "costIntervalStart": "2026-11-01T00:00:00Z"}
    assert not control.record_budget_notification(msg)["tripped"]
    msg["costAmount"] = 2600.0
    assert control.record_budget_notification(msg)["tripped"]
    assert store.get("control", "global")["killed"]
    assert store.get("agents", "screener")["enabled"] is False
    control.release_kill(by="me")
    assert store.get("agents", "screener")["enabled"] is True
    assert store.get("agents", "forensic")["enabled"] is False  # stays paused as before the kill


def test_portal_pages(env, monkeypatch):
    from fastapi.testclient import TestClient

    from mb import control
    from portal.app import app
    store, _ = env
    started = []
    monkeypatch.setattr(control, "run_now", lambda a: started.append(a) or "ok")
    c = TestClient(app)
    for path in ["/", "/candidates", "/candidates?all=1", "/alerts", "/filings", "/approvals", "/data", "/costs",
                 "/agents/screener"]:
        assert c.get(path).status_code == 200, path
    r = c.post("/data/fundamentals", files={"file": ("s.csv", CSV, "text/csv")}, follow_redirects=False)
    assert r.status_code == 303 and "Imported" in r.headers["location"]
    c.post("/agents/screener/run")
    assert started == ["screener"]
    c.post("/agents/screener/pause")
    assert store.get("agents", "screener")["enabled"] is False
    assert "Resume schedule" in c.get("/").text
    c.post("/kill", data={"action": "kill"})
    assert "Kill switch is on" in c.get("/").text
    assert "Kill switch is on" in c.post("/agents/ingestion/run").text
    assert c.post("/agents/ingestion/run", headers={"origin": "https://evil.example"}).status_code == 403
    store.set("proposals", "p1", {"symbol": "ALPHA", "action": "buy starter", "status": "pending", "tier": "B",
                                  "score": 70, "size_pct": 2.5, "reason": "test", "created": "2026-11-07"})
    assert "ALPHA" in c.get("/approvals").text
    c.post("/approvals/p1", data={"decision": "approved", "note": "ok"})
    assert store.get("proposals", "p1")["status"] == "approved" and store.list("journal")


def test_budget_hook(env):
    from fastapi.testclient import TestClient

    from portal.app import app
    payload = {"costAmount": 10, "budgetAmount": 2500, "currencyCode": "INR", "costIntervalStart": "2026-11-01T00:00:00Z"}
    body = {"message": {"data": base64.b64encode(json.dumps(payload).encode()).decode()}}
    r = TestClient(app).post("/hooks/budget", json=body)
    assert r.json()["cost"] == 10
