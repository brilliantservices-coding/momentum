"""HTTP access to public exchange data, with polite rate limiting.

NSE's JSON API needs a browser-like session (cookies from the home page). Both NSE and BSE
sometimes refuse cloud IP ranges; callers treat failures as "no data this run", not as crashes,
and the run summary records which source worked.
"""
from __future__ import annotations

import io
import time
from datetime import date

import pandas as pd
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
NSE_EQUITY_LIST = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_HOME = "https://www.nseindia.com/"
NSE_ANNOUNCEMENTS = "https://www.nseindia.com/api/corporate-announcements"
BSE_ANNOUNCEMENTS = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

MIN_INTERVAL_S = 1.5  # at most one request every 1.5 s per session


class PoliteSession:
    def __init__(self, referer: str | None = None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        if referer:
            self.s.headers["Referer"] = referer
        self._last = 0.0

    def get(self, url: str, **kw) -> requests.Response:
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.s.get(url, timeout=kw.pop("timeout", 30), **kw)
        r.raise_for_status()
        return r


def nse_equity_list() -> pd.DataFrame:
    r = PoliteSession().get(NSE_EQUITY_LIST)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip().upper() for c in df.columns]
    return df


def nse_announcements(start: date, end: date) -> list[dict]:
    sess = PoliteSession(referer=NSE_HOME)
    sess.get(NSE_HOME)  # sets the cookies the API requires
    r = sess.get(NSE_ANNOUNCEMENTS, params={
        "index": "equities", "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")})
    out = []
    for a in r.json() or []:
        out.append({
            "id": f"nse-{a.get('seq_id') or abs(hash((a.get('symbol'), a.get('an_dt'), a.get('desc'))))}",
            "exchange": "NSE",
            "symbol": (a.get("symbol") or "").upper(),
            "company": a.get("sm_name"),
            "category": a.get("desc"),
            "subject": a.get("attchmntText") or a.get("desc"),
            "date": a.get("sort_date") or a.get("an_dt"),
            "attachment": a.get("attchmntFile"),
        })
    return out


def bse_announcements(start: date, end: date, max_pages: int = 30) -> list[dict]:
    sess = PoliteSession(referer="https://www.bseindia.com/")
    out = []
    for page in range(1, max_pages + 1):
        r = sess.get(BSE_ANNOUNCEMENTS, params={
            "pageno": page, "strCat": "-1", "strPrevDate": start.strftime("%Y%m%d"), "strScrip": "",
            "strSearch": "P", "strToDate": end.strftime("%Y%m%d"), "strType": "C", "subcategory": "-1"})
        rows = (r.json() or {}).get("Table") or []
        if not rows:
            break
        for a in rows:
            att = a.get("ATTACHMENTNAME")
            out.append({
                "id": f"bse-{a.get('NEWSID')}",
                "exchange": "BSE",
                "symbol": str(a.get("SCRIP_CD") or ""),
                "company": a.get("SLONGNAME"),
                "category": a.get("CATEGORYNAME"),
                "subject": a.get("HEADLINE") or a.get("NEWSSUB"),
                "date": a.get("NEWS_DT"),
                "attachment": BSE_ATTACH + att if att else None,
            })
    return out


def download(url: str, referer: str | None = None, max_bytes: int = 25_000_000) -> bytes:
    r = PoliteSession(referer=referer).get(url, stream=True)
    data = r.raw.read(max_bytes + 1, decode_content=True)
    if len(data) > max_bytes:
        raise ValueError(f"attachment over {max_bytes} bytes, skipped")
    return data
