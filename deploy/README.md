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
