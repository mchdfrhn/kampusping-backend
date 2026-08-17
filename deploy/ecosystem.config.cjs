/** PM2 — FastAPI development/staging. */
module.exports = {
  apps: [
    {
      name: 'his-standard-backend-dev',
      cwd: __dirname + '/..',
      script: '.venv/bin/uvicorn',
      args: 'app.main:app --host 127.0.0.1 --port 8090 --reload',
      interpreter: 'none',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      env: {
        APP_ENV: 'development',
        APP_DEBUG: 'true',
      },
    },
  ],
};
