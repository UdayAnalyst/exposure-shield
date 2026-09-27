#!/usr/bin/env bash
# Runs on your Mac. Uploads the code to the VM and runs setup_vm.sh there. Re-run to redeploy.
#   ./deploy/deploy.sh azureuser@<public-ip> [site-address]
# Examples:
#   ./deploy/deploy.sh azureuser@20.1.2.3
#   ./deploy/deploy.sh azureuser@20.1.2.3 exposureshield.eastus.cloudapp.azure.com   # HTTPS
# Set SSH_KEY=~/.ssh/other_key to use a different key (default: ~/.ssh/exposureshield_azure if present).
set -euo pipefail

TARGET="${1:?usage: $0 user@host [site-address]}"
SITE="${2:-:80}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${SSH_KEY:-$HOME/.ssh/exposureshield_azure}"

# One SSH connection reused for every step, so a password (if used) is asked only once.
SSH_OPTS=(-o ControlMaster=auto -o ControlPath="/tmp/es-ssh-%r@%h" -o ControlPersist=60 -o StrictHostKeyChecking=accept-new)
[ -f "$KEY" ] && SSH_OPTS+=(-i "$KEY")

echo "==> Uploading to $TARGET:~/exposureshield"
rsync -az --delete -e "ssh ${SSH_OPTS[*]}" \
  --exclude '.venv' --exclude '__pycache__' --exclude '.DS_Store' --exclude '.env' \
  "$ROOT/backend" "$ROOT/frontend" "$ROOT/deploy" "$TARGET:exposureshield/"
[ -d "$ROOT/data" ] && rsync -az -e "ssh ${SSH_OPTS[*]}" "$ROOT/data" "$TARGET:exposureshield/"

echo "==> Running setup on the VM"
ssh -t "${SSH_OPTS[@]}" "$TARGET" "sudo bash ~/exposureshield/deploy/setup_vm.sh '$SITE'"

HOST="${TARGET#*@}"
[ "$SITE" = ":80" ] && echo "==> Live at http://$HOST/api/health" || echo "==> Live at https://$SITE/api/health"
