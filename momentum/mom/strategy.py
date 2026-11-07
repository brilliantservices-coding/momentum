"""The weekly routine from the strategy doc: regime first, then exits, then new buys.

`propose()` takes this week's signals and the current Momentum book and returns the list of
changes for the owner to review. Nothing here changes the book; sign-off in the portal does.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from .config import REGIME_EXPOSURE
from .signals import trend_template


def _f(x, nd=2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


def _i(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else int(round(float(x)))


def _excluded(cfg) -> set[str]:
    return {s.strip().upper() for s in str(cfg.get("exclude", "")).replace(";", ",").split(",") if s.strip()}


def factor_list(cfg: dict, with_quality: bool) -> list[tuple[str, str]]:
    """Every filter a stock must pass, in the order the funnel applies them."""
    out = [
        ("history", "1 year+ of price history"),
        ("liquidity", "Turnover ≥ ₹%g cr/day" % cfg["min_turnover_cr"]),
        ("above_150", "Price above 150 DMA"),
        ("ma150_200", "150 DMA above 200 DMA"),
        ("ma200_rising", "200 DMA rising for a month"),
        ("above_50", "Price above 50 DMA"),
        ("ma50_stack", "50 DMA above 150 and 200 DMA"),
        ("rs_rating", "RS rating ≥ %g" % cfg["rs_rating_min"]),
        ("near_high", "Within 25% of 52-week high"),
        ("off_low", "30%+ above 52-week low"),
        ("rsi_w", "Weekly RSI above 60"),
        ("rsi_d", "Daily RSI above 50"),
        ("rs55", "RS 55 vs Nifty above 0"),
        ("rs123", "RS 123 vs Nifty above 0"),
        ("not_excluded", "Not on your exclusion list"),
    ]
    if with_quality:
        out.append(("quality", "Quality filter (Screener data)"))
    return out


def rank_universe(m: pd.DataFrame, cfg: dict, quality: dict | None = None, sectors: dict | None = None):
    """Adds one boolean column per filter (`f_<key>`), `passes`, `fail` (first failed filter),
    `nfail`, `rank` (1 = best among stocks that pass every filter) and `rank_if_held`, the position
    used by the exit rule."""
    m = m.copy()
    excl = _excluded(cfg)
    c = m["close"]
    m["template"] = trend_template(m, cfg["rs_rating_min"])
    tests = {
        "history": m["score"].notna(),
        "liquidity": m["turnover_cr"] >= cfg["min_turnover_cr"],
        "above_150": c > m["sma150"],
        "ma150_200": m["sma150"] > m["sma200"],
        "ma200_rising": m["sma200"] > m["sma200_1m"],
        "above_50": c > m["sma50"],
        "ma50_stack": (m["sma50"] > m["sma150"]) & (m["sma50"] > m["sma200"]),
        "rs_rating": m["rs_rating"] >= cfg["rs_rating_min"],
        "near_high": c >= 0.75 * m["hi52"],
        "off_low": c >= 1.30 * m["lo52"],
        "rsi_w": m["rsi_w"] > 60,
        "rsi_d": m["rsi_d"] > 50,
        "rs55": m["rs55"] > 0,
        "rs123": m["rs123"] > 0,
        "not_excluded": pd.Series(~m.index.isin(list(excl)), index=m.index),
    }
    if quality:
        tests["quality"] = pd.Series({s: quality.get(s, {}).get("passed") for s in m.index}).ne(False)
    factors = factor_list(cfg, bool(quality))
    m["passes"], m["fail"], m["nfail"] = True, None, 0
    for key, label in factors:
        ok = pd.Series(tests[key], index=m.index).astype("boolean").fillna(False).astype(bool)
        m[f"f_{key}"] = ok
        m.loc[m["passes"] & ~ok, "fail"] = label
        m["nfail"] += (~ok).astype(int)
        m["passes"] &= ok
    passing = m[m["passes"]].sort_values("score", ascending=False)
    m["rank"] = np.nan
    m.loc[passing.index, "rank"] = range(1, len(passing) + 1)
    # Exit ranking: stocks passing every filter first, then the rest, each by score. A holding that
    # drops out of the filters therefore ranks behind every stock that still passes.
    rest = m[~m["passes"] & m["score"].notna()].sort_values("score", ascending=False)
    m["rank_if_held"] = 9999
    order = list(passing.index) + list(rest.index)
    m.loc[order, "rank_if_held"] = range(1, len(order) + 1)
    m["sector"] = [(sectors or {}).get(s, {}).get("industry", "Unknown") for s in m.index]
    if quality:
        m["quality"] = [quality.get(s, {}).get("passed") for s in m.index]
    m.attrs["factors"] = factors
    return m


def funnel(m: pd.DataFrame) -> list[dict]:
    """Stocks left after each filter, applied in order, plus how many fail each filter on its own."""
    alive = pd.Series(True, index=m.index)
    out = [{"step": "Nifty 500 stocks with prices", "left": int(len(m))}]
    for key, label in m.attrs["factors"]:
        ok = m[f"f_{key}"]
        before = int(alive.sum())
        alive &= ok
        out.append({"key": key, "step": label, "left": int(alive.sum()), "dropped": before - int(alive.sum()),
                    "fail_alone": int((~ok).sum())})
    return out


def screen_rows(m: pd.DataFrame, setups: dict[str, str] | None = None) -> list[dict]:
    """Compact per-stock record of every factor, for the portal's Screen page."""
    keys = [k for k, _ in m.attrs["factors"]]
    rows = []
    for s, r in m.iterrows():
        rows.append({
            "s": s, "sec": r["sector"], "close": _f(r["close"]), "score": _f(r["score"]),
            "rank": None if pd.isna(r["rank"]) else int(r["rank"]), "nfail": int(r["nfail"]),
            "f": "".join("1" if r[f"f_{k}"] else "0" for k in keys),
            "v": {"turnover_cr": _f(r["turnover_cr"], 1), "rs_rating": _i(r["rs_rating"]),
                  "rsi_w": _i(r["rsi_w"]), "rsi_d": _i(r["rsi_d"]), "rs55": _f(100 * r["rs55"], 1),
                  "rs123": _f(100 * r["rs123"], 1), "from_high": _f(100 * (r["close"] / r["hi52"] - 1), 1),
                  "above_low": _f(100 * (r["close"] / r["lo52"] - 1), 1),
                  "vs50": _f(100 * (r["close"] / r["sma50"] - 1), 1), "vs200": _f(100 * (r["close"] / r["sma200"] - 1), 1)},
            "setup": (setups or {}).get(s),
        })
    rows.sort(key=lambda x: (x["rank"] is None, x["rank"] or 0, x["nfail"], -(x["score"] or -99)))
    return rows


def _setup(r) -> tuple[str | None, str]:
    """Is there a proper pivot this week? Breakout of the 10-week base on volume, or a quiet
    pullback to a rising 10-week MA. Otherwise it goes on the watchlist."""
    c, pivot, ma10 = r["close"], r["pivot"], r["ma10w"]
    if pivot and c > pivot:
        if c > pivot * (1 + r["_ext"] / 100):
            return None, f"extended: {100 * (c / pivot - 1):.1f}% above the {pivot:.1f} pivot"
        if r["w_vol_ratio"] >= 1.5:
            return "breakout", f"breakout above {pivot:.1f} on {r['w_vol_ratio']:.1f}x weekly volume"
        return None, f"broke {pivot:.1f} but volume only {r['w_vol_ratio']:.1f}x average"
    if ma10 and r["ma10w"] > r["ma10w_prev"] and ma10 <= c <= ma10 * 1.03 and r["w_vol_ratio"] < 1:
        return "pullback", f"quiet pullback to the rising 10-week MA ({ma10:.1f})"
    return None, "no pivot this week (wait for a breakout or a pullback to the 10-week MA)"


def propose(m: pd.DataFrame, book: dict, reg: dict, cfg: dict, today: date | None = None) -> dict:
    today = today or date.today()
    positions: dict = book.get("positions", {})
    cash = float(book.get("cash", 0))
    px = m["close"].to_dict()
    value = cash + sum(p["qty"] * px.get(s, p["entry"]) for s, p in positions.items())
    peak = max(float(book.get("peak_value") or value), value)
    drawdown = 1 - value / peak if peak else 0
    regime_name = reg["regime"]
    changes, holds = [], []

    # Step 2: exits
    remaining = {}
    for sym, p in positions.items():
        if sym not in m.index or pd.isna(m.at[sym, "close"]):
            holds.append({"symbol": sym, "note": "no price data this week; review by hand"})
            remaining[sym] = p
            continue
        r = m.loc[sym]
        reasons = []
        if r["w_low"] <= p["stop"]:
            reasons.append(f"stop {p['stop']:.1f} hit (low {r['w_low']:.1f})")
        if r["w_close"] < r["ma30w"]:
            reasons.append("weekly close below the 30-week MA (Stage 3/4)")
        elif r["ma30w"] < r["ma30w_prev"]:
            reasons.append("30-week MA has turned down")
        if r["rs55"] < 0 and r["rs55_prev"] < 0 and r["w_close"] < r["ma10w"]:
            reasons.append("RS 55 below 0 two weeks running and price under the 10-week MA")
        if r["rsi_w"] < 40:
            reasons.append(f"weekly RSI {r['rsi_w']:.0f} below 40")
        if r["rank_if_held"] > cfg["exit_rank"]:
            reasons.append(f"rank {int(r['rank_if_held'])} is below the exit line of {cfg['exit_rank']}")
        if sym in _excluded(cfg):
            reasons.append("on your exclusion list")
        gained = p.get("max_close", p["entry"]) >= 1.10 * p["entry"]
        if gained and r["w_close"] < r["ma10w"]:
            reasons.append("after a +10% move, weekly close below the 10-week MA")
        if reasons:
            changes.append({"type": "sell", "symbol": sym, "qty": p["qty"], "price": _f(r["close"]),
                            "entry": _f(p["entry"]), "pnl_pct": _f(100 * (r["close"] / p["entry"] - 1), 1),
                            "reasons": reasons, "rank": int(r["rank_if_held"])})
            cash += p["qty"] * r["close"]
            continue
        # Stops only move up: breakeven after +10%, then trail to the 3-week swing low
        new_stop = p["stop"]
        if r["close"] >= 1.10 * p["entry"] or gained:
            new_stop = max(new_stop, p["entry"], r["w_low3"])
        if new_stop > p["stop"] * 1.005:
            changes.append({"type": "raise_stop", "symbol": sym, "qty": p["qty"], "price": _f(r["close"]),
                            "stop": _f(p["stop"]), "new_stop": _f(new_stop),
                            "reasons": ["trail stop: breakeven after +10%, then the 3-week swing low"]})
        held_days = (today - date.fromisoformat(p["entry_date"])).days
        if (held_days <= 21 and r["close"] >= 1.20 * p["entry"] and r["close"] > 1.25 * r["sma50"]
                and not p.get("trimmed") and p["qty"] >= 3):
            q = p["qty"] // 3
            changes.append({"type": "trim", "symbol": sym, "qty": q, "price": _f(r["close"]),
                            "reasons": ["up 20%+ within three weeks and 25%+ above the 50 DMA: sell one third into strength"]})
            cash += q * r["close"]
            p = {**p, "qty": p["qty"] - q}
        remaining[sym] = {**p, "stop": new_stop}
        holds.append({"symbol": sym, "rank": int(r["rank_if_held"]), "pnl_pct": _f(100 * (r["close"] / p["entry"] - 1), 1),
                      "stop": _f(new_stop), "close": _f(r["close"])})

    # Step 1 applied to holdings: trim to the regime's exposure cap, weakest rank first
    cap = REGIME_EXPOSURE[regime_name] * value
    invested = sum(p["qty"] * px.get(s, p["entry"]) for s, p in remaining.items())
    for sym in sorted(remaining, key=lambda s: -m.at[s, "rank_if_held"] if s in m.index else 0):
        if invested <= cap * 1.02:
            break
        p, price = remaining.pop(sym), px.get(sym, remaining.get(sym, {}).get("entry", 0))
        changes.append({"type": "sell", "symbol": sym, "qty": p["qty"], "price": _f(price), "entry": _f(p["entry"]),
                        "pnl_pct": _f(100 * (price / p["entry"] - 1), 1),
                        "reasons": [f"{regime_name} regime caps stocks at {REGIME_EXPOSURE[regime_name]:.0%} of the book"]})
        holds = [h for h in holds if h["symbol"] != sym]
        cash += p["qty"] * price
        invested -= p["qty"] * price

    # Steps 3 and 4: new buys
    buys, watch = [], []
    risk_pct = cfg["risk_pct"] * (0.5 if drawdown >= 0.10 else 1)
    top_n, size_mult = cfg["buy_rank"], 1.0
    if regime_name == "amber":
        top_n, size_mult = min(5, top_n), 0.5
    sector_val: dict[str, float] = {}
    for s, p in remaining.items():
        sec = m.at[s, "sector"] if s in m.index else "Unknown"
        sector_val[sec] = sector_val.get(sec, 0) + p["qty"] * px.get(s, p["entry"])
    heat = sum(max(0.0, p["qty"] * (min(px.get(s, p["entry"]), p["entry"]) - p["stop"])) for s, p in remaining.items())
    slots = cfg["max_positions"] - len(remaining)
    ranked = m[m["rank"].notna()].sort_values("rank")
    if True:
        for sym, r in ranked.head(top_n).iterrows():
            if sym in remaining:
                continue
            r = r.copy()
            r["_ext"] = cfg["extended_pct"]
            kind, why = _setup(r)
            base = {"symbol": sym, "rank": int(r["rank"]), "score": _f(r["score"]), "close": _f(r["close"]),
                    "sector": r["sector"], "rs55": _f(100 * r["rs55"], 1), "rs_rating": int(r["rs_rating"])}
            if regime_name == "red":
                watch.append({**base, "note": f"Red regime, no new buys ({why})", "setup_ok": bool(kind)})
                continue
            if not kind:
                watch.append({**base, "note": why})
                continue
            entry = float(r["close"])
            stop = max(float(r["low15"]) * 0.995, entry * (1 - cfg["max_stop_pct"] / 100))
            stop = min(stop, entry * 0.97)
            per_share = entry - stop
            limits = {
                "risk": risk_pct * size_mult / 100 * value / per_share,
                "position cap": cfg["position_cap_pct"] / 100 * value / entry,
                "cash": cash / entry,
                "regime exposure": max(0.0, cap - invested) / entry,
                "sector cap": max(0.0, cfg["sector_cap_pct"] / 100 * value - sector_val.get(r["sector"], 0)) / entry,
                "portfolio heat": max(0.0, cfg["heat_cap_pct"] / 100 * value - heat) / per_share,
            }
            bound = min(limits, key=limits.get)
            qty = int(math.floor(limits[bound]))
            if slots <= 0:
                watch.append({**base, "note": f"{why}; no free slot ({cfg['max_positions']} positions)"})
                continue
            if qty < 1:
                watch.append({**base, "note": f"{why}; no room: {bound} limit reached"})
                continue
            reasons = [why, f"rank {int(r['rank'])}, RS rating {r['rs_rating']:.0f}, RS 55 {100 * r['rs55']:+.1f}%"]
            if bound != "risk":
                reasons.append(f"size limited by {bound}")
            if size_mult < 1:
                reasons.append("amber regime: half size")
            if risk_pct < cfg["risk_pct"]:
                reasons.append("book is 10%+ below its peak: risk halved")
            if "quality" in r and r["quality"] is None:
                reasons.append("quality not checked (no fundamentals for this stock)")
            buys.append({"type": "buy", **base, "qty": qty, "price": _f(entry), "stop": _f(stop),
                         "risk_pct": _f(100 * qty * per_share / value, 2), "setup": kind, "reasons": reasons})
            cash -= qty * entry
            invested += qty * entry
            heat += qty * per_share
            sector_val[r["sector"]] = sector_val.get(r["sector"], 0) + qty * entry
            slots -= 1
    changes.extend(buys)
    eligible = int(min(top_n, len(ranked)))
    tail = [
        {"step": f"Top {top_n} by momentum score" + (" (Red regime: no new buys)" if regime_name == "red" else
                                                     " (Amber regime)" if regime_name == "amber" else ""),
         "left": eligible},
        {"step": "Valid entry this week (breakout or pullback)", "left": len(buys) + sum(
            1 for w in watch if "no room" in w["note"] or "no free slot" in w["note"] or w.get("setup_ok"))},
        {"step": "Proposed buys after sizing limits", "left": len(buys)},
    ]
    setups = {b["symbol"]: f"buy: {b['setup']}" for b in buys}
    setups.update({w["symbol"]: f"watch: {w['note']}" for w in watch})
    for i, c in enumerate(changes):
        c["id"] = f"c{i + 1}"
    ranking = [{"rank": int(r["rank"]), "symbol": s, "score": _f(r["score"]), "close": _f(r["close"]),
                "sector": r["sector"], "rs55": _f(100 * r["rs55"], 1), "rs123": _f(100 * r["rs123"], 1),
                "rs_rating": int(r["rs_rating"]), "rsi_w": int(round(r["rsi_w"])), "held": s in positions}
               for s, r in ranked.head(cfg["exit_rank"]).iterrows()]
    return {
        "changes": changes, "holds": holds, "watchlist": watch, "ranking": ranking,
        "book": {"value": _f(value), "cash_now": _f(book.get("cash", 0)), "cash_after": _f(cash),
                 "invested_after": _f(invested), "exposure_after_pct": _f(100 * invested / value, 1) if value else 0,
                 "heat_after_pct": _f(100 * heat / value, 2) if value else 0, "peak_value": _f(peak),
                 "drawdown_pct": _f(100 * drawdown, 1), "positions_after": len(remaining) + len(buys)},
        "funnel_tail": tail, "setups": setups,
        "counts": {"universe": int(len(m)), "passing": int(m["passes"].sum()),
                   "template": int(m["template"].fillna(False).sum())},
    }
