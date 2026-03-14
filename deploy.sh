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
SERVER_NAME="${SERVER_NAME:-live.tongjicdi.com}"
HTTPS_HOSTNAME="${HTTPS_HOSTNAME:-live.tongjicdi.com}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@tongjicdi.com}"  # Let's Encrypt contact email (any valid address)

# Cloudflare DNS-01 credentials (required for domain certs; never commit these).
# CLOUDFLARE_EMAIL  = the email used to log in to cloudflare.com
# CLOUDFLARE_API_KEY = Global API Key from cloudflare.com/profile/api-tokens
# Set in your shell before running:
#   export CLOUDFLARE_EMAIL=you@example.com
#   export CLOUDFLARE_API_KEY=xxx
CLOUDFLARE_EMAIL="${CLOUDFLARE_EMAIL:-}"
CLOUDFLARE_API_KEY="${CLOUDFLARE_API_KEY:-}"

# Validate Cloudflare creds are present when deploying to a domain hostname.
if [[ -z "$CLOUDFLARE_EMAIL" || -z "$CLOUDFLARE_API_KEY" ]] && \
   ! [[ "$HTTPS_HOSTNAME" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: CLOUDFLARE_EMAIL and CLOUDFLARE_API_KEY must both be set for domain-based deployments." >&2
    echo "  export CLOUDFLARE_EMAIL=<cloudflare-account-email>" >&2
    echo "  export CLOUDFLARE_API_KEY=<cloudflare-global-api-key>" >&2
    exit 1
fi

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
apt-get install -y -qq python3 python3-pip python3-venv nginx rsync openssl certbot

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
run_remote bash -s -- "$APP_DIR" "$SERVICE_NAME" "$REMOTE_USER" "$SERVER_NAME" "$HTTPS_HOSTNAME" "$CERTBOT_EMAIL" "$CLOUDFLARE_API_KEY" "$CLOUDFLARE_EMAIL" <<'REMOTE'
set -Eeuo pipefail

APP_DIR="$1"
SERVICE_NAME="$2"
RUN_USER="$3"
SERVER_NAME="$4"
HTTPS_HOSTNAME="$5"
CERTBOT_EMAIL="$6"
CLOUDFLARE_API_KEY="$7"
CLOUDFLARE_EMAIL="$8"

# ── Systemd service ────────────────────────────────────────────────────────────
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
EnvironmentFile=-${APP_DIR}/.env

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

# ── TLS: choose Let's Encrypt (domain) or self-signed (bare IP) ───────────────
IS_IP=false
if [[ "$HTTPS_HOSTNAME" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    IS_IP=true
fi

if [[ "$IS_IP" == "true" ]]; then
    # ── Fallback: self-signed cert for bare-IP deployments ────────────────────
    echo "  HTTPS_HOSTNAME is an IP address – using self-signed certificate."
    SSL_DIR="/etc/nginx/ssl/${SERVICE_NAME}"
    CRT_FILE="${SSL_DIR}/fullchain.pem"
    KEY_FILE="${SSL_DIR}/privkey.pem"
    mkdir -p "$SSL_DIR"

    if [[ ! -s "$CRT_FILE" || ! -s "$KEY_FILE" ]]; then
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
subjectAltName = IP:${HTTPS_HOSTNAME}
EOF
        openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
            -keyout "$KEY_FILE" \
            -out "$CRT_FILE" \
            -config "${SSL_DIR}/openssl.cnf" >/dev/null 2>&1
        echo "  Self-signed certificate generated."
    else
        echo "  Existing self-signed certificate found, skipping generation."
    fi
else
    # ── Let's Encrypt via certbot Cloudflare DNS-01 challenge ─────────────────
    # DNS-01 requires no port-80 access; Cloudflare adds the TXT record for us.
    echo "  HTTPS_HOSTNAME is a domain – obtaining Let's Encrypt certificate via Cloudflare DNS-01."
    apt-get install -y -qq certbot python3-certbot-dns-cloudflare

    # Write Cloudflare credentials with strict permissions (never world-readable).
    CF_CREDS="/etc/letsencrypt/cloudflare.ini"
    cat > "$CF_CREDS" <<CF
dns_cloudflare_email = ${CLOUDFLARE_EMAIL}
dns_cloudflare_api_key = ${CLOUDFLARE_API_KEY}
CF
    chmod 600 "$CF_CREDS"

    # Issue (or renew) the certificate using DNS-01.
    certbot certonly \
        --dns-cloudflare \
        --dns-cloudflare-credentials "$CF_CREDS" \
        --dns-cloudflare-propagation-seconds 30 \
        -d "$HTTPS_HOSTNAME" \
        --email "$CERTBOT_EMAIL" \
        --agree-tos \
        --non-interactive \
        --keep-until-expiring

    CRT_FILE="/etc/letsencrypt/live/${HTTPS_HOSTNAME}/fullchain.pem"
    KEY_FILE="/etc/letsencrypt/live/${HTTPS_HOSTNAME}/privkey.pem"

    # nginx reload hook – runs after every successful auto-renewal.
    mkdir -p /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'HOOK'
#!/bin/bash
systemctl reload nginx
HOOK
    chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

    echo "  Let's Encrypt certificate obtained. Auto-renewal hook installed."
fi

# ── Full nginx config (HTTP → HTTPS redirect + reverse proxy) ─────────────────
cat > "/etc/nginx/sites-available/${SERVICE_NAME}" <<NGINX
server {
    listen 80;
    server_name ${SERVER_NAME};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${SERVER_NAME};

    ssl_certificate     ${CRT_FILE};
    ssl_certificate_key ${KEY_FILE};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache   shared:TLSCache:10m;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 3600;
    }
}
NGINX

ln -sfn "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
rm -f /etc/nginx/sites-enabled/default
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
echo "App URL: https://$HTTPS_HOSTNAME"
echo "Certificate: Let's Encrypt (auto-renews every 60 days)"
echo "Service: systemctl status $SERVICE_NAME"
echo "Logs:    journalctl -u $SERVICE_NAME -f"
