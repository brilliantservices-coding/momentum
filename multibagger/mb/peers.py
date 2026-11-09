"""Industry-relative percentiles (strategy v2: judge a stock against its own sector, not one fixed bar).

For each metric, a company's percentile is the share of its peers it beats (ties count half). Peers are
the companies in the same industry when there are at least MIN_PEERS with data, else the same sector,
else the whole screened set.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

MIN_PEERS = 8
METRICS = ("roce", "roce_3y_avg", "sales_cagr_3y", "revenue_growth_1y", "ebitda_margin", "margin_delta",
           "incremental_roce", "cash_conversion")


def _value(m: dict, k: str):
    if k == "cash_conversion":
        cfo, e3 = m.get("cfo_3y"), m.get("ebitda_3y")
        return None if cfo is None or not e3 or e3 <= 0 else cfo / e3
    v = m.get(k)
    return float(v) if isinstance(v, (int, float)) else None


def _key(m: dict, field: str) -> str:
    return str(m.get(field) or "").strip().lower()


def percentiles(funds: list[dict]) -> dict[str, dict]:
    """{symbol: {metric: percentile 0-100, "peer_group": "industry: ..."}}"""
    groups: dict[tuple, list[float]] = {}
    for m in funds:
        for k in METRICS:
            v = _value(m, k)
            if v is None:
                continue
            for level in ("industry", "sector"):
                if _key(m, level):
                    groups.setdefault((level, _key(m, level), k), []).append(v)
            groups.setdefault(("all", "", k), []).append(v)
    for vals in groups.values():
        vals.sort()
    out: dict[str, dict] = {}
    for m in funds:
        row: dict = {}
        for k in METRICS:
            v = _value(m, k)
            if v is None:
                continue
            for level in ("industry", "sector", "all"):
                vals = groups.get((level, _key(m, level) if level != "all" else "", k)) or []
                if len(vals) >= MIN_PEERS or level == "all":
                    break
            if len(vals) < 2:
                continue
            below, upto = bisect_left(vals, v), bisect_right(vals, v)
            others = len(vals) - 1
            row[k] = round(100 * (below + 0.5 * (upto - below - 1)) / others, 1)
            row.setdefault("peer_group", f"{level}: {_key(m, level) or 'all'} ({len(vals)})")
        out[m.get("id") or m.get("symbol")] = row
    return out
