from datetime import date

import pandas as pd

from mom import signals, strategy
from mom.config import DEFAULTS
from synth import market, sectors


def cfg(**kw):
    return {**DEFAULTS, "min_turnover_cr": 1.0, **kw}


def ranked(px, c):
    return strategy.rank_universe(signals.metrics(px), c, None, sectors(px))


def test_rsi_bounds():
    s = pd.Series(range(1, 60), dtype=float)
    assert signals.rsi(s).iloc[-1] == 100.0
    px = market()
    r = signals.rsi(px["close"]).iloc[-1]
    assert ((r >= 0) & (r <= 100)).all()


def test_rs_against_benchmark():
    px = market()
    m = signals.metrics(px)
    c, n = px["close"]["S059"], px["nifty50"]
    expect = (c.iloc[-1] / c.iloc[-56]) / (n.iloc[-1] / n.iloc[-56]) - 1
    assert abs(m.at["S059", "rs55"] - expect) < 1e-9


def test_strong_stocks_rank_first_and_weak_fail():
    px = market()
    m = ranked(px, cfg())
    top = m[m["rank"] == 1].index[0]
    assert int(top[1:]) >= 40  # a high-drift stock
    assert not m.at["S000", "passes"]


def test_regime_green_in_uptrend_red_in_downtrend():
    up = market(index_trend=0.001)
    # make breadth high: everything rising
    up["close"] = up["close"].iloc[0] * (1 + pd.DataFrame(0.001, index=up["close"].index, columns=up["close"].columns)).cumprod()
    assert signals.regime(up, None)["regime"] == "green"
    down = market(index_trend=-0.0015)
    down["close"] = down["close"].iloc[0] * (1 - pd.DataFrame(0.001, index=down["close"].index, columns=down["close"].columns)).cumprod()
    assert signals.regime(down, None)["regime"] == "red"


def test_upgrade_needs_two_weeks():
    up = market(index_trend=0.001)
    up["close"] = up["close"].iloc[0] * (1 + pd.DataFrame(0.001, index=up["close"].index, columns=up["close"].columns)).cumprod()
    r = signals.regime(up, "red")  # both weeks are green, so it moves up one level only
    assert r["raw"] == "green" and r["regime"] == "amber"
    assert signals.regime(up, "amber")["regime"] == "green"


def test_buys_respect_limits_and_sizing():
    px = market()
    c = cfg()
    m = ranked(px, c)
    book = {"cash": 1_000_000, "positions": {}, "peak_value": 1_000_000}
    p = strategy.propose(m, book, {"regime": "green"}, c, today=date(2026, 11, 7))
    buys = [x for x in p["changes"] if x["type"] == "buy"]
    assert len(buys) <= c["max_positions"]
    for b in buys:
        assert b["qty"] * b["price"] <= 0.10 * 1_000_000 + 1
        assert (b["price"] - b["stop"]) * b["qty"] <= 0.01 * 1_000_000 + 1
        assert b["stop"] >= b["price"] * 0.92 - 0.01
    assert p["book"]["heat_after_pct"] <= c["heat_cap_pct"] + 1e-6
    assert p["book"]["cash_after"] >= 0
    # every top-15 stock is either a buy or on the watchlist with a reason
    assert len(buys) + len(p["watchlist"]) == min(15, p["counts"]["passing"])


def test_breakout_detected():
    px = market()
    m = ranked(px, cfg(rs_rating_min=0))
    r = m.loc["S058"].copy()
    r["_ext"] = 5.0
    kind, why = strategy._setup(r)
    assert kind == "breakout", why


def test_red_regime_no_buys_and_trims_exposure():
    px = market()
    c = cfg()
    m = ranked(px, c)
    good = [s for s in m.sort_values("score", ascending=False).index[:5]]
    positions = {s: {"qty": 1000, "entry": float(m.at[s, "close"]) * 0.9, "stop": float(m.at[s, "close"]) * 0.8,
                     "entry_date": "2026-06-01"} for s in good}
    book = {"cash": 10_000, "positions": positions}
    p = strategy.propose(m, book, {"regime": "red"}, c, today=date(2026, 11, 7))
    assert not [x for x in p["changes"] if x["type"] == "buy"]
    assert p["book"]["exposure_after_pct"] <= 20 * 1.02 + 1e-6


def test_exit_rules_fire():
    px = market()
    c = cfg()
    m = ranked(px, c)
    weak = "S000"
    close = float(m.at[weak, "close"])
    book = {"cash": 0, "positions": {weak: {"qty": 10, "entry": close * 2, "stop": close * 1.5, "entry_date": "2026-01-05"}}}
    p = strategy.propose(m, book, {"regime": "green"}, c, today=date(2026, 11, 7))
    sell = [x for x in p["changes"] if x["type"] == "sell" and x["symbol"] == weak]
    assert sell and any("stop" in r for r in sell[0]["reasons"]) and any("rank" in r for r in sell[0]["reasons"])


def test_trailing_stop_raises():
    px = market()
    c = cfg()
    m = ranked(px, c)
    s = m[m["passes"]].sort_values("score", ascending=False).index[0]
    close = float(m.at[s, "close"])
    book = {"cash": 0, "positions": {s: {"qty": 10, "entry": close / 1.3, "stop": close / 1.3 * 0.92,
                                         "entry_date": "2026-05-01", "max_close": close}}}
    p = strategy.propose(m, book, {"regime": "green"}, c, today=date(2026, 11, 7))
    rs = [x for x in p["changes"] if x["type"] == "raise_stop"]
    assert rs and rs[0]["new_stop"] >= close / 1.3
