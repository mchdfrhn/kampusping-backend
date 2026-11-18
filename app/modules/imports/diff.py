"""One comparison of an incoming row against the stored invoice, shared by preview and apply.

Preview predicts, and apply counts, with the same rules so the numbers staff approve are the
numbers they get.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.invoices.constants import InvoiceStatus
from app.modules.invoices.model import Invoice

from .constants import ImportKind
from .existing import is_sticky_skip
from .validation import ValidRow

InvoiceFieldChanges = dict[str, tuple[object, object]]


class RowClass(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ChangedRow:
    row: ValidRow
    invoice: Invoice
    fields: InvoiceFieldChanges


@dataclass(frozen=True)
class Classification:
    new: list[ValidRow] = field(default_factory=list)
    changed: list[ChangedRow] = field(default_factory=list)
    unchanged: list[ValidRow] = field(default_factory=list)
    skipped: list[ValidRow] = field(default_factory=list)


def invoice_changes(
    invoice: Invoice, row: ValidRow, *, kind: ImportKind, source: str
) -> InvoiceFieldChanges:
    """Fields the row would change on the stored invoice, as ``field -> (stored, incoming)``.

    A rekap row carries no real due date (the report has none, the parser stamps the import day),
    so its due date never counts as a change.
    """
    changes: InvoiceFieldChanges = {}
    if invoice.deleted_at is not None:
        changes["restored"] = (True, False)
    if invoice.amount != row.amount:
        changes["amount"] = (invoice.amount, row.amount)
    if kind != ImportKind.REKAP and invoice.due_date != row.due_date:
        changes["due_date"] = (invoice.due_date, row.due_date)
    if invoice.semester != row.semester:
        changes["semester"] = (invoice.semester, row.semester)
    if invoice.source != source:
        changes["source"] = (invoice.source, source)
    if row.status == InvoiceStatus.PAID.value and invoice.status != InvoiceStatus.PAID.value:
        changes["status"] = (invoice.status, InvoiceStatus.PAID.value)
    return changes


def classify_rows(
    rows: Sequence[ValidRow],
    invoices: dict[str, Invoice],
    *,
    kind: ImportKind,
    source: str,
) -> Classification:
    """Sort rows into new, changed, unchanged and skipped.

    Skipped means a paid invoice that an unpaid row cannot reopen.

    ``invoices`` holds stored invoices by code, soft-deleted ones included; a deleted invoice is
    restored by the import, so it is never "skipped".
    """
    result = Classification()
    for row in rows:
        invoice = invoices.get(row.invoice_code)
        if invoice is None:
            result.new.append(row)
        elif invoice.deleted_at is None and is_sticky_skip(invoice, row):
            result.skipped.append(row)
        elif fields := invoice_changes(invoice, row, kind=kind, source=source):
            result.changed.append(ChangedRow(row, invoice, fields))
        else:
            result.unchanged.append(row)
    return result
