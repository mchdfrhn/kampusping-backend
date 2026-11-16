"""Preview: validate a parsed file, apply the cross-row rules and predict the outcome.

Nothing here writes business data; the prediction is computed from lookups only.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.students.model import Student

from .absent import find_suspected_paid
from .constants import (
    MSG_CODE_OTHER_STUDENT,
    MSG_DUPLICATE_CODE,
    MSG_NAME_REQUIRED,
    ImportKind,
)
from .details import Details, build_details
from .diff import Classification, classify_rows
from .existing import (
    belongs_to_other_student,
    invoice_owner_nims,
    invoices_by_code,
    students_by_nim,
    unpaid_invoices_for_students,
)
from .parser import ParsedSheet
from .validation import RowIssue, ValidRow, validate_row


@dataclass(frozen=True)
class PreviewResult:
    rows: list[ValidRow]
    issues: list[RowIssue]
    summary: dict[str, int]
    details: Details


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
    invoices = await invoices_by_code(db, [row.invoice_code for row in valid])
    owners = await invoice_owner_nims(db, invoices)
    accepted, cross_issues = _cross_row_checks(valid, known_nims=set(students), owners=owners)
    issues = sorted([*issues, *cross_issues], key=lambda issue: issue.row_number)

    imported_codes = {row.invoice_code for row in accepted}
    # A rekap lists arrears only: a new invoice says nothing about the student's older ones.
    students_with_new_invoice = [
        students[row.nim].id
        for row in accepted
        if sheet.kind != ImportKind.REKAP
        and row.invoice_code not in invoices
        and row.nim in students
    ]
    superseded = await unpaid_invoices_for_students(
        db,
        students_with_new_invoice,
        exclude_codes=imported_codes,
    )

    classification = classify_rows(accepted, invoices, kind=sheet.kind, source=source)
    suspected = await find_suspected_paid(db, accepted) if sheet.kind == ImportKind.REKAP else None

    summary = {
        "rows_total": len(sheet.rows),
        "rows_valid": len(accepted),
        "rows_error": len({issue.row_number for issue in issues}),
        **_predict(accepted, students, classification, superseded_count=len(superseded)),
        "paid_suspected": len(suspected.items) if suspected else 0,
        "incomplete_warning": int(suspected.incomplete) if suspected else 0,
    }
    return PreviewResult(
        rows=accepted,
        issues=issues,
        summary=summary,
        details=build_details(classification, students, suspected),
    )


def _cross_row_checks(
    rows: list[ValidRow], *, known_nims: set[str], owners: dict[str, str]
) -> tuple[list[ValidRow], list[RowIssue]]:
    """Drop repeated codes, codes owned by another NIM and nameless rows for new students."""
    accepted: list[ValidRow] = []
    issues: list[RowIssue] = []
    first_row_of_code: dict[str, int] = {}
    named_nims: set[str] = set()
    for row in rows:
        if (first := first_row_of_code.get(row.invoice_code)) is not None:
            message = MSG_DUPLICATE_CODE.format(first_row=first)
            issues.append(RowIssue(row.row_number, "invoice_code", message))
            continue
        if belongs_to_other_student(row, owners):
            issues.append(RowIssue(row.row_number, "invoice_code", MSG_CODE_OTHER_STUDENT))
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
    rows: list[ValidRow],
    students: dict[str, Student],
    classification: Classification,
    *,
    superseded_count: int = 0,
) -> dict[str, int]:
    nims = {row.nim for row in rows}
    return {
        "students_new": len(nims - students.keys()),
        "students_updated": len(nims & students.keys()),
        "invoices_new": len(classification.new),
        "invoices_updated": len(classification.changed) + superseded_count,
        "invoices_unchanged": len(classification.unchanged),
        "invoices_skipped": len(classification.skipped),
    }
