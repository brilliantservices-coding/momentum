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


TL = b'''"Sl No","Stock","LTP","NSE Code","BSE Code","ISIN","LT Debt To Equity Ann ","Promoter holding pledge percentage % Qtr","ROCE Ann  3Y Avg %"
"1","Alpha Precision Ltd.","510.5","ALPHA","500001","INE000A01011","0.12","25.5","19.2"
'''


def test_trendlyne_overlay_wins_both_orders(env):
    import logging

    from mb.agents import fundamentals, screener
    from mb.yahoo import metrics
    store, blob = env
    store.set("universe", "ALPHA", {"name": "A"})
    fake = lambda sym, lo, hi: metrics(INFO, INCOME, BALANCE, CASH)  # noqa: E731
    fundamentals.run(store=store, log=LOG, fetch=fake)
    assert store.get("fundamentals", "ALPHA").get("pledge") is None
    blob.put("inbox/fundamentals/trendlyne-20261107.csv", TL)  # the Friday desktop task's upload
    screener.run(store=store, log=logging.getLogger("t"))
    f = store.get("fundamentals", "ALPHA")
    assert f["pledge"] == 25.5 and f["price"] == 510.5 and f["sales_cagr_3y"] is not None  # merged, not replaced
    assert store.get("alerts", "ALPHA-pledge_above_20")["severity"] == "hard"  # forensic ran after the import
    assert not store.get("candidates", "ALPHA")["passed"]
    assert blob.list("inbox/fundamentals/") == []
    fundamentals.run(store=store, log=LOG, fetch=fake)  # next Yahoo refresh keeps Trendlyne's fields
    assert store.get("fundamentals", "ALPHA")["pledge"] == 25.5
    assert store.get("control", "fundamentals_upload")["file"].startswith("trendlyne")


def test_trendlyne_data_downloader_xlsx():
    import io

    from mb.fundamentals import parse
    from mb.rules import pass1
    cols = ["Stock Name", "NSE Code", "BSE Code", "Stock Code", "ISIN", "Industry Name", "sector_name", "Current Price",
            "Market Capitalization", "Market Capitalization", "PE TTM", "ROCE Annual %", "ROCE Annual 3Yr Avg %",
            "Revenue Annual 3Yr Growth %", "Net Profit 3Yr Growth %", "Revenue Growth Qtr YoY %",
            "Cash from Operating Activity Annual", "Operating Cash Flow 3Yr Growth %", "EBITDA Annual",
            "Net Profit Annual", "Operating Revenue Annual", "Total Debt Annual", "Cash Plus Cash Equivalents Annual",
            "Total Debt to Total Equity Annual", "Total ShareHolders Funds Annual",
            "Contingent Liabilities plus Commitments Annual", "Capital Work In Progress Annual",
            "Trade Receivables Annual", "Inventory Turnover Ratio Annual", "Number Of Shares Qtr End",
            "Month Volume Avg", "Promoter holding latest %", "Promoter holding change 4Qtr %",
            "Promoter holding pledge percentage % Qtr", "Promoter pledge change QoQ %"]
    row = ["Alpha Precision Ltd.", "ALPHA", 500001, "A1", "INE000A01011", "Auto Parts", "Auto", 510.5, 3200, 3200, 24,
           21, 19.5, 22, 28, 30, 6, 150, 18, 1000, 900, 100, 50, 0.14, 700, 40, 30, 160, 8, 6.3, 200000, 58, 0.5, 6,
           2]
    buf = io.BytesIO()
    pd.DataFrame([row], columns=cols).to_excel(buf, index=False)
    recs, rep = parse(buf.getvalue(), "trendlyne.xlsx")
    a = recs["ALPHA"]
    assert a["pe"] == 24 and a["roce"] == 21 and a["roce_3y_avg"] == 19.5 and a["sales_cagr_3y"] == 22
    assert a["debtor_days"] == pytest.approx(64.9) and a["inventory_days"] == pytest.approx(45.6)
    assert a["pledge"] == 6 and a["pledge_prev"] == 4 and a["promoter"] == 58 and a["promoter_change"] == 0.5
    assert a["debt"] == 100 and a["net_worth"] == 700 and a["mcap"] == 3200
    assert "receivables" not in a and "cfo_3y" not in a  # annual CFO is not a 3-year sum, so it isn't mapped
    assert "revenue_annual" not in rep["recognised"] and "pledge" in rep["recognised"]
    assert pass1(a)["known"] >= 4
    # A title row above the header (some exports) is skipped.
    buf2 = io.BytesIO()
    pd.DataFrame([["Trendlyne Data Downloader"] + [None] * 2, ["NSE Code", "Stock Name", "PE TTM"], ["BETA", "Beta", 12]]
                 ).to_excel(buf2, index=False, header=False)
    assert parse(buf2.getvalue(), "t.xlsx")[0]["BETA"]["pe"] == 12
