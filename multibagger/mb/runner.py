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
    from .control import active_runs
    busy = active_runs(agent_id, exclude=run_id)
    if busy:  # e.g. the schedule fired while a long Run now was still going: let that one finish
        reason = f"an earlier run is still in progress ({busy[0].rsplit('/', 1)[-1]})"
        print(f"skip {agent_id}: {reason}")
        store.set("runs", run_id, {"agent": agent_id, "status": "skipped", "started": now_iso(),
                                   "finished": now_iso(), "summary": {"skipped": reason}})
        return {"status": "skipped", "reason": reason}
    handler = _ListHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log = logging.getLogger(f"mb.{agent_id}")
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stdout))

    started, t0 = now_iso(), time.monotonic()
    # Agents stop 10 minutes before the job timeout and leave the rest to a follow-up run.
    os.environ["MB_RUN_DEADLINE"] = str(time.time() + int(agent.get("timeout") or 3600) - 600)
    if os.getenv("MB_REDO") == "1" and not os.getenv("MB_REDO_SINCE"):
        os.environ["MB_REDO_SINCE"] = started  # follow-up runs redo only what this redo hasn't reached
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
    if status == "succeeded" and summary.get("more_to_do"):
        summary["next_run"] = _continue(store, agent_id, run_id)
        log.info("next run: %s", summary["next_run"])
    from . import inputs, requests
    only = inputs.only_symbols()
    if only:  # whatever happened, this run was the answer to these requests: don't retry them forever
        left = [x for x in requests.queued(store, agent_id) if x in only]
        requests.finish(store, agent_id, left, summary.get("error") or str(summary.get("errors") or "") or
                        f"run {status}{' (' + str(summary.get('stopped_by')) + ')' if summary.get('stopped_by') else ''}")
    if status == "succeeded" and not summary.get("more_to_do") and summary.get("stopped_by") != "daily cap":
        waiting = requests.queued(store, agent_id)
        if waiting:  # stock-page requests that arrived while this run was busy: a run for just those
            summary["next_run"] = _continue(store, agent_id, run_id, symbols=waiting)
            log.info("requested stocks waiting (%s): %s", ", ".join(waiting), summary["next_run"])
    finished = now_iso()
    duration = round(time.monotonic() - t0, 1)
    store.set("runs", run_id, {"agent": agent_id, "status": status, "started": started, "finished": finished,
                                "duration_s": duration, "summary": summary, "log": handler.lines[-200:]})
    store.set("agents", agent_id, {"last_status": status, "last_finished": finished, "last_summary": summary}, merge=True)
    if status == "failed":
        raise SystemExit(1)
    return {"status": status, "run_id": run_id, "summary": summary}


CHAIN_MAX = int(os.getenv("MB_CHAIN_MAX", "30"))


def _continue(store, agent_id: str, run_id: str, symbols=None) -> str:
    """Starts the next run straight away when eligible stocks are left (at most CHAIN_MAX in a row).
    The kill switch, a Stop or the daily cap ends the chain; scheduled runs pick up from there."""
    depth = int(os.getenv("MB_CHAIN") or 0) + 1
    if depth > CHAIN_MAX:
        return f"not started: {CHAIN_MAX} runs in a row (the next scheduled run continues)"
    if (store.get("control", "global") or {}).get("killed"):
        return "not started: kill switch is on"
    if ((store.get("runs", run_id) or {}).get("status")) == "cancelled":
        return "not started: this run was stopped"
    from . import control
    env = {"MB_CHAIN": str(depth)}
    if symbols:
        env["MB_SYMBOLS"] = ",".join(symbols)
    if os.getenv("MB_REDO_SINCE"):
        env["MB_REDO_SINCE"] = os.environ["MB_REDO_SINCE"]
    try:
        control.run_now(agent_id, exclude=run_id, env=env)
    except Exception as exc:
        return f"not started: {type(exc).__name__}: {str(exc)[:200]}"
    return f"started (run {depth} of the chain)"


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: python -m mb.runner <agent_id> [--force]")
    run(args[0], force="--force" in args or os.getenv("MB_FORCE") == "1")
