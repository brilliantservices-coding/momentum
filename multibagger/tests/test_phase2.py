import json
import logging
import subprocess

import pytest

from test_pipeline import CSV, NSE_ANN

LOG = logging.getLogger("t")


def test_conviction_rules():
    from mb.rules import conviction
    full = {"theme": 15, "financial": 20, "management": 20, "visibility": 15, "ecosystem": 10, "valuation": 10,
            "governance": 10}
    assert conviction(full) == {**conviction(full), "score": 100.0, "tier": "A"}  # 100 + 5 bonus, capped
    assert conviction({**full, "financial": 99})["families"]["financial"] == 20  # clipped to weight
    two_strong = {"theme": 15, "financial": 20, "management": 13, "visibility": 10, "ecosystem": 7, "valuation": 7}
    r = conviction(two_strong)
    assert r["strong_families"] == 2 and r["score"] == 60 and r["tier"] == "C"
    assert conviction(full, hard_flags=1)["score"] == 0
    assert conviction(full, soft_flags=3)["tier"] == "Reject"
    assert conviction({"financial": 16, "management": 15, "visibility": 12, "governance": 9, "theme": 13})["tier"] == "B"


class FakeCLI:
    def __init__(self, replies, cost=0.05):
        self.replies, self.cost, self.calls = list(replies), cost, []

    def __call__(self, cmd, input, capture_output, text, timeout):
        self.calls.append((cmd, input))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        out = {"type": "result", "is_error": False, "result": reply if isinstance(reply, str) else
               "```json\n" + json.dumps(reply) + "\n```", "total_cost_usd": self.cost}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")


@pytest.fixture()
def cli(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")

    def install(replies, cost=0.05):
        fake = FakeCLI(replies, cost)
        monkeypatch.setattr(subprocess, "run", fake)
        return fake
    return install


def test_llm_subscription_and_cap(env, cli):
    from mb import llm
    store, _ = env
    fake = cli([{"ok": 1}], cost=0.6)
    assert llm.complete_json("sys", "hello", agent="t", store=store) == {"ok": 1}
    cmd, prompt = fake.calls[0]
    assert cmd[:2] == ["claude", "-p"] and "--tools" in cmd and prompt == "hello"
    assert "claude-opus-5-5" in cmd and llm.spend_today(store)["calls"] == 1
    llm.complete_json("sys", "again", store=store)  # 0.6 spent < 1.0 cap, allowed
    with pytest.raises(llm.BudgetExceeded):
        llm.complete_json("sys", "third", store=store)
    store.set("control", "llm", {"daily_cap_usd": 5, "model": "claude-haiku-5-5"})
    llm.complete_json("sys", "fourth", store=store)
    assert "claude-haiku-5-5" in fake.calls[-1][0]


def test_llm_errors(env, cli, monkeypatch):
    from mb import llm
    store, _ = env
    cli(["sorry, no json here"])
    with pytest.raises(llm.LLMError):
        llm.complete_json("s", "p", store=store)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    with pytest.raises(llm.LLMError, match="setup-token"):
        llm.complete_json("s", "p", store=store)


def _seed_watchlist(store, blob):
    from mb.agents import forensic, ingestion, screener
    from mb.fundamentals import import_file
    import_file(store, blob, CSV, "screen.csv")
    forensic.run(store=store, log=LOG)
    screener.run(store=store, log=LOG)
    blob.put("filings/inbox/a.csv", NSE_ANN.replace(b"https://nsearchives.nseindia.com/corporate/ALPHA_1.pdf", b""))
    ingestion.run(store=store, log=LOG)
    for a in store.list("alerts", where=[("symbol", "==", "ALPHA")]):  # reviewed and dismissed by the human
        store.set("alerts", a["id"], {"status": "dismissed"}, merge=True)


FAMILIES = {k: {"points": v, "evidence": [f"fund:{k}"]} for k, v in
            {"theme": 13, "financial": 18, "management": 16, "visibility": 12, "ecosystem": 8, "valuation": 8,
             "governance": 9}.items()}


def test_research_pipeline(env, cli):
    from mb.agents import analyst, extraction, portfolio, redteam, theme
    store, blob = env
    _seed_watchlist(store, blob)
    assert [c["id"] for c in store.list("candidates", where=[("passed", "==", True)])] == ["ALPHA"]

    cli([{"themes": [{"id": "defence", "name": "Defence indigenisation", "active": True,
                      "scores": {"demand_runway": 5, "policy_support": 5, "capital_cycle": 4, "earnings_breadth": 4,
                                 "narrative_stage": 9, "valuation_headroom": 2}}]}])
    assert theme.run(store=store, log=LOG) == {"themes": 1, "active": ["defence"]}
    assert store.get("themes", "defence")["total"] == 25  # out-of-range 9 clipped to 5

    fake = cli([{"facts": [{"filing_id": "x-unknown", "symbol": "ALPHA", "type": "other", "statement": "ignored"},
                           {"filing_id": "PLACEHOLDER", "symbol": "ALPHA", "type": "auditor_remark",
                            "statement": "Statutory auditor resigned"}]}])
    fid = store.list("filings", where=[("symbol", "==", "ALPHA")])[0]["id"]
    fake.replies[0]["facts"][1]["filing_id"] = fid
    assert extraction.run(store=store, log=LOG) == {"filings": 1, "facts": 1}
    assert extraction.run(store=store, log=LOG)["filings"] == 0  # marked extracted

    cli([{"archetype": "Small leader in a large, growing market", "summary": "s", "families": FAMILIES,
          "drivers": [{"driver": "d", "proof_metric": "m"}], "key_risks": ["r"], "kill_criteria": ["k"]}])
    out = analyst.run(store=store, log=LOG)
    s = store.get("scores", "ALPHA")
    assert out["analysed"] == 1 and s["score"] == 89.0 and s["tier"] == "A" and s["redteam_needed"]
    assert store.get("memos", "ALPHA")["summary"] == "s"

    assert portfolio.run(store=store, log=LOG)["new_proposals"] == 0  # waits for the red team

    cli([{"bear_case": "b", "missed_risks": [], "score_adjustment": -30, "verdict": "lower score"}])
    assert redteam.run(store=store, log=LOG)["reviewed"] == 1
    s = store.get("scores", "ALPHA")
    assert s["score"] == 74.0 and s["tier"] == "B" and not s["redteam_needed"]  # adjustment clipped to -15

    assert portfolio.run(store=store, log=LOG)["new_proposals"] == 1
    p = store.list("proposals", where=[("status", "==", "pending")])[0]
    assert p["action"] == "buy starter" and p["size_pct"] == 2.5
    assert portfolio.run(store=store, log=LOG)["new_proposals"] == 0  # already pending


def test_analyst_stops_at_cap(env, cli):
    from mb.agents import analyst
    store, blob = env
    _seed_watchlist(store, blob)
    store.set("control", "llm", {"daily_cap_usd": 0})
    cli([{}])
    assert analyst.run(store=store, log=LOG)["analysed"] == 0


def test_phase2_agents_start_paused(env):
    from mb import control, runner
    store, _ = env
    assert runner.run("analyst")["reason"] == "agent paused in portal"
    control.set_enabled("analyst", True)
    assert runner.run("portfolio")["status"] == "skipped"
    control.kill_all("test", by="me")
    control.release_kill(by="me")
    assert store.get("agents", "analyst")["enabled"] is True
    assert store.get("agents", "portfolio")["enabled"] is False  # was off by default, stays off


def test_portal_phase2(env, monkeypatch):
    from fastapi.testclient import TestClient

    from portal.app import app
    store, _ = env
    c = TestClient(app)
    home = c.get("/").text
    assert home.count("Resume schedule") == 5  # the five phase 2 agents ship paused
    assert c.get("/research").status_code == 200 and c.get("/research/NOPE").status_code == 404
    assert "Claude subscription" in c.get("/settings").text
    r = c.post("/settings", data={"provider": "vertex", "model": "claude-sonnet-5-5", "effort": "low",
                                  "daily_cap_usd": "2"}, follow_redirects=False)
    assert r.status_code == 303 and store.get("control", "llm")["provider"] == "vertex"
    assert c.post("/settings", data={"provider": "x", "model": "claude-sonnet-5-5", "effort": "low",
                                     "daily_cap_usd": "2"}).status_code == 400
    from mb.rules import conviction
    store.set("scores", "ALPHA", {"symbol": "ALPHA", **conviction({k: v["points"] for k, v in FAMILIES.items()}),
                                  "redteam_needed": False})
    store.set("memos", "ALPHA", {"summary": "Thesis text", "families": FAMILIES, "kill_criteria": ["k"]})
    store.set("redteam", "ALPHA", {"bear_case": "Bear text", "score_before": 89, "score_after": 80})
    assert "ALPHA" in c.get("/research").text
    page = c.get("/research/alpha").text
    assert "Thesis text" in page and "Bear text" in page and "fund:financial" in page
    store.set("proposals", "p1", {"symbol": "ALPHA", "action": "buy starter", "status": "pending", "tier": "A",
                                  "score": 80, "size_pct": 4, "created": "2026-11-07"})
    c.post("/approvals/p1", data={"decision": "approved"})
    assert store.get("holdings", "ALPHA")["size_pct"] == 4
    store.set("proposals", "p2", {"symbol": "ALPHA", "action": "exit", "status": "pending", "created": "2026-11-08"})
    c.post("/approvals/p2", data={"decision": "approved"})
    assert store.get("holdings", "ALPHA") is None


class FakeOpenAI:
    calls = []

    def __init__(self, timeout=None):
        from types import SimpleNamespace as NS
        outer = self

        class Raw:
            headers = {"x-ratelimit-remaining-requests": "499", "x-ratelimit-limit-requests": "500",
                       "x-ratelimit-remaining-tokens": "199000", "x-ratelimit-limit-tokens": "200000",
                       "content-type": "application/json"}

            def parse(self):
                return NS(status="completed", output_text='{"ok": 2}', incomplete_details=None, error=None,
                          usage=NS(input_tokens=1_000_000, output_tokens=100_000))

        def create(**kw):
            FakeOpenAI.calls.append(kw)
            return Raw()
        outer.responses = NS(with_raw_response=NS(create=create))
        outer.models = NS(list=lambda: [NS(id="gpt-test-1"), NS(id="gpt-audio-x"), NS(id="dall-e-3")])


def test_llm_openai(env, monkeypatch):
    import openai

    from mb import llm
    store, _ = env
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    store.set("control", "llm", {"provider": "openai", "model": "gpt-test-1", "effort": "low", "daily_cap_usd": 5,
                                 "openai_price_in": 2, "openai_price_out": 10})
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY"):
        llm.complete_json("sys", "p", store=store)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.complete_json("sys", "hello", agent="a", store=store) == {"ok": 2}
    kw = FakeOpenAI.calls[-1]
    assert kw["model"] == "gpt-test-1" and kw["reasoning"] == {"effort": "low"} and kw["input"] == "hello"
    assert llm.spend_today(store)["usd"] == 3.0  # 1M in x $2 + 0.1M out x $10
    lim = llm.limits(store)["openai"]
    assert lim["remaining_requests"] == "499" and lim["limit_tokens"] == "200000"
    assert llm.month_usage(store)["openai"] == {"usd": 3.0, "calls": 1}
    llm._models_cache.clear()
    assert llm.openai_models() == ["gpt-test-1"]
    assert llm.valid_model("openai", "gpt-6.1-sol") and not llm.valid_model("openai", "bad model!")
    assert not llm.valid_model("vertex", "gpt-5.5")


def test_portal_settings_openai(env, monkeypatch):
    from fastapi.testclient import TestClient

    from portal.app import app
    store, _ = env
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c = TestClient(app)
    page = c.get("/settings").text
    assert "OpenAI API" in page and "key missing" in page and "left" in page
    r = c.post("/settings", data={"provider": "openai", "openai_model": "gpt-5.5", "openai_model_custom": "gpt-7-new",
                                  "effort": "medium", "daily_cap_usd": "3", "openai_price_in": "1.5",
                                  "openai_price_out": "12"}, follow_redirects=False)
    cfg = store.get("control", "llm")
    assert r.status_code == 303 and cfg["provider"] == "openai" and cfg["model"] == "gpt-7-new"
    assert cfg["openai_price_out"] == 12
    assert c.post("/settings", data={"provider": "vertex", "model": "gpt-5.5", "effort": "low",
                                     "daily_cap_usd": "2"}).status_code == 400
    store.set("control", "llm_limits", {"openai": {"remaining_requests": "42", "limit_requests": "500",
                                                   "at": "2026-11-07T10:00:00+00:00", "model": "gpt-7-new"}})
    assert "42 of 500" in c.get("/settings").text
