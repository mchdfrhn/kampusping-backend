"""What the preview shows beyond counts: the new, changed and suspected-paid invoices.

Stored as JSON on the batch until it is applied or discarded. No phone numbers: a screen that
reviews invoices needs the NIM and name, not the contact.
"""

from dataclasses import asdict
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from app.modules.students.model import Student

from .absent import SuspectedPaidResult
from .diff import Classification

Details = dict[str, list[dict[str, Any]]]


class DetailCategory(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    SUSPECTED_PAID = "suspected_paid"


def _json_value(value: object) -> object:
    return value.isoformat() if isinstance(value, date | datetime) else value


def build_details(
    classification: Classification,
    students: dict[str, Student],
    suspected: SuspectedPaidResult | None,
) -> Details:
    def name_of(nim: str, name: str | None) -> str | None:
        return name or (students[nim].name if nim in students else None)

    return {
        DetailCategory.NEW.value: [
            {
                "invoice_code": row.invoice_code,
                "nim": row.nim,
                "name": name_of(row.nim, row.name),
                "semester": row.semester,
                "amount": row.amount,
            }
            for row in classification.new
        ],
        DetailCategory.CHANGED.value: [
            {
                "invoice_id": changed.invoice.id,
                "invoice_code": changed.row.invoice_code,
                "nim": changed.row.nim,
                "name": name_of(changed.row.nim, changed.row.name),
                "semester": changed.row.semester,
                "fields": {
                    field: [_json_value(old), _json_value(new)]
                    for field, (old, new) in changed.fields.items()
                },
            }
            for changed in classification.changed
        ],
        DetailCategory.SUSPECTED_PAID.value: [
            asdict(item) for item in (suspected.items if suspected else [])
        ],
    }
