from datetime import datetime
from zoneinfo import ZoneInfo

APP_TIMEZONE = ZoneInfo("Asia/Jakarta")


def utc_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


def app_now() -> datetime:
    """Wall-clock time in Asia/Jakarta without tzinfo (MySQL/SingleStore DATETIME)."""
    return datetime.now(APP_TIMEZONE).replace(tzinfo=None)
