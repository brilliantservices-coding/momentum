"""Fundamentals from Yahoo Finance (via yfinance) in the same shape as the Screener export import.

Money values are converted to ₹ crore so the rules and red-flag checks read them exactly like a
Screener export. Yahoo has no promoter pledge data; "promoter" uses Yahoo's insider holding, which
for Indian listings is mostly the promoter group, and is marked as a proxy.
"""
from __future__ import annotations

import math
import time

CR = 1e7

ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "net_income": ["Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation Net Minority Interest"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ebit": ["EBIT", "Operating Income"],
    "cogs": ["Cost Of Revenue", "Reconciled Cost Of Revenue"],
    "ocf": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "total_assets": ["Total Assets"],
    "current_liabilities": ["Current Liabilities"],
    "debt": ["Total Debt"],
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "equity": ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
    "receivables": ["Accounts Receivable", "Receivables"],
    "inventory": ["Inventory"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
    "cwip": ["Construction In Progress"],
    "net_ppe": ["Net PPE"],
    "capex": ["Capital Expenditure", "Purchase Of PPE"],
    "depreciation": ["Depreciation And Amortization", "Depreciation Amortization Depletion", "Reconciled Depreciation"],
}


def _row(df, key: str) -> list:
    """Values for one line item, newest year first; None where missing."""
    if df is None or getattr(df, "empty", True):
        return []
    for name in ROWS[key]:
        if name in df.index:
            vals = []
            for v in df.loc[name].tolist():
                vals.append(None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
            return vals
    return []


def _at(vals: list, i: int):
    return vals[i] if len(vals) > i else None


def _cr(v):
    return None if v is None else v / CR


def _cagr(new, old, years: int):
    if new is None or old is None or old <= 0 or new <= 0:
        return None
    return ((new / old) ** (1 / years) - 1) * 100


def _sum(vals: list, n: int):
    got = [v for v in vals[:n] if v is not None]
    return sum(got) if len(got) == n else None


def metrics(info: dict, income, balance, cashflow, quarterly=None) -> dict:
    """Pure function: Yahoo info dict + annual statements -> fundamentals record (₹ crore, %)."""
    rev, ni, ebitda = _row(income, "revenue"), _row(income, "net_income"), _row(income, "ebitda")
    ebit, cogs = _row(income, "ebit"), _row(income, "cogs")
    ocf = _row(cashflow, "ocf")
    ta, cl = _row(balance, "total_assets"), _row(balance, "current_liabilities")
    debt, cash, eq = _row(balance, "debt"), _row(balance, "cash"), _row(balance, "equity")
    ar, inv, sh, cwip = _row(balance, "receivables"), _row(balance, "inventory"), _row(balance, "shares"), _row(balance, "cwip")
    ppe, capex = _row(balance, "net_ppe"), _row(cashflow, "capex")
    dep = _row(cashflow, "depreciation") or _row(income, "depreciation")

    def roce(i):
        e, a, c = _at(ebit, i), _at(ta, i), _at(cl, i)
        return None if e is None or a is None or c is None or a - c <= 0 else 100 * e / (a - c)

    def days(num, den, i):
        n, d = _at(num, i), _at(den, i)
        return None if n is None or not d or d <= 0 else 365 * n / d

    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    vol = info.get("averageVolume") or info.get("averageDailyVolume3Month")
    roces = [r for r in (roce(i) for i in range(3)) if r is not None]
    qrev = _row(quarterly, "revenue") if quarterly is not None else []
    insiders = info.get("heldPercentInsiders")
    d_now, eq_now = _at(debt, 0), _at(eq, 0)
    rec = {
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"), "industry": info.get("industry"),
        "price": price,
        "mcap": _cr(info.get("marketCap")),
        "pe": info.get("trailingPE"),
        "sales_cagr_3y": _cagr(_at(rev, 0), _at(rev, 3), 3),
        "profit_cagr_3y": _cagr(_at(ni, 0), _at(ni, 3), 3),
        "qtr_sales_yoy": (100 * (qrev[0] / qrev[4] - 1)) if len(qrev) > 4 and qrev[0] and qrev[4] and qrev[4] > 0 else None,
        "roce": roce(0),
        "roce_3y_avg": sum(roces) / len(roces) if len(roces) == 3 else None,
        "cfo_3y": _cr(_sum(ocf, 3)),
        "ebitda_3y": _cr(_sum(ebitda, 3)),
        "profit_3y": _cr(_sum(ni, 3)),
        "ebitda": _cr(_at(ebitda, 0)),
        "debt": _cr(d_now) if d_now is not None else (0.0 if balance is not None and not getattr(balance, "empty", True) else None),
        "cash": _cr(_at(cash, 0)),
        "debt_to_equity": (d_now or 0) / eq_now if eq_now and eq_now > 0 else None,
        "shares_now": _at(sh, 0),
        "shares_3y_back": _at(sh, 3),
        "promoter": 100 * insiders if insiders is not None else None,
        "debtor_days": days(ar, rev, 0),
        "debtor_days_3y_back": days(ar, rev, 3),
        "inventory_days": days(inv, cogs, 0),
        "net_worth": _cr(eq_now),
        "cwip": _cr(_at(cwip, 0)),
        "cwip_prev": _cr(_at(cwip, 1)),
        "avg_traded_value_cr": (vol * price / CR) if vol and price else None,
        "volume_1m_avg": vol,
    }
    rec.update(change_variables(rev, ebitda, ebit, ta, cl))
    rec.update(reinvestment(ebit, capex, dep, ppe, cwip, rec.get("incremental_roce")))
    if quarterly is not None:
        rec.update(quarterly_changes(qrev, _row(quarterly, "ebitda"), rec.get("revenue_growth_1y")))
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in rec.items()}


def change_variables(rev: list, ebitda: list, ebit: list, ta: list, cl: list) -> dict:
    """Strategy v2 change variables from annual statements (newest first). Static quality is not enough:
    what matters is whether growth, margins and returns on new capital are improving."""
    def growth(i):
        a, b = _at(rev, i), _at(rev, i + 1)
        return None if a is None or not b or b <= 0 else 100 * (a / b - 1)

    def margin(i):
        e, r = _at(ebitda, i), _at(rev, i)
        return None if e is None or not r or r <= 0 else 100 * e / r

    def cap(i):
        a, c = _at(ta, i), _at(cl, i)
        return None if a is None or c is None else a - c

    g0, g1, m0, m1 = growth(0), growth(1), margin(0), margin(1)
    c0, c1, e0, e1 = cap(0), cap(1), _at(ebit, 0), _at(ebit, 1)
    inc_roce = None
    if None not in (c0, c1, e0, e1) and c0 > 0:
        floor = max(abs(c0) * 0.02, 1.0)  # avoids dividing by a near-zero change in capital
        inc_roce = 100 * 0.75 * (e0 - e1) / max(c0 - c1, floor)  # NOPAT at an assumed 25% tax
    s2c = lambda i: None if cap(i) in (None, 0) or _at(rev, i) is None else _at(rev, i) / cap(i)  # noqa: E731
    return {"revenue_growth_1y": g0, "revenue_acceleration": None if g0 is None or g1 is None else g0 - g1,
            "ebitda_margin": m0, "margin_delta": None if m0 is None or m1 is None else m0 - m1,
            "incremental_roce": inc_roce,
            "sales_to_capital_delta": None if s2c(0) is None or s2c(1) is None else s2c(0) - s2c(1)}


def reinvestment(ebit: list, capex: list, dep: list, ppe: list, cwip: list, inc_roce) -> dict:
    """Strategy v2 reinvestment runway from the latest year: how much of operating profit goes back into
    the business (net capex / NOPAT), capital work in progress against the existing asset base, and the
    growth that reinvestment funds at the incremental return (reinvestment rate x incremental ROCE)."""
    e, cx, d, p, w = _at(ebit, 0), _at(capex, 0), _at(dep, 0), _at(ppe, 0), _at(cwip, 0)
    rate = None
    if e is not None and e > 0 and cx is not None:
        rate = 100 * (abs(cx) - abs(d or 0)) / (0.75 * e)
        rate = max(-100.0, min(rate, 300.0))
    funded = None
    if rate is not None and inc_roce is not None:
        funded = max(-50.0, min(rate / 100 * max(min(inc_roce, 100.0), -100.0), 100.0))
    return {"reinvestment_rate": rate, "cwip_to_net_block": None if w is None or not p or p <= 0 else 100 * w / p,
            "capex_cr": _cr(abs(cx)) if cx is not None else None, "reinvestment_growth": funded}


def quarterly_changes(qrev: list, qebitda: list, growth_1y) -> dict:
    """Latest quarter against the same quarter a year ago (Yahoo has 5-6 quarters): EBITDA margin change
    and whether revenue growth is running ahead of the last full year (acceleration)."""
    def m(i):
        e, r = _at(qebitda, i), _at(qrev, i)
        return None if e is None or not r or r <= 0 else 100 * e / r
    m0, m4 = m(0), m(4)
    yoy = None
    if len(qrev) > 4 and qrev[0] and qrev[4] and qrev[4] > 0:
        yoy = 100 * (qrev[0] / qrev[4] - 1)
    return {"opm_qtr": m0, "opm_qtr_4q_ago": m4,
            "qtr_margin_delta": None if m0 is None or m4 is None else m0 - m4,
            "qtr_acceleration": None if yoy is None or growth_1y is None else yoy - growth_1y}


def info_fields(info: dict) -> dict:
    """The fields that move weekly (price, size, valuation, liquidity), from the summary call alone."""
    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    vol = info.get("averageVolume") or info.get("averageDailyVolume3Month")
    insiders = info.get("heldPercentInsiders")
    rec = {"name": info.get("longName") or info.get("shortName"), "sector": info.get("sector"),
           "industry": info.get("industry"), "price": price, "mcap": _cr(info.get("marketCap")),
           "pe": info.get("trailingPE"), "promoter": 100 * insiders if insiders is not None else None,
           "avg_traded_value_cr": (vol * price / CR) if vol and price else None, "volume_1m_avg": vol}
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in rec.items()}


def fetch(symbol: str, mcap_min_cr: float, mcap_max_cr: float, retries: int = 3, light: bool = False) -> dict | None:
    """Fetches one NSE symbol. Statements are only downloaded when the market cap is in range and
    `light` is False; a light fetch is one request. Returns None when Yahoo has nothing for it."""
    import yfinance as yf

    for attempt in range(retries):
        try:
            t = yf.Ticker(f"{symbol}.NS")
            info = t.info or {}
            mcap = _cr(info.get("marketCap"))
            if not info or mcap is None:
                return None
            if not (mcap_min_cr <= mcap <= mcap_max_cr):
                return {**info_fields(info), "out_of_range": True}
            if light:
                return {**info_fields(info), "light": True}
            return metrics(info, t.income_stmt, t.balance_sheet, t.cashflow, t.quarterly_income_stmt)
        except Exception as exc:
            msg = str(exc).lower()
            if any(w in msg for w in ("too many requests", "429", "invalid crumb", "unauthorized")):
                time.sleep(30 * (attempt + 1))
            elif attempt == retries - 1:
                raise
            else:
                time.sleep(3 * (attempt + 1))
    return None
