#!/usr/bin/env bash
# Runs ON the Azure VM (Ubuntu 24.04) as root. Called by deploy/deploy.sh — safe to re-run.
#   sudo bash setup_vm.sh [site-address]
# site-address: ":80" (plain HTTP on the public IP, default) or a DNS name such as
# "exposureshield.eastus.cloudapp.azure.com" (Caddy then gets an HTTPS cert automatically).
set -euo pipefail

SITE="${1:-:80}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"   # uploaded copy of the repo
APP=/opt/exposureshield
USER_NAME=exposureshield

echo "==> Swap (B1s has 1 GiB RAM; pip builds and ~230 concurrent checks need headroom)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
if ! dpkg -s python3-venv python3-pip caddy rsync >/dev/null 2>&1; then
  apt-get update -y -q
  apt-get install -y -q python3-venv python3-pip caddy rsync
fi

echo "==> App files -> $APP"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$APP"
rsync -a --delete --exclude '.venv' --exclude '__pycache__' "$SRC/backend/" "$APP/backend/"
rsync -a --delete "$SRC/frontend/" "$APP/frontend/"
mkdir -p "$APP/data"
[ -d "$SRC/data" ] && rsync -a "$SRC/data/" "$APP/data/"

echo "==> Python venv (Ubuntu 24.04 ships Python 3.12 — same as local dev)"
[ -d "$APP/backend/.venv" ] || python3 -m venv "$APP/backend/.venv"
"$APP/backend/.venv/bin/pip" install -q --upgrade pip
"$APP/backend/.venv/bin/pip" install -q -r "$APP/backend/requirements.txt"
chown -R "$USER_NAME:$USER_NAME" "$APP"

echo "==> Secrets file (edit to add HIBP_API_KEY; never committed)"
if [ ! -f /etc/exposureshield.env ]; then
  printf 'HIBP_API_KEY=\n' > /etc/exposureshield.env
fi
chown root:"$USER_NAME" /etc/exposureshield.env && chmod 640 /etc/exposureshield.env

echo "==> systemd service"
cat > /etc/systemd/system/exposureshield.service <<EOF
[Unit]
Description=ExposureShield API
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP/backend
EnvironmentFile=/etc/exposureshield.env
# Loopback only — Caddy is the public entry point. No access log: we don't record who scans what.
# --loop asyncio: under uvloop only ~50-80 of 234 sites answered on this VM (vs ~144 with asyncio).
ExecStart=$APP/backend/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --loop asyncio --no-access-log
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now exposureshield
systemctl restart exposureshield

echo "==> Caddy: serves frontend/ and proxies /api/* to the app"
cat > /etc/caddy/Caddyfile <<EOF
$SITE {
	encode gzip
	reverse_proxy /api/* 127.0.0.1:8000
	root * $APP/frontend
	file_server
}
EOF
systemctl enable caddy
systemctl reload caddy || systemctl restart caddy

echo "==> Health check"
for _ in $(seq 1 30); do
  curl -fs http://127.0.0.1:8000/api/health && echo && break
  sleep 1
done
systemctl is-active --quiet exposureshield || { journalctl -u exposureshield -n 50 --no-pager; exit 1; }
echo "==> Done. Site: $SITE"
