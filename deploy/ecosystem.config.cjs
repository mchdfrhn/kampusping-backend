/** PM2: FastAPI for a development/staging host (auto-reload). */
module.exports = {
  apps: [
    {
      name: 'kampusping-backend-dev',
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
