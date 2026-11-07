"""Ingestion agent: new corporate announcements from NSE (BSE as fallback).

Stores every announcement's metadata in `filings/`. Attachments (PDFs) are downloaded to
Cloud Storage only for watchlist names (screener candidates plus MB_WATCHLIST), which keeps
storage and egress near zero.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from .. import sources
from ..blob import get_blob
from ..config import settings
from ..store import now_iso

LOOKBACK_DAYS = int(os.getenv("MB_INGEST_LOOKBACK_DAYS", "2"))
MAX_ATTACHMENTS = int(os.getenv("MB_MAX_ATTACHMENTS", "40"))


def watchlist(store) -> set[str]:
    names = {c["id"] for c in store.list("candidates", where=[("passed", "==", True)])}
    return names | set(settings.extra_watchlist)


def run(store, log, fetchers=None) -> dict:
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    fetchers = fetchers or [("NSE", sources.nse_announcements), ("BSE", sources.bse_announcements)]
    items, used, errors = [], None, {}
    for name, fn in fetchers:
        try:
            items = fn(start, end)
            used = name
            break
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("%s announcements failed: %s", name, errors[name])
    if used is None:
        raise RuntimeError(f"all announcement sources failed: {errors}")

    new = [i for i in items if not store.get("filings", i["id"])]
    wl = watchlist(store)
    blob, saved = get_blob(), 0
    for item in new:
        item["ingested"] = now_iso()
        item["stored"] = None
        if item["symbol"] in wl and item.get("attachment") and saved < MAX_ATTACHMENTS:
            try:
                data = sources.download(item["attachment"])
                ext = item["attachment"].rsplit(".", 1)[-1][:5].lower()
                item["stored"] = blob.put(f"filings/{item['symbol']}/{item['id']}.{ext}", data,
                                          "application/pdf" if ext == "pdf" else "application/octet-stream")
                saved += 1
            except Exception as exc:
                log.warning("attachment %s: %s", item["id"], exc)
    store.set_many("filings", {i["id"]: {k: v for k, v in i.items() if k != "id"} for i in new})
    log.info("ingestion via %s: %d fetched, %d new, %d attachments saved", used, len(items), len(new), saved)
    return {"source": used, "fetched": len(items), "new": len(new), "attachments_saved": saved,
            "source_errors": errors}
