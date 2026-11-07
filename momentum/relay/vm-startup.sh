#!/bin/bash
# Startup script for mom-kite-vm: installs or updates the Kite relay from the public repo's main
# branch and runs it on the VM's internal IP, port 8080. Re-runs on every boot; to update after a
# merge, run: sudo google_metadata_script_runner startup
set -euo pipefail
MD=http://metadata.google.internal/computeMetadata/v1
md() { curl -sf -H "Metadata-Flavor: Google" "$MD/$1"; }
PROJECT=$(md project/project-id)
INTERNAL_IP=$(md instance/network-interfaces/0/ip)
API_KEY=$(md instance/attributes/kite-api-key || true)
REPO=$(md instance/attributes/relay-repo || echo https://github.com/brilliantservices-coding/momentum)
BRANCH=$(md instance/attributes/relay-branch || echo main)

apt-get update -qq && apt-get install -y -qq git python3-venv >/dev/null
id momrelay >/dev/null 2>&1 || useradd --system --home /opt/mom-relay --shell /usr/sbin/nologin momrelay
mkdir -p /opt/mom-relay /var/lib/mom-relay
if [ -d /opt/mom-relay/src/.git ]; then
  git -C /opt/mom-relay/src fetch -q origin "$BRANCH" && git -C /opt/mom-relay/src checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" /opt/mom-relay/src
fi
[ -x /opt/mom-relay/venv/bin/python ] || python3 -m venv /opt/mom-relay/venv
/opt/mom-relay/venv/bin/pip install -q -r /opt/mom-relay/src/momentum/relay/requirements.txt
chown -R momrelay:momrelay /opt/mom-relay /var/lib/mom-relay
chmod 700 /var/lib/mom-relay

cat > /etc/systemd/system/mom-relay.service <<UNIT
[Unit]
Description=Momentum Kite relay
After=network-online.target
[Service]
User=momrelay
Environment=GOOGLE_CLOUD_PROJECT=$PROJECT
Environment=KITE_API_KEY=$API_KEY
Environment=RELAY_STATE=/var/lib/mom-relay/session.json
WorkingDirectory=/opt/mom-relay/src/momentum/relay
ExecStart=/opt/mom-relay/venv/bin/uvicorn kite_relay:app --host $INTERNAL_IP --port 8080
Restart=always
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/mom-relay
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable mom-relay >/dev/null
systemctl restart mom-relay
