/**
 * PM2: FastAPI in production.
 * Uvicorn listens on 127.0.0.1:8093 only; Nginx proxies to it.
 *
 * --proxy-headers makes uvicorn trust X-Forwarded-* from the local Nginx so request.client
 * (used by the login rate limiter and refresh-token audit) is the real client IP.
 * With several workers the default in-memory rate limiter is per worker; point
 * RATE_LIMIT_STORAGE_URI at a shared store (e.g. redis://) for a global limit.
 */
module.exports = {
  apps: [
    {
      name: 'kampusping-backend',
      cwd: __dirname + '/..',
      script: '.venv/bin/uvicorn',
      args: 'app.main:app --host 127.0.0.1 --port 8093 --workers 2 --proxy-headers --forwarded-allow-ips 127.0.0.1',
      interpreter: 'none',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      env: {
        APP_ENV: 'production',
        APP_DEBUG: 'false',
      },
    },
    {
      // The planner + sender loop (ADR 0003). It MUST run as exactly one instance: the
      // one-message-per-student-per-day check (AB-03) is only safe with a single sender.
      // Never raise `instances` and never use cluster mode for this app.
      name: 'kampusping-worker',
      cwd: __dirname + '/..',
      script: '.venv/bin/python',
      args: '-m app.cli run-worker',
      interpreter: 'none',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '512M',
      env: {
        APP_ENV: 'production',
        APP_DEBUG: 'false',
      },
    },
  ],
};
