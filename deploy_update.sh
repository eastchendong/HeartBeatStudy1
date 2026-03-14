#!/usr/bin/env bash
# deploy_update.sh – lightweight deploy: sync code & restart service.
# Assumes HTTPS/nginx/certs are already configured (by the full deploy.sh).

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SERVER_DIR="$SCRIPT_DIR/server"

REMOTE_HOST="${REMOTE_HOST:-47.100.80.20}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_PORT="${REMOTE_PORT:-22}"
REMOTE_PASS="${REMOTE_PASS:-REMOTE_PASS_PLACEHOLDER}"

APP_DIR="${APP_DIR:-/opt/heartbeat}"
SERVICE_NAME="${SERVICE_NAME:-heartbeat}"

if [[ ! -d "$LOCAL_SERVER_DIR" ]]; then
    echo "Missing local server directory: $LOCAL_SERVER_DIR" >&2
    exit 1
fi

for cmd in ssh rsync; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Missing required command: $cmd" >&2
        exit 1
    fi
done

SSH_OPTS=(
    -p "$REMOTE_PORT"
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
)

SSH_CMD=(ssh "${SSH_OPTS[@]}")
RSYNC_CMD=(rsync -az --delete)

if [[ -n "$REMOTE_PASS" ]]; then
    if ! command -v sshpass >/dev/null 2>&1; then
        echo "sshpass is required when REMOTE_PASS is set" >&2
        exit 1
    fi
    SSH_CMD=(sshpass -p "$REMOTE_PASS" ssh "${SSH_OPTS[@]}")
    RSYNC_CMD=(sshpass -p "$REMOTE_PASS" rsync -az --delete)
fi

REMOTE_TARGET="$REMOTE_USER@$REMOTE_HOST"

run_remote() {
    "${SSH_CMD[@]}" "$REMOTE_TARGET" "$@"
}

echo "==> [1/3] Syncing latest server code..."
"${RSYNC_CMD[@]}" \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'venv/' \
    --exclude 'data/sessions/' \
    --exclude '.env' \
    -e "${SSH_CMD[*]}" \
    "$LOCAL_SERVER_DIR/" "$REMOTE_TARGET:$APP_DIR/"

echo "==> [2/3] Installing Python dependencies (if changed)..."
run_remote bash -s -- "$APP_DIR" <<'REMOTE'
set -Eeuo pipefail
APP_DIR="$1"
cd "$APP_DIR"
if [[ ! -x venv/bin/python ]]; then
    python3 -m venv venv
fi
grep -vi '^bleak' requirements.txt > requirements_server.txt
venv/bin/python -m pip install --quiet --upgrade pip wheel
venv/bin/python -m pip install --quiet -r requirements_server.txt
rm -f requirements_server.txt
REMOTE

echo "==> [3/3] Restarting service..."
run_remote systemctl restart "$SERVICE_NAME"

# Quick health check
sleep 2
HTTP_STATUS="$(run_remote curl -ks -o /dev/null -w '%{http_code}' https://127.0.0.1/)"
echo "HTTP status: $HTTP_STATUS"

if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "WARNING: service returned HTTP $HTTP_STATUS" >&2
    echo "Check logs: journalctl -u $SERVICE_NAME -f" >&2
    exit 1
fi

echo
echo "Update deployed successfully."
echo "Service: systemctl status $SERVICE_NAME"
echo "Logs:    journalctl -u $SERVICE_NAME -f"
