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

Phase 2 research agents (Claude). They ship **paused**: press Run now once, check the output, then
Resume schedule.

| Agent | Cadence (IST) | Does |
|---|---|---|
| Theme analyst | Quarterly, 1st of Jan/Apr/Jul/Oct 08:00 | Scores Indian themes on six dimensions into `themes/` |
| Extraction | Mon, Thu 21:00 | Cited facts (guidance, capex, orders, auditor remarks) from watchlist filings into `facts/` |
| Company analyst | Sat 09:00 | Memo, bull/base/bear value and the seven-family conviction score into `memos/`, `scores/` (5 names per run, `MB_ANALYST_MAX`) |
| Red team | Sat 11:00 | Bear case for tier A/B names; can lower the score up to 15 points or reject |
| Portfolio | Sat 12:00 | Buy-starter and exit tickets into `proposals/` (no LLM). Approving one updates `holdings/` |

The model proposes points per family with evidence; caps, the agreement bonus, red-flag overrides and
tiers are computed in `mb/rules.py`. **Settings** in the portal picks the LLM:

- **Claude subscription** (default): Claude Code runs headless in the job with the token from
  `claude setup-token`, stored once in Secret Manager as `mb-claude-oauth-token`:
  `printf '%s' "<token>" | gcloud secrets create mb-claude-oauth-token --data-file=- --labels=app=multibagger`,
  then re-run `deploy.sh`. Uses your plan's limits, no GCP charge.
- **Vertex AI**: billed per token on the GCP bill. Enable the Claude models once in Model Garden.

A daily USD cap (Settings) stops all model calls for the day once reached.

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
