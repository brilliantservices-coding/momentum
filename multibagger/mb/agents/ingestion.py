"""Ingestion agent: loads announcement CSVs dropped in the upload inbox into `filings/`.

The portal's Data page saves uploads to `filings/inbox/` and starts this agent. Processed files
move to `filings/processed/`. New filings are then checked by the forensic filing rules straight
away, so a hard red flag shows up minutes after the upload.
"""
from __future__ import annotations

from .. import announcements
from ..blob import get_blob
from ..rules import filing_flags
from ..store import now_iso

INBOX, DONE = "filings/inbox/", "filings/processed/"


def run(store, log) -> dict:
    blob = get_blob()
    files = blob.list(INBOX)
    stamp, total_new, flagged = now_iso(), 0, 0
    for path in files:
        name = path.rsplit("/", 1)[-1]
        try:
            items, report = announcements.parse(blob.get(path), name)
        except Exception as exc:
            log.error("%s: %s", name, exc)
            blob.move(path, DONE + "failed-" + name)
            continue
        new = {i["id"]: {**{k: v for k, v in i.items() if k != "id"}, "ingested": stamp, "source_file": name}
               for i in items if not store.get("filings", i["id"])}
        if new:
            store.set_many("filings", new)
        alerts = {}
        for fid, f in new.items():
            for fl in filing_flags(f"{f.get('category', '')} {f.get('subject', '')}"):
                alerts[f"{fid}-{fl['rule']}"] = {"symbol": f.get("symbol"), "name": f.get("company"), **fl,
                                                 "source": f"{f['exchange']} filing {f.get('date', '')}",
                                                 "link": f.get("attachment") or None, "created": stamp, "status": "open"}
        if alerts:
            store.set_many("alerts", alerts)
        blob.move(path, DONE + name)
        total_new += len(new)
        flagged += len(alerts)
        log.info("%s: %d rows (%s), %d new, %d flags", name, report["rows"], report["exchange"], len(new), len(alerts))
    if not files:
        log.info("inbox empty: upload an announcements CSV on the portal's Data page")
    return {"files": len(files), "new_filings": total_new, "new_alerts": flagged}
