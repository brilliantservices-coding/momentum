# Multibagger agents (India)

Implements the *Multibagger Hunting Strategy (India)* as scheduled agents on Google Cloud, with a
portal to watch and control them. Agents read public data and propose; a human approves every
action and places any order manually. Nothing here talks to a broker. Educational, not advice.

## What runs

| Agent | Cadence (IST) | Does |
|---|---|---|
| Universe builder | Mon 06:00 | NSE equity list into `universe/` |
| Ingestion | Mon, Thu 19:30 | Loads NSE announcement CSVs (desktop task or portal upload) into `filings/` and flags red-flag keywords |
| Forensic | Mon, Thu 20:00 | Red-flag checklist on fundamentals and new filings into `alerts/` |
| Screener | Sat 07:00 | Pass 1 screen on the uploaded fundamentals export into `candidates/` |

Phase 2 (next): extraction, theme analyst, company analyst, ecosystem, red-team and portfolio
agents (LLM-based), writing order tickets into `proposals/` for the portal's Approvals page.

## Architecture (frugal)

- One container image. Each agent is a **Cloud Run Job** (`mb-<agent>`), triggered by **Cloud Scheduler**; nothing runs or bills while idle.
- **Firestore** (free tier) holds state and outputs; **Cloud Storage** holds raw documents.
- **Portal** (`mb-portal`) is a Cloud Run service at min-instances 0 behind IAP (Google sign-in, owner only).
- **Costs:** a Cloud Billing budget alerts at 50/90/100%. Its Pub/Sub notifications go to a private `mb-guard` service, which records month-to-date spend for the portal and, at 100%, trips the kill switch (pauses every schedule, cancels running jobs). Artifact Registry keeps only the 3 newest images.
- Every agent run checks the portal's pause flag and the kill switch before doing anything.

## Deploy

```bash
cp infra/config.example.env infra/config.env   # fill in project, region, budget, your email
bash infra/deploy.sh                           # Cloud Shell, macOS/Linux or Git Bash; re-runnable
```

Then open the portal URL, upload a Screener.in export on **Data**, and press **Run now** on the agents.

## Develop locally

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest httpx
python -m pytest -q
MB_BACKEND=local uvicorn portal.app:app --reload     # data goes to .localdata/
python -m mb.runner screener --force                 # run one agent
```
