"""Market data: Nifty 500 constituents from NSE, daily prices from Yahoo Finance.

Both are free and need no account. Later, Kite Connect's historical API can replace Yahoo once the
Zerodha app is live. Failures for individual symbols are tolerated and reported in the run summary.
"""
from __future__ import annotations

import io
import logging
import time

import pandas as pd
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
NIFTY500_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
NIFTY50 = "^NSEI"
NIFTY500 = "^CRSLDX"

log = logging.getLogger("mom.data")


def nifty500_constituents() -> dict[str, dict]:
    """Returns {symbol: {name, industry}} from the first NSE mirror that answers."""
    last = None
    for url in NIFTY500_URLS:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            out = {}
            for row in df.to_dict("records"):
                sym = str(row.get("Symbol", "")).strip().upper()
                if sym and str(row.get("Series", "EQ")).strip().upper() == "EQ":
                    out[sym] = {"name": str(row.get("Company Name", "")).strip(),
                                "industry": str(row.get("Industry", "")).strip() or "Unknown"}
            if len(out) > 400:
                return out
            last = ValueError(f"{url} returned only {len(out)} rows")
        except Exception as exc:  # try the next mirror
            last = exc
            log.warning("constituents from %s failed: %s", url, exc)
    raise RuntimeError(f"could not download the Nifty 500 list: {last}")


def _download(tickers: list[str], period: str, threads: bool = True) -> pd.DataFrame:
    import yfinance as yf

    for attempt in range(4):
        try:
            df = yf.download(tickers, period=period, interval="1d", auto_adjust=True, group_by="column",
                             threads=threads, progress=False)
            if df is not None and not df.empty:
                return df
        except Exception as exc:
            log.warning("yahoo download failed (attempt %d): %s", attempt + 1, exc)
        time.sleep(5 * (attempt + 1))
    return pd.DataFrame()


def prices(symbols: list[str], period: str = "2y", chunk: int = 80) -> dict[str, pd.DataFrame]:
    """Daily adjusted OHLCV as wide frames {"close","high","low","volume"} (columns = NSE symbols),
    plus the "nifty50" and "nifty500" close series. Symbols with no data are dropped."""
    parts = {k: [] for k in ("Close", "High", "Low", "Volume")}
    for i in range(0, len(symbols), chunk):
        batch = [f"{s}.NS" for s in symbols[i:i + chunk]]
        df = _download(batch, period)
        if df.empty:
            log.warning("no data for chunk %d", i // chunk)
            continue
        for k in parts:
            if k in df.columns.get_level_values(0):
                sub = df[k]
                if isinstance(sub, pd.Series):
                    sub = sub.to_frame(batch[0])
                parts[k].append(sub)
        time.sleep(1)
    # Threaded downloads occasionally lose a symbol to yfinance's sqlite cache lock; retry those serially
    got = set()
    for f in parts["Close"]:
        got |= {c for c in f.columns if f[c].notna().any()}
    missing = [f"{s}.NS" for s in symbols if f"{s}.NS" not in got]
    if missing:
        df = _download(missing, period, threads=False)
        if not df.empty:
            for k in parts:
                if k in df.columns.get_level_values(0):
                    sub = df[k]
                    parts[k].append(sub.to_frame(missing[0]) if isinstance(sub, pd.Series) else sub)
    out = {}
    for k, frames in parts.items():
        wide = pd.concat(frames, axis=1) if frames else pd.DataFrame()
        wide.columns = [c.removesuffix(".NS") for c in wide.columns]
        out[k.lower()] = wide.sort_index()
    idx = _download([NIFTY50, NIFTY500], period)
    out["nifty50"] = idx["Close"][NIFTY50].dropna() if not idx.empty else pd.Series(dtype=float)
    out["nifty500"] = idx["Close"][NIFTY500].dropna() if not idx.empty else pd.Series(dtype=float)
    if out["nifty500"].empty:
        out["nifty500"] = out["nifty50"]
    good = out["close"].columns[out["close"].notna().sum() > 0]
    for k in ("close", "high", "low", "volume"):
        out[k] = out[k].reindex(columns=good)
    return out
