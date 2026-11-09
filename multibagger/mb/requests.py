"""Per-stock stage requests from the portal's stock page.

Clicking "Run" for one stage of one stock records a request in `requests/` and starts the agent for that
stock alone (MB_SYMBOLS). If the agent is already busy, the request stays queued and the agent's next
run (including the next run of a chain in progress) takes it first. The agent marks it done.
"""
from __future__ import annotations

from .store import now_iso

# Stages a person can run for a single stock, in pipeline order: (agent id, label, uses the LLM).
STAGES = [("fundamentals", "Fundamentals (Yahoo)", False), ("screener", "Screen (Pass 1 lanes)", False),
          ("extraction", "Fact extraction (filings and documents)", True), ("analyst", "Company analyst", True),
          ("redteam", "Red team", True)]
STAGE_IDS = {s[0] for s in STAGES}


def _id(agent: str, symbol: str) -> str:
    return f"{agent}-{symbol}"


def add(store, agent: str, symbol: str, by: str) -> None:
    store.set("requests", _id(agent, symbol), {"agent": agent, "symbol": symbol, "status": "queued",
                                               "requested": now_iso(), "by": by})


def queued(store, agent: str) -> list[str]:
    rows = store.list("requests", where=[("agent", "==", agent), ("status", "==", "queued")])
    return [r["symbol"] for r in sorted(rows, key=lambda r: r.get("requested") or "")]


def finish(store, agent: str, symbols, result: str) -> None:
    for sym in symbols:
        if store.get("requests", _id(agent, sym)):
            store.set("requests", _id(agent, sym), {"status": "done", "finished": now_iso(), "result": result},
                      merge=True)


def for_symbol(store, symbol: str) -> dict:
    return {r["agent"]: r for r in store.list("requests", where=[("symbol", "==", symbol)])}
