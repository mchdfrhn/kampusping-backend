from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .api.v1.router import router as api_v1_router
from .core.config import settings
from .core.constants import CSRF_HEADER, REQUEST_ID_HEADER
from .core.exceptions import register_exception_handlers
from .core.logging import configure_logging
from .core.middleware import RequestContextMiddleware
from .core.rate_limit import limiter
from .db import models as _models  # noqa: F401 - registers every table on Base.metadata
from .db.session import get_engine


def create_app() -> FastAPI:
    configure_logging()
    docs_enabled = not settings.is_production

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="REST API with JWT authentication and role-based access control.",
        debug=settings.app_debug,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.limiter = limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=True,  # the refresh cookie must be accepted cross-origin in development
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", CSRF_HEADER, REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Content-Disposition"],
    )
    # Added last = outermost, so request ids/security headers also cover CORS and error responses.
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    async def ready(response: Response) -> dict[str, str]:
        """Readiness: the database answers."""
        try:
            async with get_engine().connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unavailable"}
        return {"status": "ready"}

    return app


app = create_app()
