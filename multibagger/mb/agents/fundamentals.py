"""Fundamentals agent: weekly refresh of `fundamentals/` from Yahoo Finance for the NSE universe.

To keep the weekly run short, each symbol gets the cheapest refresh that keeps it correct:
- skip: last seen far outside the market-cap range (above ₹40,000 cr / below ₹200 cr) and checked
  within the last 4 weeks;
- light (one request: price, market cap, P/E, volume): statements are under 4 weeks old and no
  results announcement has arrived since;
- full (summary + annual P&L, balance sheet, cash flow, quarterly P&L): everything else.

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
WORKERS = int(os.getenv("MB_YAHOO_WORKERS", "1"))
PAUSE_S = float(os.getenv("MB_YAHOO_PAUSE", "1.0"))  # between requests; Yahoo cut off ~10 req/s after 10 minutes
REFUSED_STOP = 15  # consecutive refusals (401 crumb / 429) before the run stops; the rest go first next run
REFUSED_WORDS = ("invalid crumb", "unauthorized", "too many requests", "429", "unable to access")
BUDGET_MIN = float(os.getenv("MB_YAHOO_MAX_MINUTES", "150"))
LIMIT = int(os.getenv("MB_YAHOO_LIMIT", "0"))  # 0 = whole universe; set for a quick test run
SOURCE = "Yahoo Finance (weekly agent)"
FAR_LOW, FAR_HIGH = 200.0, 40000.0
STATEMENT_MAX_AGE_DAYS = 28
RESULTS_WORDS = ("financial result", "quarterly result", "audited result", "unaudited result")


def _days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def plan(store, universe: list[str]) -> dict[str, str]:
    """symbol -> "skip" | "light" | "full"."""
    prev = {f["id"]: f for f in store.list("fundamentals")}
    cutoff = _days_ago(STATEMENT_MAX_AGE_DAYS)
    results = {}
    for f in store.list("filings", where=[("ingested", ">=", cutoff)]):
        text = f"{f.get('category', '')} {f.get('subject', '')}".lower()
        if any(w in text for w in RESULTS_WORDS):
            results[f.get("symbol")] = max(results.get(f.get("symbol"), ""), f.get("ingested") or "")
    out = {}
    for sym in universe:
        p = prev.get(sym) or {}
        mcap, checked = p.get("mcap"), p.get("as_of") or ""
        # Records from before stmt_as_of existed: a full fetch is the one that has statement fields.
        stmts = p.get("stmt_as_of") or (checked if "roce_3y_avg" in p and not p.get("out_of_range") else "")
        if mcap is not None and (mcap < FAR_LOW or mcap > FAR_HIGH) and checked >= cutoff:
            out[sym] = "skip"
        elif stmts >= cutoff and results.get(sym, "") <= stmts:
            out[sym] = "light"
        else:
            out[sym] = "full"
    return out


def run(store, log, fetch=yahoo.fetch) -> dict:
    universe = [u["id"] for u in store.list("universe")]
    if not universe:
        raise RuntimeError("universe is empty: run the Universe builder first")
    seen = {f["id"]: f.get("as_of", "") for f in store.list("fundamentals")}
    modes = plan(store, universe)
    symbols = sorted((s for s in universe if modes[s] != "skip"), key=lambda s: (modes[s] != "full", seen.get(s, "")))
    if LIMIT:
        symbols = symbols[:LIMIT]
    overlays = fresh_overlays(store)  # Trendlyne/Screener fields win over Yahoo's for the stocks they cover
    start, stamp = time.monotonic(), now_iso()
    done, in_range, missing, errors, buf = 0, 0, 0, {}, {}
    refused, cut_off = 0, False

    def flush():
        if buf:
            store.set_many("fundamentals", dict(buf), merge=True)  # light refreshes keep the statement fields
            buf.clear()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        it = iter(symbols)
        stopped = False

        def submit_next():
            sym = next(it, None)
            if sym is not None:
                futures[pool.submit(fetch, sym, MCAP_MIN, MCAP_MAX, light=modes[sym] == "light")] = sym

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
                    refused = refused + 1 if any(w in str(exc).lower() for w in REFUSED_WORDS) else 0
                else:
                    refused = 0
                if rec is None:
                    missing += 1
                else:
                    full = not rec.get("out_of_range") and not rec.pop("light", False)
                    in_range += full
                    extra = {"stmt_as_of": now_iso()} if full else {}
                    buf[sym] = {**rec, **overlays.get(sym, {}), **extra, "symbol": sym, "source": "yahoo",
                                "as_of": now_iso()}
                done += 1
                if len(buf) >= 100:
                    flush()
                if not stopped and (time.monotonic() - start) / 60 > BUDGET_MIN:
                    stopped = True
                    log.warning("time budget reached after %d symbols; the rest go first next run", done)
                if not stopped and refused >= REFUSED_STOP:
                    stopped = cut_off = True
                    log.warning("Yahoo is refusing requests after %d symbols; stopping, the rest go first next run", done)
                if not stopped:
                    if PAUSE_S:
                        time.sleep(PAUSE_S)
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
    counts = {m: sum(1 for v in modes.values() if v == m) for m in ("full", "light", "skip")}
    return {"symbols": done, "planned": counts, "minutes": round((time.monotonic() - start) / 60, 1),
            "with_statements": in_range, "not_found": missing, "errors": len(errors),
            "sample_errors": dict(list(errors.items())[:5]), "complete": done == len(symbols),
            "yahoo_cut_off": cut_off,
            "new_red_flags": flags.get("new_alerts")}
