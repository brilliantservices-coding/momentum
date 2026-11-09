"""Input fingerprints so research agents skip work whose inputs haven't changed.

Each agent hashes exactly what it sends to the model (minus timestamps and day-to-day market noise)
and stores the hash with its output. Next run, a stock whose hash matches is left alone, so a run
cut short continues with the stocks that are still pending, and an unchanged stock costs nothing.
Set MB_REDO=1 (the portal's "Redo all" button) to ignore the saved results once.
"""
from __future__ import annotations

import hashlib
import json
import os

# Timestamps and bookkeeping that change on every refresh without changing the evidence.
VOLATILE = {"id", "as_of", "stmt_as_of", "overlay_as_of", "overlay_file", "source", "screened", "ingested",
            "extracted", "created", "imported", "updated", "written", "analysed", "redteamed", "scored"}
# Market data moves every day; the analyst re-runs on a big price move instead (see PRICE_MOVE).
MARKET = {"price", "mcap", "pe", "volume_1m_avg", "avg_traded_value_cr"}
PRICE_MOVE = float(os.getenv("MB_REANALYSE_PRICE_MOVE", "0.15"))


def redo() -> bool:
    return os.getenv("MB_REDO") == "1" or bool(os.getenv("MB_REDO_SINCE"))


def redo_due(last_done: str | None) -> bool:
    """With "redo all", a stock is due when its saved result is older than the start of the redo.
    The chained follow-up runs carry MB_REDO_SINCE, so each stock is redone once, not once per run."""
    since = os.getenv("MB_REDO_SINCE")
    if since:
        return (last_done or "") < since
    return os.getenv("MB_REDO") == "1"


def out_of_time() -> bool:
    """True once the run is near its job timeout (MB_RUN_DEADLINE, set by the runner): save and hand over."""
    import time
    deadline = os.getenv("MB_RUN_DEADLINE")
    return bool(deadline) and time.time() > float(deadline)


def _clean(obj, drop):
    if isinstance(obj, dict):
        return {k: _clean(v, drop) for k, v in sorted(obj.items()) if k not in drop and v is not None}
    if isinstance(obj, list):
        items = [_clean(v, drop) for v in obj]  # order-free: stores don't promise a stable order
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(obj, float):
        return round(obj, 4)
    return obj


def digest(*parts, drop=VOLATILE | MARKET) -> str:
    text = json.dumps([_clean(p, drop) for p in parts], sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def price_moved(old, new) -> bool:
    try:
        return abs(float(new) / float(old) - 1) >= PRICE_MOVE
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def only_symbols() -> set[str]:
    """MB_SYMBOLS (comma separated): a per-stock run requested on the portal's stock page. The agent
    handles just these stocks, redoes them even if unchanged, and starts no follow-up run."""
    return {s.strip().upper() for s in (os.getenv("MB_SYMBOLS") or "").split(",") if s.strip()}
