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
    import mb.control
    import mb.store
    monkeypatch.setattr(mb.control, "_local_procs", {})
    monkeypatch.setattr(mb.store, "_store", mb.store.LocalStore(tmp_path))
    monkeypatch.setattr(mb.blob, "_blob", mb.blob.LocalBlob(tmp_path))
    return mb.store._store, mb.blob._blob


import json  # noqa: E402
import subprocess  # noqa: E402


class FakeCLI:
    def __init__(self, replies, cost=0.05):
        self.replies, self.cost, self.calls = list(replies), cost, []

    def __call__(self, cmd, input, capture_output, text, timeout):
        self.calls.append((cmd, input))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        out = {"type": "result", "is_error": False, "result": reply if isinstance(reply, str) else
               "```json\n" + json.dumps(reply) + "\n```", "total_cost_usd": self.cost}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")


@pytest.fixture()
def cli(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "x" * 90)

    def install(replies, cost=0.05):
        fake = FakeCLI(replies, cost)
        monkeypatch.setattr(subprocess, "run", fake)
        return fake
    return install
