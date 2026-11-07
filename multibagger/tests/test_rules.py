from mb.rules import filing_flags, pass1, structured_flags

GOOD = dict(sales_cagr_3y=22, roce=21, cfo_3y=300, ebitda_3y=380, debt=50, cash=40, ebitda=140,
            promoter=58, pledge=0, promoter_change=0.5, pe=24, profit_cagr_3y=28,
            debtor_days=60, debtor_days_3y_back=62, mcap=3200, avg_traded_value_cr=4.0)


def test_good_company_passes():
    r = pass1(GOOD)
    assert r["passed"] and r["met"] == 7 and r["known"] == 7


def test_mcap_gate_rejects_large_cap():
    assert not pass1({**GOOD, "mcap": 60000})["passed"]


def test_illiquid_rejected():
    assert not pass1({**GOOD, "avg_traded_value_cr": 0.3})["passed"]


def test_most_not_all():
    r = pass1({**GOOD, "pe": 80, "debtor_days": 120})  # two misses out of seven
    assert r["met"] == 5 and r["passed"]
    r = pass1({**GOOD, "pe": 80, "debtor_days": 120, "roce": 8})
    assert not r["passed"]


def test_unknown_metrics_not_counted():
    r = pass1({"sales_cagr_3y": 20, "roce": 18, "promoter": 50, "pledge": 0, "pe": 10, "profit_cagr_3y": 20})
    assert r["known"] == 4 and r["passed"]
    assert r["rules"]["cash_conversion"] is None


def test_hard_flags():
    rules = {f["rule"] for f in structured_flags({"pledge": 35, "cfo_3y": -10, "profit_3y": 200})}
    assert {"pledge_above_20", "profit_without_cash"} <= rules


def test_soft_flags():
    rules = {f["rule"]: f["severity"] for f in structured_flags(
        {"contingent_liabilities": 400, "net_worth": 1000, "shares_now": 120, "shares_3y_back": 100,
         "debtor_days": 110, "debtor_days_3y_back": 60})}
    assert rules == {"contingent_liabilities": "soft", "equity_dilution": "soft", "receivables_rising": "soft"}


def test_filing_keywords():
    flags = filing_flags("Resignation of Statutory Auditor - M/s ABC & Co")
    assert flags[0]["rule"] == "auditor_resignation" and flags[0]["severity"] == "hard"
    assert filing_flags("Outcome of board meeting - quarterly results") == []


def test_flag_tuning():
    small = structured_flags({"pledge": 2.0, "pledge_prev": 0.0})
    assert small[0]["rule"] == "pledge_rising" and small[0]["severity"] == "soft"
    assert structured_flags({"pledge": 11.5, "pledge_prev": 0.0})[0]["severity"] == "hard"
    assert filing_flags("Resignation of Statutory Auditor of Material Subsidiary")[0]["severity"] == "soft"
    assert filing_flags("Update on Corporate Insolvency Resolution Process of X, a wholly owned subsidiary")[0]["severity"] == "soft"
    assert filing_flags("Corporate Insolvency Resolution Process")[0]["severity"] == "hard"
    assert filing_flags("SEBI Order Granting Exemption to business trusts") == []
    assert filing_flags("Reply to Show Cause Notice, Department of Commercial Taxes")[0]["severity"] == "soft"


def test_financials_skip_cash_tests():
    lender = {"cfo_3y": -5000, "profit_3y": 1700, "ebitda_3y": 2500, "industry": "Housing Finance"}
    assert not [f for f in structured_flags(lender) if f["rule"] == "profit_without_cash"]
    assert pass1(lender)["rules"]["cash_conversion"] is None
    assert structured_flags({**lender, "industry": "Auto Parts"})[0]["rule"] == "profit_without_cash"
