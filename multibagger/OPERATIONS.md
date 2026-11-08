# Multibagger operations manual

How to run, change and fix the multibagger system. Design is in [README.md](README.md).

## Where things are

| What | Value |
|---|---|
| GCP project / region | `arched-tape-447611-r0` / `asia-south1` (shared with TradeRank and the momentum app) |
| Portal | https://mb-portal-vqfwlbuylq-el.a.run.app (Google sign-in) |
| Portal users | `MB_ALLOWED_USERS` env on `mb-portal` and IAP bindings, both set from `OWNER_EMAILS` in `infra/config.env` |
| Bucket | `gs://mb-arched-tape-447611-r0-raw` |
| Firestore | `(default)` database; multibagger collections are unprefixed, momentum uses `mom_*` |
| Service accounts | `mb-agents` (jobs), `mb-portal` (portal and guard), `mb-scheduler` (Scheduler → jobs), `mb-pubsub` (budget push) |
| Secret | `mb-claude-oauth-token` (Claude subscription token) |
| Budget | `mb-monthly-<project>`, ₹2,500, label filter `app=multibagger`, topic `mb-budget` |
| Deploy machine | Windows PC, Git Bash, gcloud signed in as services.brilliant@gmail.com; checkout in `C:\Users\Admin\MultiBaggerClaudeAgentRun\momentum` |
| Desktop tasks (Claude app on the PC) | "NSE filings → multibagger bucket" Mon/Thu 19:00 IST; Trendlyne fundamentals download, Fri evening |

## Deploying a change

1. Merge the PR into `main`.
2. On the deploy machine, in Git Bash:
   ```bash
   cd /c/Users/Admin/MultiBaggerClaudeAgentRun/momentum && git pull
   cd multibagger && bash infra/deploy.sh
   ```
   The script is idempotent: it builds one image with Cloud Build, then updates every job, schedule,
   the portal, the guard, Pub/Sub and the budget. It takes 5-10 minutes.
3. Check the portal Overview loads and the agent states look right.

`infra/config.env` (never committed) holds `PROJECT_ID`, `REGION`, `BUDGET_AMOUNT`, `BUDGET_CURRENCY`,
`OWNER_EMAILS` and optional `DRIVE_INBOX_ID`. Copy `infra/config.example.env` to start a new one.

What a deploy does **not** change:
- **Pause state** of existing schedules: the portal owns it. A schedule for an agent with
  `default_enabled: false` is created paused, only the first time.
- **Budget thresholds and label filter**: set only when the budget is created (gcloud can't update them).
  Change them in the Cloud console under Billing → Budgets & alerts.
- **Secret values**.

## Common changes

### Add or change an agent
1. Write `mb/agents/<id>.py` exposing `run(store, log) -> dict`. The returned dict is the run summary shown in the portal.
2. Add it to `agents.yaml`: `id`, `name`, `description`, `schedule` (cron, IST), `cadence`, `phase`, and
   optionally `default_enabled: false` and `timeout:` in seconds (default 3600).
3. Add a test in `tests/`, run `python -m pytest -q && pyflakes mb portal tests`, merge, deploy.

### Change a schedule
Edit `schedule:` in `agents.yaml` and deploy. To stop an agent temporarily, use **Pause schedule** in the
portal instead. That keeps the cron and resumes cleanly.

### Change screen rules, red flags or scoring
Everything is in `mb/rules.py`: `pass1`, `structured_flags`, `FILING_RULES`, `WEIGHTS`, `TIERS`, `SIZING`.
Update `tests/test_rules.py` alongside. A changed Pass 1 rule takes effect on the next Screener run.

### Add portal users
Add the email to `OWNER_EMAILS` in `infra/config.env` and deploy. That updates both the IAP binding and the
app's allow-list. To remove a user, also remove their IAP binding:
`gcloud iap web remove-iam-policy-binding --member=user:<email> --role=roles/iap.httpsResourceAccessor --region=asia-south1 --resource-type=cloud-run --service=mb-portal`.

### Change the budget amount
Set `BUDGET_AMOUNT` in `config.env` and deploy, or edit it in the Cloud console.

### Switch the AI provider, model or daily cap
Use the portal's **Settings** page. It takes effect on each agent's next run. Vertex needs the Claude models enabled
once in Vertex AI Model Garden for the project.

### Use OpenAI
1. Create an API key at platform.openai.com (a project key with a monthly budget set there is safest).
2. In Git Bash on the PC, without echoing it:
   `read -rs T && printf '%s' "$T" | gcloud secrets create mb-openai-api-key --project=arched-tape-447611-r0 --data-file=- --labels=app=multibagger && unset T`
   (for a new key later: `gcloud secrets versions add mb-openai-api-key ...` with the same pipe).
3. Deploy once (`bash infra/deploy.sh`) so the jobs and portal mount it.
4. On **Settings**, pick *OpenAI API*, a model, and its input/output price per million tokens, then Save.

### Renew the Claude subscription token
The token from `claude setup-token` lasts about a year. When AI runs fail with an authentication error:
1. On the PC, double-click **Claude setup-token** on the Desktop, or run
   `C:\Users\Admin\AppData\Roaming\npm\claude.cmd setup-token`. PowerShell blocks `claude.ps1`, so use the
   `.cmd` file. Copy the printed `sk-ant-oat…` token, and never paste it into chat or a file.
2. Double-click **Store Claude token** (Git Bash), or run:
   `read -rs T && printf '%s' "$T" | gcloud secrets versions add mb-claude-oauth-token --project=arched-tape-447611-r0 --data-file=- && unset T`
3. No redeploy is needed: jobs read `:latest` at start. Disable old versions in Secret Manager if you like.

### Fundamentals sources
- **Yahoo** (Fundamentals agent, Fri 20:00): no action needed. Tunables are env vars on the `mb-fundamentals` job:
  `MB_YAHOO_WORKERS` (1), `MB_YAHOO_PAUSE` (1.0 s between requests), `MB_YAHOO_MAX_MINUTES` (150),
  `MB_YAHOO_LIMIT` (0 means all; set it for a quick test). Yahoo cuts off fast callers with "401 Invalid Crumb";
  after 15 refusals in a row the run stops (`yahoo_cut_off: true` in the summary) and the rest go first next time.
  A run cut short by the time budget carries on from the oldest symbols next time. Each week only
  stocks with new results (seen in the filings) or statements older than 4 weeks get a full download;
  the rest get a one-request price/market-cap refresh, and stocks far outside ₹200-40,000 cr are checked monthly.
  Symbols refreshed in the last 72 h with no results since are left alone (`MB_YAHOO_FRESH_HOURS`), so a
  second run the same week only fetches what is still pending.
  The run summary shows the plan (`planned: full/light/skip/fresh`) and `minutes`.
- **Trendlyne** (desktop task, Friday): downloads the "Multibagger fundamentals" data to
  `gs://…-raw/inbox/fundamentals/`, and the Saturday Screener imports it first. If Trendlyne renames a column,
  add the new name to `ALIASES` in `mb/fundamentals.py`. The Data page shows which columns were recognised.
- **Manual**: upload any Screener/Trendlyne export on the Data page. It is layered over Yahoo in the same way.

### Announcements
The desktop task needs the Claude desktop app open and Chrome available at 19:00 Mon/Thu. If the PC was off,
download the last 7 days from nseindia.com (Corporate filings → Announcements → Download .csv) and upload
it on the Data page. Duplicates are skipped. The Data page shows the latest filing date loaded.

### One run at a time, and no repeated work
- Run now is refused while the agent's previous run is still going (the portal checks Cloud Run's
  executions). A scheduled run that fires meanwhile exits straight away and shows as `skipped` in the run list.
- The theme analyst, company analyst and red team save a fingerprint of their inputs with each result
  (`inputs_hash`). The analyst skips a stock unless its fundamentals, screen, red flags, filings, facts,
  the active themes or the prompt changed, or the price moved 15%+ since the memo
  (`MB_REANALYSE_PRICE_MOVE`). Timestamps and day-to-day market data don't count. Results are saved per
  stock, so a run that stops early carries on with the pending ones next time. The red team re-reviews
  only when the memo or evidence changed, and the theme analyst only when new filings headlines arrived.
- Extraction marks each filing extracted, so it never reads one twice.
- To redo everything once anyway, use **Run now, redo all** on the agent page (sets `MB_REDO=1`).

## Running things by hand

```bash
P=--project=arched-tape-447611-r0; R=--region=asia-south1
gcloud run jobs execute mb-<agent> $R $P --update-env-vars=MB_FORCE=1   # same as Run now (ignores pause)
gcloud run jobs executions list --job=mb-<agent> $R $P --limit=5
gcloud scheduler jobs list --location=asia-south1 $P --filter="name~mb-"
gcloud scheduler jobs pause|resume mb-<agent>-schedule --location=asia-south1 $P
```
Prefer the portal buttons, which also update the Firestore flags the runner checks.

## Troubleshooting

| Symptom | Where to look / fix |
|---|---|
| Agent shows **failed** | Portal → agent page → run's log. More detail: Cloud Run → Jobs → `mb-<agent>` → Logs. |
| Screener: "no fundamentals loaded" | The Fundamentals agent hasn't run or failed. Run it, or upload an export. |
| Fundamentals: many errors / 429 / Invalid Crumb | Yahoo rate limiting. Raise `MB_YAHOO_PAUSE` (e.g. 2) on the job and run again later. |
| AI agent: "CLAUDE_CODE_OAUTH_TOKEN is not set" | Secret missing or not mounted. Store it, then redeploy. |
| AI agent: authentication error | Token expired. Renew it (above). |
| AI agent: "daily LLM cap reached" | Wait for tomorrow (UTC date) or raise the cap on Settings. |
| AI agent: "reply was not valid JSON" | One-off model output issue. Run again. If it repeats, check the prompt in the agent module. |
| Everything paused, red banner | Kill switch tripped (budget at 100% or manual). Check Costs, then **Release kill switch** on Overview. |
| Portal 403 "Not authorised" | Email missing from `OWNER_EMAILS`. Add it and deploy. |
| Portal 502 "Empty Google Account OAuth client" | The project-wide IAP OAuth client was removed. Re-apply it with `gcloud iap settings set` (client id in `C:\Users\Admin\MultiBaggerClaudeAgentRun\iap-oauth.txt`). |
| No new filings since Thursday | Desktop task didn't run (PC off or Claude app closed). Upload manually. |
| Deploy: budget step error | gcloud budget updates are limited. Thresholds and labels are create-only (see above). |

## Costs

Expected monthly spend is well under the cap: jobs run minutes a week, the portal scales to zero, and
Firestore and Cloud Build stay mostly in their free tiers. The Costs page shows month-to-date spend from the
budget notifications. Vertex AI (if selected) is the only component that can grow quickly, and the daily
LLM cap bounds it.

## Ground rules

- No broker integration and no automated orders. Approvals only record your decision.
- Don't spoof browser headers or scrape sites that block automation. Use exports from accounts you own.
- Never commit `infra/config.env`, tokens or OAuth secrets. Secrets live in Secret Manager.
- Keep momentum (`mom-*`, `mom_*`) and TradeRank resources untouched from this folder.
