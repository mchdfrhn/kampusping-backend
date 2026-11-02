# Deploying the backend

Production layout: PM2 runs uvicorn on `127.0.0.1:8093`, Nginx proxies port 80 to it, and TLS
is terminated in front (load balancer or Cloudflare).

```bash
cp deploy/.env.production.example .env      # then fill in SECRET_KEY, DATABASE_URL, CORS, ...
chmod +x deploy/*.sh
bash deploy/deploy-prod.sh --migrate --all  # first deploy: migrate, seed, PM2, Nginx
python -m app.cli create-admin              # run once inside the venv; creates the first admin
```

Later releases: `git pull && bash deploy/deploy-prod.sh --migrate`.

- Edit `server_name` in `nginx-prod.conf.example` first; the installer refuses the placeholder.
- The app **refuses to start** with `APP_ENV=production` if `SECRET_KEY` is weak, `COOKIE_SECURE`
  is false, debug is on, or SQLite is configured.
- Pick a distinct port per project/host; it appears in `ecosystem.prod.config.js`,
  `nginx-prod.conf.example` and `deploy-prod.sh`.
- Prefer containers? See the repository root `docker-compose.yml`.

## Deploy order

The four processes belong to one release. Roll them out in this order:

1. **Migrate** the database (`--migrate` here).
2. **API** (`kampusping-backend`, 127.0.0.1:8093).
3. **Worker** (`kampusping-worker`).
4. **Gateway** (`kampusping-wa-gateway`, 127.0.0.1:8095, see `kampusping-wa-gateway/deploy/`).
5. **Web** (`kampusping-frontend`, 127.0.0.1:8094, see `kampusping-frontend/deploy/`).

`deploy-prod.sh` handles steps 1 to 3 in one run: it migrates, then restarts `kampusping-backend`
and `kampusping-worker` with PM2.

**Never run two workers.** `kampusping-worker` must stay at exactly one instance (`instances: 1`,
fork mode, never cluster, never a second copy on another host): the one-message-per-student-per-day
rule (AB-03) is only safe with a single sender. A single `pm2 start` of the config creates exactly
one. Do not add `-i`, do not `pm2 scale` it, and do not start the same worker by hand (`python -m app.cli
run-worker`) while PM2 runs it; use `run-worker --once` only with the PM2 worker stopped.
