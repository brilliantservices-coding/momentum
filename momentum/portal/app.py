"""Momentum portal: review each week's proposed changes and sign off on them.

Runs on Cloud Run behind IAP (Google sign-in); the app also checks the signed-in email against
MOM_ALLOWED_USERS. Signing off updates the "Momentum" book; it never places orders.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mom import control
from mom.book import get_book, get_cfg, sign_off
from mom.config import BOOK, DEFAULTS, REGIME_EXPOSURE, settings
from mom.store import get_store, now_iso

HERE = Path(__file__).parent
app = FastAPI(title="Momentum portal", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


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
               msg=request.query_params.get("msg", ""))
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
    rows, invested = [], 0.0
    for sym, p in sorted(book.get("positions", {}).items()):
        last = prices.get(sym) or p["entry"]
        val = p["qty"] * last
        invested += val
        rows.append({"symbol": sym, **p, "last": last, "value": val, "pnl_pct": 100 * (last / p["entry"] - 1),
                     "risk": max(0.0, p["qty"] * (min(last, p["entry"]) - p["stop"]))})
    total = book["cash"] + invested
    return book, rows, {"cash": book["cash"], "invested": invested, "total": total,
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


@app.get("/api/weeks/{wid}")
def week_json(wid: str):
    w = get_store().get("mom_weeks", wid)
    return JSONResponse(w) if w else JSONResponse({"error": "not found"}, status_code=404)
