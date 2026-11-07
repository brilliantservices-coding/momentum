import base64
import json

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


NSE_ANN = b"""SYMBOL,COMPANY NAME,SUBJECT,DETAILS,BROADCAST DATE/TIME,ATTACHMENT
ALPHA,Alpha Precision Ltd,Resignation of Statutory Auditors,Resignation of Statutory Auditor M/s XYZ & Co,06-Nov-2026 18:02:11,https://nsearchives.nseindia.com/corporate/ALPHA_1.pdf
BETA,Beta Ltd,Outcome of Board Meeting,Quarterly results,06-Nov-2026 17:00:00,
GAMMA,Gamma Ltd,Preferential Issue,Allotment of convertible warrants on preferential basis,05-Nov-2026 10:00:00,
"""


def test_announcements_upload_flow(env):
    import logging

    from mb.agents import forensic, ingestion
    store, blob = env
    blob.put("filings/inbox/a.csv", NSE_ANN)
    out = ingestion.run(store=store, log=logging.getLogger("t"))
    assert out == {"files": 1, "new_filings": 3, "new_alerts": 2}
    assert blob.list("filings/inbox/") == [] and blob.list("filings/processed/") == ["filings/processed/a.csv"]
    hard = store.list("alerts", where=[("severity", "==", "hard")])
    assert [a["symbol"] for a in hard] == ["ALPHA"]
    blob.put("filings/inbox/b.csv", NSE_ANN)  # same rows again
    assert ingestion.run(store=store, log=logging.getLogger("t"))["new_filings"] == 0
    assert forensic.run(store=store, log=logging.getLogger("t"))["new_alerts"] == 0  # already flagged


def test_bse_export_and_bad_file():
    from mb.announcements import parse
    rows, rep = parse(b"Security Code,Security Name,Category,Headline,News Date\n500001,X Ltd,Company Update,Credit rating downgrade,06-11-2026\n", "bse.csv")
    assert rep["exchange"] == "BSE" and rows[0]["symbol"] == "500001"
    with pytest.raises(ValueError):
        parse(b"a,b\n1,2\n", "x.csv")


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
    r = c.post("/data/announcements", files={"file": ("ann.csv", NSE_ANN, "text/csv")}, follow_redirects=False)
    assert "Queued%203%20NSE" in r.headers["location"] and started[-1] == "ingestion"
    assert "Waiting to load: 1" in c.get("/data").text
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
