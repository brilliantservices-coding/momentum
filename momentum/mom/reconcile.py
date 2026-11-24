"""Reconciliation: what Zerodha actually holds vs the Momentum book and this week's findings.

For every stock in the Kite account (and every book position Kite does not show) it suggests one
action: SELL, TRIM, HOLD, ADD or REVIEW, with the reasons. It also lists stocks that pass every
filter but are not held, so the owner can place override buys. Nothing here places orders.
"""
from __future__ import annotations

import math

from .config import LIQUID, REGIME_EXPOSURE
from .strategy import EXIT_FIELDS, _excluded, exit_reasons

# How much of the normal size an override buy suggests, by regime. The owner can change the qty.
OVERRIDE_SIZE = {"green": 1.0, "amber": 0.5, "red": 0.25}


def tick(price: float) -> float:
    """NSE limit prices must be a multiple of the tick; 0.05 is valid for every equity."""
    return round(round(float(price) * 20) / 20, 2)


def size(close: float, value: float, cash: float | None, cfg: dict, regime: str, held_value: float = 0.0) -> int:
    """Suggested quantity: 1% risk to a max-stop exit, within the position cap and available cash."""
    if not close or close <= 0 or not value:
        return 0
    per_share = close * cfg["max_stop_pct"] / 100
    limits = [cfg["risk_pct"] * OVERRIDE_SIZE.get(regime, 0.5) / 100 * value / per_share,
              max(0.0, cfg["position_cap_pct"] / 100 * value - held_value) / close]
    if cash is not None:
        limits.append(max(0.0, cash) / close)
    return max(0, int(math.floor(min(limits))))


def _first_fail(row: dict, factors: list[dict]) -> str | None:
    for i, ch in enumerate(row.get("f", "")):
        if ch == "0" and i < len(factors):
            return factors[i]["label"]
    return None


def build(holdings: list[dict], week: dict | None, screen: dict | None, book: dict, cfg: dict,
          book_value: float, cash_available: float | None = None) -> dict:
    week, screen = week or {}, screen or {}
    regime = (week.get("regime") or {}).get("regime", "amber")
    signed = week.get("status") == "signed_off"
    rows_by_sym = {r["s"]: r for r in screen.get("rows", [])}
    factors = screen.get("factors", [])
    changes: dict[str, list[dict]] = {}
    for c in week.get("changes", []):
        changes.setdefault(c["symbol"], []).append(c)
    holds = {h["symbol"]: h for h in week.get("holds", [])}
    positions = book.get("positions", {})
    top_n = min(5, cfg["buy_rank"]) if regime == "amber" else cfg["buy_rank"]
    excluded = _excluded(cfg)

    kite = {}
    for h in holdings:
        sym = str(h.get("symbol") or "").upper()
        if not sym:
            continue
        qty = int(h.get("qty") or 0) + int(h.get("t1_qty") or 0)
        kite[sym] = {"qty": qty, "avg": h.get("avg"), "last": h.get("last")}

    out = []
    for sym in sorted(set(kite) | set(positions)):
        k, p = kite.get(sym), positions.get(sym)
        kq, bq = (k or {}).get("qty", 0), (p or {}).get("qty", 0)
        row = rows_by_sym.get(sym)
        last = (k or {}).get("last") or (row or {}).get("close") or (p or {}).get("entry")
        r = {"symbol": sym, "kite_qty": kq, "book_qty": bq, "avg": (k or {}).get("avg") or (p or {}).get("entry"),
             "last": last, "value": kq * (last or 0), "in_book": bool(p), "rank": (row or {}).get("rank"),
             "sector": (row or {}).get("sec") or (p or {}).get("sector"), "stop": (p or {}).get("stop"),
             "reasons": [], "qty": 0}
        avg = r["avg"] = round(float(r["avg"]), 2) if r["avg"] else r["avg"]
        r["pnl_pct"] = round(100 * (last / avg - 1), 1) if avg and last else None
        if p and kq == bq:
            r["recon"] = "matches the book"
        elif p and kq == 0:
            r["recon"] = f"book has {bq}, Zerodha has none: order not placed or not filled yet"
        elif p:
            r["recon"] = f"book has {bq}, Zerodha has {kq}"
        else:
            r["recon"] = "not in the Momentum book (your own holding)"

        if sym == LIQUID:
            liq = week.get("liquid") or {}
            target = bq if signed or not liq else int(liq.get("qty_after", bq))
            diff = target - kq
            r["action"] = "ADD" if diff > 0 else "TRIM" if diff < 0 else "HOLD"
            r["qty"] = abs(diff)
            r["reasons"] = [f"parking target for idle cash is {target} units"] if diff else ["parked cash, no change"]
            r["side"] = "BUY" if diff > 0 else "SELL"
            out.append(r)
            continue

        mine = changes.get(sym, [])
        sell = next((c for c in mine if c["type"] in ("sell", "trim")), None)
        buy = next((c for c in mine if c["type"] == "buy"), None)
        stop = next((c for c in mine if c["type"] == "raise_stop"), None)
        if p or (signed and sell):
            # Book positions follow this week's proposal (or the sign-off already applied to the book)
            if sell and kq:
                r["action"] = "SELL" if sell["type"] == "sell" else "TRIM"
                r["qty"] = kq if sell["type"] == "sell" else min(kq, int(sell["qty"]))
                r["reasons"] = list(sell["reasons"])
                if signed and not p:
                    r["reasons"].append("signed off already: the book has sold it but Zerodha still holds it")
            elif buy:
                r["action"], r["qty"], r["reasons"] = "ADD", int(buy["qty"]), list(buy["reasons"])
            elif kq == 0 and bq:
                r["action"], r["qty"] = "ADD", bq
                r["reasons"] = ["the book holds it but Zerodha does not: buy it or fix the book"]
            else:
                r["action"] = "HOLD"
                if stop:
                    r["stop"] = stop["new_stop"]
                    r["reasons"].append(f"raise the stop to {stop['new_stop']} ({stop['reasons'][0]})")
                h = holds.get(sym)
                if h and h.get("rank"):
                    r["reasons"].append(f"no exit rule triggered; rank {h['rank']}")
                elif h and h.get("note"):
                    r["reasons"].append(h["note"])
                else:
                    r["reasons"].append("no exit rule triggered")
                if kq < bq:
                    r["qty"], r["side"] = bq - kq, "BUY"
                    r["reasons"].append(f"Zerodha has {bq - kq} fewer than the book")
            r.setdefault("side", "SELL" if r["action"] in ("SELL", "TRIM") else "BUY")
            out.append(r)
            continue

        # Your own holding: judge it with the same rules the book uses
        if not row:
            r["action"] = "REVIEW"
            r["reasons"] = ["outside the Nifty 500 universe or no price data, so the strategy cannot score it"]
        elif row.get("x") and all(row["x"].get(f) is not None for f in EXIT_FIELDS):
            why = exit_reasons(row["x"], cfg, sym)
            if why:
                r["action"], r["qty"], r["reasons"] = "SELL", kq, why
            elif row.get("rank") and row["rank"] <= top_n and regime != "red" and str(row.get("setup", "")).startswith("buy"):
                r["action"] = "ADD"
                r["qty"] = size(last, book_value, cash_available, cfg, regime, held_value=kq * last)
                r["reasons"] = [f"rank {row['rank']} with a valid entry this week ({row['setup'][5:]})"]
                if not r["qty"]:
                    r["action"], r["reasons"] = "HOLD", r["reasons"] + ["already at the position cap or no cash"]
            else:
                r["action"] = "HOLD"
                if row.get("rank"):
                    r["reasons"] = [f"passes every filter, rank {row['rank']}; no exit rule triggered"]
                    if regime == "red":
                        r["reasons"].append("Red regime: no adds")
                    elif row["rank"] > top_n:
                        r["reasons"].append(f"adds only for the top {top_n}")
                    elif row.get("setup"):
                        r["reasons"].append(row["setup"].replace("watch: ", ""))
                else:
                    fail = _first_fail(row, factors)
                    r["reasons"] = [f"no exit rule triggered, but fails “{fail}”, so do not add" if fail
                                    else "no exit rule triggered"]
        else:
            fail = _first_fail(row, factors)
            r["action"] = "REVIEW"
            r["reasons"] = [(f"rank {row['rank']}, passes every filter" if row.get("rank") else f"fails “{fail}”")
                            + "; full exit checks appear after the next weekly run"]
        if sym in excluded and r["action"] != "SELL":
            r["action"], r["qty"] = "SELL", kq
            r["reasons"].insert(0, "on your exclusion list")
        r["side"] = "SELL" if r["action"] in ("SELL", "TRIM") else "BUY"
        out.append(r)

    order = {"SELL": 0, "TRIM": 1, "ADD": 2, "REVIEW": 3, "HOLD": 4}
    out.sort(key=lambda x: (order[x["action"]], x["symbol"] == LIQUID, x["symbol"]))

    # Override candidates: every stock that passes every filter and is not held anywhere
    held = set(kite) | set(positions)
    candidates = []
    for row in screen.get("rows", []):
        if not row.get("rank") or row["s"] in held or row["s"] in excluded:
            continue
        setup = row.get("setup") or ""
        candidates.append({"symbol": row["s"], "rank": row["rank"], "close": row.get("close"), "sector": row.get("sec"),
                           "rs55": (row.get("v") or {}).get("rs55"), "rs_rating": (row.get("v") or {}).get("rs_rating"),
                           "setup": setup.split(": ", 1)[-1] if setup else "not in the top ranks this week",
                           "proposed": setup.startswith("buy"),
                           "qty": size(row.get("close"), book_value, cash_available, cfg, regime),
                           "stop": tick(row["close"] * (1 - cfg["max_stop_pct"] / 100)) if row.get("close") else None})
    candidates.sort(key=lambda c: c["rank"])

    invested = sum(r["value"] for r in out if r["symbol"] != LIQUID)
    counts = {a: sum(1 for r in out if r["action"] == a) for a in order}
    return {"rows": out, "candidates": candidates, "regime": regime, "top_n": top_n, "counts": counts,
            "kite_value": sum(r["value"] for r in out), "invested": invested,
            "regime_cap": REGIME_EXPOSURE.get(regime, 1.0), "week": week.get("week"),
            "week_status": week.get("status"), "has_exit_data": any(r.get("x") for r in screen.get("rows", [])[:5]),
            "cash_available": cash_available, "book_value": book_value}
