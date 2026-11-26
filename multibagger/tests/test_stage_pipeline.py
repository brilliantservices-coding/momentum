"""Overview pipeline: readiness per stage, running the stages one after another, run in place."""
import logging

LOG = logging.getLogger("t")


def _starts(monkeypatch):
    import mb.control
    started = []

    def fake_run_now(agent_id, redo=False, exclude=None, env=None):
        started.append((agent_id, dict(env or {})))
        return "started"
    monkeypatch.setattr(mb.control, "run_now", fake_run_now)
    return started


def test_ready_reports_waiting_input(env):
    from mb import pipeline
    store, blob = env
    blob.put("inbox/nse/announcements/a.csv", b"x")
    store.set("agents", "ingestion", {"last_status": "succeeded", "last_started": "2026-11-20T00:00:00+00:00"})
    store.set("agents", "universe", {"last_ok_started": pipeline.now_iso()})
    info = pipeline.ready(store)
    assert info["ingestion"]["due"] and info["ingestion"]["n"] == 1
    assert not info["universe"]["due"] and "refreshed 0 days ago" in info["universe"]["why"]
    assert info["fundamentals"]["due"]  # nothing loaded yet
    assert not info["redteam"]["due"] and not info["portfolio"]["due"]
    store.set("scores", "ALPHA", {"tier": "B", "score": 70, "redteam_needed": True})
    info = pipeline.ready(store)
    assert info["redteam"]["due"] and info["redteam"]["n"] == 1 and not info["portfolio"]["due"]
    store.set("scores", "ALPHA", {"tier": "B", "score": 70, "redteam_needed": False}, merge=True)
    assert pipeline.ready(store)["portfolio"]["n"] == 1


def test_pipeline_runs_stages_in_order_and_skips_idle(env, monkeypatch):
    from mb import pipeline
    store, blob = env
    started = _starts(monkeypatch)
    now = pipeline.now_iso()
    for a in pipeline.ORDER:
        store.set("agents", a, {"last_ok_started": now, "last_status": "succeeded", "last_finished": now})
    store.set("control", "fundamentals", {"imported": now})
    blob.put("inbox/nse/announcements/a.csv", b"x")
    doc = pipeline.start(store)
    assert doc["current"] == "ingestion" and started[-1] == ("ingestion", {"MB_PIPELINE": doc["id"]})
    assert [s["agent"] for s in doc["steps"]] == ["universe", "fundamentals"]  # nothing new: skipped
    # ingestion done; the forensic agent now has new filings
    store.set("filings", "f1", {"symbol": "ALPHA", "ingested": "2999-01-01T00:00:00+00:00"})
    monkeypatch.setenv("MB_PIPELINE", doc["id"])
    assert pipeline.advance(store, "ingestion", "succeeded", {"files": 1}) == "pipeline: started forensic"
    store.set("agents", "forensic", {"last_finished": "2999-01-02T00:00:00+00:00"}, merge=True)
    assert pipeline.advance(store, "forensic", "succeeded", {}) == "pipeline: started screener"
    out = pipeline.advance(store, "screener", "failed", {"error": "boom"})
    assert out.startswith("pipeline stopped: screener failed")
    assert pipeline.state(store)["status"] == "stopped"
    assert pipeline.advance(store, "extraction", "succeeded", {}) == "pipeline not active"


def test_run_from_here_and_finish(env, monkeypatch):
    from mb import pipeline
    store, _ = env
    started = _starts(monkeypatch)
    doc = pipeline.start(store, first="portfolio")
    assert doc["current"] == "portfolio" and started[-1][0] == "portfolio"
    monkeypatch.setenv("MB_PIPELINE", doc["id"])
    assert pipeline.advance(store, "portfolio", "succeeded", {}) == "pipeline finished"


def test_runner_hands_over_to_next_stage(env, monkeypatch):
    from mb import pipeline, runner
    store, _ = env
    started = _starts(monkeypatch)
    monkeypatch.setattr("mb.control.active_runs", lambda *a, **k: [])
    store.set("scores", "ALPHA", {"tier": "B", "score": 70, "redteam_needed": False})
    doc = pipeline.start(store, first="redteam")
    monkeypatch.setenv("MB_PIPELINE", doc["id"])
    out = runner.run("redteam", force=True)
    assert out["summary"]["pipeline"] == "pipeline: started portfolio" and started[-1][0] == "portfolio"
    assert store.get("agents", "redteam")["last_ok_started"]


def test_scheduled_run_continues_only_when_switched_on(env, monkeypatch):
    from mb import runner
    store, _ = env
    started = _starts(monkeypatch)
    monkeypatch.setattr("mb.control.active_runs", lambda *a, **k: [])
    monkeypatch.delenv("MB_PIPELINE", raising=False)
    store.set("agents", "redteam", {"enabled": True})
    store.set("scores", "ALPHA", {"tier": "B", "score": 70, "redteam_needed": False})
    assert runner.run("redteam")["summary"]["pipeline"] == "pipeline not active" and not started
    store.set("control", "llm", {"pipeline_auto": True})
    assert runner.run("redteam")["summary"]["pipeline"] == "pipeline: started portfolio"


def test_overview_pipeline_runs_in_place(env, monkeypatch):
    from fastapi.testclient import TestClient

    from portal.app import app
    store, blob = env
    started = _starts(monkeypatch)
    blob.put("inbox/nse/announcements/a.csv", b"x")
    c = TestClient(app)
    page = c.get("/").text
    assert 'id="pipeline"' in page and "Run pipeline" in page and "Ready: 1 · 1 announcement file" in page
    assert page.index("Universe builder") < page.index("Screener agent") < page.index("Portfolio agent")
    r = c.post("/agents/screener/run", headers={"X-Requested-With": "fetch"})
    assert r.json() == {"ok": True, "msg": "Run started"} and started[-1][0] == "screener"
    s = c.get("/pipeline/status").json()
    assert s["agents"]["screener"]["status"] == "starting"
    assert "starting" in c.get("/pipeline/view").text
    r = c.post("/pipeline/run", data={"start": "ingestion"}, headers={"X-Requested-With": "fetch"})
    assert r.json()["ok"] and c.get("/pipeline/status").json()["pipeline"]["current"] == "ingestion"
    assert "pipeline running" in c.get("/pipeline/view").text
    assert not c.post("/pipeline/run", headers={"X-Requested-With": "fetch"}).json()["ok"]  # one at a time
    assert c.post("/pipeline/stop", headers={"X-Requested-With": "fetch"}).json()["ok"]
    assert c.get("/pipeline/status").json()["pipeline"]["status"] == "stopped"
    r = c.post("/agents/screener/run", follow_redirects=False)  # without JavaScript: the old redirect
    assert r.status_code == 303
