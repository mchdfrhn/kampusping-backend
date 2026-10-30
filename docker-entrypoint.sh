#!/bin/sh
# Container entrypoint. Optionally prepares the database, then runs the given command.
#
#   RUN_MIGRATIONS=true   apply Alembic migrations and sync permissions/system roles
#   INITIAL_ADMIN_EMAIL + INITIAL_ADMIN_PASSWORD
#                         create the first administrator if that account does not exist yet
set -eu

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "==> alembic upgrade head"
  alembic upgrade head
  echo "==> seed permissions and system roles"
  python -m app.cli seed

  if [ -n "${INITIAL_ADMIN_EMAIL:-}" ] && [ -n "${INITIAL_ADMIN_PASSWORD:-}" ]; then
    # Exits non-zero when the account already exists; that is expected on later starts.
    python -m app.cli create-admin || true
  fi
fi

exec "$@"
