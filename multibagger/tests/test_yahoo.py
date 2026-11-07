import logging

import pandas as pd
import pytest

LOG = logging.getLogger("t")
YEARS = pd.to_datetime(["2026-03-31", "2025-03-31", "2024-03-31", "2023-03-31"])
CR = 1e7


def frame(rows):
    return pd.DataFrame({k: [v * CR if v is not None else float("nan") for v in vals] for k, vals in rows.items()},
                        index=YEARS).T


INCOME = frame({"Total Revenue": [1000, 800, 650, 500], "Net Income": [120, 90, 70, 50],
                "EBITDA": [200, 160, 130, 100], "EBIT": [170, 135, 110, 85], "Cost Of Revenue": [600, 500, 400, 300]})
BALANCE = frame({"Total Assets": [1200, 1000, 900, 800], "Current Liabilities": [300, 250, 250, 200],
                 "Total Debt": [100, 120, 130, 150], "Cash And Cash Equivalents": [50, 40, 30, 20],
                 "Stockholders Equity": [700, 600, 500, 450], "Accounts Receivable": [160, 140, 120, 100],
                 "Inventory": [90, 80, 70, 60], "Construction In Progress": [30, 10, 5, 5]})
BALANCE.loc["Ordinary Shares Number"] = [10e7, 10e7, 9.8e7, 9.5e7]
CASH = frame({"Operating Cash Flow": [150, 120, 100, 80]})
INFO = {"longName": "Alpha Precision Ltd", "currentPrice": 500.0, "marketCap": 3200 * CR, "trailingPE": 24.0,
        "heldPercentInsiders": 0.58, "averageVolume": 200000}


def test_metrics_match_screener_shape():
    from mb.rules import pass1
    from mb.yahoo import metrics
    m = metrics(INFO, INCOME, BALANCE, CASH)
    assert m["mcap"] == 3200 and m["pe"] == 24 and m["promoter"] == pytest.approx(58)
    assert m["sales_cagr_3y"] == pytest.approx(25.99, abs=0.01)
    assert m["roce"] == pytest.approx(100 * 170 / 900, abs=0.01)
    assert m["cfo_3y"] == 370 and m["ebitda_3y"] == 490 and m["profit_3y"] == 280
    assert m["debt"] == 100 and m["cash"] == 50 and m["net_worth"] == 700
    assert m["debtor_days"] == pytest.approx(58.4) and m["shares_3y_back"] == 9.5e7
    assert m["avg_traded_value_cr"] == pytest.approx(10.0)
    res = pass1(m)
    assert res["known"] == 7 and res["passed"]


def test_metrics_tolerate_missing_statements():
    from mb.yahoo import metrics
    m = metrics({"marketCap": 900 * CR}, None, pd.DataFrame(), None)
    assert m["mcap"] == 900 and m["sales_cagr_3y"] is None and m["debt"] is None


def test_fundamentals_agent(env):
    from mb.agents import fundamentals
    from mb.yahoo import metrics
    store, _ = env
    for s in ["ALPHA", "BIGCO", "GONE", "BROKEN"]:
        store.set("universe", s, {"name": s})

    def fake(sym, lo, hi):
        if sym == "GONE":
            return None
        if sym == "BROKEN":
            raise ValueError("boom")
        if sym == "BIGCO":
            return {"mcap": 90000.0, "name": "Big", "out_of_range": True}
        return metrics(INFO, INCOME, BALANCE, CASH)
    out = fundamentals.run(store=store, log=LOG, fetch=fake)
    assert out["symbols"] == 4 and out["with_statements"] == 1 and out["not_found"] == 2 and out["errors"] == 1
    assert store.get("fundamentals", "ALPHA")["source"] == "yahoo"
    ctl = store.get("control", "fundamentals")
    assert ctl["source"] == "yahoo" and ctl["rows"] == 2 and ctl["complete"]

    from mb.agents import screener
    assert screener.run(store=store, log=LOG)["passed"] == 1  # ALPHA passes, BIGCO fails the mcap gate

    from fastapi.testclient import TestClient
    from portal.app import app
    page = TestClient(app).get("/").text
    assert "automatic</span></h3>" in page and "Yahoo Finance (weekly agent)" in page
