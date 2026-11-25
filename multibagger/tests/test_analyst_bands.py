"""Company analyst bands: full memos for the top of the queue, quick triage for the next band, nothing for the tail."""
import logging

from test_phase2 import FAMILIES, V2, _seed_watchlist

LOG = logging.getLogger("t")


def _cands(store, n):
    for i in range(n):
        store.set("candidates", f"S{i:02d}", {"symbol": f"S{i:02d}", "name": f"Stock {i}", "passed": True, "met": 6,
                                              "known": 7, "lanes": {"compounder": True, "inflection": False},
                                              "research_priority": 100 - i, "playbook": "general", "mcap": 2000, "pe": 20,
                                              "rules": {"growth": True}})


def test_bands(env):
    from mb.agents._context import analyst_bands
    store, _ = env
    _cands(store, 10)
    store.set("control", "llm", {"analyst_deep_count": 2, "analyst_light_count": 3})
    store.set("scores", "S07", {"tier": "C", "score": 55})                        # full analysis at tier C
    store.set("scores", "S08", {"depth": "triage", "tier": "Triage", "triage_score": 64})  # promoted
    store.set("documents", "d1", {"symbol": "S09"})
    store.set("candidates", "S06", {"signal_groups": ["event", "margin"]}, merge=True)
    b = analyst_bands(store)
    deep = sorted(k for k, v in b.items() if v["band"] == "deep")
    assert deep == ["S00", "S01", "S06", "S07", "S08", "S09"]
    assert [k for k, v in sorted(b.items()) if v["band"] == "light"] == ["S02", "S03", "S04"]
    assert b["S05"]["band"] == "tail"


def test_analyst_runs_by_band(env, cli):
    from mb.agents import analyst
    store, _ = env
    _cands(store, 6)
    store.set("control", "llm", {"analyst_deep_count": 1, "analyst_light_count": 2, "triage_model": "claude-haiku-5-5"})
    for i in range(6):
        store.set("fundamentals", f"S{i:02d}", {"name": f"Stock {i}", "price": 100, "roce": 20})
    fake = cli([{**V2, "summary": "s", "families": FAMILIES},
                {"archetype": "x", "summary": "quick", "families": {"theme": 12, "financial": 16, "management": 14,
                                                                    "visibility": 12, "ecosystem": 6, "valuation": 7,
                                                                    "governance": 6}}])
    out = analyst.run(store=store, log=LOG)
    assert out["bands"] == {"deep": 1, "light": 2, "tail": 3}
    assert list(out["scores"]) == ["S00"] and sorted(out["triage_scores"]) == ["S01", "S02"]
    assert len(fake.calls) == 3 and "claude-haiku-5-5" in fake.calls[1][0] and "--effort" in fake.calls[1][0]
    t = store.get("scores", "S01")
    assert t["depth"] == "triage" and t["tier"] == "Triage" and not t["redteam_needed"]
    assert t["triage_score"] == 78.0  # 12+16+14+12+6+7+6, +5 for five strong families
    assert store.get("scores", "S03") is None  # tail: no call
    # Next run: nothing to do (deep unchanged, triages fresh); the 78 triages is promoted to a full memo.
    fake = cli([{**V2, "summary": "s2", "families": FAMILIES}])
    out = analyst.run(store=store, log=LOG)
    assert sorted(out["scores"]) == ["S01", "S02"]
    assert sorted(out["triage_scores"]) == ["S03", "S04"]  # the triage band moves down to the next two
    assert store.get("scores", "S01")["depth"] == "deep"


def test_recent_memo_not_redone_for_a_new_headline(env, cli):
    from mb.agents import analyst
    store, blob = env
    _seed_watchlist(store, blob)
    cli([{**V2, "summary": "s", "families": FAMILIES}])
    assert analyst.run(store=store, log=LOG)["analysed"] == 1
    store.set("filings", "new1", {"symbol": "ALPHA", "subject": "Newspaper advertisement", "ingested": "2099-01-01"})
    out = analyst.run(store=store, log=LOG)
    assert out["analysed"] == 0 and out["recent_skipped"] == 1
    store.set("facts", "f-new", {"symbol": "ALPHA", "type": "order_win", "statement": "Won an order"})
    assert analyst.run(store=store, log=LOG)["analysed"] == 1  # new evidence


def test_portal_shows_bands(env, cli):
    from fastapi.testclient import TestClient

    from portal.app import app
    store, _ = env
    _cands(store, 4)
    store.set("control", "llm", {"analyst_deep_count": 1, "analyst_light_count": 1})
    for i in range(4):
        store.set("fundamentals", f"S{i:02d}", {"name": f"Stock {i}"})
    c = TestClient(app)
    s = c.get("/settings").text
    assert "Which stocks the company analyst covers" in s and "<strong>1</strong> stocks get a full analysis" in s
    assert "tail: screen only" in c.get("/candidates").text
    assert "analyst band: Not analysed" in c.get("/stock/S03").text
    assert "below the analyst" in c.get("/funnel").text
    r = c.post("/settings", data={"provider": "subscription", "model": "claude-opus-5-5", "effort": "medium",
                                  "daily_cap_usd": 20, "analyst_deep_count": 30, "analyst_light_count": 60,
                                  "analyst_redo_days": 21, "triage_model": "claude-haiku-5-5"}, follow_redirects=False)
    assert r.status_code == 303 and store.get("control", "llm")["analyst_deep_count"] == 30
