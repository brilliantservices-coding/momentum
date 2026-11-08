"""Management plane: run now, stop, pause/resume schedules, and the global budget kill switch.

GCP mode drives Cloud Run Jobs (one per agent, named <prefix><agent_id>) and their Cloud
Scheduler triggers. Local mode runs agents as subprocesses so the portal works on a laptop.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from .config import ROOT, agent_by_id, load_agents, settings
from .store import get_store, now_iso

_local_procs: dict[str, subprocess.Popen] = {}


def _job_name(agent_id: str) -> str:
    return f"projects/{settings.project}/locations/{settings.region}/jobs/{settings.job_prefix}{agent_id}"


def _sched_name(agent_id: str) -> str:
    return f"projects/{settings.project}/locations/{settings.region}/jobs/{settings.job_prefix}{agent_id}-schedule"


class AlreadyRunning(RuntimeError):
    """An earlier run of the agent hasn't finished; a second one would redo the same work."""


def is_stale(started: str | None, timeout_s) -> bool:
    """A run still marked running after its job timeout (plus slack) was cancelled or killed outside the runner."""
    try:
        t = datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - t > timedelta(seconds=int(timeout_s or 3600) + 600)


def active_runs(agent_id: str, exclude: str | None = None) -> list[str]:
    """Runs of the agent still in progress. Cloud Run's execution list is the truth in GCP (it also
    covers an execution that is starting up); the runs table is the fallback when it can't be read."""
    if settings.backend == "gcp":
        try:
            return [n for n in running_executions(agent_id) if not exclude or n.rsplit("/", 1)[-1] != exclude]
        except Exception:
            pass
    elif running_executions(agent_id):
        return running_executions(agent_id)
    timeout = agent_by_id(agent_id).get("timeout")
    return [r["id"] for r in get_store().list("runs", where=[("agent", "==", agent_id), ("status", "==", "running")])
            if r["id"] != exclude and not is_stale(r.get("started"), timeout)]


def run_now(agent_id: str, redo: bool = False) -> str:
    """Starts an execution that ignores the paused flag (an explicit human request).

    Refuses (AlreadyRunning) while an earlier run is in progress. redo=True makes the research agents
    ignore their saved results once and redo every stock (MB_REDO=1).
    """
    if active_runs(agent_id):
        raise AlreadyRunning(f"{agent_id} is already running")
    extra = {"MB_FORCE": "1", **({"MB_REDO": "1"} if redo else {})}
    if settings.backend != "gcp":
        proc = subprocess.Popen([sys.executable, "-m", "mb.runner", agent_id, "--force"], cwd=ROOT,
                                env={**os.environ, **extra}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _local_procs[agent_id] = proc
        return f"local pid {proc.pid}"
    from google.cloud import run_v2

    req = run_v2.RunJobRequest(
        name=_job_name(agent_id),
        overrides=run_v2.RunJobRequest.Overrides(container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                env=[run_v2.EnvVar(name=k, value=v) for k, v in extra.items()])]),
    )
    op = run_v2.JobsClient().run_job(request=req)
    return op.metadata.name if op.metadata else "started"


def running_executions(agent_id: str) -> list[str]:
    if settings.backend != "gcp":
        p = _local_procs.get(agent_id)
        return [str(p.pid)] if p and p.poll() is None else []
    from google.cloud import run_v2

    client = run_v2.ExecutionsClient()
    return [e.name for e in client.list_executions(parent=_job_name(agent_id))
            if not e.completion_time and not e.cancelled_count]


def stop(agent_id: str) -> int:
    """Cancels running executions of the agent. Returns how many were stopped."""
    names = running_executions(agent_id)
    if settings.backend != "gcp":
        p = _local_procs.get(agent_id)
        if p and p.poll() is None:
            p.terminate()
    else:
        from google.cloud import run_v2

        client = run_v2.ExecutionsClient()
        for n in names:
            client.cancel_execution(name=n)
            run_id = n.rsplit("/", 1)[-1]
            if get_store().get("runs", run_id):
                get_store().set("runs", run_id, {"status": "cancelled", "finished": now_iso()}, merge=True)
    if names:
        get_store().set("agents", agent_id, {"last_status": "cancelled", "last_finished": now_iso()}, merge=True)
    return len(names)


def set_enabled(agent_id: str, enabled: bool, by: str = "portal") -> None:
    get_store().set("agents", agent_id, {"enabled": enabled, "enabled_changed": now_iso(), "enabled_by": by}, merge=True)
    if settings.backend == "gcp":
        from google.cloud import scheduler_v1

        client = scheduler_v1.CloudSchedulerClient()
        try:
            (client.resume_job if enabled else client.pause_job)(name=_sched_name(agent_id))
        except Exception as exc:  # the Firestore flag still stops runs even if the scheduler call fails
            get_store().set("agents", agent_id, {"scheduler_error": str(exc)[:300]}, merge=True)


def kill_all(reason: str, by: str) -> None:
    """Pauses every schedule, cancels running executions and blocks scheduled runs."""
    store = get_store()
    already = (store.get("control", "global") or {}).get("killed")
    store.set("control", "global", {"killed": True, "reason": reason, "at": now_iso(), "by": by})
    for a in load_agents():
        if not already:  # remember what to resume when the switch is released
            enabled_before = (store.get("agents", a["id"]) or {}).get("enabled", a.get("default_enabled", True))
            store.set("agents", a["id"], {"enabled_before_kill": enabled_before}, merge=True)
        set_enabled(a["id"], False, by=by)
        try:
            stop(a["id"])
        except Exception:
            pass


def release_kill(by: str) -> None:
    store = get_store()
    store.set("control", "global", {"killed": False, "reason": None, "at": now_iso(), "by": by})
    for a in load_agents():
        if (store.get("agents", a["id"]) or {}).get("enabled_before_kill", a.get("default_enabled", True)):
            set_enabled(a["id"], True, by=by)


def record_budget_notification(msg: dict) -> dict:
    """Handles a Cloud Billing budget Pub/Sub message; trips the kill switch at 100% of budget."""
    cost, budget = float(msg.get("costAmount", 0)), float(msg.get("budgetAmount", 0) or 0)
    month = str(msg.get("costIntervalStart", now_iso()))[:7]
    store = get_store()
    store.set("costs", month, {"month": month, "cost": cost, "budget": budget,
                               "currency": msg.get("currencyCode"), "updated": now_iso(),
                               "threshold": msg.get("alertThresholdExceeded")}, merge=True)
    tripped = budget > 0 and cost >= budget
    if tripped and not (store.get("control", "global") or {}).get("killed"):
        kill_all(f"monthly budget reached: {cost:.2f} of {budget:.2f} {msg.get('currencyCode', '')}", by="budget")
    return {"month": month, "cost": cost, "budget": budget, "tripped": tripped}
