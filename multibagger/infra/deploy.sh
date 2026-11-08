#!/usr/bin/env bash
# One-shot, re-runnable deploy of the multibagger agents, portal, budget and kill switch.
# Runs anywhere gcloud is signed in: Cloud Shell, macOS/Linux, or Git Bash on Windows.
#   cp infra/config.example.env infra/config.env   # fill it in
#   bash infra/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source infra/config.env
: "${PROJECT_ID:?}" "${REGION:?}" "${BUDGET_AMOUNT:?}" "${BUDGET_CURRENCY:?}" "${OWNER_EMAILS:?}"

G="gcloud --project=$PROJECT_ID --quiet"
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

PROJECT_NUMBER=$($G projects describe "$PROJECT_ID" --format='value(projectNumber)')
BILLING_ACCOUNT=$($G billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' | sed 's#billingAccounts/##')
[ -n "$BILLING_ACCOUNT" ] || { echo "Billing is not enabled on $PROJECT_ID. Link a billing account first."; exit 1; }
BUCKET="mb-${PROJECT_ID}-raw"
AR="$REGION-docker.pkg.dev/$PROJECT_ID/mb"
TAG=$(date +%Y%m%d-%H%M%S)
IMAGE="$AR/app:$TAG"
SA_AGENTS="mb-agents@$PROJECT_ID.iam.gserviceaccount.com"
SA_PORTAL="mb-portal@$PROJECT_ID.iam.gserviceaccount.com"
SA_SCHED="mb-scheduler@$PROJECT_ID.iam.gserviceaccount.com"
SA_PUSH="mb-pubsub@$PROJECT_ID.iam.gserviceaccount.com"
# Every billable resource carries this label; the budget only counts labelled spend, so other apps
# in a shared project don't eat into this cap or trip the kill switch.
LABELS="app=multibagger"

say "Enabling APIs"
gcloud components list --filter="id=beta" --format="value(state.name)" 2>/dev/null | grep -qi installed || \
  gcloud components install beta --quiet || true
$G services enable run.googleapis.com cloudscheduler.googleapis.com firestore.googleapis.com \
  storage.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com pubsub.googleapis.com \
  billingbudgets.googleapis.com cloudbilling.googleapis.com iap.googleapis.com iam.googleapis.com \
  drive.googleapis.com aiplatform.googleapis.com secretmanager.googleapis.com

say "Firestore (native mode, free tier applies to the default database)"
exists $G firestore databases describe --database='(default)' || \
  $G firestore databases create --database='(default)' --location="$REGION" --type=firestore-native

say "Raw document bucket"
exists gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT_ID" || \
  gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT_ID" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$BUCKET" --project="$PROJECT_ID" --update-labels="$LABELS" >/dev/null

say "Service accounts and least-privilege roles"
for sa in mb-agents mb-portal mb-scheduler mb-pubsub; do
  exists $G iam service-accounts describe "$sa@$PROJECT_ID.iam.gserviceaccount.com" || \
    $G iam service-accounts create "$sa" --display-name="multibagger $sa"
done
sleep 5
bind() { $G projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$1" --role="$2" --condition=None >/dev/null; }
bind "$SA_AGENTS" roles/datastore.user
bind "$SA_PORTAL" roles/datastore.user
bind "$SA_PORTAL" roles/run.developer          # run / cancel agent jobs
bind "$SA_PORTAL" roles/cloudscheduler.admin   # pause / resume schedules
bind "$SA_SCHED" roles/run.invoker
bind "$SA_AGENTS" roles/aiplatform.user        # only used when Settings switches the LLM to Vertex
for sa in "$SA_AGENTS" "$SA_PORTAL"; do
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --project="$PROJECT_ID" \
    --member="serviceAccount:$sa" --role=roles/storage.objectAdmin >/dev/null
done
$G iam service-accounts add-iam-policy-binding "$SA_AGENTS" --member="serviceAccount:$SA_PORTAL" \
  --role=roles/iam.serviceAccountUser >/dev/null
# The research agents start their own follow-up run when stocks are left (job-level grant below).
$G iam service-accounts add-iam-policy-binding "$SA_AGENTS" --member="serviceAccount:$SA_AGENTS" \
  --role=roles/iam.serviceAccountUser >/dev/null

say "Container registry (keeps only the 3 newest images, to stay inside the 0.5 GB free tier)"
exists $G artifacts repositories describe mb --location="$REGION" || \
  $G artifacts repositories create mb --location="$REGION" --repository-format=docker --labels="$LABELS"
cat > /tmp/mb-cleanup.json <<'JSON'
[{"name":"keep-3","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":3}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"tagState":"any","olderThan":"1d"}}]
JSON
$G artifacts repositories set-cleanup-policies mb --location="$REGION" --policy=/tmp/mb-cleanup.json --no-dry-run >/dev/null

say "Building image $IMAGE (Cloud Build free tier)"
$G builds submit --region="$REGION" --tag="$IMAGE" .

say "LLM secrets (Secret Manager: mb-claude-oauth-token, mb-openai-api-key)"
# Store each once, without echoing it, e.g.:
#   read -rs T && printf '%s' "$T" | gcloud secrets create mb-openai-api-key --data-file=- --labels=app=multibagger && unset T
SECRETS=()
PORTAL_SECRETS=()
LLM_ENV=""
if exists $G secrets describe mb-claude-oauth-token; then
  $G secrets add-iam-policy-binding mb-claude-oauth-token --member="serviceAccount:$SA_AGENTS" \
    --role=roles/secretmanager.secretAccessor >/dev/null
  SECRETS+=("CLAUDE_CODE_OAUTH_TOKEN=mb-claude-oauth-token:latest")
  LLM_ENV="$LLM_ENV,MB_CLAUDE_TOKEN_SET=1"
  echo "Claude token found; mounted into the agent jobs"
else
  echo "Claude token not found; the subscription provider stays unavailable until it is stored"
fi
if exists $G secrets describe mb-openai-api-key; then
  for SA in "$SA_AGENTS" "$SA_PORTAL"; do
    $G secrets add-iam-policy-binding mb-openai-api-key --member="serviceAccount:$SA" \
      --role=roles/secretmanager.secretAccessor >/dev/null
  done
  SECRETS+=("OPENAI_API_KEY=mb-openai-api-key:latest")
  PORTAL_SECRETS+=("OPENAI_API_KEY=mb-openai-api-key:latest")  # the portal lists the account's models
  LLM_ENV="$LLM_ENV,MB_OPENAI_KEY_SET=1"
  echo "OpenAI key found; mounted into the agent jobs and the portal"
else
  echo "OpenAI key not found; the OpenAI provider stays unavailable until it is stored"
fi
join() { local IFS=,; echo "$*"; }
SECRET_FLAGS=(--clear-secrets)
[ ${#SECRETS[@]} -gt 0 ] && SECRET_FLAGS=(--set-secrets="$(join "${SECRETS[@]}")")
PORTAL_SECRET_FLAGS=(--clear-secrets)
[ ${#PORTAL_SECRETS[@]} -gt 0 ] && PORTAL_SECRET_FLAGS=(--set-secrets="$(join "${PORTAL_SECRETS[@]}")")

DRIVE_INBOX_ID="${DRIVE_INBOX_ID:-}"
COMMON_ENV="MB_DRIVE_INBOX_ID=$DRIVE_INBOX_ID,MB_BACKEND=gcp,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,MB_REGION=$REGION,MB_BUCKET=$BUCKET,MB_MONTHLY_BUDGET_INR=$BUDGET_AMOUNT$LLM_ENV"

say "Agent jobs and schedules"
# Reads id|schedule|default_enabled from agents.yaml without needing Python locally.
awk '/^  - id:/{if(id)print id"|"cron"|"en"|"to; id=$3; cron=""; en="true"; to="3600"}
     /^    schedule:/{match($0,/"[^"]+"/); cron=substr($0,RSTART+1,RLENGTH-2)}
     /^    timeout:/{to=$2}
     /^    default_enabled:/{en=$2} END{if(id)print id"|"cron"|"en"|"to}' agents.yaml |
while IFS='|' read -r AGENT CRON DEFAULT_ON TIMEOUT; do
  echo "-- $AGENT ($CRON)"
  $G run jobs deploy "mb-$AGENT" --region="$REGION" --image="$IMAGE" \
    --command=python --args="-m,mb.runner,$AGENT" --service-account="$SA_AGENTS" \
    --set-env-vars="$COMMON_ENV" "${SECRET_FLAGS[@]}" --cpu=1 --memory=1Gi --max-retries=0 --task-timeout="$TIMEOUT" --tasks=1 --labels="$LABELS" >/dev/null
  case "$AGENT" in extraction|analyst|redteam)
    $G run jobs add-iam-policy-binding "mb-$AGENT" --region="$REGION" --member="serviceAccount:$SA_AGENTS" \
      --role=roles/run.developer >/dev/null ;;  # only this job: start its own next run
  esac
  URI="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/mb-$AGENT:run"
  if exists $G scheduler jobs describe "mb-$AGENT-schedule" --location="$REGION"; then
    $G scheduler jobs update http "mb-$AGENT-schedule" --location="$REGION" --schedule="$CRON" \
      --time-zone=Asia/Kolkata --uri="$URI" --http-method=POST --oauth-service-account-email="$SA_SCHED" >/dev/null
  else
    $G scheduler jobs create http "mb-$AGENT-schedule" --location="$REGION" --schedule="$CRON" \
      --time-zone=Asia/Kolkata --uri="$URI" --http-method=POST --oauth-service-account-email="$SA_SCHED" >/dev/null
    # Agents shipped switched off start paused; after that the portal's pause/resume owns the state.
    if [ "$DEFAULT_ON" = false ]; then
      $G scheduler jobs pause "mb-$AGENT-schedule" --location="$REGION" >/dev/null
      echo "   created paused (resume it from the portal)"
    fi
  fi
done
# (comma lists are passed with ';' because gcloud splits --set-env-vars on commas; the app accepts both)

say "Portal (Cloud Run, scales to zero, Google sign-in via IAP)"
PORTAL_FLAGS=(--region="$REGION" --image="$IMAGE" --service-account="$SA_PORTAL" --no-allow-unauthenticated
  --min-instances=0 --max-instances=2 --cpu=1 --memory=512Mi --concurrency=40 --labels="$LABELS" "${PORTAL_SECRET_FLAGS[@]}")
IAP_ENV="$COMMON_ENV,MB_ROLE=portal,MB_ALLOWED_USERS=${OWNER_EMAILS//,/;}"
if $G run deploy mb-portal "${PORTAL_FLAGS[@]}" --iap --set-env-vars="$IAP_ENV" || \
   $G beta run deploy mb-portal "${PORTAL_FLAGS[@]}" --iap --set-env-vars="$IAP_ENV"; then
  $G beta services identity create --service=iap.googleapis.com >/dev/null 2>&1 || true
  $G run services add-iam-policy-binding mb-portal --region="$REGION" \
    --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker >/dev/null
  IFS=',' read -ra OWNERS <<< "$OWNER_EMAILS"
  for u in "${OWNERS[@]}"; do
    $G iap web add-iam-policy-binding --member="user:$u" --role=roles/iap.httpsResourceAccessor \
      --region="$REGION" --resource-type=cloud-run --service=mb-portal >/dev/null
  done
  PORTAL_MODE=iap
else
  echo "IAP could not be enabled on this project; deploying private-only (open it with gcloud run services proxy)."
  $G run deploy mb-portal "${PORTAL_FLAGS[@]}" --set-env-vars="$COMMON_ENV,MB_ROLE=portal"
  IFS=',' read -ra OWNERS <<< "$OWNER_EMAILS"
  for u in "${OWNERS[@]}"; do
    $G run services add-iam-policy-binding mb-portal --region="$REGION" --member="user:$u" --role=roles/run.invoker >/dev/null
  done
  PORTAL_MODE=proxy
fi
PORTAL_URL=$($G run services describe mb-portal --region="$REGION" --format='value(status.url)')

say "Budget guard (private service called by Pub/Sub)"
$G run deploy mb-guard --region="$REGION" --image="$IMAGE" --service-account="$SA_PORTAL" \
  --no-allow-unauthenticated --min-instances=0 --max-instances=1 --cpu=1 --memory=512Mi --labels="$LABELS" \
  --set-env-vars="$COMMON_ENV,MB_ROLE=guard" >/dev/null
GUARD_URL=$($G run services describe mb-guard --region="$REGION" --format='value(status.url)')
$G run services add-iam-policy-binding mb-guard --region="$REGION" --member="serviceAccount:$SA_PUSH" \
  --role=roles/run.invoker >/dev/null
exists $G pubsub topics describe mb-budget || $G pubsub topics create mb-budget
if exists $G pubsub subscriptions describe mb-budget-guard; then
  $G pubsub subscriptions update mb-budget-guard --push-endpoint="$GUARD_URL/hooks/budget" \
    --push-auth-service-account="$SA_PUSH" >/dev/null
else
  $G pubsub subscriptions create mb-budget-guard --topic=mb-budget --push-endpoint="$GUARD_URL/hooks/budget" \
    --push-auth-service-account="$SA_PUSH" --ack-deadline=60 --message-retention-duration=1d >/dev/null
fi

say "Monthly budget: $BUDGET_AMOUNT $BUDGET_CURRENCY with alerts at 50/90/100% and the kill switch"
BUDGET_ID=$($G billing budgets list --billing-account="$BILLING_ACCOUNT" \
  --filter="displayName=mb-monthly-$PROJECT_ID" --format='value(name)' | head -1)
BUDGET_FLAGS=(--budget-amount="${BUDGET_AMOUNT}${BUDGET_CURRENCY}"
  --notifications-rule-pubsub-topic="projects/$PROJECT_ID/topics/mb-budget")
# On re-runs only the amount and topic are refreshed: `budgets update` rejects --threshold-rule, and
# with --filter-labels (gcloud 575) it crashes with YAMLStreamError. The label filter and the
# 50/90/100% thresholds set at creation stay in place.
if [ -n "$BUDGET_ID" ]; then
  $G billing budgets update "$BUDGET_ID" --billing-account="$BILLING_ACCOUNT" "${BUDGET_FLAGS[@]}" >/dev/null
else
  $G billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name="mb-monthly-$PROJECT_ID" \
    --filter-projects="projects/$PROJECT_ID" --filter-labels="$LABELS" "${BUDGET_FLAGS[@]}" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 >/dev/null
fi

say "Done"
echo "Portal:  $PORTAL_URL  (mode: $PORTAL_MODE)"
if [ "$PORTAL_MODE" = proxy ]; then echo "Open it with: gcloud run services proxy mb-portal --project=$PROJECT_ID --region=$REGION  then visit http://localhost:8080"; fi
echo "Image:   $IMAGE"
echo "Budget:  $BUDGET_AMOUNT $BUDGET_CURRENCY/month on billing account $BILLING_ACCOUNT"
