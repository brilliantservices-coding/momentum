"""Run the weekly job on demand from the portal (Cloud Run Job in GCP, a subprocess locally)."""
from __future__ import annotations

import os
import subprocess
import sys

from .config import ROOT, settings


def run_now() -> str:
    if settings.backend != "gcp":
        p = subprocess.Popen([sys.executable, "-m", "mom.weekly", "--force"], cwd=ROOT, env={**os.environ},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"local pid {p.pid}"
    from google.cloud import run_v2

    name = f"projects/{settings.project}/locations/{settings.region}/jobs/{settings.job_name}"
    req = run_v2.RunJobRequest(name=name, overrides=run_v2.RunJobRequest.Overrides(container_overrides=[
        run_v2.RunJobRequest.Overrides.ContainerOverride(env=[run_v2.EnvVar(name="MOM_FORCE", value="1")])]))
    op = run_v2.JobsClient().run_job(request=req)
    return op.metadata.name if op.metadata else "started"
