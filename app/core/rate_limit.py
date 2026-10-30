from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings

# Behind a reverse proxy run uvicorn with --proxy-headers so the client IP is the real one.
limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.rate_limit_enabled,
    storage_uri=settings.rate_limit_storage_uri,
)
