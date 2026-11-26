"""Firestore queries never combine an equality filter with a range/!= filter on another field
(that needs a composite index, which this project does not manage)."""
import sys
import types

from mb.store import FirestoreStore


class _Snap:
    def __init__(self, i, d):
        self.id, self._d = i, d

    def to_dict(self):
        return dict(self._d)


class _Query:
    def __init__(self, docs, sent):
        self.docs, self.sent = docs, sent

    def where(self, filter):
        self.sent.append((filter.field, filter.op, filter.value))
        f, op, v = self.sent[-1]
        assert op == "==", "only equality filters may be sent alongside other fields"
        return _Query({k: d for k, d in self.docs.items() if d.get(f) == v}, self.sent)

    def stream(self):
        return [_Snap(k, d) for k, d in self.docs.items()]


class _FF:
    def __init__(self, field, op, value):
        self.field, self.op, self.value = field, op, value


def test_mixed_filters_need_no_composite_index(monkeypatch):
    mod = types.ModuleType("google.cloud.firestore_v1.base_query")
    mod.FieldFilter = _FF
    monkeypatch.setitem(sys.modules, "google.cloud.firestore_v1.base_query", mod)
    docs = {"a": {"severity": "hard", "status": "open", "symbol": "A"},
            "b": {"severity": "hard", "status": "dismissed", "symbol": "B"},
            "c": {"severity": "soft", "status": "open", "symbol": "C"}}
    sent = []
    st = FirestoreStore.__new__(FirestoreStore)
    st.db = types.SimpleNamespace(collection=lambda c: _Query(docs, sent))
    got = st.list("alerts", where=[("severity", "==", "hard"), ("status", "!=", "dismissed")])
    assert [g["symbol"] for g in got] == ["A"]
    assert sent == [("severity", "==", "hard")]
