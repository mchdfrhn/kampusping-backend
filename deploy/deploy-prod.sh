#!/usr/bin/env bash
# Deploy HIS Standard Backend to production.
#
# Usage:
#   bash deploy/deploy-prod.sh
#   bash deploy/deploy-prod.sh --nginx
#   bash deploy/deploy-prod.sh --all
#   bash deploy/deploy-prod.sh --migrate
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PM2_CONFIG="${SCRIPT_DIR}/ecosystem.prod.config.js"
APP_NAME="his-standard-backend"
APP_PORT=8093
RUN_NGINX=false
RUN_MIGRATE=false

for arg in "$@"; do
  case "${arg}" in
    --nginx|--all) RUN_NGINX=true ;;
    --migrate) RUN_MIGRATE=true ;;
    -h|--help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
  esac
done

cd "${REPO_ROOT}"

if [[ ! -f ".env" ]]; then
  echo "Missing .env. Copy .env.example and fill production values." >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  python3.11 -m venv .venv
fi

echo "==> pip install"
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt || true

if [[ "${RUN_MIGRATE}" == true ]]; then
  echo "==> alembic upgrade"
  .venv/bin/alembic upgrade head
fi

echo "==> PM2 — ${APP_NAME}"
pm2 delete "${APP_NAME}" 2>/dev/null || true
pm2 start "${PM2_CONFIG}"
pm2 save

sleep 3
if ! curl -fsS "http://127.0.0.1:${APP_PORT}/health" | grep -q ok; then
  echo "ERROR: health check gagal" >&2
  pm2 logs "${APP_NAME}" --lines 40 --nostream || true
  exit 1
fi

if [[ "${RUN_NGINX}" == true ]]; then
  bash "${SCRIPT_DIR}/install-nginx-prod.sh"
fi

echo "==> Deploy backend selesai"
pm2 status "${APP_NAME}"
