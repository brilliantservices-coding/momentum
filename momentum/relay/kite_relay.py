"""Kite relay: the only component that talks to Zerodha. Runs on the mom-kite-vm VM so every
Kite API call leaves from the whitelisted static IP.

It listens on the VM's internal address only; the portal reaches it over the VPC and must send
the shared relay token. The Kite API secret and the relay token come from Secret Manager; the
day's access token stays on this VM (Kite expires it every morning).

Endpoints (all POST, JSON):
  /session   {request_token}   -> exchanges the login token for the day's access token
  /check                       -> profile, funds and holdings summary (read-only)
  /test-order {price}          -> 1 x LIQUIDBEES limit buy at a price that cannot fill, cancelled at once
  /status                      -> whether a session for today exists
Nothing else can place orders.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request

KITE = "https://api.kite.trade"
API_KEY = os.getenv("KITE_API_KEY", "")
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
STATE = Path(os.getenv("RELAY_STATE", "/var/lib/mom-relay/session.json"))
TEST_SYMBOL = "LIQUIDBEES"
TAG = "Momentum"
IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(docs_url=None, redoc_url=None)
_secrets: dict[str, str] = {}


def secret(name: str) -> str:
    """Reads a Secret Manager secret (cached). Tests set RELAY_SECRET_<NAME> instead."""
    env = os.getenv("RELAY_SECRET_" + name.upper().replace("-", "_"))
    if env is not None:
        return env
    if name not in _secrets:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        path = f"projects/{PROJECT}/secrets/{name}/versions/latest"
        _secrets[name] = client.access_secret_version(name=path).payload.data.decode().strip()
    return _secrets[name]


def _auth(request: Request) -> None:
    sent = request.headers.get("x-relay-token", "")
    if not sent or not hmac.compare_digest(sent, secret("mom-relay-token")):
        raise HTTPException(401, "bad relay token")


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _session() -> dict:
    s = _load()
    if not s.get("access_token"):
        raise HTTPException(409, "Not connected today. Use Connect Zerodha in the portal first.")
    # Kite access tokens expire at 06:00 IST the next day
    if datetime.now(IST) >= datetime.fromisoformat(s["expires"]):
        raise HTTPException(409, "Today's Zerodha session has expired. Connect Zerodha again.")
    return s


def _kite(method: str, path: str, token: str | None = None, **kw) -> dict:
    headers = {"X-Kite-Version": "3"}
    if token:
        headers["Authorization"] = f"token {API_KEY}:{token}"
    r = requests.request(method, KITE + path, headers=headers, timeout=20, **kw)
    try:
        body = r.json()
    except ValueError:
        raise HTTPException(502, f"Kite returned HTTP {r.status_code}")
    if r.status_code >= 400 or body.get("status") == "error":
        raise HTTPException(r.status_code if r.status_code >= 400 else 502,
                            f"Kite: {body.get('error_type', '')} {body.get('message', '')}".strip())
    return body.get("data", body)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/status")
def status(request: Request):
    _auth(request)
    s = _load()
    live = bool(s.get("access_token")) and datetime.now(IST) < datetime.fromisoformat(s.get("expires", "2000-01-01T00:00:00+05:30"))
    return {"connected": live, "user_id": s.get("user_id") if live else None, "since": s.get("created")}


@app.post("/session")
async def session(request: Request):
    _auth(request)
    rt = (await request.json()).get("request_token", "")
    if not rt or len(rt) > 64:
        raise HTTPException(400, "missing request_token")
    checksum = hashlib.sha256((API_KEY + rt + secret("mom-kite-api-secret")).encode()).hexdigest()
    data = _kite("POST", "/session/token", data={"api_key": API_KEY, "request_token": rt, "checksum": checksum})
    now = datetime.now(IST)
    expires = (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    if now.hour < 6:
        expires = now.replace(hour=6, minute=0, second=0, microsecond=0)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"access_token": data["access_token"], "user_id": data.get("user_id"),
                                 "created": now.isoformat(), "expires": expires.isoformat()}))
    os.chmod(STATE, 0o600)
    return {"connected": True, "user_id": data.get("user_id"), "user_name": data.get("user_name")}


@app.post("/check")
def check(request: Request):
    """Read-only: proves the key, secret, IP whitelist and today's login all work."""
    _auth(request)
    tok = _session()["access_token"]
    profile = _kite("GET", "/user/profile", tok)
    margins = _kite("GET", "/user/margins/equity", tok)
    holdings = _kite("GET", "/portfolio/holdings", tok)
    ip = None
    try:
        ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        pass
    return {
        "user_id": profile.get("user_id"), "user_name": profile.get("user_name"),
        "exchanges": profile.get("exchanges"), "products": profile.get("products"),
        "cash_available": (margins.get("available") or {}).get("live_balance"),
        "net": margins.get("net"),
        "holdings_count": len(holdings),
        "holdings": [{"symbol": h.get("tradingsymbol"), "qty": h.get("quantity"),
                      "avg": h.get("average_price"), "last": h.get("last_price")} for h in holdings][:200],
        "egress_ip": ip,
    }


def _market_open(now: datetime) -> bool:
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) < (15, 30)


@app.post("/test-order")
async def test_order(request: Request):
    """Places 1 x LIQUIDBEES BUY LIMIT at a price far enough below the market that it cannot fill,
    tagged Momentum, then cancels it and returns what Kite recorded at each step."""
    _auth(request)
    body = await request.json()
    ref = float(body.get("reference_price") or 0)
    if not 100 <= ref <= 5000:
        raise HTTPException(400, "need LIQUIDBEES reference price")
    tok = _session()["access_token"]
    price = round(ref * 0.97, 2)  # LIQUIDBEES barely moves; 3% below cannot fill but is inside the price band
    now = datetime.now(IST)
    variety = "regular" if _market_open(now) else "amo"
    placed = _kite("POST", f"/orders/{variety}", tok, data={
        "exchange": "NSE", "tradingsymbol": TEST_SYMBOL, "transaction_type": "BUY", "order_type": "LIMIT",
        "quantity": 1, "price": price, "product": "CNC", "validity": "DAY", "tag": TAG})
    oid = placed["order_id"]
    steps = [{"step": "placed", "order_id": oid, "variety": variety, "price": price}]
    time.sleep(1.5)
    cancel_error = None
    try:
        _kite("DELETE", f"/orders/{variety}/{oid}", tok)
        steps.append({"step": "cancel requested"})
    except HTTPException as exc:  # e.g. the exchange already rejected it
        cancel_error = exc.detail
        steps.append({"step": "cancel failed", "error": exc.detail})
    time.sleep(1.5)
    history = _kite("GET", f"/orders/{oid}", tok)
    statuses = [{"status": h.get("status"), "message": h.get("status_message"), "at": h.get("order_timestamp")}
                for h in history]
    final = statuses[-1]["status"] if statuses else None
    return {"order_id": oid, "variety": variety, "price": price, "final_status": final, "steps": steps,
            "history": statuses, "ok": final in ("CANCELLED", "REJECTED") or cancel_error is None,
            "filled": final == "COMPLETE"}
