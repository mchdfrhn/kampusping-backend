#!/usr/bin/env bash
# Deploy the backend to a Linux host with PM2 (+ optional Nginx).
#
# Usage:
#   bash deploy/deploy-prod.sh                 # install deps, restart PM2
#   bash deploy/deploy-prod.sh --migrate       # also run Alembic migrations + seed
#   bash deploy/deploy-prod.sh --nginx         # also install the Nginx site
#   bash deploy/deploy-prod.sh --all           # --nginx (combine with --migrate as needed)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PM2_CONFIG="${SCRIPT_DIR}/ecosystem.prod.config.js"
APP_NAME="kampusping-backend"
WORKER_NAME="kampusping-worker"  # planner + sender loop; must stay a single instance
APP_PORT=8093
RUN_NGINX=false
RUN_MIGRATE=false

for arg in "$@"; do
  case "${arg}" in
    --nginx|--all) RUN_NGINX=true ;;
    --migrate) RUN_MIGRATE=true ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: ${arg}" >&2
      exit 1
      ;;
  esac
done

cd "${REPO_ROOT}"

if [[ ! -f ".env" ]]; then
  echo "Missing .env. Copy deploy/.env.production.example to .env and fill in real values." >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  python3.11 -m venv .venv
fi

echo "==> Installing dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if [[ "${RUN_MIGRATE}" == true ]]; then
  echo "==> Running migrations"
  .venv/bin/alembic upgrade head
  echo "==> Syncing permissions and system roles"
  .venv/bin/python -m app.cli seed
fi

echo "==> PM2: ${APP_NAME}, ${WORKER_NAME}"
pm2 delete "${APP_NAME}" 2>/dev/null || true
pm2 delete "${WORKER_NAME}" 2>/dev/null || true
pm2 start "${PM2_CONFIG}"
pm2 save

sleep 3
if ! curl -fsS "http://127.0.0.1:${APP_PORT}/ready" | grep -q ready; then
  echo "ERROR: readiness check failed (is the database reachable?)" >&2
  pm2 logs "${APP_NAME}" --lines 40 --nostream || true
  exit 1
fi

if [[ "${RUN_NGINX}" == true ]]; then
  bash "${SCRIPT_DIR}/install-nginx-prod.sh"
fi

echo "==> Backend deployed"
pm2 status "${APP_NAME}"
pm2 status "${WORKER_NAME}"
