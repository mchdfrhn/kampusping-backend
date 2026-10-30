from datetime import UTC, datetime


def utc_now() -> datetime:
    """Timezone-aware current time. Timestamps are stored and exchanged in UTC."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes (returned by drivers that drop tzinfo, e.g. SQLite/MySQL)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def like_pattern(term: str) -> str:
    """Escape LIKE wildcards in user input and wrap it for a contains-search (escape is ``\\``)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
