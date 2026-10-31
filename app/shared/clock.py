"""Injectable clock and local-time helpers.

Timestamps are stored in UTC; business rules (sending window, "today", working days) are evaluated
in ``settings.app_timezone``. Services take a ``Clock`` so tests can control time.
"""

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

Clock = Callable[[], datetime]


def local_now(now: datetime, tz_name: str) -> datetime:
    """``now`` converted to the zone ``tz_name``. ``now`` must be timezone-aware."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ZoneInfo(tz_name))


def local_today(now: datetime, tz_name: str) -> date:
    """The calendar date at ``now`` in the zone ``tz_name``."""
    return local_now(now, tz_name).date()
