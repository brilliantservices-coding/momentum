"""Fundamentals agent: weekly refresh of `fundamentals/` from Yahoo Finance for the NSE universe.

Replaces the manual Screener export (which needs a paid plan to download). A portal upload still
works and overwrites the same records. Runs with a few threads and a time budget; symbols are
taken oldest-refreshed first, so a run cut short continues where it stopped next time.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import yahoo
from ..fundamentals import fresh_overlays
from ..store import now_iso

MCAP_MIN, MCAP_MAX = 400.0, 22000.0  # fetch statements a little either side of the ₹500-20,000 cr gate
WORKERS = int(os.getenv("MB_YAHOO_WORKERS", "3"))
BUDGET_MIN = float(os.getenv("MB_YAHOO_MAX_MINUTES", "150"))
LIMIT = int(os.getenv("MB_YAHOO_LIMIT", "0"))  # 0 = whole universe; set for a quick test run
SOURCE = "Yahoo Finance (weekly agent)"


def run(store, log, fetch=yahoo.fetch) -> dict:
    universe = [u["id"] for u in store.list("universe")]
    if not universe:
        raise RuntimeError("universe is empty: run the Universe builder first")
    seen = {f["id"]: f.get("as_of", "") for f in store.list("fundamentals")}
    symbols = sorted(universe, key=lambda s: seen.get(s, ""))
    if LIMIT:
        symbols = symbols[:LIMIT]
    overlays = fresh_overlays(store)  # Trendlyne/Screener fields win over Yahoo's for the stocks they cover
    start, stamp = time.monotonic(), now_iso()
    done, in_range, missing, errors, buf = 0, 0, 0, {}, {}

    def flush():
        if buf:
            store.set_many("fundamentals", dict(buf))
            buf.clear()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        it = iter(symbols)
        stopped = False

        def submit_next():
            sym = next(it, None)
            if sym is not None:
                futures[pool.submit(fetch, sym, MCAP_MIN, MCAP_MAX)] = sym

        for _ in range(WORKERS * 2):
            submit_next()
        while futures:
            for fut in as_completed(list(futures)):
                sym = futures.pop(fut)
                try:
                    rec = fut.result()
                except Exception as exc:
                    errors[sym] = f"{type(exc).__name__}: {exc}"[:160]
                    rec = None
                if rec is None:
                    missing += 1
                else:
                    in_range += not rec.get("out_of_range")
                    buf[sym] = {**rec, **overlays.get(sym, {}), "symbol": sym, "source": "yahoo", "as_of": now_iso()}
                done += 1
                if len(buf) >= 100:
                    flush()
                if not stopped and (time.monotonic() - start) / 60 > BUDGET_MIN:
                    stopped = True
                    log.warning("time budget reached after %d symbols; the rest go first next run", done)
                if not stopped:
                    submit_next()
                break
    flush()
    store.set("control", "fundamentals", {
        "file": SOURCE, "source": "yahoo", "imported": stamp, "rows": done - missing,
        "recognised": sorted(k for k in yahoo.ROWS) + ["info"], "with_statements": in_range,
        "missing": ["pledge", "promoter_change (Yahoo has no pledge or promoter-change data)"],
        "complete": done == len(symbols), "overlaid": sum(1 for s in overlays if s in seen or s in symbols)})
    # Structured red flags (cash-less profits, dilution, receivables) straight away, before Saturday's screen.
    from . import forensic
    flags = forensic.run(store=store, log=log)
    log.info("yahoo: %d symbols, %d with full statements, %d not on Yahoo, %d errors",
             done, in_range, missing, len(errors))
    return {"symbols": done, "with_statements": in_range, "not_found": missing, "errors": len(errors),
            "sample_errors": dict(list(errors.items())[:5]), "complete": done == len(symbols),
            "new_red_flags": flags.get("new_alerts")}
