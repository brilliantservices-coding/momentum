#!/usr/bin/env bash
# One-shot, re-runnable deploy of the weekly momentum job and its sign-off portal.
# Runs anywhere gcloud is signed in: Cloud Shell, macOS/Linux, or Git Bash on Windows.
#   cp infra/config.example.env infra/config.env   # fill it in
#   bash infra/deploy.sh
# Everything it creates is named mom-* and labelled app=momentum. It shares the project's (default)
# Firestore database with other apps but only uses collections named mom_*.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source infra/config.env
: "${PROJECT_ID:?}" "${REGION:?}" "${BUDGET_AMOUNT:?}" "${BUDGET_CURRENCY:?}" "${OWNER_EMAILS:?}"
SCHEDULE="${SCHEDULE:-0 8 * * 6}"

G="gcloud --project=$PROJECT_ID --quiet"
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

PROJECT_NUMBER=$($G projects describe "$PROJECT_ID" --format='value(projectNumber)')
BILLING_ACCOUNT=$($G billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' | sed 's#billingAccounts/##')
AR="$REGION-docker.pkg.dev/$PROJECT_ID/mom"
IMAGE="$AR/app:$(date +%Y%m%d-%H%M%S)"
SA_JOB="mom-agents@$PROJECT_ID.iam.gserviceaccount.com"
SA_PORTAL="mom-portal@$PROJECT_ID.iam.gserviceaccount.com"
SA_SCHED="mom-scheduler@$PROJECT_ID.iam.gserviceaccount.com"
LABELS="app=momentum"

say "Enabling APIs"
gcloud components list --filter="id=beta" --format="value(state.name)" 2>/dev/null | grep -qi installed || \
  gcloud components install beta --quiet || true
$G services enable run.googleapis.com cloudscheduler.googleapis.com firestore.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com billingbudgets.googleapis.com \
  cloudbilling.googleapis.com iap.googleapis.com iam.googleapis.com

say "Firestore (shared default database; only mom_* collections are used)"
exists $G firestore databases describe --database='(default)' || \
  $G firestore databases create --database='(default)' --location="$REGION" --type=firestore-native

say "Service accounts"
for sa in mom-agents mom-portal mom-scheduler; do
  exists $G iam service-accounts describe "$sa@$PROJECT_ID.iam.gserviceaccount.com" || \
    $G iam service-accounts create "$sa" --display-name="momentum $sa"
done
sleep 5
bind() { $G projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$1" --role="$2" --condition=None >/dev/null; }
bind "$SA_JOB" roles/datastore.user
bind "$SA_PORTAL" roles/datastore.user
bind "$SA_SCHED" roles/run.invoker
$G iam service-accounts add-iam-policy-binding "$SA_JOB" --member="serviceAccount:$SA_PORTAL" \
  --role=roles/iam.serviceAccountUser >/dev/null

say "Container registry (keeps the 3 newest images)"
exists $G artifacts repositories describe mom --location="$REGION" || \
  $G artifacts repositories create mom --location="$REGION" --repository-format=docker --labels="$LABELS"
POLICY=$(mktemp)
cat > "$POLICY" <<'JSON'
[{"name":"keep-3","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":3}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"tagState":"any","olderThan":"1d"}}]
JSON
$G artifacts repositories set-cleanup-policies mom --location="$REGION" --policy="$POLICY" --no-dry-run >/dev/null

say "Building image $IMAGE"
$G builds submit --region="$REGION" --tag="$IMAGE" .

ENV="MOM_BACKEND=gcp,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,MOM_REGION=$REGION,MOM_JOB=mom-weekly"

say "Weekly job and schedule ($SCHEDULE IST)"
$G run jobs deploy mom-weekly --region="$REGION" --image="$IMAGE" --command=python --args="-m,mom.weekly" \
  --service-account="$SA_JOB" --set-env-vars="$ENV" --cpu=1 --memory=2Gi --max-retries=1 \
  --task-timeout=1800 --tasks=1 --labels="$LABELS" >/dev/null
# The portal's "Run now" button starts this job
$G run jobs add-iam-policy-binding mom-weekly --region="$REGION" --member="serviceAccount:$SA_PORTAL" \
  --role=roles/run.developer >/dev/null
URI="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/mom-weekly:run"
if exists $G scheduler jobs describe mom-weekly-schedule --location="$REGION"; then
  $G scheduler jobs update http mom-weekly-schedule --location="$REGION" --schedule="$SCHEDULE" \
    --time-zone=Asia/Kolkata --uri="$URI" --http-method=POST --oauth-service-account-email="$SA_SCHED" >/dev/null
else
  $G scheduler jobs create http mom-weekly-schedule --location="$REGION" --schedule="$SCHEDULE" \
    --time-zone=Asia/Kolkata --uri="$URI" --http-method=POST --oauth-service-account-email="$SA_SCHED" >/dev/null
fi

say "Portal (Cloud Run, scales to zero, Google sign-in via IAP)"
PORTAL_FLAGS=(--region="$REGION" --image="$IMAGE" --service-account="$SA_PORTAL" --no-allow-unauthenticated
  --min-instances=0 --max-instances=1 --cpu=1 --memory=512Mi --labels="$LABELS")
PENV="$ENV,MOM_ALLOWED_USERS=${OWNER_EMAILS//,/;},MOM_SCHEDULE=$SCHEDULE"
if $G run deploy mom-portal "${PORTAL_FLAGS[@]}" --iap --set-env-vars="$PENV" || \
   $G beta run deploy mom-portal "${PORTAL_FLAGS[@]}" --iap --set-env-vars="$PENV"; then
  $G beta services identity create --service=iap.googleapis.com >/dev/null 2>&1 || true
  $G run services add-iam-policy-binding mom-portal --region="$REGION" \
    --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker >/dev/null
  IFS=',' read -ra OWNERS <<< "$OWNER_EMAILS"
  for u in "${OWNERS[@]}"; do
    $G iap web add-iam-policy-binding --member="user:$u" --role=roles/iap.httpsResourceAccessor \
      --region="$REGION" --resource-type=cloud-run --service=mom-portal >/dev/null
  done
  PORTAL_MODE=iap
else
  echo "IAP could not be enabled; deploying private-only (open it with gcloud run services proxy)."
  $G run deploy mom-portal "${PORTAL_FLAGS[@]}" --set-env-vars="$PENV"
  IFS=',' read -ra OWNERS <<< "$OWNER_EMAILS"
  for u in "${OWNERS[@]}"; do
    $G run services add-iam-policy-binding mom-portal --region="$REGION" --member="user:$u" --role=roles/run.invoker >/dev/null
  done
  PORTAL_MODE=proxy
fi
PORTAL_URL=$($G run services describe mom-portal --region="$REGION" --format='value(status.url)')

say "Budget alert: $BUDGET_AMOUNT $BUDGET_CURRENCY/month on app=momentum spend (email at 50/90/100%)"
BUDGET_ID=$($G billing budgets list --billing-account="$BILLING_ACCOUNT" \
  --filter="displayName=mom-monthly-$PROJECT_ID" --format='value(name)' | head -1)
BFLAGS=(--budget-amount="${BUDGET_AMOUNT}${BUDGET_CURRENCY}" --threshold-rule=percent=0.5
  --threshold-rule=percent=0.9 --threshold-rule=percent=1.0)
if [ -n "$BUDGET_ID" ]; then
  $G billing budgets update "$BUDGET_ID" --billing-account="$BILLING_ACCOUNT" --filter-labels="$LABELS" "${BFLAGS[@]}" >/dev/null
else
  $G billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name="mom-monthly-$PROJECT_ID" \
    --filter-projects="projects/$PROJECT_ID" --filter-labels="$LABELS" "${BFLAGS[@]}" >/dev/null
fi

say "Done"
echo "Portal:   $PORTAL_URL  (mode: $PORTAL_MODE)"
echo "Job:      mom-weekly, schedule '$SCHEDULE' Asia/Kolkata"
echo "Image:    $IMAGE"
echo "First run: gcloud run jobs execute mom-weekly --project=$PROJECT_ID --region=$REGION --wait"
