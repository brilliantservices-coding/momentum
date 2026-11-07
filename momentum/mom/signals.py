"""Indicators from the strategy doc, computed on wide daily price frames (columns = symbols).

Everything here is a pure function of prices, so the same data always gives the same answer.
Weekly values use Friday-ending weeks; run after Friday's close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import REGIME_LEVEL


def rsi(close: pd.DataFrame | pd.Series, n: int = 14):
    """Wilder's RSI."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).where(dn != 0, 100.0)


def weekly(df: pd.DataFrame, how: str) -> pd.DataFrame:
    r = df.resample("W-FRI")
    return {"last": r.last, "max": r.max, "min": r.min, "sum": r.sum}[how]().dropna(how="all")


def _ret(close: pd.DataFrame, n: int, skip: int = 0) -> pd.Series:
    if len(close) <= n + skip:
        return pd.Series(np.nan, index=close.columns)
    end = close.iloc[-1 - skip]
    return end / close.iloc[-1 - skip - n] - 1


def metrics(px: dict, min_history: int = 260) -> pd.DataFrame:
    """One row per symbol with every signal the strategy uses, as of the last bar."""
    close, high, low, vol = px["close"].ffill(), px["high"], px["low"], px["volume"]
    bench = px["nifty50"].reindex(close.index).ffill()
    hist = close.notna().sum()
    m = pd.DataFrame(index=close.columns)
    last = close.iloc[-1]
    m["close"] = last
    m["history"] = hist
    for n in (50, 150, 200):
        m[f"sma{n}"] = close.rolling(n).mean().iloc[-1]
    m["sma200_1m"] = close.rolling(200).mean().iloc[-22] if len(close) > 222 else np.nan
    m["hi52"] = high.rolling(252, min_periods=200).max().iloc[-1]
    m["lo52"] = low.rolling(252, min_periods=200).min().iloc[-1]
    m["low15"] = low.iloc[-15:].min()
    m["rsi_d"] = rsi(close).iloc[-1]
    m["turnover_cr"] = (close * vol).rolling(50).mean().iloc[-1] / 1e7

    # Relative strength vs Nifty 50: (P/P[-n]) / (N/N[-n]) - 1
    for n in (55, 123):
        m[f"rs{n}"] = (close.iloc[-1] / close.iloc[-1 - n]) / (bench.iloc[-1] / bench.iloc[-1 - n]) - 1
    m["rs55_prev"] = (close.iloc[-6] / close.iloc[-6 - 55]) / (bench.iloc[-6] / bench.iloc[-6 - 55]) - 1

    # IBD-style RS rating: 40% latest quarter, 20% each of the three before, as a 1-99 percentile
    raw = 2 * _ret(close, 63) + _ret(close, 126) + _ret(close, 189) + _ret(close, 252)
    m["rs_rating"] = (raw.rank(pct=True) * 98 + 1).round()

    # Volatility-adjusted score (NSE momentum index idea), skipping the latest month
    sigma = close.pct_change(fill_method=None).iloc[-252:].std() * np.sqrt(252)
    r6, r12 = _ret(close, 126, skip=21), _ret(close, 252, skip=21)
    z = lambda s: (s - s.mean()) / s.std(ddof=0)  # noqa: E731
    m["mom6"], m["mom12"], m["vol"] = r6, r12, sigma
    m["score"] = 0.5 * z(r6 / sigma) + 0.5 * z(r12 / sigma)

    # Weekly chart
    wc, wh, wl = weekly(close, "last"), weekly(high, "max"), weekly(low, "min")
    wv = weekly(vol, "sum")
    ma30, ma10 = wc.rolling(30).mean(), wc.rolling(10).mean()
    m["w_close"] = wc.iloc[-1]
    m["w_low"] = wl.iloc[-1]
    m["w_low3"] = wl.iloc[-3:].min()
    m["ma30w"], m["ma30w_prev"] = ma30.iloc[-1], ma30.iloc[-2]
    m["ma10w"], m["ma10w_prev"] = ma10.iloc[-1], ma10.iloc[-2]
    m["rsi_w"] = rsi(wc).iloc[-1]
    m["pivot"] = wh.iloc[-11:-1].max()          # highest high of the 10 weeks before this one
    m["w_vol_ratio"] = wv.iloc[-1] / wv.iloc[-11:-1].mean()

    m["template"] = trend_template(m)
    m.loc[hist < min_history, "score"] = np.nan
    return m


def trend_template(m: pd.DataFrame, rs_min: float = 70) -> pd.Series:
    """Minervini's eight conditions plus the weekly/daily RSI zone check from the doc."""
    c = m["close"]
    return ((c > m["sma150"]) & (m["sma150"] > m["sma200"]) & (m["sma200"] > m["sma200_1m"])
            & (c > m["sma50"]) & (m["sma50"] > m["sma150"]) & (m["sma50"] > m["sma200"])
            & (m["rs_rating"] >= rs_min) & (c >= 0.75 * m["hi52"]) & (c >= 1.30 * m["lo52"])
            & (m["rsi_w"] > 60) & (m["rsi_d"] > 50))


def raw_regime(n500: pd.Series, breadth: float, offset: int = 0) -> dict:
    """Green / Amber / Red from Nifty 500 vs its 40-week MA and % of stocks above their 200 DMA.
    offset=1 evaluates the previous weekly close."""
    wc = n500.resample("W-FRI").last().dropna()
    if offset:
        wc = wc.iloc[:-offset]
    ma40 = wc.rolling(40).mean()
    above = bool(wc.iloc[-1] > ma40.iloc[-1])
    rising = bool(ma40.iloc[-1] > ma40.iloc[-5])
    trend_ok, breadth_ok = above and rising, breadth > 0.50
    if (not above and not rising) or breadth < 0.35:
        name = "red"
    elif trend_ok and breadth_ok:
        name = "green"
    else:
        name = "amber"
    return {"regime": name, "index_close": float(wc.iloc[-1]), "ma40w": float(ma40.iloc[-1]),
            "above_ma40": above, "ma40_rising": rising, "breadth": round(float(breadth), 4)}


def breadth(close: pd.DataFrame, offset_days: int = 0) -> float:
    c = close.ffill()
    s200 = c.rolling(200).mean()
    i = -1 - offset_days
    ok = s200.iloc[i].notna()
    return float((c.iloc[i][ok] > s200.iloc[i][ok]).mean()) if ok.any() else 0.0


def regime(px: dict, previous_effective: str | None) -> dict:
    """Applies the two-week confirmation: downgrades act at once, upgrades move one level and
    only after two consecutive weekly closes in the better regime."""
    now = raw_regime(px["nifty500"], breadth(px["close"]))
    prev = raw_regime(px["nifty500"], breadth(px["close"], offset_days=5), offset=1)
    eff_prev = previous_effective or prev["regime"]
    lvl_now, lvl_prev, lvl_eff = (REGIME_LEVEL[now["regime"]], REGIME_LEVEL[prev["regime"]],
                                  REGIME_LEVEL[eff_prev])
    if lvl_now <= lvl_eff:
        eff = lvl_now
    elif lvl_prev > lvl_eff:
        eff = lvl_eff + 1
    else:
        eff = lvl_eff
    name = {v: k for k, v in REGIME_LEVEL.items()}[eff]
    return {**now, "raw": now["regime"], "previous_raw": prev["regime"], "previous_effective": eff_prev,
            "regime": name}
