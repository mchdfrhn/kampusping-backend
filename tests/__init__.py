import os

# Runs before any `app` import (pytest imports this package first): settings are read once at
# import time, so the test configuration has to be in the environment by then.
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_ENGINE": "sqlite",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256-0123456789",
        "RATE_LIMIT_ENABLED": "false",
        "BACKEND_CORS_ORIGINS": "http://localhost:3000",
        "LOG_LEVEL": "WARNING",
    }
)
