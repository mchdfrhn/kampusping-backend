import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import UTC, datetime

from .config import settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "request_id",
}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        payload.update(
            {key: value for key, value in record.__dict__.items() if key not in _RESERVED_ATTRS}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    level = "DEBUG" if settings.app_debug else settings.log_level.upper()
    formatter = "json" if settings.log_format == "json" else "text"
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": RequestIdFilter}},
            "formatters": {
                "text": {
                    "format": (
                        "%(asctime)s | %(levelname)s | %(name)s | %(request_id)s | %(message)s"
                    ),
                },
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": formatter,
                    "filters": ["request_id"],
                }
            },
            "root": {"level": level, "handlers": ["console"]},
            "loggers": {
                # Our middleware writes one access line per request.
                "uvicorn.access": {"level": "WARNING"},
                "sqlalchemy.engine": {"level": "INFO" if settings.database_echo else "WARNING"},
            },
        }
    )
