"""Entry point for every agent: `python -m mb.runner <agent_id> [--force]`.

Cloud Run Jobs call this. It honours the portal's controls: a paused agent or an active
budget kill switch makes the run exit immediately (unless --force from the portal's Run now).
Every run is recorded in `runs/` with its summary and a log tail the portal shows.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import time
import traceback
import uuid

from .config import agent_by_id
from .store import get_store, now_iso


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


def run(agent_id: str, force: bool = False) -> dict:
    agent = agent_by_id(agent_id)  # validates the id
    store = get_store()
    control = store.get("agents", agent_id) or {}
    enabled = control.get("enabled", agent.get("default_enabled", True))
    killed = (store.get("control", "global") or {}).get("killed", False)
    if not force and (killed or not enabled):
        reason = "budget kill switch active" if killed else "agent paused in portal"
        print(f"skip {agent_id}: {reason}")
        return {"status": "skipped", "reason": reason}

    run_id = os.getenv("CLOUD_RUN_EXECUTION") or f"{agent_id}-{uuid.uuid4().hex[:8]}"
    handler = _ListHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log = logging.getLogger(f"mb.{agent_id}")
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stdout))

    started, t0 = now_iso(), time.monotonic()
    store.set("runs", run_id, {"agent": agent_id, "status": "running", "started": started})
    store.set("agents", agent_id, {"last_run_id": run_id, "last_status": "running", "last_started": started}, merge=True)
    status, summary = "succeeded", {}
    try:
        module = importlib.import_module(f"mb.agents.{agent_id}")
        summary = module.run(store=store, log=log) or {}
    except Exception as exc:  # recorded for the portal, then re-raised so Cloud Run marks the task failed
        status = "failed"
        summary = {"error": f"{type(exc).__name__}: {exc}"}
        log.error(traceback.format_exc())
    finished = now_iso()
    duration = round(time.monotonic() - t0, 1)
    store.set("runs", run_id, {"agent": agent_id, "status": status, "started": started, "finished": finished,
                                "duration_s": duration, "summary": summary, "log": handler.lines[-200:]})
    store.set("agents", agent_id, {"last_status": status, "last_finished": finished, "last_summary": summary}, merge=True)
    if status == "failed":
        raise SystemExit(1)
    return {"status": status, "run_id": run_id, "summary": summary}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: python -m mb.runner <agent_id> [--force]")
    run(args[0], force="--force" in args or os.getenv("MB_FORCE") == "1")
