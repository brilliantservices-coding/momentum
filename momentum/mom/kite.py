"""Portal side of the Zerodha connection. Every call goes to the Kite relay on mom-kite-vm, which
is the only thing that talks to Kite (from the whitelisted static IP)."""
from __future__ import annotations

import os

import requests

RELAY_URL = os.getenv("MOM_RELAY_URL", "")           # e.g. http://10.160.0.2:8080 (VM internal IP)
API_KEY = os.getenv("MOM_KITE_API_KEY", "")
LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key={key}"


class RelayError(Exception):
    pass


def configured() -> bool:
    return bool(RELAY_URL and API_KEY and os.getenv("MOM_RELAY_TOKEN"))


def login_url() -> str:
    return LOGIN_URL.format(key=API_KEY)


def call(path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    if not configured():
        raise RelayError("The Zerodha connection is not set up yet.")
    try:
        r = requests.post(RELAY_URL.rstrip("/") + path, json=payload or {}, timeout=timeout,
                          headers={"x-relay-token": os.environ["MOM_RELAY_TOKEN"]})
    except requests.RequestException as exc:
        raise RelayError(f"Could not reach the Kite relay on the VM: {type(exc).__name__}") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        raise RelayError(body.get("detail") or f"relay returned HTTP {r.status_code}")
    return body
