from datetime import datetime, time, timedelta

from app.shared.utils import ensure_utc

SETTINGS_ROW_ID = 1

DEFAULT_WINDOW_START = time(8, 0)
DEFAULT_WINDOW_END = time(17, 0)
# ISO weekdays: 1 = Monday ... 7 = Sunday.
DEFAULT_WORKING_DAYS = "1,2,3,4,5"  # comma-separated, as stored
DEFAULT_THROTTLE_MIN_S = 8
DEFAULT_THROTTLE_MAX_S = 20
DEFAULT_DAILY_CAP = 200
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_CONSECUTIVE_FAILURE_LIMIT = 5
DEFAULT_ESCALATION_OFFSET_DAYS = 14

MAX_ATTEMPTS_LIMIT = 10

AUDIT_SETTINGS_UPDATED = "settings.updated"
AUDIT_SETTINGS_RESUMED = "settings.resumed"
AUDIT_ENTITY_SETTINGS = "settings"


def worker_is_alive(last_seen_at: datetime | None, *, now: datetime, tick_seconds: int) -> bool:
    """True when the worker recorded a heartbeat recently enough to be considered running."""
    if last_seen_at is None:
        return False
    stale_after = timedelta(seconds=max(tick_seconds * 2 + 30, 150))
    return (ensure_utc(now) - ensure_utc(last_seen_at)) <= stale_after
