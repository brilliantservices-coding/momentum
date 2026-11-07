"""Ingestion agent: loads announcement CSVs into `filings/`.

Two drop points: the Google Drive folder MB_DRIVE_INBOX_ID (filled by a scheduled Cowork task on
the user's desktop) is copied into the bucket inbox first, then everything in `filings/inbox/`
(including portal uploads) is loaded. Processed files
move to `filings/processed/`. New filings are then checked by the forensic filing rules straight
away, so a hard red flag shows up minutes after the upload.
"""
from __future__ import annotations

import os

from .. import announcements, drive
from ..blob import get_blob
from ..rules import filing_flags
from ..store import now_iso

INBOX, DONE = "filings/inbox/", "filings/processed/"


def pull_drive(store, blob, log, folder_id: str, session=None) -> int:
    pulled = 0
    for f in drive.list_files(folder_id, session):
        if store.get("drive_seen", f["id"]):
            continue
        data, name = drive.download(f, session)
        blob.put(f"{INBOX}drive-{f['id'][:8]}-{name.replace('/', '_')}", data, "text/csv")
        store.set("drive_seen", f["id"], {"name": f["name"], "copied": now_iso()})
        pulled += 1
    log.info("drive: %d new file(s) copied from the inbox folder", pulled)
    return pulled


def run(store, log, drive_session=None) -> dict:
    blob = get_blob()
    folder = os.getenv("MB_DRIVE_INBOX_ID")
    drive_files, drive_error = 0, None
    if folder:
        try:
            drive_files = pull_drive(store, blob, log, folder, drive_session)
        except Exception as exc:  # keep loading portal uploads even if Drive is unreachable
            drive_error = f"{type(exc).__name__}: {exc}"[:300]
            log.error("drive: %s", drive_error)
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
    out = {"files": len(files), "from_drive": drive_files, "new_filings": total_new, "new_alerts": flagged}
    if drive_error:
        out["drive_error"] = drive_error
    return out
