"""HTTP access to public data, used only for official bulk downloads that allow it.

The client identifies itself honestly and is rate-limited. NSE and BSE block automated access
to their announcement APIs, so announcements come in through CSV uploads on the portal
(see mb/announcements.py) rather than by imitating a browser.
"""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

UA = "multibagger-research/1.0 (personal, non-commercial research; github.com/brilliantservices-coding/momentum)"
NSE_EQUITY_LIST = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
MIN_INTERVAL_S = 1.5  # at most one request every 1.5 s per session


class PoliteSession:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})
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
