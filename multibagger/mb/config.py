"""Runtime configuration, read from environment variables.

MB_BACKEND=local keeps everything on disk (for development and tests);
MB_BACKEND=gcp uses Firestore, Cloud Storage, Cloud Run and Cloud Scheduler.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    backend: str = os.getenv("MB_BACKEND", "local")
    project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    region: str = os.getenv("MB_REGION", "asia-south1")
    bucket: str = os.getenv("MB_BUCKET", "")
    data_dir: Path = Path(os.getenv("MB_DATA_DIR", str(ROOT / ".localdata")))
    # Comma-separated Google accounts allowed into the portal (checked against the IAP header).
    allowed_users: tuple[str, ...] = tuple(
        u.strip().lower() for u in re.split(r"[,;]", os.getenv("MB_ALLOWED_USERS", "")) if u.strip()
    )
    monthly_budget_inr: float = float(os.getenv("MB_MONTHLY_BUDGET_INR", "2500"))
    daily_llm_cap_usd: float = float(os.getenv("MB_DAILY_LLM_CAP_USD", "1.0"))
    job_prefix: str = os.getenv("MB_JOB_PREFIX", "mb-")
    # Symbols for which ingestion also downloads attachments (in addition to the screener watchlist).
    extra_watchlist: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip().upper() for s in re.split(r"[,;]", os.getenv("MB_WATCHLIST", "")) if s.strip()
        )
    )


settings = Settings()


def load_agents() -> list[dict]:
    with open(ROOT / "agents.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["agents"]


def agent_by_id(agent_id: str) -> dict:
    for a in load_agents():
        if a["id"] == agent_id:
            return a
    raise KeyError(agent_id)
