"""Corporate announcements from CSV exports downloaded in a browser.

NSE: Companies listing > Corporate filings > Announcements, "Download (.csv)".
BSE: Corporate announcements page export. Column names differ, so aliases are matched the same
way as the fundamentals import. Each row becomes a `filings/` document with a stable id.
"""
from __future__ import annotations

import hashlib
import io
import re

import pandas as pd

ALIASES = {
    "symbol": ["symbol", "nse symbol", "scrip code", "security code", "scrip cd"],
    "company": ["company name", "company", "security name", "scrip name"],
    "category": ["subject", "category", "category name", "purpose"],
    "subject": ["details", "headline", "announcement", "news subject", "description"],
    "date": ["broadcast date time", "broadcast date", "dissemination", "news date", "date", "exchange disseminated time"],
    "attachment": ["attachment", "attachment link", "attachment file", "pdf", "link"],
}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def parse(data: bytes, filename: str) -> tuple[list[dict], dict]:
    if filename.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    else:
        df = pd.read_csv(io.BytesIO(data), dtype=str, encoding_errors="replace")
    cols = {_norm(c): c for c in df.columns}
    mapping = {}
    for field, names in ALIASES.items():
        for n in names:
            if _norm(n) in cols:
                mapping[field] = cols[_norm(n)]
                break
    if "subject" not in mapping and "category" not in mapping:
        raise ValueError("couldn't find a subject/details column; is this an announcements export?")
    exchange = "BSE" if "scrip code" in cols or "security code" in cols else "NSE"
    out = []
    for row in df.fillna("").to_dict("records"):
        rec = {f: str(row.get(c, "")).strip() for f, c in mapping.items()}
        if not (rec.get("subject") or rec.get("category")):
            continue
        rec["symbol"] = rec.get("symbol", "").upper()
        key = "|".join([exchange, rec["symbol"], rec.get("date", ""), rec.get("category", ""), rec.get("subject", "")[:200]])
        rec["id"] = f"{exchange.lower()}-{hashlib.sha1(key.encode()).hexdigest()[:16]}"
        rec["exchange"] = exchange
        out.append(rec)
    return out, {"rows": len(out), "exchange": exchange, "recognised": sorted(mapping)}
