"""Override orders: buys and sells the owner picks on the Reconcile page, placed through the Kite
relay as CNC limit orders tagged "Momentum".

Every order is recorded in `mom_orders`. When Kite reports it filled, the fill is applied to the
Momentum book (if the owner chose that) and written to the trade journal as an override.
"""
from __future__ import annotations

import re
from datetime import date

from . import kite
from .book import get_book, get_cfg, save_book
from .config import BOOK, LIQUID
from .store import get_store, now_iso

SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")
FINAL = {"COMPLETE", "CANCELLED", "REJECTED"}


def parse(form, max_order_inr: float) -> tuple[list[dict], list[str]]:
    """Reads the selected rows of the Reconcile form: sel_<SYM>, side_<SYM>, qty_<SYM>, price_<SYM>, stop_<SYM>."""
    orders, errors = [], []
    for key in form.keys():
        if not key.startswith("sel_") or form.get(key) != "on":
            continue
        sym = key[4:].upper()
        side = str(form.get(f"side_{sym}", "")).upper()

        def num(name, cast):
            v = str(form.get(f"{name}_{sym}", "")).replace(",", "").strip()
            try:
                return cast(v) if v else None
            except ValueError:
                return None
        qty, price, stop = num("qty", int), num("price", float), num("stop", float)
        if not SYMBOL.match(sym) or side not in ("BUY", "SELL"):
            errors.append(f"{sym}: unknown stock or side")
        elif not qty or qty < 1:
            errors.append(f"{sym}: enter a quantity")
        elif not price or price <= 0:
            errors.append(f"{sym}: enter a limit price")
        elif qty * price > max_order_inr:
            errors.append(f"{sym}: ₹{qty * price:,.0f} is above the ₹{max_order_inr:,.0f} per-order limit in Settings")
        else:
            price = round(round(price * 20) / 20, 2)
            orders.append({"symbol": sym, "side": side, "qty": qty, "price": price,
                           "stop": round(stop, 2) if stop and side == "BUY" and stop < price else None,
                           "value": round(qty * price, 2)})
    orders.sort(key=lambda o: (o["side"] != "SELL", o["symbol"]))  # sells first, they free up cash
    return orders, errors


def filled(rec: dict) -> bool:
    """Final and at least partly filled (a cancelled order can still have a partial fill)."""
    return rec.get("status") == "COMPLETE" or (rec.get("status") in FINAL and int(rec.get("filled_qty") or 0) > 0)


def place(orders: list[dict], user: str, to_book: bool, week: str | None, store=None) -> list[dict]:
    store = store or get_store()
    results = []
    for o in orders:
        rec = {**o, "at": now_iso(), "by": user, "to_book": to_book, "week": week, "override": True, "applied": False}
        try:
            res = kite.call("/order", {k: o[k] for k in ("symbol", "side", "qty", "price")}, timeout=45)
            rec.update(order_id=res.get("order_id"), status=res.get("status"), message=res.get("message"),
                       filled_qty=res.get("filled_qty") or 0, avg_price=res.get("avg_price"), variety=res.get("variety"))
        except kite.RelayError as exc:
            rec.update(status="FAILED", message=str(exc))
        doc_id = rec.get("order_id") or f"failed-{rec['at']}-{o['symbol']}"
        store.set("mom_orders", doc_id, rec)
        store.set("mom_audit", f"{rec['at']}-order-{o['symbol']}", {"at": rec["at"], "user": user, "action": "override_order",
                                                                   "symbol": o["symbol"], "side": o["side"], "qty": o["qty"],
                                                                   "price": o["price"], "order_id": rec.get("order_id"),
                                                                   "status": rec["status"]})
        if filled(rec):
            apply_fill(store, {**rec, "id": doc_id})
        results.append({**rec, "id": doc_id})
    return results


def refresh(store=None) -> int:
    """Asks Kite for the latest state of every open override order; applies new fills to the book."""
    store = store or get_store()
    n = 0
    for rec in store.list("mom_orders", order_by="at", limit=100):
        if rec.get("status") in FINAL or rec.get("status") == "FAILED" or not rec.get("order_id"):
            continue
        res = kite.call("/order-status", {"order_id": rec["order_id"]})
        upd = {"status": res.get("status"), "message": res.get("message"), "filled_qty": res.get("filled_qty") or 0,
               "avg_price": res.get("avg_price"), "checked": now_iso()}
        store.set("mom_orders", rec["id"], upd, merge=True)
        n += 1
        if filled(upd):
            apply_fill(store, {**rec, **upd})
    return n


def apply_fill(store, rec: dict) -> None:
    """Writes a filled override order into the Momentum book and the trade journal (once)."""
    if not rec.get("to_book") or rec.get("applied"):
        return
    qty = int(rec.get("filled_qty") or (rec["qty"] if rec.get("status") == "COMPLETE" else 0))
    if qty < 1:
        return
    price = float(rec.get("avg_price") or rec["price"])
    sym, book, cfg = rec["symbol"], get_book(store), get_cfg(store)
    pos, cash = dict(book.get("positions", {})), float(book["cash"])
    entry = {"at": now_iso(), "week": rec.get("week"), "type": f"override_{rec['side'].lower()}", "symbol": sym,
             "qty": qty, "price": price, "by": rec.get("by"), "order_id": rec.get("order_id"), "book": BOOK,
             "reasons": ["override order placed from the Reconcile page"]}
    if rec["side"] == "BUY":
        stop = float(rec.get("stop") or round(price * (1 - cfg["max_stop_pct"] / 100), 2))
        old = pos.get(sym)
        if old:
            tot = old["qty"] + qty
            pos[sym] = {**old, "qty": tot, "entry": round((old["entry"] * old["qty"] + price * qty) / tot, 2),
                        "stop": max(old.get("stop", 0), stop) if sym != LIQUID else 0.0}
        else:
            pos[sym] = {"qty": qty, "entry": price, "stop": 0.0 if sym == LIQUID else stop,
                        "entry_date": date.today().isoformat(), "max_close": price, "setup": "override",
                        **({"liquid": True, "sector": "Liquid fund"} if sym == LIQUID else {})}
        cash -= qty * price
        entry["stop"] = pos[sym]["stop"]
    else:
        p = pos.get(sym)
        if not p:  # a sale of your own holding: journal only, the book does not hold it
            entry["reasons"].append("not in the Momentum book; book unchanged")
            store.set("mom_journal", f"override-{rec.get('order_id')}", entry)
            store.set("mom_orders", rec["id"], {"applied": True}, merge=True)
            return
        qty = min(qty, p["qty"])
        cash += qty * price
        entry.update(qty=qty, entry=p["entry"], pnl=round(qty * (price - p["entry"]), 2),
                     pnl_pct=round(100 * (price / p["entry"] - 1), 2))
        if qty >= p["qty"]:
            pos.pop(sym)
        else:
            pos[sym] = {**p, "qty": p["qty"] - qty}
    book.update(positions=pos, cash=round(cash, 2))
    save_book(store, book)
    store.set("mom_journal", f"override-{rec.get('order_id')}", entry)
    store.set("mom_orders", rec["id"], {"applied": True}, merge=True)
