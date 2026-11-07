"""The weekly job: `python -m mom.weekly [--force]`. Cloud Scheduler runs it on Saturday morning.

It downloads prices, computes the signals, and writes this week's proposal to `mom_weeks/{week}`
with status "pending". The Momentum book only changes when the owner signs off in the portal.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone

from . import data as mdata
from . import signals, strategy
from .book import get_book, get_cfg, save_book
from .config import LIQUID
from .store import get_store, now_iso

log = logging.getLogger("mom.weekly")


def week_id(d: date) -> str:
    """Week ending Friday, e.g. 2026-11-06 (a Saturday run reviews the week that just closed)."""
    return (d - timedelta(days=(d.weekday() - 4) % 7)).isoformat()


def quality_from_fundamentals(store, sectors: dict) -> dict:
    """Optional Screener.in quality filter, using the fundamentals the multibagger portal already
    imported (shared Firestore). Stocks without a record are 'unknown' and not blocked."""
    out = {}
    for f in store.list("fundamentals"):
        sym = str(f.get("symbol") or f.get("id") or "").upper()
        fin = "financ" in sectors.get(sym, {}).get("industry", "").lower()
        rules = [("mcap", lambda v: v > 1000), ("sales_cagr_3y", lambda v: v > 12),
                 ("profit_cagr_3y", lambda v: v > 15), ("pledge", lambda v: v < 5), ("cfo_3y", lambda v: v > 0)]
        if not fin:
            rules += [("roce", lambda v: v > 15), ("debt_to_equity", lambda v: v < 1)]
        failed = [k for k, ok in rules if f.get(k) is not None and not ok(f[k])]
        out[sym] = {"passed": not failed, "failed": failed}
    return out


def build(px: dict, sectors: dict, store, today: date) -> dict:
    cfg = get_cfg(store)
    book = get_book(store)
    prev = (store.list("mom_weeks", order_by="week", limit=1) or [None])[0]
    reg = signals.regime(px, prev.get("regime", {}).get("regime") if prev else None)
    m = signals.metrics(px)
    quality = quality_from_fundamentals(store, sectors)
    m = strategy.rank_universe(m, cfg, quality or None, sectors)
    proposal = strategy.propose(m, book, reg, cfg, today=today, liquid_price=px.get("liquid_price"))
    proposal["regime"] = reg
    proposal["funnel"] = strategy.funnel(m) + proposal.pop("funnel_tail")
    proposal["screen"] = strategy.screen_rows(m, proposal.pop("setups"))
    proposal["factors"] = [{"key": k, "label": l} for k, l in m.attrs["factors"]]
    proposal["summary"] = strategy.summarize(proposal, cfg)
    fmeta = store.get("control", "fundamentals") or {}
    proposal["quality_source"] = (f"{len(quality)} stocks from the Screener export uploaded {fmeta.get('imported', '')}"
                                  if quality else "none uploaded")
    proposal["fundamentals_imported"] = fmeta.get("imported") if quality else None
    return proposal, m, book


def run(force: bool = False, px: dict | None = None, sectors: dict | None = None, today: date | None = None) -> dict:
    store = get_store()
    today = today or datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    wid = week_id(today)
    run_id = os.getenv("CLOUD_RUN_EXECUTION") or f"weekly-{uuid.uuid4().hex[:8]}"
    lines: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda rec: lines.append(f"{rec.levelname} {rec.getMessage()}")
    for name in ("mom.weekly", "mom.data"):
        logging.getLogger(name).addHandler(handler)
        logging.getLogger(name).setLevel(logging.INFO)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    existing = store.get("mom_weeks", wid)
    if existing and existing.get("status") == "signed_off" and not force:
        log.info("week %s already signed off; nothing to do", wid)
        return {"status": "skipped", "week": wid}

    t0 = time.monotonic()
    store.set("mom_runs", run_id, {"status": "running", "started": now_iso(), "week": wid})
    universe_updated = now_iso()
    try:
        if sectors is None:
            try:
                sectors = mdata.nifty500_constituents()
                universe_updated = now_iso()
                store.set("mom_universe", "current", {"symbols": sectors, "updated": universe_updated})
            except Exception as exc:
                cached = store.get("mom_universe", "current")
                if not cached:
                    raise
                log.warning("using cached Nifty 500 list from %s: %s", cached.get("updated"), exc)
                sectors = cached["symbols"]
                universe_updated = cached.get("updated")
        log.info("universe: %d symbols", len(sectors))
        if px is None:
            px = mdata.prices(sorted(sectors) + [LIQUID])
        if LIQUID in px["close"].columns:  # the parking ETF is priced but never screened
            last = px["close"][LIQUID].dropna()
            px["liquid_price"] = float(last.iloc[-1]) if len(last) else None
            for k in ("close", "high", "low", "volume"):
                px[k] = px[k].drop(columns=[LIQUID])
        log.info("prices: %d symbols with data, last bar %s", px["close"].shape[1], px["close"].index[-1].date())
        proposal, m, book = build(px, sectors, store, today)

        # Bookkeeping that is not a trade: highest close since entry, used by the +10% trailing rule
        if book.get("positions"):
            for sym, p in book["positions"].items():
                if sym in m.index and m.at[sym, "close"] == m.at[sym, "close"]:
                    p["max_close"] = max(float(p.get("max_close", p["entry"])), float(m.at[sym, "close"]))
            save_book(store, book)

        for w in store.list("mom_weeks", where=[("status", "==", "pending")]):
            if w["id"] != wid:
                store.set("mom_weeks", w["id"], {"status": "superseded", "superseded_by": wid}, merge=True)
        screen = {"week": wid, "factors": proposal.pop("factors"), "rows": proposal.pop("screen"), "created": now_iso()}
        doc = {"week": wid, "status": "pending", "created": now_iso(), "run_id": run_id,
               "data_as_of": str(px["close"].index[-1].date()), "universe_updated": universe_updated,
               "prices_count": int(px["close"].shape[1]), "universe_count": len(sectors), **proposal}
        store.set("mom_weeks", wid, doc)
        store.set("mom_screens", wid, screen)
        store.set("mom_perf", wid, {"week": wid, "value": proposal["book"]["value"],
                                    "nifty500": float(px["nifty500"].iloc[-1]), "nifty50": float(px["nifty50"].iloc[-1])})
        summary = {"week": wid, "regime": proposal["regime"]["regime"], "changes": len(proposal["changes"]),
                   "passing": proposal["counts"]["passing"]}
        status = "succeeded"
        log.info("week %s: %s", wid, summary)
    except Exception as exc:
        status, summary = "failed", {"error": f"{type(exc).__name__}: {exc}"}
        log.error(traceback.format_exc())
    store.set("mom_runs", run_id, {"status": status, "started": store.get("mom_runs", run_id)["started"],
                                    "finished": now_iso(), "duration_s": round(time.monotonic() - t0, 1),
                                    "week": wid, "summary": summary, "log": lines[-200:]})
    if status == "failed" and __name__ == "__main__":
        raise SystemExit(1)
    return {"status": status, "week": wid, "summary": summary}


if __name__ == "__main__":
    out = run(force="--force" in sys.argv or os.getenv("MOM_FORCE") == "1")
    print(out)
    if out["status"] == "failed":
        sys.exit(1)
