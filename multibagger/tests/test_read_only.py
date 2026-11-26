"""Read-only viewers: they see every page, the landing page is the funnel, and every action is refused
by the server (not just greyed out)."""
import dataclasses

import pytest

ADMIN, VIEWER = "shreyas.bhatewara@gmail.com", "viewer@example.com"


@pytest.fixture()
def portal(env, monkeypatch):
    from fastapi.testclient import TestClient

    import portal.app as pa
    monkeypatch.setattr(pa, "settings", dataclasses.replace(
        pa.settings, allowed_users=(ADMIN, "services.brilliant@gmail.com", "shreyasbforu@gmail.com"),
        admin_users=(ADMIN, "services.brilliant@gmail.com"), viewer_users=(VIEWER,)))
    started = []
    monkeypatch.setattr("mb.control.run_now", lambda *a, **k: started.append(a) or "started")

    def client(email):
        return TestClient(pa.app, headers={"x-goog-authenticated-user-email": f"accounts.google.com:{email}"})
    return pa, client, started


def test_roles(portal):
    pa, _, _ = portal
    assert pa.role_of(ADMIN) == "admin" and pa.role_of("services.brilliant@gmail.com") == "admin"
    assert pa.role_of("shreyasbforu@gmail.com") == "viewer" and pa.role_of(VIEWER) == "viewer"
    assert pa.role_of("stranger@example.com") is None


def test_viewer_sees_pages_read_only(portal):
    pa, client, _ = portal
    from tests.test_analyst_bands import _cands
    _cands(pa.get_store(), 1)
    v = client(VIEWER)
    home = v.get("/")
    assert home.status_code == 200 and "<h1>Stock funnel" in home.text and "view only" in home.text
    over = v.get("/overview").text
    assert 'id="pipeline"' in over and "window.mbLock = " in over and pa.NOT_ALLOWED in over
    for path in ("/funnel", "/candidates", "/alerts", "/filings", "/research", "/approvals", "/data", "/costs",
                 "/settings", "/agents/screener", "/stock/S00", "/pipeline/view", "/pipeline/status"):
        assert v.get(path).status_code == 200, path
    assert v.get("/funnel?q=alp").status_code == 200  # search forms (GET) still work
    assert "window.mbLock = " in v.get("/stock/S00").text
    a = client(ADMIN)
    page = a.get("/").text
    assert 'id="pipeline"' in page and "window.mbLock = " not in page and "view only" not in page
    assert client("stranger@example.com").get("/").status_code == 403


def test_viewer_cannot_post_anything(portal):
    pa, client, started = portal
    from fastapi.routing import APIRoute
    v = client(VIEWER)
    posts = [r.path for r in pa.app.routes if isinstance(r, APIRoute) and "POST" in r.methods
             and r.path not in pa.OPEN_PATHS]
    assert len(posts) >= 12
    fill = {"agent_id": "screener", "action": "run", "alert_id": "x", "pid": "x", "symbol": "ALPHA", "email": "a@x.com"}
    for path in posts:
        url = path.format(**fill)
        r = v.post(url, data={"action": "kill", "provider": "subscription", "effort": "low", "daily_cap_usd": 50})
        assert r.status_code == 403, url
        r = v.post(url, headers={"X-Requested-With": "fetch"})
        assert r.status_code == 403 and r.json()["ok"] is False, url
    assert v.post("/agents/screener/run").text == pa.NOT_ALLOWED
    assert v.post("/settings").text == pa.NOT_ALLOWED_CHANGE
    assert not started and not (pa.get_store().get("control", "global") or {}).get("killed")
    r = client(ADMIN).post("/agents/screener/run", headers={"X-Requested-With": "fetch"})
    assert r.json()["ok"] and started


def test_open_signup_cap_and_block(env, monkeypatch):
    from fastapi.testclient import TestClient

    import portal.app as pa
    monkeypatch.setattr(pa, "settings", dataclasses.replace(
        pa.settings, allowed_users=(ADMIN, "shreyasbforu@gmail.com"), admin_users=(ADMIN,), viewer_users=("*",)))
    store = pa.get_store()
    store.set("control", "viewers", {"cap": 2})

    def get(email, path="/"):
        return TestClient(pa.app, headers={"x-goog-authenticated-user-email": f"accounts.google.com:{email}"}).get(path)
    assert get("a@x.com").status_code == 200 and get("b@x.com").status_code == 200
    assert get("shreyasbforu@gmail.com").status_code == 200  # named: not counted
    full = get("c@x.com")
    assert full.status_code == 403 and "limit of viewers" in full.text
    assert get("a@x.com").status_code == 200  # already in: still fine
    assert store.get("viewers", "a@x.com")["first_seen"] and store.get("viewers", "shreyasbforu@gmail.com")["named"]
    assert get("a@x.com", "/viewers").status_code == 403  # viewers can't see the list
    admin = TestClient(pa.app, headers={"x-goog-authenticated-user-email": f"accounts.google.com:{ADMIN}"})
    page = admin.get("/viewers").text
    assert "a@x.com" in page and "2 of 2" in page
    admin.post("/viewers/a@x.com/block")
    blocked = get("a@x.com")
    assert blocked.status_code == 403 and "removed" in blocked.text
    assert get("c@x.com").status_code == 200  # blocking frees the place
    assert get("d@x.com").status_code == 403
    admin.post("/viewers/b@x.com/remove")
    assert get("d@x.com").status_code == 200  # so does removing
    assert get("a@x.com").status_code == 403  # still blocked
    admin.post("/viewers/a@x.com/unblock")
    assert get("a@x.com").status_code == 200
    v = TestClient(pa.app, headers={"x-goog-authenticated-user-email": "accounts.google.com:c@x.com"})
    assert v.post("/viewers/a@x.com/block").status_code == 403 and v.post("/viewers/cap", data={"cap": 99}).status_code == 403
