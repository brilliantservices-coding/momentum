"""The "Momentum" book: this strategy's holdings, kept separate from anything else in the account.

Sign-off applies the approved changes of a week to the book at the prices the owner enters (the
actual fills), and writes each one to the trade journal. Orders are placed by the owner; tag them
"Momentum" in Kite so the broker side can be matched to this book.
"""
from __future__ import annotations

from datetime import date

from .config import BOOK, DEFAULTS
from .store import get_store, now_iso


def get_cfg(store=None) -> dict:
    store = store or get_store()
    saved = store.get("mom_settings", "current") or {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def get_book(store=None) -> dict:
    store = store or get_store()
    book = store.get("mom_portfolio", BOOK)
    if not book:
        cap = float(get_cfg(store)["capital"])
        book = {"name": BOOK, "cash": cap, "capital": cap, "positions": {}, "peak_value": cap, "created": now_iso()}
        store.set("mom_portfolio", BOOK, book)
    return book


def save_book(store, book: dict) -> None:
    store.set("mom_portfolio", BOOK, {**book, "updated": now_iso()})


def sign_off(week: dict, decisions: dict[str, dict], user: str, note: str = "", store=None) -> dict:
    """decisions: {change_id: {"approve": bool, "qty": int|None, "price": float|None, "stop": float|None}}"""
    store = store or get_store()
    if week.get("status") != "pending":
        raise ValueError(f"week {week.get('week')} is {week.get('status')}, not pending")
    book = get_book(store)
    pos, cash = dict(book.get("positions", {})), float(book["cash"])
    today = date.today().isoformat()
    applied, rejected = [], []
    for c in week.get("changes", []):
        d = decisions.get(c["id"], {})
        if not d.get("approve"):
            rejected.append(c["id"])
            continue
        sym = c["symbol"]
        qty = int(d.get("qty") or c.get("qty") or 0)
        price = float(d.get("price") or c.get("price") or 0)
        entry = {"at": now_iso(), "week": week["week"], "change": c["id"], "type": c["type"], "symbol": sym,
                 "qty": qty, "price": price, "by": user, "reasons": c.get("reasons", []), "book": BOOK}
        if c["type"] == "buy":
            stop = float(d.get("stop") or c["stop"])
            old = pos.get(sym)
            if old:  # averaging into an existing line
                tot = old["qty"] + qty
                old = {**old, "entry": round((old["entry"] * old["qty"] + price * qty) / tot, 2), "qty": tot,
                       "stop": max(old["stop"], stop)}
                pos[sym] = old
            else:
                pos[sym] = {"qty": qty, "entry": price, "stop": stop, "entry_date": today, "sector": c.get("sector"),
                            "max_close": price, "setup": c.get("setup")}
            cash -= qty * price
            entry["stop"] = stop
        elif c["type"] in ("sell", "trim"):
            p = pos.get(sym)
            if not p:
                continue
            qty = min(qty, p["qty"])
            cash += qty * price
            entry["pnl"] = round(qty * (price - p["entry"]), 2)
            entry["pnl_pct"] = round(100 * (price / p["entry"] - 1), 2)
            entry["entry"] = p["entry"]
            if qty >= p["qty"]:
                pos.pop(sym)
            else:
                pos[sym] = {**p, "qty": p["qty"] - qty, "trimmed": c["type"] == "trim" or p.get("trimmed", False)}
            entry["qty"] = qty
        elif c["type"] == "raise_stop":
            if sym in pos:
                new = float(d.get("stop") or c["new_stop"])
                pos[sym] = {**pos[sym], "stop": max(pos[sym]["stop"], new)}
                entry["stop"] = new
        store.set("mom_journal", f"{week['week']}-{c['id']}-{sym}", entry)
        applied.append(c["id"])
    value = cash + sum(p["qty"] * p["entry"] for p in pos.values())
    book.update(positions=pos, cash=round(cash, 2), peak_value=max(float(book.get("peak_value") or 0),
                                                                   float(week.get("book", {}).get("value") or 0)))
    save_book(store, book)
    store.set("mom_weeks", week["week"], {"status": "signed_off", "signed_off_by": user, "signed_off": now_iso(),
                                          "note": note, "applied": applied, "rejected": rejected,
                                          "decisions": {k: {kk: vv for kk, vv in v.items() if vv is not None}
                                                        for k, v in decisions.items()}}, merge=True)
    return {"applied": applied, "rejected": rejected, "cash": round(cash, 2), "positions": len(pos),
            "cost_basis_value": round(value, 2)}
