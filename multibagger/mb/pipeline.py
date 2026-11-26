"""The stage pipeline: what each agent has waiting, and running the stages one after another.

`ready(store)` says, per stage, whether it has unprocessed input (new filings, documents, stocks without
an analysis...) using cheap reads only. A pipeline run starts the first stage with MB_PIPELINE set; the
runner calls `advance()` when a stage's run (and its own follow-up runs) end, which starts the next stage
that has work ready and skips the ones that don't. The state lives in control/pipeline.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from .store import now_iso

# Run order. The theme analyst is quarterly and sits outside the chain (run it from its card).
ORDER = ["universe", "fundamentals", "ingestion", "forensic", "screener", "extraction", "analyst", "redteam", "portfolio"]
SIDE = ["theme"]
UNIVERSE_DAYS, THEME_DAYS = 7, 90


def _age_days(ts) -> float | None:
    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def _since(store, agent_id: str) -> str:
    """When the stage last started a successful run ('' if never): input newer than this is unprocessed."""
    st = store.get("agents", agent_id) or {}
    return st.get("last_ok_started") or (st.get("last_started") if st.get("last_status") == "succeeded" else "") or ""


def _later(a, b) -> bool:
    return bool(a) and (not b or str(a) > str(b))


def ready(store) -> dict[str, dict]:
    """{agent_id: {"n": count or None, "due": bool, "why": text}} for every stage."""
    from .blob import get_blob
    out: dict[str, dict] = {}
    blob = get_blob()

    def put(agent, due, why, n=None):
        out[agent] = {"due": bool(due), "why": why, "n": n}

    age = _age_days(_since(store, "universe"))
    put("universe", age is None or age > UNIVERSE_DAYS,
        "never run" if age is None else f"NSE list refreshed {age:.0f} days ago" + (" (weekly refresh due)" if age > UNIVERSE_DAYS else ""))

    fund = store.get("control", "fundamentals") or {}
    fage = _age_days(fund.get("imported"))
    stale = fage is None or fage > 6
    put("fundamentals", stale, "no fundamentals yet" if fage is None else
        f"fundamentals are {fage:.0f} days old" + (" (refresh due)" if stale else ""))

    from .agents.ingestion import DESKTOP_INBOX, INBOX
    files = [p for p in blob.list(INBOX) + blob.list(DESKTOP_INBOX) if not p.endswith("/")]
    put("ingestion", files, f"{len(files)} announcement file(s) waiting" if files else "no new announcement files", len(files))

    since = _since(store, "forensic")
    new_filings = len(store.list("filings", where=[("ingested", ">", since)])) if since else None
    fund_new = _later(fund.get("imported"), since)
    n = new_filings or 0
    put("forensic", (not since) or n or fund_new,
        "never run" if not since else ", ".join(x for x in [f"{n} new filing(s)" if n else "",
                                                           "new fundamentals" if fund_new else ""] if x)
        or "nothing new since the last run", n if since else None)

    since = _since(store, "screener")
    newer = [x for x, ts in (("fundamentals", fund.get("imported")),
                             ("forensic flags", (store.get("agents", "forensic") or {}).get("last_finished")))
             if _later(ts, since)]
    put("screener", (not since) or newer, "never run" if not since else
        ("new " + " and ".join(newer)) if newer else "nothing new since the last screen")

    from .agents._context import analyst_bands
    scores = {s["id"]: s for s in store.list("scores")}
    bands = analyst_bands(store, scores=scores)
    deep = {s for s, b in bands.items() if b.get("band") == "deep"}

    from .agents.extraction import DOC_INBOX, _old, routine
    docs = [p for p in blob.list(DOC_INBOX) if p.count("/") >= 3]
    st = store.get("agents", "extraction") or {}
    since = _since(store, "extraction")
    fresh = [f for f in (store.list("filings", where=[("ingested", ">", since)]) if since else [])
             if f.get("symbol") in deep and not f.get("extracted") and not routine(f) and not _old(f)]
    left = int((st.get("last_summary") or {}).get("filings_still_to_do") or 0)
    n = len(docs) + len(fresh) + left
    parts = [f"{len(docs)} company document(s)" if docs else "", f"{len(fresh) + left} filing(s) of deep-band stocks"
             if fresh or left else ""]
    put("extraction", n or not since, ", ".join(p for p in parts if p) or ("never run" if not since else "nothing new to read"),
        n if since else None)

    since = _since(store, "analyst")
    new_facts = {f.get("symbol") for f in store.list("facts", where=[("extracted", ">", since)])} if since else set()
    no_memo = [s for s in deep if (scores.get(s) or {}).get("depth") == "triage" or s not in scores]
    changed = [s for s in deep & new_facts if s not in no_memo]
    light = [s for s, b in bands.items() if b.get("band") == "light" and
             (s not in scores or (scores[s].get("depth") == "triage" and (_age_days(scores[s].get("analysed")) or 99) >= 30))]
    n = len(no_memo) + len(changed) + len(light)
    put("analyst", n, ", ".join(p for p in [f"{len(no_memo)} deep-band stock(s) without a full memo" if no_memo else "",
                                            f"{len(changed)} with new facts" if changed else "",
                                            f"{len(light)} to triage" if light else ""] if p) or "every stock is up to date", n)

    rt = [s for s in scores.values() if s.get("redteam_needed")]
    put("redteam", rt, f"{len(rt)} tier A/B name(s) awaiting the bear case" if rt else "no names waiting", len(rt))

    from .rules import SIZING
    held = {h["id"] for h in store.list("holdings")}
    pending = {p.get("symbol") for p in store.list("proposals", where=[("status", "==", "pending")])}
    tickets = [s for s in scores.values() if s["id"] not in held and s["id"] not in pending
               and s.get("tier") in SIZING and not s.get("redteam_needed")]
    exits = [s for s in scores.values() if s["id"] in held and s["id"] not in pending and (s.get("score") or 0) < 50]
    n = len(tickets) + len(exits)
    put("portfolio", n, ", ".join(p for p in [f"{len(tickets)} name(s) could get a buy ticket" if tickets else "",
                                              f"{len(exits)} holding(s) below 50" if exits else ""] if p)
        or "no new tickets to write", n)

    tage = _age_days(_since(store, "theme"))
    put("theme", tage is None or tage > THEME_DAYS,
        "never run" if tage is None else f"themes scored {tage:.0f} days ago" + (" (quarterly refresh due)" if tage > THEME_DAYS else ""))
    return out


def state(store) -> dict:
    return store.get("control", "pipeline") or {}


def start(store, first: str | None = None, by: str = "portal", only_ready: bool = True, after: str | None = None) -> dict:
    """Starts a pipeline run at `first` (always run), or at the first stage with work ready from the top or
    from the stage after `after`. Returns the state."""
    from . import control
    begin = ORDER.index(first) if first in ORDER else (ORDER.index(after) + 1 if after in ORDER else 0)
    pid = f"{now_iso()[:16]}-{uuid.uuid4().hex[:4]}"
    doc = {"id": pid, "status": "running", "started": now_iso(), "by": by, "from": ORDER[begin],
           "only_ready": only_ready, "steps": [], "current": None}
    store.set("control", "pipeline", doc)
    nxt = _next(store, doc, begin, force_first=first in ORDER)
    if nxt:
        try:
            control.run_now(nxt, env={"MB_PIPELINE": pid})
        except control.AlreadyRunning:  # already going: the pipeline takes over when it ends
            pass
        doc.update(current=nxt)
    else:
        doc.update(status="finished", finished=now_iso(), current=None, note="no stage had work ready")
    store.set("control", "pipeline", doc)
    return doc


def _next(store, doc: dict, begin: int, force_first=False) -> str | None:
    """The first stage from `begin` with work ready; stages without are recorded as skipped."""
    info = ready(store) if doc.get("only_ready", True) else {}
    for i, agent in enumerate(ORDER[begin:]):
        r = info.get(agent) or {"due": True}
        if (force_first and i == 0) or r.get("due"):
            return agent
        doc["steps"].append({"agent": agent, "status": "skipped", "why": r.get("why"), "at": now_iso()})
    return None


def advance(store, agent_id: str, status: str, summary: dict | None = None, pid: str | None = None) -> str:
    """Called by the runner when a stage's run ends with no follow-up run of its own."""
    from . import control
    pid = pid or os.getenv("MB_PIPELINE")
    doc = state(store)
    if doc.get("status") != "running" or not (pid == doc.get("id") or (not pid and doc.get("current") == agent_id)):
        return "pipeline not active"  # (no pid: a run that was already going when the pipeline reached its stage)
    doc["steps"].append({"agent": agent_id, "status": status, "at": now_iso(),
                         "why": ", ".join(f"{k} {v}" for k, v in (summary or {}).items()
                                          if isinstance(v, (int, float, str)) and k != "error")[:300]})
    stop = None
    if (store.get("control", "global") or {}).get("killed"):
        stop = "kill switch is on"
    elif status != "succeeded":
        stop = f"{agent_id} {status}" + (f": {(summary or {}).get('error')}" if (summary or {}).get("error") else "")
    elif (summary or {}).get("stopped_by") == "daily cap":
        stop = "the daily LLM cap was reached"
    if stop:
        doc.update(status="stopped", finished=now_iso(), current=None, note=stop[:300])
        store.set("control", "pipeline", doc)
        return f"pipeline stopped: {stop}"
    nxt = _next(store, doc, ORDER.index(agent_id) + 1) if agent_id in ORDER and agent_id != ORDER[-1] else None
    if not nxt:
        doc.update(status="finished", finished=now_iso(), current=None)
        store.set("control", "pipeline", doc)
        return "pipeline finished"
    try:
        control.run_now(nxt, env={"MB_PIPELINE": pid})
    except Exception as exc:
        doc.update(status="stopped", finished=now_iso(), current=None, note=f"could not start {nxt}: {exc}"[:300])
        store.set("control", "pipeline", doc)
        return f"pipeline stopped: could not start {nxt}"
    doc.update(current=nxt)
    store.set("control", "pipeline", doc)
    return f"pipeline: started {nxt}"


def cancel(store, by: str = "portal") -> dict:
    doc = state(store)
    if doc.get("status") == "running":
        doc.update(status="stopped", finished=now_iso(), current=None, note=f"stopped by {by}")
        store.set("control", "pipeline", doc)
    return doc


def auto_after_schedule(store) -> bool:
    """Settings: after a scheduled stage run, carry on with the later stages that have work ready."""
    from . import llm
    return bool(llm.get_settings(store).get("pipeline_auto"))
