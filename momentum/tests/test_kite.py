"""Kite relay and portal Zerodha page, with Kite's HTTP API mocked."""
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RELAY = Path(__file__).resolve().parent.parent / "relay"


class FakeResp:
    def __init__(self, data, code=200):
        self.status_code, self._d = code, data
        self.text = "34.93.168.72"

    def json(self):
        return self._d


@pytest.fixture
def relay(tmp_path, monkeypatch):
    monkeypatch.setenv("RELAY_SECRET_MOM_RELAY_TOKEN", "t0k")
    monkeypatch.setenv("RELAY_SECRET_MOM_KITE_API_SECRET", "sec")
    monkeypatch.setenv("KITE_API_KEY", "key1")
    monkeypatch.setenv("RELAY_STATE", str(tmp_path / "s.json"))
    sys.path.insert(0, str(RELAY))
    mod = importlib.reload(importlib.import_module("kite_relay"))
    calls = []

    def fake(method, url, headers=None, timeout=None, **kw):
        calls.append((method, url, kw.get("data"), headers))
        if url.endswith("/session/token"):
            return FakeResp({"status": "success", "data": {"access_token": "AT", "user_id": "TOA478"}})
        if url.endswith("/user/profile"):
            return FakeResp({"data": {"user_id": "TOA478", "user_name": "S"}})
        if url.endswith("/user/margins/equity"):
            return FakeResp({"data": {"available": {"live_balance": 5000}, "net": 5000}})
        if url.endswith("/portfolio/holdings"):
            return FakeResp({"data": [{"tradingsymbol": "X", "quantity": 1}]})
        if method == "POST" and "/orders/" in url:
            return FakeResp({"data": {"order_id": "O1"}})
        if method == "DELETE":
            return FakeResp({"data": {"order_id": "O1"}})
        if url.endswith("/orders/O1"):
            return FakeResp({"data": [{"status": "OPEN"}, {"status": "CANCELLED"}]})
        return FakeResp({"status": "error", "message": "unexpected"}, 400)

    monkeypatch.setattr(mod.requests, "request", fake)
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: FakeResp(None))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod, TestClient(mod.app), calls


def test_relay_requires_token(relay):
    _, c, _ = relay
    assert c.post("/check").status_code == 401
    assert c.post("/check", headers={"x-relay-token": "wrong"}).status_code == 401


def test_relay_session_check_and_test_order(relay):
    mod, c, calls = relay
    h = {"x-relay-token": "t0k"}
    assert c.post("/check", headers=h).status_code == 409  # not logged in yet
    r = c.post("/session", headers=h, json={"request_token": "RT"})
    assert r.status_code == 200 and r.json()["user_id"] == "TOA478"
    import hashlib
    assert calls[0][2]["checksum"] == hashlib.sha256(b"key1RTsec").hexdigest()
    assert "AT" not in r.text  # the access token never leaves the VM
    chk = c.post("/check", headers=h).json()
    assert chk["cash_available"] == 5000 and chk["egress_ip"] == "34.93.168.72"
    assert any(x[3].get("Authorization") == "token key1:AT" for x in calls)
    t = c.post("/test-order", headers=h, json={"reference_price": 1000.0}).json()
    order = next(x for x in calls if x[0] == "POST" and "/orders/" in x[1])
    assert order[2]["tradingsymbol"] == "LIQUIDBEES" and order[2]["quantity"] == 1
    assert order[2]["price"] == 970.0 and order[2]["tag"] == "Momentum" and order[2]["order_type"] == "LIMIT"
    assert any(x[0] == "DELETE" for x in calls)
    assert t["final_status"] == "CANCELLED" and not t["filled"]
    assert c.post("/test-order", headers=h, json={"reference_price": 5}).status_code == 400


def test_portal_zerodha_page(monkeypatch):
    from mom import kite
    from portal import app as appmod
    client = TestClient(appmod.app)
    assert "not set up yet" in client.get("/zerodha").text
    monkeypatch.setattr(kite, "configured", lambda: True)
    monkeypatch.setattr(kite, "API_KEY", "key1")
    sent = []

    def fake_call(path, payload=None, timeout=30):
        sent.append((path, payload))
        return {"/status": {"connected": False}, "/session": {"user_id": "TOA478"},
                "/check": {"user_id": "TOA478", "user_name": "S", "egress_ip": "34.93.168.72", "cash_available": 1,
                           "holdings_count": 0},
                "/test-order": {"order_id": "O1", "final_status": "CANCELLED", "history": [], "price": 970}}[path]
    monkeypatch.setattr(kite, "call", fake_call)
    assert "Connect Zerodha" in client.get("/zerodha").text
    r = client.get("/zerodha/login", follow_redirects=False)
    assert r.headers["location"].startswith("https://kite.zerodha.com/connect/login?v=3&api_key=key1")
    assert "Connected to Zerodha as TOA478" in client.get("/kite/callback?request_token=RT&status=success").text
    assert "whitelisted static IP" in client.post("/zerodha/check", follow_redirects=True).text
    # the test order needs the confirmation box and a LIQUIDBEES price from a weekly run
    assert "Tick the box" in client.post("/zerodha/test-order", data={}, follow_redirects=True).text
    from mom.store import get_store
    get_store().set("mom_weeks", "2099-01-02", {"week": "2099-01-02", "liquid": {"price": 1000.0}, "regime": {"regime": "red"},
                                                "changes": [], "counts": {}, "book": {}})
    out = client.post("/zerodha/test-order", data={"confirm": "on"}, follow_redirects=True).text
    assert "Test order O1: CANCELLED" in out
    assert ("/test-order", {"reference_price": 1000.0}) in sent
    get_store().delete("mom_weeks", "2099-01-02")
