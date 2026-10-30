import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .constants import PROCESS_TIME_HEADER, REQUEST_ID_HEADER
from .logging import request_id_ctx

logger = logging.getLogger("app.access")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_UNLOGGED_PATHS = frozenset({"/health", "/ready"})

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request id, timing, access log and baseline security headers."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid4().hex
        token = request_id_ctx.set(request_id)
        start_time = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed = time.perf_counter() - start_time

            response.headers[REQUEST_ID_HEADER] = request_id
            response.headers[PROCESS_TIME_HEADER] = f"{elapsed:.6f}"
            for name, value in SECURITY_HEADERS.items():
                response.headers.setdefault(name, value)

            if request.url.path not in _UNLOGGED_PATHS:
                logger.info(
                    "%s %s -> %s",
                    request.method,
                    request.url.path,
                    response.status_code,
                    extra={
                        "duration_ms": round(elapsed * 1000, 2),
                        "status_code": response.status_code,
                    },
                )
            return response
        finally:
            request_id_ctx.reset(token)
