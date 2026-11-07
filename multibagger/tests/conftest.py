import os
import sys
from pathlib import Path

os.environ.setdefault("MB_BACKEND", "local")
os.environ.setdefault("MB_YAHOO_PAUSE", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import mb.blob
    import mb.store
    monkeypatch.setattr(mb.store, "_store", mb.store.LocalStore(tmp_path))
    monkeypatch.setattr(mb.blob, "_blob", mb.blob.LocalBlob(tmp_path))
    return mb.store._store, mb.blob._blob
