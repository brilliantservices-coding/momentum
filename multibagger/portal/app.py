"""Portal: agents, their outputs, the approval queue, costs, and the controls.

Runs on Cloud Run behind IAP (Google sign-in). MB_ROLE=guard serves only the budget webhook,
deployed as a separate private service that Pub/Sub calls.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mb import control
from mb.blob import get_blob
from mb.config import load_agents, settings
from mb.fundamentals import import_file
from mb.store import get_store, now_iso

HERE = Path(__file__).parent
ROLE = os.getenv("MB_ROLE", "portal")
app = FastAPI(title="Multibagger control plane", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
OPEN_PATHS = {"/healthz", "/hooks/budget"}


def current_user(request: Request) -> str:
    raw = request.headers.get("x-goog-authenticated-user-email", "")
    return raw.split(":", 1)[-1].lower() if raw else ("local" if settings.backend != "gcp" else "")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if ROLE == "guard" and path not in OPEN_PATHS:
        return JSONResponse({"error": "not found"}, status_code=404)
    if path not in OPEN_PATHS and not path.startswith("/static"):
        user = current_user(request)
        if settings.allowed_users and user not in settings.allowed_users:
            return HTMLResponse("Not authorised for this portal.", status_code=403)
        if request.method == "POST":  # same-origin check against cross-site form posts
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and urlparse(origin).netloc != request.url.netloc:
                return HTMLResponse("Cross-site request blocked.", status_code=403)
    return await call_next(request)


def render(request: Request, name: str, **ctx):
    store = get_store()
    ctx.update(request=request, user=current_user(request), kill=store.get("control", "global") or {},
               nav=request.url.path, settings=settings)
    return templates.TemplateResponse(request, name, ctx)


def back(path: str, msg: str = ""):
    return RedirectResponse(f"{path}{'?msg=' + msg if msg else ''}", status_code=303)


def _agents_with_state():
    store = get_store()
    out = []
    for a in load_agents():
        st = store.get("agents", a["id"]) or {}
        out.append({**a, **st, "enabled": st.get("enabled", True)})
    return out


def _month_cost():
    store = get_store()
    month = now_iso()[:7]
    c = store.get("costs", month) or {}
    budget = c.get("budget") or settings.monthly_budget_inr
    cost = c.get("cost")
    return {"month": month, "cost": cost, "budget": budget, "currency": c.get("currency") or "INR",
            "pct": (100 * cost / budget) if cost is not None and budget else None, "updated": c.get("updated")}


@app.get("/healthz")
def healthz():
    return {"ok": True, "role": ROLE}


@app.get("/", response_class=HTMLResponse)
def overview(request: Request, msg: str = ""):
    store = get_store()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    stats = {
        "candidates": len(store.list("candidates", where=[("passed", "==", True)])),
        "hard_alerts": len(store.list("alerts", where=[("severity", "==", "hard"), ("status", "==", "open")])),
        "soft_alerts": len(store.list("alerts", where=[("severity", "==", "soft"), ("status", "==", "open")])),
        "filings_7d": len(store.list("filings", where=[("ingested", ">=", week_ago)])),
        "pending": len(store.list("proposals", where=[("status", "==", "pending")])),
    }
    return render(request, "overview.html", agents=_agents_with_state(), stats=stats, cost=_month_cost(),
                  fundamentals=store.get("control", "fundamentals"), msg=msg)


@app.get("/agents/{agent_id}", response_class=HTMLResponse)
def agent_detail(request: Request, agent_id: str, msg: str = ""):
    agent = next((a for a in _agents_with_state() if a["id"] == agent_id), None)
    if not agent:
        raise HTTPException(404)
    runs = get_store().list("runs", where=[("agent", "==", agent_id)], order_by="started", limit=30)
    return render(request, "agent.html", agent=agent, runs=runs, msg=msg)


@app.post("/agents/{agent_id}/{action}")
def agent_action(request: Request, agent_id: str, action: str):
    if agent_id not in {a["id"] for a in load_agents()}:
        raise HTTPException(404)
    user = current_user(request)
    if action == "run":
        if (get_store().get("control", "global") or {}).get("killed"):
            return back(f"/agents/{agent_id}", "Kill switch is on: release it on the overview first")
        control.run_now(agent_id)
        msg = "Run started"
    elif action == "stop":
        msg = f"Stopped {control.stop(agent_id)} running execution(s)"
    elif action in ("pause", "resume"):
        control.set_enabled(agent_id, action == "resume", by=user)
        msg = "Schedule resumed" if action == "resume" else "Schedule paused"
    else:
        raise HTTPException(400)
    get_store().set("audit", f"{now_iso()}-{agent_id}-{action}",
                    {"at": now_iso(), "user": user, "agent": agent_id, "action": action})
    return back(f"/agents/{agent_id}", msg)


@app.post("/kill")
def kill(request: Request, action: str = Form(...)):
    user = current_user(request)
    if action == "kill":
        control.kill_all("stopped from portal", by=user)
        msg = "All agents stopped and schedules paused"
    else:
        control.release_kill(by=user)
        msg = "Kill switch released, schedules restored"
    get_store().set("audit", f"{now_iso()}-global-{action}", {"at": now_iso(), "user": user, "action": action})
    return back("/", msg)


@app.get("/candidates", response_class=HTMLResponse)
def candidates(request: Request, all: int = 0):
    store = get_store()
    where = [] if all else [("passed", "==", True)]
    rows = store.list("candidates", where=where, order_by="met", limit=500)
    alerts = {}
    for a in store.list("alerts", where=[("status", "==", "open")]):
        alerts.setdefault(a.get("symbol"), []).append(a)
    return render(request, "candidates.html", rows=rows, alerts=alerts, show_all=all,
                  screen=(store.list("screens", order_by="date", limit=1) or [None])[0])


@app.get("/alerts", response_class=HTMLResponse)
def alerts(request: Request, status: str = "open"):
    rows = get_store().list("alerts", where=[("status", "==", status)], order_by="created", limit=300)
    rows.sort(key=lambda a: a.get("severity") != "hard")
    return render(request, "alerts.html", rows=rows, status=status)


@app.post("/alerts/{alert_id}")
def alert_update(request: Request, alert_id: str, status: str = Form(...), note: str = Form("")):
    if status not in ("open", "dismissed", "confirmed"):
        raise HTTPException(400)
    get_store().set("alerts", alert_id, {"status": status, "note": note, "reviewed_by": current_user(request),
                                         "reviewed": now_iso()}, merge=True)
    return back("/alerts", "Alert updated")


@app.get("/filings", response_class=HTMLResponse)
def filings(request: Request, symbol: str = ""):
    store = get_store()
    where = [("symbol", "==", symbol.upper())] if symbol else []
    rows = store.list("filings", where=where, order_by="ingested", limit=200)
    return render(request, "filings.html", rows=rows, symbol=symbol)


@app.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, status: str = "pending"):
    rows = get_store().list("proposals", where=[("status", "==", status)], order_by="created", limit=200)
    return render(request, "approvals.html", rows=rows, status=status)


@app.post("/approvals/{pid}")
def approval_decide(request: Request, pid: str, decision: str = Form(...), note: str = Form("")):
    if decision not in ("approved", "rejected", "edited"):
        raise HTTPException(400)
    store = get_store()
    p = store.get("proposals", pid)
    if not p:
        raise HTTPException(404)
    store.set("proposals", pid, {"status": decision, "decision_note": note, "decided_by": current_user(request),
                                 "decided": now_iso()}, merge=True)
    store.set("journal", f"{now_iso()}-{pid}", {"at": now_iso(), "proposal": pid, "symbol": p.get("symbol"),
                                                 "action": p.get("action"), "decision": decision, "note": note})
    return back("/approvals", f"Proposal {decision}. Place any order yourself with your broker.")


@app.get("/data", response_class=HTMLResponse)
def data_page(request: Request, msg: str = ""):
    store = get_store()
    return render(request, "data.html", fundamentals=store.get("control", "fundamentals"),
                  universe=len(store.list("universe")), msg=msg)


@app.post("/data/fundamentals")
async def upload_fundamentals(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 20_000_000:
        return back("/data", "File too large (20 MB max)")
    try:
        report = import_file(get_store(), get_blob(), data, file.filename or "export.csv")
    except Exception as exc:
        return back("/data", f"Import failed: {exc}")
    return back("/data", f"Imported {report['rows']} companies")


@app.get("/costs", response_class=HTMLResponse)
def costs(request: Request):
    months = get_store().list("costs", order_by="month", limit=12)
    return render(request, "costs.html", cost=_month_cost(), months=months)


@app.post("/hooks/budget")
async def budget_hook(request: Request):
    """Pub/Sub push from the billing budget. Only the guard service exposes this to Pub/Sub."""
    if ROLE != "guard" and settings.backend == "gcp":
        raise HTTPException(404)
    body = await request.json()
    data = body.get("message", {}).get("data")
    if not data:
        return {"ignored": True}
    return control.record_budget_notification(json.loads(base64.b64decode(data)))
