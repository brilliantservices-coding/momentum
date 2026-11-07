"""Forensic agent: red-flag checklist over fundamentals and recent filings, results in `alerts/`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..rules import filing_flags, structured_flags
from ..store import now_iso

FILING_LOOKBACK_DAYS = 3


def run(store, log) -> dict:
    stamp = now_iso()
    new_alerts: dict[str, dict] = {}

    for f in store.list("fundamentals"):
        for fl in structured_flags(f):
            aid = f"{f['id']}-{fl['rule']}"
            if not store.get("alerts", aid):
                new_alerts[aid] = {"symbol": f["id"], "name": f.get("name"), **fl, "source": "fundamentals",
                                   "created": stamp, "status": "open"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=FILING_LOOKBACK_DAYS)).isoformat(timespec="seconds")
    filings = store.list("filings", where=[("ingested", ">=", cutoff)])
    for fil in filings:
        for fl in filing_flags(f"{fil.get('category', '')} {fil.get('subject', '')}"):
            aid = f"{fil['id']}-{fl['rule']}"
            if not store.get("alerts", aid):
                new_alerts[aid] = {"symbol": fil.get("symbol"), "name": fil.get("company"), **fl,
                                   "source": f"{fil.get('exchange')} filing {fil.get('date')}",
                                   "link": fil.get("attachment"), "created": stamp, "status": "open"}
    if new_alerts:
        store.set_many("alerts", new_alerts)
    hard = sum(a["severity"] == "hard" for a in new_alerts.values())
    log.info("forensic: %d new alerts (%d hard) from %d filings", len(new_alerts), hard, len(filings))
    return {"new_alerts": len(new_alerts), "hard": hard, "filings_checked": len(filings)}
