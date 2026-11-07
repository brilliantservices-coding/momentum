"""Document store. Firestore in GCP, JSON files locally. Same tiny interface for both.

The (default) Firestore database is shared with the multibagger app, so every collection this app
uses starts with `mom_`:
  mom_weeks/{week}        one weekly run: regime, rankings, proposed changes, sign-off state
  mom_portfolio/{book}    the "Momentum" book: cash, positions with entry/stop, peak value
  mom_journal/{id}        every signed-off change (the trade log)
  mom_perf/{week}         weekly book value vs Nifty 500 and Nifty 50
  mom_runs/{id}           job executions with a log tail
  mom_settings/current    capital, risk and exclusion settings edited in the portal
  mom_universe/current    last good Nifty 500 constituent list (fallback when NSE is unreachable)
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LocalStore:
    _lock = threading.Lock()

    def __init__(self, root=None):
        self.root = (root or settings.data_dir) / "db"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, coll: str):
        return self.root / f"{coll}.json"

    def _load(self, coll: str) -> dict:
        p = self._path(coll)
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, coll: str, data: dict) -> None:
        tmp = self._path(coll).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
        tmp.replace(self._path(coll))

    def get(self, coll: str, doc_id: str) -> dict | None:
        with self._lock:
            return self._load(coll).get(doc_id)

    def set(self, coll: str, doc_id: str, doc: dict, merge: bool = False) -> None:
        with self._lock:
            data = self._load(coll)
            data[doc_id] = {**data.get(doc_id, {}), **doc} if merge else doc
            self._save(coll, data)

    def set_many(self, coll: str, docs: dict[str, dict], merge: bool = False) -> None:
        with self._lock:
            data = self._load(coll)
            for k, v in docs.items():
                data[k] = {**data.get(k, {}), **v} if merge else v
            self._save(coll, data)

    def delete(self, coll: str, doc_id: str) -> None:
        with self._lock:
            data = self._load(coll)
            data.pop(doc_id, None)
            self._save(coll, data)

    def list(self, coll: str, where: Iterable[tuple[str, str, Any]] = (), order_by: str | None = None,
             desc: bool = True, limit: int | None = None) -> list[dict]:
        with self._lock:
            items = [{"id": k, **v} for k, v in self._load(coll).items()]
        for field, op, value in where:
            items = [i for i in items if _match(i.get(field), op, value)]
        if order_by:
            present = [i for i in items if i.get(order_by) is not None]
            present.sort(key=lambda i: i[order_by], reverse=desc)
            items = present + [i for i in items if i.get(order_by) is None]
        return items[:limit] if limit else items


def _match(actual, op, value) -> bool:
    if op == "==":
        return actual == value
    if op == "!=":
        return actual != value
    if op == "in":
        return actual in value
    if actual is None:
        return False
    return {">": actual > value, ">=": actual >= value, "<": actual < value, "<=": actual <= value}[op]


class FirestoreStore:
    def __init__(self):
        from google.cloud import firestore  # imported lazily so local mode needs no GCP libs at runtime

        self._fs = firestore
        self.db = firestore.Client(project=settings.project or None)

    def get(self, coll, doc_id):
        snap = self.db.collection(coll).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def set(self, coll, doc_id, doc, merge=False):
        self.db.collection(coll).document(doc_id).set(doc, merge=merge)

    def set_many(self, coll, docs, merge=False):
        items = list(docs.items())
        for i in range(0, len(items), 400):  # Firestore batch limit is 500 writes
            batch = self.db.batch()
            for k, v in items[i:i + 400]:
                batch.set(self.db.collection(coll).document(k), v, merge=merge)
            batch.commit()

    def delete(self, coll, doc_id):
        self.db.collection(coll).document(doc_id).delete()

    def list(self, coll, where=(), order_by=None, desc=True, limit=None):
        """Filtered queries sort client-side so no composite indexes are needed (collections stay small)."""
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = self.db.collection(coll)
        where = list(where)
        for field, op, value in where:
            q = q.where(filter=FieldFilter(field, op, value))
        if order_by and not where:
            q = q.order_by(order_by, direction=self._fs.Query.DESCENDING if desc else self._fs.Query.ASCENDING)
            if limit:
                q = q.limit(limit)
        items = [{"id": s.id, **s.to_dict()} for s in q.stream()]
        if order_by and where:
            present = sorted((i for i in items if i.get(order_by) is not None), key=lambda i: i[order_by], reverse=desc)
            items = present + [i for i in items if i.get(order_by) is None]
        return items[:limit] if limit else items


_store = None


def get_store():
    global _store
    if _store is None:
        _store = FirestoreStore() if settings.backend == "gcp" else LocalStore()
    return _store
