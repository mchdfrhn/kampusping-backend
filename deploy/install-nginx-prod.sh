#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITE_NAME="webapp-backend"
SOURCE="${SCRIPT_DIR}/nginx-prod.conf.example"
TARGET="/etc/nginx/sites-available/${SITE_NAME}"

if grep -q "api.example.com" "${SOURCE}"; then
  echo "Edit server_name in ${SOURCE} before installing it." >&2
  exit 1
fi

sudo cp "${SOURCE}" "${TARGET}"
sudo ln -sf "${TARGET}" "/etc/nginx/sites-enabled/${SITE_NAME}"
sudo nginx -t
sudo systemctl reload nginx
echo "==> Nginx site ${SITE_NAME} enabled (upstream 127.0.0.1:8093)"
