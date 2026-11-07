# Multibagger agents (India)

Implements the *Multibagger Hunting Strategy (India)* as scheduled agents on Google Cloud, with a portal
to watch and control them. Agents read public data, score companies and propose order tickets; a human
approves every ticket and places any order manually. Nothing here talks to a broker. Educational, not advice.

For day-to-day operation, deploys, secrets and troubleshooting see **[OPERATIONS.md](OPERATIONS.md)**.

## How it fits together

```
 Your PC (Claude desktop scheduled tasks)          Google Cloud project (asia-south1)
 ─────────────────────────────────────────        ──────────────────────────────────────────────────────
 Mon+Thu 19:00  NSE announcements CSV  ──gcloud──▶ GCS bucket mb-<project>-raw
 Fri evening    Trendlyne download     ──gcloud──▶   inbox/nse/announcements/   inbox/fundamentals/
                                                          │                         │
                Cloud Scheduler (IST) ──▶ Cloud Run Jobs mb-<agent> (python -m mb.runner <agent>)
                                                          │
                                         Firestore (default DB): every agent's inputs and outputs
                                                          │
                     Portal mb-portal (Cloud Run + IAP Google sign-in) ◀── you
                     Guard  mb-guard  ◀── Pub/Sub ◀── Cloud Billing budget (kill switch at 100%)
```

- **One container image** (`Dockerfile`) serves everything: the portal, the budget guard and every agent job.
  It also carries the Claude Code CLI for the "subscription" LLM provider.
- **Agents are Cloud Run Jobs**, one per agent, started by Cloud Scheduler or by *Run now* in the portal.
  Nothing runs or bills while idle. `agents.yaml` is the registry that the runner, portal and deploy script all read.
- **Every run** goes through `mb/runner.py`: it checks the kill switch and the agent's pause flag (a portal
  *Run now* overrides the pause), records a `runs/` document with status, duration, summary and log tail,
  and updates `agents/<id>` with the last status.
- **State** lives in Firestore collections (unprefixed; the momentum app in the same project uses `mom_*`).
  Raw files live in the bucket. `MB_BACKEND=local` swaps both for JSON files under `.localdata/` for development.

## The pipeline

| # | Agent | Cadence (IST) | Reads | Writes |
|---|---|---|---|---|
| 1 | Universe builder | Mon 06:00 | NSE equity list | `universe/` |
| 2 | Ingestion | Mon, Thu 19:30 | bucket `inbox/nse/announcements/`, `filings/inbox/`, optional Drive folder | `filings/`, keyword `alerts/`, `control/filings_coverage` |
| 3 | Forensic | Mon, Thu 20:00 | `fundamentals/`, `filings/` | `alerts/` (hard / soft red flags) |
| 4 | Fundamentals | Fri 20:00 | Yahoo Finance for every universe symbol, plus Trendlyne/Screener overlays | `fundamentals/`, then runs Forensic |
| 5 | Screener | Sat 07:00 | imports `inbox/fundamentals/` first, then `fundamentals/`, open hard alerts | `candidates/`, `screens/` |
| 6 | Theme analyst (AI) | Quarterly | filings headlines, previous themes | `themes/` |
| 7 | Extraction (AI) | Mon, Thu 21:00 | watchlist filings and their PDFs | `facts/` |
| 8 | Company analyst (AI) | Sat 09:00 | company record (fundamentals, screen, flags, filings, facts) | `memos/`, `memo_history/`, `scores/` |
| 9 | Red team (AI) | Sat 11:00 | memo + record for tier A/B names | `redteam/`, adjusts `scores/` |
| 10 | Portfolio | Sat 12:00 | `scores/`, `holdings/`, alerts | `proposals/` (tickets for the Approvals page) |

Agents 6-10 shipped **paused**; run each once from the portal, check its output, then resume its schedule.

### Data sources and why

- **NSE/BSE block automated downloads**, from cloud and home IPs alike, and spoofing browser headers is
  off the table (terms of use). Announcements therefore come from a desktop scheduled task that downloads
  the NSE CSV in your own signed-in Chrome and copies it to the bucket. Portal upload is the manual fallback.
- **Fundamentals**: Screener.in charges for downloads, so the base layer is Yahoo Finance (`mb/yahoo.py`,
  free, covers the whole universe, no promoter pledge data; promoter holding uses Yahoo's insider figure as
  a proxy). A weekly Trendlyne download for ~650 small/mid caps (your GuruQ plan, 2,000-row limit) is
  layered on top: its non-empty fields win over Yahoo's and are kept in `fundamentals_overlay/` for 21 days,
  so a later Yahoo refresh does not wipe them. Any Screener-style export uploaded on the Data page works
  the same way; column names are matched by alias (`mb/fundamentals.py`).

### Scoring (strategy doc rules in code)

- **Pass 1 screen** (`rules.pass1`): seven rules (growth, ROCE, cash conversion, balance sheet, promoter,
  valuation, working capital) plus two gates (market cap ₹500-20,000 cr, ₹1 cr/day traded). A company passes
  with at least 4 known rules, 70% of known rules met, no failed gate and no open hard red flag.
- **Red flags** (`rules.structured_flags`, `rules.FILING_RULES`): pledge, cash-less profits, auditor
  resignation, SEBI orders, defaults and so on. Hard flags block a company until you dismiss the alert.
- **Conviction score** (`rules.conviction`): the analyst model proposes points per family with evidence;
  the code clips them to the weights (theme 15, financial 20, management 20, visibility 15, ecosystem 10,
  valuation 10, governance 10), adds +5 when 5+ families are strong, caps at 60 when fewer than 3 are,
  zeroes on a hard flag or 3+ soft flags, and maps to tiers A ≥80, B 65-79, C 50-64.
- **Sizing** (`rules.SIZING`): starter / max % of portfolio at cost, A 4/8, B 2.5/5, C 1/2, at most 25 names.

### AI layer (`mb/llm.py`)

- Provider chosen on the portal's **Settings** page, stored in `control/llm`:
  - `subscription` (default): runs `claude -p` headless with no tools, using the token from
    `claude setup-token` (Secret Manager `mb-claude-oauth-token`, mounted as `CLAUDE_CODE_OAUTH_TOKEN`).
    Counts against your Claude plan, not the GCP bill.
  - `vertex`: Claude on Vertex AI via the Anthropic SDK, billed per token to the project.
- Every call asks for one JSON object, checks a daily USD cap first (`llm_spend/<date>`) and logs to `llm_calls/`.

### Portal (`portal/app.py`)

Overview (agents, kill switch, data freshness, funnel counts), Funnel (every company, the furthest stage it
reached, factor ticks and why it stopped), Watchlist, Red flags, Filings, Research (scores, themes, memos,
red-team notes), Approvals (tickets; approving updates `holdings/` and the `journal/`), Data (uploads),
Costs, Settings. All times show in IST. Access is Google IAP plus an allow-list (`MB_ALLOWED_USERS`);
POSTs are same-origin checked.

### Costs and safety

- Every resource carries the label `app=multibagger`; the Cloud Billing budget (₹2,500/month) counts only
  that label. At 50/90/100% it notifies Pub/Sub; `mb-guard` records month-to-date spend, and at 100% trips
  the kill switch (pauses every schedule, cancels running executions, blocks scheduled runs).
- Scale to zero everywhere, Firestore free tier, Artifact Registry keeps 3 images, jobs have timeouts.
- The daily LLM cap stops model calls for the day; no agent can place an order.

## Code map

```
agents.yaml            agent registry: id, name, schedule (IST cron), phase, default_enabled, timeout
mb/config.py           settings from env vars (MB_BACKEND, project, region, bucket, allowed users, caps)
mb/store.py, blob.py   Firestore / GCS, or local JSON files (MB_BACKEND=local)
mb/runner.py           entry point for jobs: pause/kill checks, run records, logs
mb/control.py          run now, stop, pause/resume (Firestore flag + Cloud Scheduler), kill switch, budget hook
mb/rules.py            Pass 1, red flags, conviction score, tiers, sizing (pure, unit tested)
mb/fundamentals.py     export parser with column aliases, overlay import, bucket inbox import
mb/yahoo.py            Yahoo Finance fetch and statement-to-metrics mapping
mb/announcements.py    NSE/BSE announcement CSV parser
mb/llm.py              Claude via subscription CLI or Vertex, daily cap, JSON replies
mb/timefmt.py          IST display and exchange date parsing
mb/agents/*.py         one module per agent, each exposing run(store, log) -> summary dict
portal/                FastAPI app, Jinja templates, CSS
infra/deploy.sh        idempotent deploy of everything; infra/config.env (not committed) holds the inputs
tests/                 pytest suite; LLM and Yahoo are mocked
```

## Develop locally

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest httpx pyflakes
python -m pytest -q && pyflakes mb portal tests
MB_BACKEND=local uvicorn portal.app:app --reload     # data goes to .localdata/
python -m mb.runner screener --force                 # run one agent locally
```
