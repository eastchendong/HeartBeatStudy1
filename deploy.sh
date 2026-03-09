#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SERVER_DIR="$SCRIPT_DIR/server"

REMOTE_HOST="${REMOTE_HOST:-47.100.80.20}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_PORT="${REMOTE_PORT:-22}"
REMOTE_PASS="${REMOTE_PASS:-REMOTE_PASS_PLACEHOLDER}"

APP_DIR="${APP_DIR:-/opt/heartbeat}"
SERVICE_NAME="${SERVICE_NAME:-heartbeat}"
SERVER_NAME="${SERVER_NAME:-_}"
HTTPS_HOSTNAME="${HTTPS_HOSTNAME:-$REMOTE_HOST}"

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

echo "==> [1/5] Preparing remote host..."
run_remote bash -s -- "$APP_DIR" <<'REMOTE'
set -Eeuo pipefail

APP_DIR="$1"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nginx rsync openssl

mkdir -p "$APP_DIR"
mkdir -p "$APP_DIR/data/sessions"
REMOTE

echo "==> [2/5] Syncing latest server code..."
"${RSYNC_CMD[@]}" \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'venv/' \
    --exclude 'data/sessions/' \
    -e "${SSH_CMD[*]}" \
    "$LOCAL_SERVER_DIR/" "$REMOTE_TARGET:$APP_DIR/"

echo "==> [3/5] Installing Python dependencies..."
run_remote bash -s -- "$APP_DIR" <<'REMOTE'
set -Eeuo pipefail

APP_DIR="$1"
cd "$APP_DIR"

if [[ ! -x venv/bin/python ]]; then
    rm -rf venv
    python3 -m venv venv
fi

grep -vi '^bleak' requirements.txt > requirements_server.txt
venv/bin/python -m pip install --quiet --upgrade pip wheel
venv/bin/python -m pip install --quiet -r requirements_server.txt
rm -f requirements_server.txt
REMOTE

echo "==> [4/5] Restarting app service..."
run_remote bash -s -- "$APP_DIR" "$SERVICE_NAME" "$REMOTE_USER" "$SERVER_NAME" "$HTTPS_HOSTNAME" <<'REMOTE'
set -Eeuo pipefail

APP_DIR="$1"
SERVICE_NAME="$2"
RUN_USER="$3"
SERVER_NAME="$4"
HTTPS_HOSTNAME="$5"

SSL_DIR="/etc/nginx/ssl/${SERVICE_NAME}"
CRT_FILE="${SSL_DIR}/fullchain.pem"
KEY_FILE="${SSL_DIR}/privkey.pem"

mkdir -p "$SSL_DIR"

if [[ ! -s "$CRT_FILE" || ! -s "$KEY_FILE" ]]; then
    if [[ "$HTTPS_HOSTNAME" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        SAN_ENTRY="IP:${HTTPS_HOSTNAME}"
    else
        SAN_ENTRY="DNS:${HTTPS_HOSTNAME}"
    fi

    cat > "${SSL_DIR}/openssl.cnf" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = ${HTTPS_HOSTNAME}

[v3_req]
subjectAltName = ${SAN_ENTRY}
EOF

    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -keyout "$KEY_FILE" \
        -out "$CRT_FILE" \
        -config "${SSL_DIR}/openssl.cnf" >/dev/null 2>&1
fi

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=HeartBeat Study Flask Server
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/app.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

cat > "/etc/nginx/sites-available/${SERVICE_NAME}" <<NGINX
server {
        listen 80;
        server_name ${SERVER_NAME};

    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${SERVER_NAME};

    ssl_certificate ${CRT_FILE};
    ssl_certificate_key ${KEY_FILE};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:TLSCache:10m;

        location / {
                proxy_pass http://127.0.0.1:5000;
                proxy_http_version 1.1;
                proxy_set_header Host \$host;
                proxy_set_header X-Real-IP \$remote_addr;
                proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
                proxy_buffering off;
                proxy_cache off;
                proxy_read_timeout 3600;
        }
}
NGINX

ln -sfn "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
rm -f /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"
nginx -t
systemctl restart nginx
REMOTE

echo "==> [5/5] Verifying deployment..."
HTTP_STATUS="$(run_remote curl -ks -o /dev/null -w '%{http_code}' https://127.0.0.1/)"
echo "HTTP status: $HTTP_STATUS"

if [[ "$HTTP_STATUS" != "200" ]]; then
    echo "Deployment failed verification with HTTP $HTTP_STATUS" >&2
    exit 1
fi

echo
echo "Deployment complete."
echo "App URL: https://$REMOTE_HOST"
echo "Certificate host: $HTTPS_HOSTNAME"
echo "Note: this is a self-signed certificate. Browsers will warn unless you replace it with a trusted cert."
echo "Service: systemctl status $SERVICE_NAME"
echo "Logs: journalctl -u $SERVICE_NAME -f"
