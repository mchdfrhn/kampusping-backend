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

# --- Send phase ----------------------------------------------------------------------------

# Statuses of a message that reached (or is reaching) the provider; they count towards the daily
# cap and towards AB-03 (one automatic message per student per day).
SENT_STATUSES = (
    MessageStatus.SENDING.value,
    MessageStatus.SENT.value,
    MessageStatus.DELIVERED.value,
    MessageStatus.READ.value,
)
# WhatsApp rejects longer texts.
MAX_BODY_LENGTH = 4096
# Back-off before attempt n + 1 after the n-th failed attempt; the last value repeats.
RETRY_DELAYS_MINUTES = (5, 15, 45)
# A message left in ``sending`` longer than this belonged to a worker that died.
STALE_SENDING_MINUTES = 10
CIRCUIT_BREAKER_REASON = "circuit_breaker"
MAX_ERROR_LENGTH = 200

ERROR_DELETED = "Mahasiswa atau tagihan sudah dihapus."
ERROR_TOO_LONG = "Pesan terlalu panjang."
ERROR_NOT_ON_WHATSAPP = "Nomor tidak terdaftar di WhatsApp."
ERROR_REJECTED = "Permintaan ditolak gateway."
ERROR_INTERNAL = "Terjadi kesalahan internal saat memproses pesan."

# --- Message log and delivery receipts -----------------------------------------------------

AUDIT_MESSAGE_CANCELLED = "message.cancelled"
AUDIT_ENTITY_MESSAGE = "message"
WEBHOOK_ACTOR = "whatsapp-webhook"
ERROR_DELIVERY_FAILED = "Gagal terkirim ke WhatsApp."
MSG_NOT_FOUND = "Pesan tidak ditemukan."
MSG_ONLY_QUEUED_CANCELLABLE = "Hanya pesan berstatus antre yang dapat dibatalkan."
