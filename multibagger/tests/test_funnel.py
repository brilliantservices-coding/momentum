import logging

from test_pipeline import CSV, NSE_ANN

LOG = logging.getLogger("t")


def test_ist_and_filing_dates():
    from mb.timefmt import coverage, filing_date, ist
    assert ist("2026-11-07T10:31:13+00:00") == "07 Nov 2026 16:01 IST"
    assert ist("2026-11-07T10:31:13+00:00", "%d %b %Y") == "07 Nov 2026"
    assert ist(None) == "" and ist("garbage") == "garbage"
    assert filing_date("07-Nov-2026 12:48:45").hour == 12 and filing_date("06-11-2026").day == 6
    cov = coverage(["06-Nov-2026 18:02:11", "05-Nov-2026 10:00:00", "bad"])
    assert cov["first"].startswith("2026-11-05T10:00") and cov["last"].startswith("2026-11-06T18:02")
    assert coverage(["07-Nov-2026 09:00:00"], cov)["first"] == cov["first"]


def test_coverage_tracked_by_ingestion(env):
    from mb.agents import ingestion
    store, blob = env
    blob.put("filings/inbox/a.csv", NSE_ANN)
    ingestion.run(store=store, log=LOG)
    cov = store.get("control", "filings_coverage")
    assert cov["count"] == 3 and cov["last"].startswith("2026-11-06T18:02") and cov["last_loaded"]
    store.delete("control", "filings_coverage")
    assert ingestion.rebuild_coverage(store)["count"] == 3


def test_funnel_page(env):
    from fastapi.testclient import TestClient

    from mb.agents import forensic, screener
    from mb.fundamentals import import_file
    from portal.app import _funnel, app
    store, blob = env
    c = TestClient(app)
    page = c.get("/funnel").text
    assert "no fundamentals are loaded" in page and "Not loaded yet" in page
    import_file(store, blob, CSV, "screen.csv")
    forensic.run(store=store, log=LOG)
    screener.run(store=store, log=LOG)
    stages, factors, rows = _funnel()
    by = {r["id"]: r for r in rows}
    assert stages[1]["n"] == 3 and stages[5]["n"] == 1  # 3 screened, 1 on the watchlist
    assert by["ALPHA"]["reached"] == 5 and "not analysed" in by["ALPHA"]["why"]
    assert "Market cap" in by["GAMMA"]["why"] and by["GAMMA"]["reached"] == 1
    assert "hard red flag" in by["BETA"]["why"]
    assert next(f for f in factors if f["key"] == "mcap_range")["fail"] == 1
    page = c.get("/funnel").text
    assert "GAMMA" in page and "Favourable" in page
    assert "GAMMA" in c.get("/funnel?stage=1").text and "ALPHA" not in c.get("/funnel?stage=1").text
    assert "BETA" in c.get("/funnel?q=beta").text
    home = c.get("/").text
    assert "Screened (fundamentals loaded)" in home and "IST" in home
    assert "UTC" not in home and "UTC" not in c.get("/data").text


def test_stock_links_to_yahoo():
    from portal.app import stock_link
    assert 'href="https://finance.yahoo.com/quote/ALPHA.NS/"' in stock_link("ALPHA", "Alpha <Ltd>")
    assert "Alpha &lt;Ltd&gt;" in stock_link("ALPHA", "Alpha <Ltd>")
    assert "522101.BO" in stock_link("522101")


def test_stale_running_runs_show_stopped(env):
    from fastapi.testclient import TestClient

    from portal.app import app
    store, _ = env
    store.set("runs", "old-1", {"agent": "universe", "status": "running", "started": "2026-01-01T00:00:00+00:00"})
    store.set("agents", "universe", {"last_status": "running", "last_started": "2026-01-01T00:00:00+00:00"})
    c = TestClient(app)
    page = c.get("/agents/universe").text
    assert "stopped" in page and ">running<" not in page
    assert "stopped" in c.get("/").text
