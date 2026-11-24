"""Momentum portal: review each week's proposed changes and sign off on them.

Runs on Cloud Run behind IAP (Google sign-in); the app also checks the signed-in email against
MOM_ALLOWED_USERS. Signing off updates the "Momentum" book; it never places orders.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mom import control, kite, orders as morders, reconcile as mrecon, stockview
from mom.book import get_book, get_cfg, sign_off
from mom.config import BOOK, DEFAULTS, REGIME_EXPOSURE, settings
from mom.store import get_store, now_iso

HERE = Path(__file__).parent
app = FastAPI(title="Momentum portal", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
IST = timezone(timedelta(hours=5, minutes=30))
SCHEDULE = os.getenv("MOM_SCHEDULE", "0 8 * * 6")  # cron, Asia/Kolkata


def ist(value, fmt: str = "%d %b %Y, %H:%M IST") -> str:
    """ISO timestamp (UTC or with offset) -> India time for display. Dates pass through as dates."""
    if not value:
        return "–"
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if len(str(value)) <= 10:
        return dt.strftime("%a %d %b %Y")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime(fmt)


def next_run(now: datetime | None = None) -> str:
    """Next firing of a simple 'M H * * D' schedule in IST."""
    try:
        minute, hour, _, _, dow = SCHEDULE.split()
        minute, hour, dow = int(minute), int(hour), int(dow) % 7  # cron: 0 = Sunday
    except ValueError:
        return SCHEDULE
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    target_wd = (dow - 1) % 7  # Python: Monday = 0
    for add in range(0, 8):
        d = now + timedelta(days=add)
        cand = d.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if d.weekday() == target_wd and cand > now:
            return cand.strftime("%a %d %b %Y, %H:%M IST")
    return SCHEDULE


templates.env.filters["ist"] = ist


def current_user(request: Request) -> str:
    raw = request.headers.get("x-goog-authenticated-user-email", "")
    return raw.split(":", 1)[-1].lower() if raw else ("local" if settings.backend != "gcp" else "")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path != "/healthz" and not path.startswith("/static"):
        if settings.backend == "gcp" or settings.allowed_users:
            if current_user(request) not in settings.allowed_users:
                return HTMLResponse("Not authorised for this portal.", status_code=403)
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and urlparse(origin).netloc != request.url.netloc:
                return HTMLResponse("Cross-site request blocked.", status_code=403)
    return await call_next(request)


def render(request: Request, name: str, **ctx):
    ctx.update(request=request, user=current_user(request), nav=request.url.path, book_name=BOOK,
               msg=request.query_params.get("msg", ""), next_run=next_run())
    return templates.TemplateResponse(request, name, ctx)


def back(path: str, msg: str = ""):
    return RedirectResponse(f"{path}{'?msg=' + quote(msg) if msg else ''}", status_code=303)


def _latest_week(store):
    return (store.list("mom_weeks", order_by="week", limit=1) or [None])[0]


def _book_view(store):
    book = get_book(store)
    week = _latest_week(store)
    prices = {}
    if week:
        prices.update({h["symbol"]: h.get("close") for h in week.get("holds", []) if h.get("close")})
        prices.update({c["symbol"]: c.get("price") for c in week.get("changes", []) if c.get("price")})
    rows, invested, parked = [], 0.0, 0.0
    for sym, p in sorted(book.get("positions", {}).items()):
        last = prices.get(sym) or p["entry"]
        val = p["qty"] * last
        if p.get("liquid"):
            parked += val
        else:
            invested += val
        rows.append({"symbol": sym, **p, "last": last, "value": val, "pnl_pct": 100 * (last / p["entry"] - 1),
                     "risk": 0.0 if p.get("liquid") else max(0.0, p["qty"] * (min(last, p["entry"]) - p["stop"]))})
    total = book["cash"] + invested + parked
    return book, rows, {"cash": book["cash"], "invested": invested, "parked": parked, "total": total,
                        "exposure_pct": 100 * invested / total if total else 0,
                        "heat_pct": 100 * sum(r["risk"] for r in rows) / total if total else 0,
                        "return_pct": 100 * (total / book["capital"] - 1) if book.get("capital") else 0}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    store = get_store()
    book, rows, totals = _book_view(store)
    runs = store.list("mom_runs", order_by="started", limit=1)
    return render(request, "overview.html", week=_latest_week(store), book=book, rows=rows, totals=totals,
                  last_run=runs[0] if runs else None, exposure=REGIME_EXPOSURE,
                  fundamentals=store.get("control", "fundamentals"),
                  pending=store.list("mom_weeks", where=[("status", "==", "pending")]))


@app.get("/weeks", response_class=HTMLResponse)
def weeks(request: Request):
    store = get_store()
    return render(request, "weeks.html", weeks=store.list("mom_weeks", order_by="week", limit=60),
                  perf=store.list("mom_perf", order_by="week", desc=False, limit=520))


@app.get("/weeks/{wid}", response_class=HTMLResponse)
def week_detail(request: Request, wid: str):
    w = get_store().get("mom_weeks", wid)
    if not w:
        raise HTTPException(404)
    return render(request, "week.html", w={**w, "id": wid}, exposure=REGIME_EXPOSURE)


@app.get("/weeks/{wid}/screen", response_class=HTMLResponse)
def week_screen(request: Request, wid: str):
    store = get_store()
    w, sc = store.get("mom_weeks", wid), store.get("mom_screens", wid)
    if not w:
        raise HTTPException(404)
    return render(request, "screen.html", w={**w, "id": wid}, screen=sc)


@app.post("/weeks/{wid}/signoff")
async def week_signoff(request: Request, wid: str):
    store = get_store()
    w = store.get("mom_weeks", wid)
    if not w:
        raise HTTPException(404)
    form = await request.form()
    decisions = {}

    def num(key, cast):
        v = str(form.get(key, "")).replace(",", "").strip()
        try:
            return cast(v) if v else None
        except ValueError:
            return None

    reject_all = form.get("action") == "reject_all"
    for c in w.get("changes", []):
        cid = c["id"]
        decisions[cid] = {"approve": (not reject_all) and form.get(f"ok_{cid}") == "on",
                          "qty": num(f"qty_{cid}", int), "price": num(f"price_{cid}", float),
                          "stop": num(f"stop_{cid}", float)}
    try:
        res = sign_off({**w, "week": wid}, decisions, current_user(request), str(form.get("note", ""))[:1000], store)
    except ValueError as exc:
        return back(f"/weeks/{wid}", str(exc))
    return back(f"/weeks/{wid}", f"Signed off: {len(res['applied'])} change(s) applied to the {BOOK} book, "
                                 f"{len(res['rejected'])} rejected. Place the orders yourself in Kite, tagged {BOOK}.")


@app.get("/book", response_class=HTMLResponse)
def book_page(request: Request):
    store = get_store()
    book, rows, totals = _book_view(store)
    journal = store.list("mom_journal", order_by="at", limit=200)
    return render(request, "book.html", book=book, rows=rows, totals=totals, journal=journal)


@app.post("/run")
def run(request: Request):
    store = get_store()
    ref = control.run_now()
    store.set("mom_audit", f"{now_iso()}-run", {"at": now_iso(), "user": current_user(request), "action": "run", "ref": ref})
    return back("/", "Weekly run started. Refresh in a few minutes.")


@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    return render(request, "runs.html", runs=get_store().list("mom_runs", order_by="started", limit=30))


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    store = get_store()
    return render(request, "settings.html", cfg=get_cfg(store), defaults=DEFAULTS, book=get_book(store))


@app.post("/settings")
async def settings_save(request: Request):
    store = get_store()
    form = await request.form()
    out = {}
    for k, dv in DEFAULTS.items():
        if k not in form:
            continue
        v = str(form[k]).strip()
        if isinstance(dv, str):
            out[k] = v.upper()
        else:
            try:
                out[k] = type(dv)(float(v.replace(",", "")))
            except ValueError:
                return back("/settings", f"{k} must be a number")
    store.set("mom_settings", "current", {**out, "updated": now_iso(), "by": current_user(request)}, merge=True)
    book = get_book(store)
    if "capital" in out and not book.get("positions") and not store.list("mom_journal", limit=1):
        book.update(cash=out["capital"], capital=out["capital"], peak_value=out["capital"])
        store.set("mom_portfolio", BOOK, book)
    return back("/settings", "Settings saved")


def _liquid_ref_price(store) -> float | None:
    w = _latest_week(store)
    return (w or {}).get("liquid", {}).get("price")


@app.get("/zerodha", response_class=HTMLResponse)
def zerodha(request: Request):
    store = get_store()
    st, err = None, None
    if kite.configured():
        try:
            st = kite.call("/status")
        except kite.RelayError as exc:
            err = str(exc)
    last = store.get("mom_kite", "last") or {}
    return render(request, "zerodha.html", configured=kite.configured(), status=st, error=err, last=last,
                  ref_price=_liquid_ref_price(store))


@app.get("/zerodha/login")
def zerodha_login():
    if not kite.configured():
        return back("/zerodha", "The Zerodha connection is not set up yet.")
    return RedirectResponse(kite.login_url(), status_code=303)


@app.get("/kite/callback")
def kite_callback(request: Request, request_token: str = "", status: str = ""):
    """Kite redirects here after the daily login (set as the app's redirect URL)."""
    if status != "success" or not request_token:
        return back("/zerodha", "Zerodha login was cancelled or failed.")
    try:
        res = kite.call("/session", {"request_token": request_token})
    except kite.RelayError as exc:
        return back("/zerodha", f"Login failed: {exc}")
    get_store().set("mom_audit", f"{now_iso()}-kite-login", {"at": now_iso(), "user": current_user(request),
                                                           "action": "kite_login", "kite_user": res.get("user_id")})
    return back("/zerodha", f"Connected to Zerodha as {res.get('user_id')} for today.")


@app.post("/zerodha/check")
def zerodha_check(request: Request):
    store = get_store()
    try:
        res = kite.call("/check")
    except kite.RelayError as exc:
        return back("/zerodha", f"Check failed: {exc}")
    store.set("mom_kite", "last", {**(store.get("mom_kite", "last") or {}), "check": res, "check_at": now_iso()})
    return back("/zerodha", "Connection check passed.")


@app.post("/zerodha/test-order")
async def zerodha_test_order(request: Request):
    store = get_store()
    form = await request.form()
    if form.get("confirm") != "on":
        return back("/zerodha", "Tick the box to confirm the test order first.")
    ref = _liquid_ref_price(store)
    if not ref:
        return back("/zerodha", "No LIQUIDBEES price yet: run the weekly review first.")
    try:
        res = kite.call("/test-order", {"reference_price": ref}, timeout=60)
    except kite.RelayError as exc:
        return back("/zerodha", f"Test order failed: {exc}")
    store.set("mom_kite", "last", {**(store.get("mom_kite", "last") or {}), "test": res, "test_at": now_iso()})
    store.set("mom_audit", f"{now_iso()}-kite-test", {"at": now_iso(), "user": current_user(request),
                                                     "action": "kite_test_order", "order_id": res.get("order_id"),
                                                     "final_status": res.get("final_status")})
    return back("/zerodha", f"Test order {res.get('order_id')}: {res.get('final_status')}.")


def _recon(store):
    week = _latest_week(store)
    screen = store.get("mom_screens", week["week"]) if week else None
    last = store.get("mom_kite", "last") or {}
    chk = last.get("check") or {}
    _, _, totals = _book_view(store)
    data = mrecon.build(chk.get("holdings") or [], week, screen, get_book(store), get_cfg(store),
                        book_value=totals["total"], cash_available=chk.get("cash_available"))
    return data, last


@app.get("/reconcile", response_class=HTMLResponse)
def reconcile_page(request: Request):
    store = get_store()
    data, last = _recon(store)
    st = None
    if kite.configured():
        try:
            st = kite.call("/status")
        except kite.RelayError:
            st = None
    return render(request, "reconcile.html", r=data, checked_at=last.get("check_at"), status=st,
                  configured=kite.configured(), cfg=get_cfg(store),
                  orders=store.list("mom_orders", order_by="at", limit=30))


@app.post("/reconcile/refresh")
def reconcile_refresh(request: Request):
    store = get_store()
    try:
        res = kite.call("/check")
    except kite.RelayError as exc:
        return back("/reconcile", f"Could not read Zerodha: {exc}")
    store.set("mom_kite", "last", {**(store.get("mom_kite", "last") or {}), "check": res, "check_at": now_iso()})
    try:
        n = morders.refresh(store)
    except kite.RelayError:
        n = 0
    return back("/reconcile", f"Read {res.get('holdings_count', 0)} holdings from Zerodha"
                              + (f" and updated {n} open order(s)." if n else "."))


@app.post("/orders/review", response_class=HTMLResponse)
async def orders_review(request: Request):
    store = get_store()
    form = await request.form()
    cfg = get_cfg(store)
    picked, errors = morders.parse(form, float(cfg["max_order_inr"]))
    if not picked:
        return back("/reconcile", "; ".join(errors) or "Tick at least one stock to order.")
    data, _ = _recon(store)
    return render(request, "orders_review.html", picked=picked, errors=errors, r=data,
                  buy_total=sum(o["value"] for o in picked if o["side"] == "BUY"),
                  sell_total=sum(o["value"] for o in picked if o["side"] == "SELL"))


@app.post("/orders/place")
async def orders_place(request: Request):
    store = get_store()
    form = await request.form()
    if form.get("confirm") != "on":
        return back("/reconcile", "Not sent: tick the confirmation box on the review page.")
    cfg = get_cfg(store)
    picked, errors = morders.parse(form, float(cfg["max_order_inr"]))
    if errors or not picked:
        return back("/reconcile", "Not sent: " + ("; ".join(errors) or "no orders"))
    week = _latest_week(store)
    res = morders.place(picked, current_user(request), form.get("to_book") == "on", (week or {}).get("week"), store)
    summary = ", ".join(f"{o['side'].lower()} {o['qty']} {o['symbol']}: {o['status']}" for o in res)
    return back("/reconcile", f"Sent {len(res)} order(s) to Zerodha. {summary}.")


@app.post("/orders/refresh")
def orders_refresh(request: Request):
    try:
        n = morders.refresh(get_store())
    except kite.RelayError as exc:
        return back("/reconcile", f"Could not check orders: {exc}")
    return back("/reconcile", f"Checked {n} open order(s).")


SYM_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789&-")


@app.get("/stock", response_class=HTMLResponse)
def stock_search(request: Request, q: str = ""):
    store = get_store()
    week = _latest_week(store)
    screen = store.get("mom_screens", week["week"]) if week else None
    hits = stockview.search((screen or {}).get("rows", []), q)
    sym = q.strip().upper()
    if hits and hits[0]["s"] == sym:
        return RedirectResponse(f"/stock/{quote(sym, safe='')}", status_code=303)
    return render(request, "stock_search.html", q=q, hits=hits, week=week)


@app.get("/stock/{sym}", response_class=HTMLResponse)
def stock_page(request: Request, sym: str):
    sym = sym.strip().upper()
    if not sym or len(sym) > 20 or not set(sym) <= SYM_OK:
        raise HTTPException(404)
    store = get_store()
    cfg = get_cfg(store)
    weeks = store.list("mom_weeks", order_by="week", limit=8)
    week = weeks[0] if weeks else None
    screen = store.get("mom_screens", week["week"]) if week else None
    v = stockview.view(sym, week, screen, cfg)
    history = []
    for w in weeks:
        sc = screen if w is week else store.get("mom_screens", w["week"])
        r = next((x for x in (sc or {}).get("rows", []) if x["s"] == sym), None)
        if r:
            history.append({"week": w["week"], "rank": r.get("rank"), "nfail": r.get("nfail"), "close": r.get("close"),
                            "setup": r.get("setup")})
    data, last = _recon(store)
    recon = next((x for x in data["rows"] if x["symbol"] == sym), None)
    pos = get_book(store).get("positions", {}).get(sym)
    changes = [c for c in (week or {}).get("changes", []) if c["symbol"] == sym]
    journal = [j for j in store.list("mom_journal", where=[("symbol", "==", sym)])]
    journal.sort(key=lambda j: j.get("at", ""), reverse=True)
    orders = [o for o in store.list("mom_orders", order_by="at", limit=100) if o.get("symbol") == sym]
    return render(request, "stock.html", v=v, week=week, history=history, recon=recon, pos=pos, changes=changes,
                  journal=journal[:30], orders=orders, checked_at=last.get("check_at"))


@app.get("/api/weeks/{wid}")
def week_json(wid: str):
    w = get_store().get("mom_weeks", wid)
    return JSONResponse(w) if w else JSONResponse({"error": "not found"}, status_code=404)
