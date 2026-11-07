"""Time display in IST, and parsing of the exchange date strings found in announcement exports."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")
_FILING_FORMATS = ["%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
                   "%d-%m-%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]


def to_ist(value) -> datetime | None:
    """ISO timestamp (UTC or with offset) or datetime -> aware IST datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist(value, fmt: str = "%d %b %Y %H:%M") -> str:
    """Jinja filter: '07 Nov 2026 16:01 IST'. Date-only formats drop the suffix."""
    dt = to_ist(value)
    if not dt:
        return str(value or "")
    return dt.strftime(fmt) + (" IST" if "%H" in fmt else "")


def filing_date(text) -> datetime | None:
    """Exchange broadcast times are already IST: '07-Nov-2026 12:48:45', '06-11-2026', ..."""
    s = str(text or "").strip()
    for f in _FILING_FORMATS:
        try:
            return datetime.strptime(s, f).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def coverage(dates, prev: dict | None = None) -> dict:
    """Merges filing dates into {first, last} ISO strings (IST)."""
    prev = prev or {}
    parsed = [d for d in (filing_date(x) for x in dates) if d]
    cands_first = [to_ist(prev.get("first"))] if prev.get("first") else []
    cands_last = [to_ist(prev.get("last"))] if prev.get("last") else []
    first = min(parsed + cands_first, default=None)
    last = max(parsed + cands_last, default=None)
    return {"first": first.isoformat() if first else None, "last": last.isoformat() if last else None}


def age_days(value) -> float | None:
    dt = to_ist(value)
    return None if not dt else (datetime.now(timezone.utc) - dt).total_seconds() / 86400
