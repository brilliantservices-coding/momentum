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
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in rec.items()}


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
