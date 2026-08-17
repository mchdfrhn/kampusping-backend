/** PM2 — FastAPI production. Uvicorn listen 127.0.0.1:8093; Nginx proxy_pass ke port ini. */
module.exports = {
  apps: [
    {
      name: 'his-standard-backend',
      cwd: __dirname + '/..',
      script: '.venv/bin/uvicorn',
      args: 'app.main:app --host 127.0.0.1 --port 8093 --workers 2',
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
  ],
};
