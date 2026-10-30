import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from .constants import (
    APPLICATION_ERROR,
    FORBIDDEN,
    INTERNAL_SERVER_ERROR,
    NOT_FOUND,
    TOO_MANY_REQUESTS,
    UNAUTHORIZED,
    VALIDATION_ERROR,
)

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    status.HTTP_401_UNAUTHORIZED: UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN: FORBIDDEN,
    status.HTTP_404_NOT_FOUND: NOT_FOUND,
    status.HTTP_429_TOO_MANY_REQUESTS: TOO_MANY_REQUESTS,
}


class AppException(Exception):
    """Domain error rendered as the standard error envelope."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        code: str = APPLICATION_ERROR,
        details: Any | None = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details
        super().__init__(message)


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Every error leaves the API as ``{"error": {"code", "message", "details"}}``."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
        headers=headers,
    )


async def app_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppException):
        raise exc
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return error_response(exc.status_code, exc.code, exc.message, exc.details, headers)


async def validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    details = [
        {
            "loc": [str(part) for part in error["loc"]],
            "message": str(error["msg"]).removeprefix("Value error, "),
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        VALIDATION_ERROR,
        "Request validation failed.",
        details,
    )


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    code = _STATUS_CODES.get(exc.status_code, APPLICATION_ERROR)
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def rate_limit_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return error_response(
        status.HTTP_429_TOO_MANY_REQUESTS,
        TOO_MANY_REQUESTS,
        "Too many requests. Please try again later.",
        headers={"Retry-After": "60"},
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled application error", exc_info=exc)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        INTERNAL_SERVER_ERROR,
        "An unexpected error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
