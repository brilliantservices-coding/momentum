"""End to end in local mode: weekly run, portal pages, sign-off updates the Momentum book."""
from datetime import date

from fastapi.testclient import TestClient

from mom import weekly
from mom.book import get_book
from mom.store import get_store
from synth import market, sectors


def test_weekly_run_and_signoff():
    px = market()
    store = get_store()
    store.set("mom_settings", "current", {"min_turnover_cr": 1.0})
    out = weekly.run(px=px, sectors=sectors(px), today=date(2026, 11, 7))
    assert out["status"] == "succeeded", out
    wid = out["week"]
    assert wid == "2026-11-06"
    w = store.get("mom_weeks", wid)
    assert w["status"] == "pending" and w["ranking"]
    buys = [c for c in w["changes"] if c["type"] == "buy"]
    assert buys

    from portal.app import app
    client = TestClient(app)
    sc = store.get("mom_screens", wid)
    assert len(sc["rows"]) == 60 and all(len(r["f"]) == len(sc["factors"]) for r in sc["rows"])
    f = w["funnel"]
    assert f[0]["left"] == 60 and all(a["left"] >= b["left"] for a, b in zip(f, f[1:]))
    assert f[-1]["left"] == len(buys)
    assert f[len(sc["factors"])]["left"] == w["counts"]["passing"]
    for path in ("/", "/weeks", f"/weeks/{wid}", f"/weeks/{wid}/screen", "/book", "/runs", "/settings", "/healthz"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:500])
    assert "Sign off ticked changes" in client.get(f"/weeks/{wid}").text
    home = client.get("/").text
    assert "IST" in home and "UTC" not in home and "Next scheduled run" in home
    assert "Failed: Weekly RSI above 60" in client.get(f"/weeks/{wid}/screen").text

    first, second = buys[0], buys[1] if len(buys) > 1 else None
    form = {f"ok_{first['id']}": "on", f"qty_{first['id']}": "7", f"price_{first['id']}": str(first["price"] + 1),
            "note": "test", "action": "signoff"}
    r = client.post(f"/weeks/{wid}/signoff", data=form, follow_redirects=False)
    assert r.status_code == 303
    book = get_book(store)
    assert book["positions"][first["symbol"]]["qty"] == 7
    assert abs(book["cash"] - (1_000_000 - 7 * (first["price"] + 1))) < 0.01
    if second:
        assert second["symbol"] not in book["positions"]
    w = store.get("mom_weeks", wid)
    assert w["status"] == "signed_off" and first["id"] in w["applied"]
    assert store.list("mom_journal")
    # a second sign-off is refused
    r = client.post(f"/weeks/{wid}/signoff", data=form, follow_redirects=True)
    assert "not pending" in r.text
    assert client.get("/book").status_code == 200

    # next week: the held stock is reviewed, nothing crashes, a signed-off week is not overwritten
    assert weekly.run(px=px, sectors=sectors(px), today=date(2026, 11, 7))["status"] == "skipped"
    out2 = weekly.run(px=px, sectors=sectors(px), today=date(2026, 11, 14))
    assert out2["status"] == "succeeded"
    w2 = store.get("mom_weeks", out2["week"])
    assert any(h["symbol"] == first["symbol"] for h in w2["holds"]) or \
        any(c["symbol"] == first["symbol"] for c in w2["changes"])


def test_settings_and_auth(monkeypatch):
    from portal import app as appmod
    client = TestClient(appmod.app)
    r = client.post("/settings", data={"risk_pct": "0.75", "exclude": "abc, xyz"}, follow_redirects=True)
    assert r.status_code == 200
    s = get_store().get("mom_settings", "current")
    assert s["risk_pct"] == 0.75 and s["exclude"] == "ABC, XYZ"
    assert client.post("/settings", data={"risk_pct": "x"}, follow_redirects=True).text.count("must be a number")
    # cross-site posts and unknown users are blocked
    assert client.post("/settings", data={}, headers={"origin": "https://evil.example"}).status_code == 403
    import dataclasses
    monkeypatch.setattr(appmod, "settings", dataclasses.replace(appmod.settings, allowed_users=("me@x.com",)))
    assert client.get("/", headers={"x-goog-authenticated-user-email": "accounts.google.com:other@x.com"}).status_code == 403
    assert client.get("/", headers={"x-goog-authenticated-user-email": "accounts.google.com:me@x.com"}).status_code == 200
