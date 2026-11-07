"""Synthetic market: a rising index, a few smooth leaders, laggards and a breakout setup."""
import numpy as np
import pandas as pd


def market(n_days=520, seed=1, index_trend=0.0006):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-11-06", periods=n_days)
    syms = [f"S{i:03d}" for i in range(60)]
    drift = np.linspace(-0.0008, 0.0025, len(syms))  # S059 strongest
    rets = rng.normal(drift, 0.012, size=(n_days, len(syms)))
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=syms)
    high, low = close * 1.01, close * 0.99
    volume = pd.DataFrame(rng.integers(2_000_000, 4_000_000, size=close.shape), index=idx, columns=syms).astype(float)
    nifty = pd.Series(10000 * np.exp(np.cumsum(rng.normal(index_trend, 0.008, n_days))), index=idx)
    # S058: a tight base for 10 weeks then a breakout on heavy volume in the last week
    base = close["S058"].iloc[-56]
    close.loc[close.index[-55:-5], "S058"] = base * (1 + 0.01 * np.sin(np.arange(50)))
    close.loc[close.index[-5:], "S058"] = base * 1.03
    high["S058"], low["S058"] = close["S058"] * 1.005, close["S058"] * 0.995
    volume.loc[volume.index[-5:], "S058"] *= 3
    return {"close": close, "high": high, "low": low, "volume": volume, "nifty50": nifty, "nifty500": nifty}


def sectors(px):
    return {s: {"name": s, "industry": ["IT", "Auto", "Banks", "Pharma", "Capital Goods"][i % 5]}
            for i, s in enumerate(px["close"].columns)}
