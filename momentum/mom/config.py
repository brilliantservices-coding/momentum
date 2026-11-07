"""Runtime configuration from environment variables, plus the strategy defaults.

MOM_BACKEND=local keeps everything on disk (development and tests); MOM_BACKEND=gcp uses Firestore
and Cloud Run. Strategy parameters can be changed in the portal's Settings page; these are the
defaults from the strategy doc.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOK = "Momentum"  # name of the holding/book this strategy's trades are kept in
LIQUID = "LIQUIDBEES"  # Nippon India ETF Liquid BeES: where cash not needed for stocks is parked


def _list(name: str) -> tuple[str, ...]:
    return tuple(u.strip().lower() for u in re.split(r"[,;]", os.getenv(name, "")) if u.strip())


@dataclass(frozen=True)
class Settings:
    backend: str = os.getenv("MOM_BACKEND", "local")
    project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    region: str = os.getenv("MOM_REGION", "asia-south1")
    data_dir: Path = Path(os.getenv("MOM_DATA_DIR", str(ROOT / ".localdata")))
    allowed_users: tuple[str, ...] = field(default_factory=lambda: _list("MOM_ALLOWED_USERS"))
    job_name: str = os.getenv("MOM_JOB", "mom-weekly")


settings = Settings()

# Strategy defaults (all editable in the portal except where noted).
DEFAULTS = {
    "capital": 1_000_000.0,      # starting cash of the Momentum book, used only when the book is created
    "risk_pct": 1.0,             # % of book value risked per new position
    "max_positions": 15,
    "position_cap_pct": 10.0,    # max % of book value in one stock
    "sector_cap_pct": 25.0,
    "heat_cap_pct": 6.0,         # max total open risk (entry - stop) as % of book value
    "max_stop_pct": 8.0,         # initial stop never more than this below entry
    "buy_rank": 15,              # new buys only from the top N of the ranking
    "exit_rank": 40,             # holdings whose rank falls below this are sold
    "min_turnover_cr": 10.0,     # 50-day average daily traded value, in ₹ crore
    "rs_rating_min": 70,
    "extended_pct": 5.0,         # more than this above the pivot = extended, watchlist only
    "exclude": "",               # symbols to never buy (ASM/GSM, red flags), comma separated
    "park_in_liquid": 1,         # 1 = park idle cash in LIQUIDBEES, 0 = keep it as cash
    "cash_buffer_pct": 1.0,      # % of the book kept as plain cash for charges and rounding
}
REGIME_EXPOSURE = {"green": 1.0, "amber": 0.55, "red": 0.20}
REGIME_LEVEL = {"red": 0, "amber": 1, "green": 2}
