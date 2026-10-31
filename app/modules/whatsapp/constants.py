SIGNATURE_HEADER = "X-Signature"
# The gateway only sends small JSON events; anything bigger is refused before it is parsed.
MAX_WEBHOOK_BODY_BYTES = 64 * 1024

AUDIT_WHATSAPP_LOGOUT = "whatsapp.logout"
AUDIT_ENTITY_WHATSAPP = "whatsapp"

MSG_INVALID_SIGNATURE = "Tanda tangan tidak valid."
MSG_BODY_TOO_LARGE = "Isi permintaan terlalu besar."
MSG_INVALID_PAYLOAD = "Data yang dikirim tidak valid."
MSG_LOGOUT_FAILED = "Gagal memutuskan sesi WhatsApp."

EVENT_STATUS = "status"
