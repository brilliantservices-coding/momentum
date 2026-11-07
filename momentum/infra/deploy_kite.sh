#!/usr/bin/env bash
# Sets up the Zerodha (Kite Connect) link: the relay on mom-kite-vm and the portal's private path
# to it. Re-runnable. Run after infra/deploy.sh, once the Kite API secret is in Secret Manager:
#   printf '%s' 'YOUR_API_SECRET' | gcloud secrets create mom-kite-api-secret --data-file=- \
#     --replication-policy=automatic --labels=app=momentum --project=PROJECT_ID
# Needs KITE_API_KEY in infra/config.env (the API key is not secret; the API secret is).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source infra/config.env
: "${PROJECT_ID:?}" "${REGION:?}" "${KITE_API_KEY:?set KITE_API_KEY in infra/config.env}"
ZONE="${KITE_VM_ZONE:-asia-south1-a}"
VM=mom-kite-vm
G="gcloud --project=$PROJECT_ID --quiet"
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }
PROJECT_NUMBER=$($G projects describe "$PROJECT_ID" --format='value(projectNumber)')
VM_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
SA_PORTAL="mom-portal@$PROJECT_ID.iam.gserviceaccount.com"

say "Secrets"
$G services enable secretmanager.googleapis.com
exists $G secrets describe mom-kite-api-secret || { echo "Create the mom-kite-api-secret secret first (see the top of this file)."; exit 1; }
if ! exists $G secrets describe mom-relay-token; then  # random token shared by the portal and the relay; never printed
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | $G secrets create mom-relay-token --data-file=- \
    --replication-policy=automatic --labels=app=momentum >/dev/null
fi
for s in mom-kite-api-secret mom-relay-token; do
  $G secrets add-iam-policy-binding "$s" --member="serviceAccount:$VM_SA" --role=roles/secretmanager.secretAccessor >/dev/null
done
$G secrets add-iam-policy-binding mom-relay-token --member="serviceAccount:$SA_PORTAL" \
  --role=roles/secretmanager.secretAccessor >/dev/null

say "Relay on $VM (startup script installs it from the repo's main branch)"
$G compute instances add-tags "$VM" --zone="$ZONE" --tags=mom-kite >/dev/null
$G compute instances add-metadata "$VM" --zone="$ZONE" \
  --metadata=kite-api-key="$KITE_API_KEY",relay-branch="${RELAY_BRANCH:-main}" \
  --metadata-from-file=startup-script=relay/vm-startup.sh >/dev/null
$G compute ssh "$VM" --zone="$ZONE" --tunnel-through-iap --command="sudo google_metadata_script_runner startup && systemctl is-active mom-relay"
VM_IP=$($G compute instances describe "$VM" --zone="$ZONE" --format='value(networkInterfaces[0].networkIP)')
SUBNET_RANGE=$($G compute networks subnets describe default --region="$REGION" --format='value(ipCidrRange)')

say "Firewall: relay port open only to the $REGION subnet ($SUBNET_RANGE)"
if exists $G compute firewall-rules describe mom-allow-relay; then
  $G compute firewall-rules update mom-allow-relay --source-ranges="$SUBNET_RANGE" >/dev/null
else
  $G compute firewall-rules create mom-allow-relay --network=default --direction=INGRESS --action=ALLOW \
    --rules=tcp:8080 --source-ranges="$SUBNET_RANGE" --target-tags=mom-kite >/dev/null
fi

say "Portal: private route to the relay (Direct VPC egress, no connector cost)"
$G run services update mom-portal --region="$REGION" --network=default --subnet=default \
  --vpc-egress=private-ranges-only --update-secrets=MOM_RELAY_TOKEN=mom-relay-token:latest \
  --update-env-vars="MOM_RELAY_URL=http://$VM_IP:8080,MOM_KITE_API_KEY=$KITE_API_KEY" >/dev/null
PORTAL_URL=$($G run services describe mom-portal --region="$REGION" --format='value(status.url)')

say "Done"
echo "Relay:    http://$VM_IP:8080 on $VM (internal only)"
echo "Set the Kite app's Redirect URL (developers.kite.trade > My apps > Momentum) to:"
echo "          $PORTAL_URL/kite/callback"
