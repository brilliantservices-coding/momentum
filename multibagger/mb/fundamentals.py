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
    "name": ["name", "company", "company name", "stock", "stock name"],
    "sector": ["sector name", "sector"],
    "industry": ["industry name", "industry"],
    "price": ["current price", "cmp", "price", "cmp rs", "ltp", "current price rs"],
    "mcap": ["market capitalization", "market cap", "mar cap rs cr", "mcap", "market cap rs cr"],
    "pe": ["price to earning", "pe", "p e", "price to earnings", "pe ttm", "pe ttm price to earnings"],
    "sales_cagr_3y": ["revenue annual 3yr growth", "sales growth 3years", "sales growth 3 years", "revenue cagr 3y", "sales cagr 3y"],
    "profit_cagr_3y": ["net profit 3yr growth", "net profit annual 3yr growth", "profit growth 3years", "profit growth 3 years", "profit cagr 3y"],
    "qtr_sales_yoy": ["revenue growth qtr yoy", "yoy quarterly sales growth", "qtr sales var", "qtr sales var percent", "quarterly sales growth yoy"],
    "roce": ["return on capital employed", "roce", "roce percent", "roce annual"],
    "roce_3y_avg": ["average return on capital employed 3years", "roce 3yr avg", "average roce 3years", "roce ann 3y avg", "roce annual 3yr avg"],
    "cfo_3y": ["operating cash flow 3years", "cash from operations 3years", "cfo 3y"],
    "ebitda_3y": ["ebitda 3years", "ebitda 3y"],
    "profit_3y": ["net profit 3years", "profit after tax 3years", "pat 3y"],
    "ebitda": ["ebitda", "ebitda last year", "ebitda annual"],
    "debt": ["debt", "borrowings", "total debt", "total debt annual"],
    "cash": ["cash equivalents", "cash", "cash and equivalents", "cash plus cash equivalents annual"],
    "debt_to_equity": ["debt to equity", "d e", "lt debt to equity ann", "total debt to equity ann", "total debt to total equity annual"],
    "shares_now": ["number of equity shares", "no of equity shares", "number of shares qtr end"],
    "shares_3y_back": ["number of equity shares 3years back", "number of equity shares 3 years back"],
    "promoter": ["promoter holding", "promoter holding percent", "promoters", "promoter holding latest"],
    "promoter_change": ["change in promoter holding", "change in promoter holding 3years", "promoter holding change 4qtr"],
    "pledge": ["pledged percentage", "pledge", "pledged percent", "promoter pledge",
               "promoter holding pledge percentage qtr", "promoter pledge percentage qtr"],
    "pledge_prev": ["pledged percentage preceding quarter", "pledged percentage preceding year"],
    "debtor_days": ["debtor days"],
    "debtor_days_3y_back": ["debtor days 3years back", "debtor days 3 years back"],
    "inventory_days": ["inventory days"],
    "contingent_liabilities": ["contingent liabilities"],
    "net_worth": ["net worth", "networth", "shareholders funds", "total shareholders funds annual"],
    "cwip": ["capital work in progress", "cwip", "capital work in progress annual"],
    "cwip_prev": ["capital work in progress annual 1yr ago", "capital work in progress annual 1 yr ago", "capital work in progress 1yr ago", "capital work in progress preceding year", "cwip preceding year"],
    "avg_traded_value_cr": ["average daily traded value", "avg traded value rs cr"],
    # Inputs used only to derive the fields above (Trendlyne has no direct debtor/inventory days).
    "revenue_annual": ["operating revenue annual", "revenue annual", "sales annual"],
    "receivables": ["trade receivables annual", "trade receivables"],
    "inventory_turnover": ["inventory turnover ratio annual", "inventory turnover ratio"],
    "pledge_change_qoq": ["promoter pledge change qoq"],
    "volume_1m_avg": ["month volume avg", "volume 1month average", "volume 1 month average", "avg volume 1m"],
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


TEXT_FIELDS = ("symbol", "name", "bse_code", "sector", "industry")
DERIVED_INPUTS = {"revenue_annual", "receivables", "inventory_turnover", "pledge_change_qoq"}
_KEY_HEADERS = {"nse code", "nse symbol", "symbol", "stock name", "name", "company name"}


def _read(data: bytes, filename: str) -> pd.DataFrame:
    """Reads CSV/XLSX; if the first row isn't the header (report title rows), finds the header row."""
    excel = filename.lower().endswith((".xlsx", ".xls"))
    df = pd.read_excel(io.BytesIO(data)) if excel else pd.read_csv(io.BytesIO(data))
    if {_norm(c) for c in df.columns} & _KEY_HEADERS:
        return df
    for i, row in df.head(15).iterrows():
        if {_norm(v) for v in row.tolist() if isinstance(v, str)} & _KEY_HEADERS:
            skip = i + 1
            return pd.read_excel(io.BytesIO(data), skiprows=skip) if excel else pd.read_csv(io.BytesIO(data), skiprows=skip)
    return df


def _derive(rec: dict) -> None:
    rev, ar, turn = rec.pop("revenue_annual", None), rec.pop("receivables", None), rec.pop("inventory_turnover", None)
    if rec.get("debtor_days") is None and ar is not None and rev:
        rec["debtor_days"] = round(365 * ar / rev, 1) if rev > 0 else None
    if rec.get("inventory_days") is None and turn:
        rec["inventory_days"] = round(365 / turn, 1) if turn > 0 else None
    chg = rec.pop("pledge_change_qoq", None)
    if rec.get("pledge_prev") is None and chg is not None and rec.get("pledge") is not None:
        rec["pledge_prev"] = max(0.0, rec["pledge"] - chg)


def parse(data: bytes, filename: str) -> tuple[dict[str, dict], dict]:
    """Returns ({symbol: metrics}, report) where report lists recognised and missing metrics."""
    df = _read(data, filename)
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
            if metric in TEXT_FIELDS:
                txt = str(v).strip() if v is not None and not pd.isna(v) else None
                if txt and metric == "bse_code":
                    txt = re.sub(r"\.0+$", "", txt)  # Excel numbers come back as 522101.0
                rec[metric] = txt
            else:
                rec[metric] = _num(v)
        key = (rec.get("symbol") or rec.get("bse_code") or rec.get("name") or "").upper()
        key = re.sub(r"[/\s]+", "_", key)
        if not key or key == "NAN":
            continue
        _derive(rec)
        if rec.get("avg_traded_value_cr") is None and rec.get("volume_1m_avg") and rec.get("price"):
            rec["avg_traded_value_cr"] = rec["volume_1m_avg"] * rec["price"] / 1e7
        out[key] = rec
    derived = {k for rec in out.values() for k in ("debtor_days", "inventory_days", "pledge_prev", "avg_traded_value_cr")
               if rec.get(k) is not None}
    found = (set(mapping) | derived) - DERIVED_INPUTS
    report = {"rows": len(out), "recognised": sorted(found), "missing": sorted(set(ALIASES) - found - DERIVED_INPUTS)}
    return out, report


OVERLAY_MAX_AGE_DAYS = 21


def import_file(store, blob, data: bytes, filename: str) -> dict:
    """An uploaded export (Screener, Trendlyne, ...) is layered on top of the Yahoo numbers: its
    non-empty fields overwrite Yahoo's for the stocks it covers and stay in `fundamentals_overlay/`
    so the next Yahoo refresh keeps them (for up to three weeks)."""
    from .store import now_iso

    records, report = parse(data, filename)
    blob.put(f"fundamentals/uploads/{now_iso().replace(':', '')}-{filename}", data)
    stamp = now_iso()
    overlay = {k: {**{f: v for f, v in r.items() if v is not None}, "overlay_as_of": stamp, "overlay_file": filename}
               for k, r in records.items()}
    store.set_many("fundamentals_overlay", overlay)
    store.set_many("fundamentals", overlay, merge=True)
    store.set("control", "fundamentals_upload", {**report, "file": filename, "imported": stamp})
    if not store.get("control", "fundamentals"):  # nothing from Yahoo yet: this upload is the dataset
        store.set("control", "fundamentals", {**report, "file": filename, "imported": stamp, "source": "upload"})
    return report


def fresh_overlays(store) -> dict[str, dict]:
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=OVERLAY_MAX_AGE_DAYS)).isoformat(timespec="seconds")
    return {o["id"]: {k: v for k, v in o.items() if k != "id"} for o in store.list("fundamentals_overlay")
            if (o.get("overlay_as_of") or "") >= cutoff}


INBOX = "inbox/fundamentals/"


def import_inbox(store, blob, log) -> int:
    """Loads exports dropped in the bucket by the desktop task (Trendlyne download)."""
    n = 0
    for path in blob.list(INBOX):
        name = path.rsplit("/", 1)[-1]
        try:
            rep = import_file(store, blob, blob.get(path), name)
            log.info("%s: %d companies, recognised %s", name, rep["rows"], ", ".join(rep["recognised"]))
            blob.move(path, "fundamentals/processed/" + name)
            n += 1
        except Exception as exc:
            log.error("%s: %s", name, exc)
            blob.move(path, "fundamentals/processed/failed-" + name)
    return n
