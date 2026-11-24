"""Reconciliation of Zerodha holdings and override orders, with Kite mocked."""
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mom import reconcile
from mom.config import DEFAULTS

FACTORS = [{"key": "history", "label": "1 year+ of price history"}, {"key": "rs55", "label": "RS 55 vs Nifty above 0"}]
X_OK = {"w_close": 110, "ma30w": 100, "ma30w_prev": 99, "rs55": 0.05, "rs55_prev": 0.04, "ma10w": 105, "rsi_w": 65,
        "rank_if_held": 3}


def _screen():
    return {"factors": FACTORS, "rows": [
        {"s": "GOOD", "rank": 2, "close": 100.0, "sec": "IT", "f": "11", "setup": "buy: breakout above 98.0", "x": X_OK,
         "v": {"rs55": 5.0, "rs_rating": 90}},
        {"s": "WEAK", "rank": None, "close": 50.0, "sec": "Auto", "f": "10",
         "x": {**X_OK, "w_close": 90, "rank_if_held": 300}, "v": {}},
        {"s": "BOOKED", "rank": 7, "close": 200.0, "sec": "Bank", "f": "11", "x": X_OK, "v": {}},
        {"s": "NEWONE", "rank": 1, "close": 400.0, "sec": "Pharma", "f": "11", "setup": "watch: extended", "x": X_OK,
         "v": {"rs55": 9.0, "rs_rating": 95}},
        {"s": "OUTSIDE", "rank": None, "close": 10.0, "sec": "x", "f": "01", "x": None, "v": {}},
    ]}


def _week(regime="amber"):
    return {"week": "2099-01-02", "status": "pending", "regime": {"regime": regime},
            "changes": [{"id": "c1", "type": "sell", "symbol": "SOLDME", "qty": 10, "reasons": ["stop hit"]}],
            "holds": [{"symbol": "BOOKED", "rank": 7}], "liquid": {"qty_after": 5}}


def test_build_actions():
    book = {"cash": 0, "positions": {"BOOKED": {"qty": 5, "entry": 180.0, "stop": 170.0},
                                      "SOLDME": {"qty": 10, "entry": 50.0, "stop": 45.0},
                                      "MISSING": {"qty": 3, "entry": 10.0, "stop": 9.0}}}
    hold = [{"symbol": "BOOKED", "qty": 4, "t1_qty": 1, "avg": 180, "last": 205},
            {"symbol": "SOLDME", "qty": 10, "avg": 50, "last": 40},
            {"symbol": "GOOD", "qty": 10, "avg": 90, "last": 100},
            {"symbol": "WEAK", "qty": 20, "avg": 60, "last": 50},
            {"symbol": "PENNY", "qty": 100, "avg": 1, "last": 2},
            {"symbol": "LIQUIDBEES", "qty": 2, "avg": 1000, "last": 1000}]
    out = reconcile.build(hold, _week(), _screen(), book, dict(DEFAULTS), book_value=1_000_000, cash_available=200_000)
    by = {r["symbol"]: r for r in out["rows"]}
    assert by["BOOKED"]["action"] == "HOLD" and by["BOOKED"]["kite_qty"] == 5 and by["BOOKED"]["recon"] == "matches the book"
    assert by["SOLDME"]["action"] == "SELL" and by["SOLDME"]["qty"] == 10 and "stop hit" in by["SOLDME"]["reasons"]
    assert by["MISSING"]["action"] == "ADD" and by["MISSING"]["qty"] == 3
    assert by["GOOD"]["action"] == "ADD" and by["GOOD"]["qty"] > 0  # amber: top 5, valid entry
    assert by["WEAK"]["action"] == "SELL" and any("30-week" in x for x in by["WEAK"]["reasons"])
    assert by["PENNY"]["action"] == "REVIEW"
    assert by["LIQUIDBEES"]["action"] == "ADD" and by["LIQUIDBEES"]["qty"] == 3
    assert [c["symbol"] for c in out["candidates"]] == ["NEWONE"]  # passes all, not held
    assert out["candidates"][0]["qty"] > 0 and out["candidates"][0]["stop"] == 368.0
    assert [r["action"] for r in out["rows"]][0] == "SELL"
    red = reconcile.build(hold, _week("red"), _screen(), book, dict(DEFAULTS), 1_000_000, 200_000)
    assert {r["symbol"]: r for r in red["rows"]}["GOOD"]["action"] == "HOLD"


def test_size_and_tick():
    cfg = dict(DEFAULTS)
    # 1% of 10L at an 8% stop on ₹100 = 1250 shares, half in amber = 625; position cap 10% = 1000
    assert reconcile.size(100, 1_000_000, None, cfg, "amber") == 625
    assert reconcile.size(100, 1_000_000, 10_000, cfg, "green") == 100  # cash bound
    assert reconcile.tick(101.03) == 101.05


@pytest.fixture
def portal(monkeypatch):
    from mom import kite
    from mom.store import get_store
    from portal import app as appmod
    store = get_store()
    store.delete("mom_kite", "last")
    store.set("mom_weeks", "2099-01-02", {**_week(), "counts": {}, "book": {}})
    store.set("mom_screens", "2099-01-02", _screen())
    monkeypatch.setattr(kite, "configured", lambda: True)
    sent = []

    def fake_call(path, payload=None, timeout=30):
        sent.append((path, payload))
        if path == "/status":
            return {"connected": True}
        if path == "/check":
            return {"holdings_count": 1, "cash_available": 500000,
                    "holdings": [{"symbol": "WEAK", "qty": 20, "t1_qty": 0, "avg": 60, "last": 50}]}
        if path == "/order":
            return {"order_id": f"O{len(sent)}", "status": "OPEN", "filled_qty": 0, "variety": "amo"}
        if path == "/order-status":
            return {"order_id": payload["order_id"], "status": "COMPLETE", "filled_qty": 10, "avg_price": 401.0}
        raise AssertionError(path)
    monkeypatch.setattr(kite, "call", fake_call)
    yield TestClient(appmod.app), sent, store
    for coll in ("mom_weeks", "mom_screens", "mom_orders", "mom_journal", "mom_portfolio", "mom_kite"):
        for d in store.list(coll):
            store.delete(coll, d["id"])


def test_portal_reconcile_and_override(portal):
    client, sent, store = portal
    assert "Not read yet" in client.get("/reconcile").text
    page = client.post("/reconcile/refresh", follow_redirects=True).text
    assert "Read 1 holdings" in page and "WEAK" in page and "act-sell" in page and "NEWONE" in page
    # review: nothing ticked, then one buy and one bad row
    assert "Tick at least one" in client.post("/orders/review", data={}, follow_redirects=True).text
    form = {"sel_NEWONE": "on", "side_NEWONE": "BUY", "qty_NEWONE": "10", "price_NEWONE": "400.02", "stop_NEWONE": "370",
            "sel_WEAK": "on", "side_WEAK": "SELL", "qty_WEAK": "", "price_WEAK": "50"}
    review = client.post("/orders/review", data=form).text
    assert "Confirm override orders" in review and "400.0" in review and "WEAK: enter a quantity" in review
    assert "Amber" in review
    assert not any(p == "/order" for p, _ in sent)
    # sending needs the confirm box
    form2 = {"sel_NEWONE": "on", "side_NEWONE": "BUY", "qty_NEWONE": "10", "price_NEWONE": "400.0", "stop_NEWONE": "370"}
    assert "tick the confirmation" in client.post("/orders/place", data=form2, follow_redirects=True).text
    assert not any(p == "/order" for p, _ in sent)
    out = client.post("/orders/place", data={**form2, "confirm": "on", "to_book": "on"}, follow_redirects=True).text
    assert "Sent 1 order(s)" in out
    assert ("/order", {"symbol": "NEWONE", "side": "BUY", "qty": 10, "price": 400.0}) in sent
    # over the per-order limit is refused before anything is sent
    big = {**form2, "qty_NEWONE": "100000", "confirm": "on"}
    assert "per-order limit" in client.post("/orders/place", data=big, follow_redirects=True).text
    # the fill is applied to the book once, on status refresh
    client.post("/orders/refresh")
    client.post("/orders/refresh")
    from mom.book import get_book
    pos = get_book(store)["positions"]["NEWONE"]
    assert pos["qty"] == 10 and pos["entry"] == 401.0 and pos["stop"] == 370.0
    assert len([j for j in store.list("mom_journal") if j["type"] == "override_buy"]) == 1


def test_relay_order(tmp_path, monkeypatch):
    monkeypatch.setenv("RELAY_SECRET_MOM_RELAY_TOKEN", "t0k")
    monkeypatch.setenv("KITE_API_KEY", "key1")
    monkeypatch.setenv("RELAY_STATE", str(tmp_path / "s.json"))
    monkeypatch.setenv("RELAY_MAX_ORDER_INR", "50000")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "relay"))
    mod = importlib.reload(importlib.import_module("kite_relay"))
    (tmp_path / "s.json").write_text('{"access_token": "AT", "expires": "2999-01-01T06:00:00+05:30"}')
    calls = []

    class R:
        def __init__(self, d):
            self.status_code, self.d = 200, d

        def json(self):
            return self.d

    def fake(method, url, headers=None, timeout=None, **kw):
        calls.append((method, url, kw.get("data")))
        if method == "POST":
            return R({"data": {"order_id": "123"}})
        return R({"data": [{"status": "OPEN"}, {"status": "COMPLETE", "filled_quantity": 5, "average_price": 99.5}]})
    monkeypatch.setattr(mod.requests, "request", fake)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    c, h = TestClient(mod.app), {"x-relay-token": "t0k"}
    r = c.post("/order", headers=h, json={"symbol": "ABC", "side": "SELL", "qty": 5, "price": 100}).json()
    assert r["status"] == "COMPLETE" and r["filled_qty"] == 5 and r["avg_price"] == 99.5
    d = calls[0][2]
    assert d["transaction_type"] == "SELL" and d["product"] == "CNC" and d["order_type"] == "LIMIT" and d["tag"] == "Momentum"
    assert c.post("/order", headers=h, json={"symbol": "ABC", "side": "BUY", "qty": 1000, "price": 100}).status_code == 400
    assert c.post("/order", headers=h, json={"symbol": "A;B", "side": "BUY", "qty": 1, "price": 1}).status_code == 400
    assert c.post("/order", json={"symbol": "ABC", "side": "BUY", "qty": 1, "price": 1}).status_code == 401
    assert c.post("/order-status", headers=h, json={"order_id": "123"}).json()["status"] == "COMPLETE"


def test_stock_search_and_page(portal):
    client, sent, store = portal
    client.post("/reconcile/refresh")
    r = client.get("/stock?q=good", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/stock/GOOD"
    page = client.get("/stock/GOOD").text
    assert "finance.yahoo.com/quote/GOOD.NS" in page and "Passed every filter" in page and "Ranked 2" in page
    weak = client.get("/stock/WEAK").text
    assert "left the funnel at “RS 55 vs Nifty above 0”" in weak and "Sell signal" in weak and "Zerodha:</strong> 20 shares" in weak
    assert "not in this week" in client.get("/stock/NOPE").text
    assert "NEWONE" in client.get("/stock?q=pharma").text  # sector search
    assert client.get("/stock/A;B").status_code == 404
    rec = client.get("/reconcile").text
    assert 'href="/stock/WEAK"' in rec and 'href="/stock/NEWONE"' in rec
