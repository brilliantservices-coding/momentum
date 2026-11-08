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
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mb import control, llm
from mb.blob import get_blob
from mb.config import load_agents, settings
from mb.announcements import parse as parse_announcements
from mb.fundamentals import import_file
from mb.agents.ingestion import rebuild_coverage
from mb.rules import GATE_LABELS, RULE_LABELS, WEIGHTS, pass1_reasons
from mb.timefmt import age_days, ist
from mb.store import get_store, now_iso

HERE = Path(__file__).parent
ROLE = os.getenv("MB_ROLE", "portal")
app = FastAPI(title="Multibagger control plane", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["ist"] = ist


def stock_link(symbol, name=None):
    """A company's name (or symbol) linking to its Yahoo Finance page; BSE-only codes use .BO."""
    from markupsafe import Markup, escape
    sym = str(symbol or "").strip().upper()
    if not sym:
        return escape(name or "")
    suffix = "BO" if sym.isdigit() else "NS"
    url = f"https://finance.yahoo.com/quote/{quote(sym)}.{suffix}/"
    return Markup(f'<a class="ext" href="{url}" target="_blank" rel="noopener" title="Open on Yahoo Finance">'
                  f'{escape(name or sym)} ↗</a>')


templates.env.globals["stock_link"] = stock_link
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
    return RedirectResponse(f"{path}{'?msg=' + quote(msg) if msg else ''}", status_code=303)


def _stale(started: str | None, timeout_s) -> bool:
    """A run still marked running after its job timeout (plus slack) was cancelled or killed outside the runner."""
    try:
        t = datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - t > timedelta(seconds=int(timeout_s or 3600) + 600)


def _agents_with_state():
    store = get_store()
    out = []
    for a in load_agents():
        st = store.get("agents", a["id"]) or {}
        row = {**a, **st, "enabled": st.get("enabled", a.get("default_enabled", True))}
        if row.get("last_status") == "running" and _stale(row.get("last_started"), a.get("timeout")):
            row["last_status"] = "stopped"
        out.append(row)
    return out


def _freshness():
    """What data the agents have, and how current it is (overview and Data page)."""
    store = get_store()
    cov = store.get("control", "filings_coverage")
    if not cov and store.list("filings", limit=1):
        cov = rebuild_coverage(store)  # one-off for filings loaded before coverage was tracked
    fund = store.get("control", "fundamentals")
    upload = store.get("control", "fundamentals_upload")
    ann_age = age_days((cov or {}).get("last"))
    fund_age = age_days((fund or {}).get("imported"))
    return {"ann": cov, "ann_age": ann_age, "upload": upload, "ann_stale": ann_age is None or ann_age > 5,
            "fund": fund, "fund_age": fund_age,
            "fund_stale": fund_age is None or fund_age > (9 if (fund or {}).get("source") == "yahoo" else 30)}


def _funnel():
    """Every screened company, the furthest stage it reached and why it stopped there."""
    store = get_store()
    universe = len(store.list("universe"))
    cands = store.list("candidates")
    scores = {s["id"]: s for s in store.list("scores")}
    proposals = {}
    for p in store.list("proposals"):
        if p.get("action") == "buy starter":
            proposals.setdefault(p.get("symbol"), p)
    held = {h["id"] for h in store.list("holdings")}
    stage_names = ["Screened (fundamentals loaded)", "Market cap and liquidity gates", "Enough data (4+ rules known)",
                   "Pass 1 rules met (70%+ of known)", "No open hard red flag: watchlist", "Analysed by the AI analyst",
                   "Conviction 50+ (tier A, B or C)", "Cleared the red team", "Proposed for purchase",
                   "Approved by you / held"]
    rows = []
    for c in cands:
        sym, gates = c["id"], c.get("gates") or {}
        known, met = c.get("known") or 0, c.get("met") or 0
        checks = [True, all(v is not False for v in gates.values()), known >= 4,
                  known >= 4 and met >= max(4, -(-7 * known // 10)), c.get("passed") and not c.get("blocked_by")]
        s = scores.get(sym)
        checks += [bool(s), bool(s) and s.get("tier") in ("A", "B", "C"),
                   bool(s) and s.get("tier") in ("A", "B", "C") and not s.get("redteam_needed"),
                   sym in proposals or sym in held, sym in held]
        reached = 0
        for ok in checks:
            if not ok:
                break
            reached += 1
        if reached == len(checks):
            why = "held"
        elif reached < 5:
            why = "; ".join(pass1_reasons(c)) or "did not pass"
        else:
            why = ["not analysed yet (analyst runs 5 names per run)", f"score {s and s.get('score')} below 50",
                   "waiting for the red team", "no ticket yet (portfolio agent)", "ticket awaiting your decision"][reached - 5]
        rows.append({**c, "reached": reached, "stage": stage_names[reached - 1], "why": why,
                     "score": s and s.get("score"), "tier": s and s.get("tier")})
    counts = [sum(1 for r in rows if r["reached"] > i) for i in range(len(stage_names))]
    stages = [{"name": "NSE universe", "n": universe}] + [{"name": n, "n": counts[i], "idx": i + 1}
                                                          for i, n in enumerate(stage_names)]
    factors = []
    for k, label in {**GATE_LABELS, **RULE_LABELS}.items():
        vals = [(c.get("gates") or {}).get(k) if k in GATE_LABELS else (c.get("rules") or {}).get(k) for c in cands]
        factors.append({"key": k, "label": label, "pass": sum(v is True for v in vals),
                        "fail": sum(v is False for v in vals), "unknown": sum(v is None for v in vals)})
    rows.sort(key=lambda r: (-r["reached"], -(r.get("met") or 0), r["id"]))
    return stages, factors, rows


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
    stages, _, _ = _funnel()
    return render(request, "overview.html", agents=_agents_with_state(), stats=stats, cost=_month_cost(),
                  fresh=_freshness(), stages=stages, msg=msg)


@app.get("/agents/{agent_id}", response_class=HTMLResponse)
def agent_detail(request: Request, agent_id: str, msg: str = ""):
    agent = next((a for a in _agents_with_state() if a["id"] == agent_id), None)
    if not agent:
        raise HTTPException(404)
    runs = get_store().list("runs", where=[("agent", "==", agent_id)], order_by="started", limit=30)
    for r in runs:
        if r.get("status") == "running" and _stale(r.get("started"), agent.get("timeout")):
            r["status"] = "stopped"  # cancelled outside the portal; Cloud Run has the details
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


@app.get("/funnel", response_class=HTMLResponse)
def funnel(request: Request, stage: int = 0, q: str = ""):
    stages, factors, rows = _funnel()
    if stage:
        rows = [r for r in rows if r["reached"] == stage]
    if q:
        rows = [r for r in rows if q.upper() in r["id"] or q.lower() in (r.get("name") or "").lower()]
    store = get_store()
    return render(request, "funnel.html", stages=stages, factors=factors, rows=rows[:500], total=len(rows),
                  stage=stage, q=q, rule_labels=RULE_LABELS, gate_labels=GATE_LABELS, fresh=_freshness(),
                  screen=(store.list("screens", order_by="date", limit=1) or [None])[0])


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
    # Approval means "I am placing this order myself"; the holdings list drives exit checks and the 25-name cap.
    if decision in ("approved", "edited") and p.get("symbol"):
        if p.get("action") == "exit":
            store.delete("holdings", p["symbol"])
        else:
            store.set("holdings", p["symbol"], {"symbol": p["symbol"], "tier": p.get("tier"), "size_pct": p.get("size_pct"),
                                                "since": now_iso(), "proposal": pid}, merge=True)
    return back("/approvals", f"Proposal {decision}. Place any order yourself with your broker.")


@app.get("/data", response_class=HTMLResponse)
def data_page(request: Request, msg: str = ""):
    store = get_store()
    return render(request, "data.html", fundamentals=store.get("control", "fundamentals"), fresh=_freshness(),
                  universe=len(store.list("universe")), inbox=get_blob().list("filings/inbox/"),
                  processed=get_blob().list("filings/processed/")[-5:], msg=msg)


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


@app.post("/data/announcements")
async def upload_announcements(file: UploadFile = File(...)):
    data = await file.read()
    name = (file.filename or "announcements.csv").replace("/", "_")
    if len(data) > 20_000_000:
        return back("/data", "File too large (20 MB max)")
    try:
        _, report = parse_announcements(data, name)  # validate before queueing
    except Exception as exc:
        return back("/data", f"Import failed: {exc}")
    get_blob().put(f"filings/inbox/{now_iso().replace(':', '')}-{name}", data, "text/csv")
    control.run_now("ingestion")
    return back("/data", f"Queued {report['rows']} {report['exchange']} announcements; the ingestion agent is loading them now")


@app.get("/research", response_class=HTMLResponse)
def research(request: Request):
    store = get_store()
    scores = sorted(store.list("scores"), key=lambda s: -(s.get("score") or 0))
    themes = sorted(store.list("themes"), key=lambda t: (not t.get("active"), -(t.get("total") or 0)))
    held = {h["id"] for h in store.list("holdings")}
    return render(request, "research.html", scores=scores, themes=themes, held=held,
                  facts=len(store.list("facts")))


@app.get("/research/{symbol}", response_class=HTMLResponse)
def research_company(request: Request, symbol: str):
    store = get_store()
    sym = symbol.upper()
    memo, score = store.get("memos", sym), store.get("scores", sym)
    if not memo and not score:
        raise HTTPException(404, "No research on this company yet")
    facts = store.list("facts", where=[("symbol", "==", sym)], order_by="extracted", limit=100)
    history = store.list("memo_history", where=[("symbol", "==", sym)], order_by="written", limit=10)
    return render(request, "research_company.html", sym=sym, memo=memo or {}, score=score or {},
                  redteam=store.get("redteam", sym), facts=facts, history=history,
                  fundamentals=store.get("fundamentals", sym) or {}, weights=WEIGHTS)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = ""):
    store = get_store()
    cfg = llm.get_settings(store)
    calls = store.list("llm_calls", order_by="at", limit=20)
    spend = llm.spend_today(store)
    cap = float(cfg["daily_cap_usd"])
    ready = {"subscription": bool(os.getenv("MB_CLAUDE_TOKEN_SET") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN")),
             "vertex": True,
             "openai": bool(os.getenv("MB_OPENAI_KEY_SET") or os.getenv("OPENAI_API_KEY"))}
    return render(request, "settings.html", llm=cfg, providers=llm.PROVIDERS, models=llm.MODELS, prices=llm.PRICES,
                  openai_models=llm.openai_models(), efforts=llm.EFFORTS, spend=spend, cap=cap,
                  left=max(0.0, cap - float(spend.get("usd") or 0)), month=llm.month_usage(store),
                  limits=llm.limits(store), calls=calls, ready=ready, msg=msg)


@app.post("/settings")
def settings_save(request: Request, provider: str = Form(...), model: str = Form(""), openai_model: str = Form(""),
                  openai_model_custom: str = Form(""), effort: str = Form(...), daily_cap_usd: float = Form(...),
                  openai_price_in: float = Form(2.5), openai_price_out: float = Form(15.0)):
    if provider == "openai":
        model = (openai_model_custom or openai_model).strip()
    if provider not in llm.PROVIDERS or not llm.valid_model(provider, model) or effort not in llm.EFFORTS or \
            not 0 <= daily_cap_usd <= 50 or not (0 <= openai_price_in <= 500 and 0 <= openai_price_out <= 2000):
        raise HTTPException(400)
    user = current_user(request)
    get_store().set("control", "llm", {"provider": provider, "model": model, "effort": effort,
                                       "daily_cap_usd": daily_cap_usd, "openai_price_in": openai_price_in,
                                       "openai_price_out": openai_price_out, "updated": now_iso(), "by": user},
                    merge=True)
    get_store().set("audit", f"{now_iso()}-llm-settings", {"at": now_iso(), "user": user, "action": "llm settings",
                                                          "provider": provider, "model": model})
    return back("/settings", "Saved. Agents use the new settings from their next run.")


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
