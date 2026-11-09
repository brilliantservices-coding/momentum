"""Forensic agent: red-flag checklist over fundamentals and recent filings, results in `alerts/`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..rules import filing_flags, positive_triggers, structured_flags
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
    backfill = not (store.get("control", "triggers") or {}).get("backfilled")
    triggers: dict[str, dict] = {}
    for fil in (store.list("filings") if backfill else filings):
        for t in positive_triggers(f"{fil.get('category', '')} {fil.get('subject', '')}"):
            triggers[f"{fil['id']}-{t['rule']}"] = {"symbol": fil.get("symbol"), **t, "date": fil.get("date"),
                                                    "filing_id": fil["id"], "subject": (fil.get("subject") or "")[:300],
                                                    "link": fil.get("attachment"), "created": stamp}
    if triggers:
        store.set_many("triggers", triggers)
    store.set("control", "triggers", {"backfilled": True, "updated": stamp})
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
    log.info("forensic: %d new alerts (%d hard) and %d positive triggers from %d filings", len(new_alerts), hard,
             len(triggers), len(filings))
    return {"new_alerts": len(new_alerts), "hard": hard, "filings_checked": len(filings),
            "positive_triggers": len(triggers)}
