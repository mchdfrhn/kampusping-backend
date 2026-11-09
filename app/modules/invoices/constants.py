from enum import StrEnum


class InvoiceStatus(StrEnum):
    UNPAID = "unpaid"
    PAID = "paid"


class InvoiceSource(StrEnum):
    """Where an invoice row came from; also the channel a payment was made through."""

    SIAKAD = "siakad"
    SIPEMA = "sipema"


AUDIT_INVOICE_STATUS_CHANGED = "invoice.status_changed"
AUDIT_INVOICE_DELETED_BY_FILE = "invoice.deleted_by_file"
AUDIT_ENTITY_INVOICE = "invoice"
