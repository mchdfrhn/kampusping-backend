from enum import StrEnum


class MessageStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CancelReason(StrEnum):
    PAID = "paid"
    OPTED_OUT = "opted_out"
    EXEMPT = "exempt"
    PHONE_INVALID = "phone_invalid"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    MANUAL = "manual"
    ESCALATED = "escalated"


class MessageKind(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


# A rule date may be at most this many days in the past and still be planned (a Friday rule date
# is still planned on Monday); a queued message older than this expires instead of being sent.
PLANNING_WINDOW_DAYS = 3

SYSTEM_ACTOR = "system"
