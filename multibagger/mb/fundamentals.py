"""Fundamentals import from a Screener-style export (CSV or XLSX).

Column names vary between screens, so each metric accepts several aliases, matched after
lower-casing and stripping punctuation. Missing metrics stay None and rules that need them are
reported as "unknown" instead of failing. Upload the file in the portal (Data page).
"""
from __future__ import annotations

import io
import re

import pandas as pd

ALIASES: dict[str, list[str]] = {
    "symbol": ["nse code", "nse symbol", "symbol", "ticker"],
    "bse_code": ["bse code"],
    "name": ["name", "company", "company name"],
    "price": ["current price", "cmp", "price", "cmp rs"],
    "mcap": ["market capitalization", "market cap", "mar cap rs cr", "mcap", "market cap rs cr"],
    "pe": ["price to earning", "pe", "p e", "price to earnings"],
    "sales_cagr_3y": ["sales growth 3years", "sales growth 3 years", "revenue cagr 3y", "sales cagr 3y"],
    "profit_cagr_3y": ["profit growth 3years", "profit growth 3 years", "profit cagr 3y"],
    "qtr_sales_yoy": ["yoy quarterly sales growth", "qtr sales var", "qtr sales var percent", "quarterly sales growth yoy"],
    "roce": ["return on capital employed", "roce", "roce percent"],
    "roce_3y_avg": ["average return on capital employed 3years", "roce 3yr avg", "average roce 3years"],
    "cfo_3y": ["operating cash flow 3years", "cash from operations 3years", "cfo 3y"],
    "ebitda_3y": ["ebitda 3years", "ebitda 3y"],
    "profit_3y": ["net profit 3years", "profit after tax 3years", "pat 3y"],
    "ebitda": ["ebitda", "ebitda last year"],
    "debt": ["debt", "borrowings", "total debt"],
    "cash": ["cash equivalents", "cash", "cash and equivalents"],
    "debt_to_equity": ["debt to equity", "d e"],
    "shares_now": ["number of equity shares", "no of equity shares"],
    "shares_3y_back": ["number of equity shares 3years back", "number of equity shares 3 years back"],
    "promoter": ["promoter holding", "promoter holding percent", "promoters"],
    "promoter_change": ["change in promoter holding", "change in promoter holding 3years"],
    "pledge": ["pledged percentage", "pledge", "pledged percent", "promoter pledge"],
    "pledge_prev": ["pledged percentage preceding quarter", "pledged percentage preceding year"],
    "debtor_days": ["debtor days"],
    "debtor_days_3y_back": ["debtor days 3years back", "debtor days 3 years back"],
    "inventory_days": ["inventory days"],
    "contingent_liabilities": ["contingent liabilities"],
    "net_worth": ["net worth", "networth", "shareholders funds"],
    "cwip": ["capital work in progress", "cwip"],
    "cwip_prev": ["capital work in progress preceding year", "cwip preceding year"],
    "avg_traded_value_cr": ["average daily traded value", "avg traded value rs cr"],
    "volume_1m_avg": ["volume 1month average", "volume 1 month average", "avg volume 1m"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse(data: bytes, filename: str) -> tuple[dict[str, dict], dict]:
    """Returns ({symbol: metrics}, report) where report lists recognised and missing metrics."""
    if filename.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data))
    else:
        df = pd.read_csv(io.BytesIO(data))
    cols = {_norm(c): c for c in df.columns}
    mapping = {}
    for metric, names in ALIASES.items():
        for n in names:
            if _norm(n) in cols:
                mapping[metric] = cols[_norm(n)]
                break
    if "symbol" not in mapping and "name" not in mapping:
        raise ValueError("export needs an 'NSE Code' (or Symbol) or 'Name' column")
    out = {}
    for row in df.to_dict("records"):
        rec = {}
        for metric, col in mapping.items():
            v = row.get(col)
            rec[metric] = (str(v).strip() if v is not None and not pd.isna(v) else None) \
                if metric in ("symbol", "name", "bse_code") else _num(v)
        key = (rec.get("symbol") or rec.get("bse_code") or rec.get("name") or "").upper()
        key = re.sub(r"[/\s]+", "_", key)
        if not key or key == "NAN":
            continue
        if rec.get("avg_traded_value_cr") is None and rec.get("volume_1m_avg") and rec.get("price"):
            rec["avg_traded_value_cr"] = rec["volume_1m_avg"] * rec["price"] / 1e7
        out[key] = rec
    report = {"rows": len(out), "recognised": sorted(mapping), "missing": sorted(set(ALIASES) - set(mapping))}
    return out, report


def import_file(store, blob, data: bytes, filename: str) -> dict:
    from .store import now_iso

    records, report = parse(data, filename)
    blob.put(f"fundamentals/uploads/{now_iso().replace(':', '')}-{filename}", data)
    stamp = now_iso()
    store.set_many("fundamentals", {k: {**v, "as_of": stamp} for k, v in records.items()})
    store.set("control", "fundamentals", {**report, "file": filename, "imported": stamp})
    return report
