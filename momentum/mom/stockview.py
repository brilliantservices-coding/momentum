"""One stock across the app: where it got to in this week's funnel, every factor with its value,
the exit-rule check, its place in the book and in Zerodha, and its recent weekly ranks."""
from __future__ import annotations

from urllib.parse import quote

from .strategy import EXIT_FIELDS, exit_reasons

# Which number to show beside each factor (keys of a screen row's "v")
FACTOR_VALUE = {
    "liquidity": ("turnover_cr", "₹{} cr/day"), "rs_rating": ("rs_rating", "{}"), "near_high": ("from_high", "{}% from high"),
    "off_low": ("above_low", "{}% above low"), "rsi_w": ("rsi_w", "{}"), "rsi_d": ("rsi_d", "{}"),
    "rs55": ("rs55", "{}%"), "rs123": ("rs123", "{}%"), "above_50": ("vs50", "{}% vs 50 DMA"),
    "ma150_200": ("vs200", "price {}% vs 200 DMA"),
}


def links(sym: str) -> dict:
    s = quote(sym, safe="")
    return {"yahoo": f"https://finance.yahoo.com/quote/{s}.NS", "screener": f"https://www.screener.in/company/{s}/",
            "tradingview": f"https://in.tradingview.com/chart/?symbol=NSE%3A{s}"}


def search(rows: list[dict], q: str, limit: int = 30) -> list[dict]:
    q = q.strip().upper()
    if not q:
        return []
    hits = [r for r in rows if q in r["s"] or q in str(r.get("sec", "")).upper()]
    hits.sort(key=lambda r: (r["s"] != q, not r["s"].startswith(q), r["s"]))
    return hits[:limit]


def view(sym: str, week: dict | None, screen: dict | None, cfg: dict) -> dict:
    """Funnel position and factor table for one stock from this week's screen."""
    week, screen = week or {}, screen or {}
    row = next((r for r in screen.get("rows", []) if r["s"] == sym), None)
    out = {"symbol": sym, "links": links(sym), "row": row, "in_universe": bool(row)}
    if not row:
        return out
    factors, funnel = screen.get("factors", []), week.get("funnel") or []
    left_at = {f.get("key"): f.get("left") for f in funnel if f.get("key")}
    items, reached, stopped = [], 0, None
    for i, f in enumerate(factors):
        ok = row["f"][i] == "1" if i < len(row["f"]) else None
        key = f["key"]
        val = None
        if key in FACTOR_VALUE:
            vk, fmt = FACTOR_VALUE[key]
            v = (row.get("v") or {}).get(vk)
            val = fmt.format(v) if v is not None else None
        if stopped is None:
            if ok:
                reached = i + 1
            else:
                stopped = f["label"]
        items.append({"key": key, "label": f["label"], "ok": ok, "value": val, "left": left_at.get(key)})
    out.update(factors=items, reached=reached, total=len(factors), stopped_at=stopped,
               passed=sum(1 for x in items if x["ok"]), universe=(funnel[0]["left"] if funnel else None))
    tail = [f for f in funnel if not f.get("key")]
    if row.get("rank"):
        top_n = min(5, cfg["buy_rank"]) if (week.get("regime") or {}).get("regime") == "amber" else cfg["buy_rank"]
        setup = row.get("setup") or ""
        out["stages"] = [
            {"label": f"Ranked {row['rank']} of {week.get('counts', {}).get('passing', '?')} stocks passing every filter", "ok": True},
            {"label": f"In the top {top_n} eligible to buy", "ok": row["rank"] <= top_n},
            {"label": "Valid entry this week" + (f": {setup.split(': ', 1)[-1]}" if setup else ""),
             "ok": setup.startswith("buy")},
            {"label": "Proposed as a buy this week", "ok": setup.startswith("buy")},
        ]
        out["tail"] = tail
    x = row.get("x") or {}
    if x and all(x.get(k) is not None for k in EXIT_FIELDS):
        out["exit"] = exit_reasons(x, cfg, sym)
    return out
