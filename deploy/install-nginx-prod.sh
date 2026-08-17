#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITE_NAME="his-standard-backend"
SOURCE="${SCRIPT_DIR}/nginx-prod.conf.example"
TARGET="/etc/nginx/sites-available/${SITE_NAME}"

sudo cp "${SOURCE}" "${TARGET}"
sudo ln -sf "${TARGET}" "/etc/nginx/sites-enabled/${SITE_NAME}"
sudo nginx -t
sudo systemctl reload nginx
echo "==> Nginx ${SITE_NAME} aktif. Uvicorn listen 127.0.0.1:8093"
