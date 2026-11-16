"""The one comparison preview and apply share (no database)."""

from datetime import UTC, date, datetime

from app.modules.imports.constants import ImportKind
from app.modules.imports.diff import classify_rows, invoice_changes
from app.modules.imports.validation import ValidRow
from app.modules.invoices.model import Invoice

SOURCE = "siakad"


def make_row(
    code: str = "INV-1",
    *,
    amount: int = 100,
    due: date = date(2026, 11, 15),
    status: str = "unpaid",
    semester: str = "2026/2027 Ganjil",
) -> ValidRow:
    return ValidRow(
        row_number=2,
        nim="1001",
        name="Budi",
        study_program=None,
        cohort=None,
        phone=None,
        invoice_code=code,
        semester=semester,
        amount=amount,
        due_date=due,
        status=status,  # type: ignore[arg-type]
        paid_at=None,
        paid_via=None,
    )


def make_invoice(
    code: str = "INV-1",
    *,
    amount: int = 100,
    due: date = date(2026, 11, 15),
    status: str = "unpaid",
    semester: str = "2026/2027 Ganjil",
    deleted: bool = False,
) -> Invoice:
    return Invoice(
        invoice_code=code,
        student_id=1,
        semester=semester,
        amount=amount,
        due_date=due,
        status=status,
        source=SOURCE,
        deleted_at=datetime(2026, 1, 1, tzinfo=UTC) if deleted else None,
    )


def classify(rows: list[ValidRow], invoices: dict[str, Invoice], kind=ImportKind.STANDARD):
    return classify_rows(rows, invoices, kind=kind, source=SOURCE)


def test_row_without_a_stored_invoice_is_new() -> None:
    result = classify([make_row()], {})
    assert [row.invoice_code for row in result.new] == ["INV-1"]
    assert (result.changed, result.unchanged, result.skipped) == ([], [], [])


def test_amount_change_is_changed_with_old_and_new() -> None:
    result = classify([make_row(amount=200)], {"INV-1": make_invoice(amount=100)})
    assert [changed.fields for changed in result.changed] == [{"amount": (100, 200)}]


def test_identical_row_is_unchanged() -> None:
    result = classify([make_row()], {"INV-1": make_invoice()})
    assert len(result.unchanged) == 1
    assert (result.new, result.changed, result.skipped) == ([], [], [])


def test_paid_invoice_with_unpaid_row_is_skipped() -> None:
    result = classify([make_row(amount=999)], {"INV-1": make_invoice(status="paid")})
    assert len(result.skipped) == 1
    assert not result.changed


def test_deleted_paid_invoice_with_unpaid_row_is_changed_not_skipped() -> None:
    result = classify([make_row()], {"INV-1": make_invoice(status="paid", deleted=True)})
    assert not result.skipped
    assert "restored" in result.changed[0].fields


def test_rekap_ignores_due_date_difference() -> None:
    row = make_row(due=date(2026, 11, 20))
    stored = make_invoice(due=date(2026, 11, 16))
    assert invoice_changes(stored, row, kind=ImportKind.REKAP, source=SOURCE) == {}


def test_standard_notices_due_date_difference() -> None:
    row = make_row(due=date(2026, 11, 20))
    stored = make_invoice(due=date(2026, 11, 16))
    changes = invoice_changes(stored, row, kind=ImportKind.STANDARD, source=SOURCE)
    assert changes == {"due_date": (date(2026, 11, 16), date(2026, 11, 20))}


def test_unpaid_invoice_with_paid_row_reports_status() -> None:
    changes = invoice_changes(
        make_invoice(), make_row(status="paid"), kind=ImportKind.STANDARD, source=SOURCE
    )
    assert changes == {"status": ("unpaid", "paid")}


def test_source_change_counts_as_a_change() -> None:
    changes = invoice_changes(make_invoice(), make_row(), kind=ImportKind.STANDARD, source="sipema")
    assert changes == {"source": ("siakad", "sipema")}
