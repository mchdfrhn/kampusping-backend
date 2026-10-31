"""Preview: validate a parsed file, apply the cross-row rules and predict the outcome.

Nothing here writes business data; the prediction is computed from lookups only.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.invoices.model import Invoice
from app.modules.students.model import Student

from .constants import MSG_DUPLICATE_CODE, MSG_NAME_REQUIRED
from .existing import invoices_by_code, is_sticky_skip, students_by_nim
from .parser import ParsedSheet
from .validation import RowIssue, ValidRow, validate_row


@dataclass(frozen=True)
class PreviewResult:
    rows: list[ValidRow]
    issues: list[RowIssue]
    summary: dict[str, int]


async def build_preview(
    db: AsyncSession,
    sheet: ParsedSheet,
    columns: dict[str, int],
    *,
    source: str,
    semester: str | None,
) -> PreviewResult:
    valid: list[ValidRow] = []
    issues: list[RowIssue] = []
    # Row-level problems first; each row is either a ValidRow or a list of RowIssue.
    for number, cells in zip(sheet.row_numbers, sheet.rows, strict=True):
        outcome = validate_row(number, cells, columns, default_semester=semester, source=source)
        if isinstance(outcome, ValidRow):
            valid.append(outcome)
        else:
            issues.extend(outcome)

    students = await students_by_nim(db, [row.nim for row in valid])
    accepted, cross_issues = _cross_row_checks(valid, known_nims=set(students))
    issues = sorted([*issues, *cross_issues], key=lambda issue: issue.row_number)

    invoices = await invoices_by_code(db, [row.invoice_code for row in accepted])
    summary = {
        "rows_total": len(sheet.rows),
        "rows_valid": len(accepted),
        "rows_error": len({issue.row_number for issue in issues}),
        **_predict(accepted, students, invoices),
    }
    return PreviewResult(rows=accepted, issues=issues, summary=summary)


def _cross_row_checks(
    rows: list[ValidRow], *, known_nims: set[str]
) -> tuple[list[ValidRow], list[RowIssue]]:
    """Drop repeated invoice codes and nameless rows for students that do not exist yet."""
    accepted: list[ValidRow] = []
    issues: list[RowIssue] = []
    first_row_of_code: dict[str, int] = {}
    named_nims: set[str] = set()
    for row in rows:
        if (first := first_row_of_code.get(row.invoice_code)) is not None:
            message = MSG_DUPLICATE_CODE.format(first_row=first)
            issues.append(RowIssue(row.row_number, "invoice_code", message))
            continue
        if row.nim not in known_nims and row.nim not in named_nims and not row.name:
            issues.append(RowIssue(row.row_number, "name", MSG_NAME_REQUIRED))
            continue
        first_row_of_code[row.invoice_code] = row.row_number
        if row.name:
            named_nims.add(row.nim)
        accepted.append(row)
    return accepted, issues


def _predict(
    rows: list[ValidRow], students: dict[str, Student], invoices: dict[str, Invoice]
) -> dict[str, int]:
    nims = {row.nim for row in rows}
    new_invoices = updated = skipped = 0
    for row in rows:
        invoice = invoices.get(row.invoice_code)
        if invoice is None:
            new_invoices += 1
        elif is_sticky_skip(invoice, row):
            skipped += 1
        else:
            updated += 1
    return {
        "students_new": len(nims - students.keys()),
        "students_updated": len(nims & students.keys()),
        "invoices_new": new_invoices,
        "invoices_updated": updated,
        "invoices_skipped": skipped,
    }
