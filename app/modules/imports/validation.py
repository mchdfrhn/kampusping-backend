"""Row-level validation of import files.

Cells keep their native type (str, int, float, datetime, date, None) until they get here, so a
NIM stored as ``20210001.0`` or a phone stored as ``6.28123456789e11`` is repaired instead of
corrupted. Nothing in this module touches the database or raises on bad data.
"""

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from app.shared.phone import normalize_phone

Status = Literal["paid", "unpaid"]

NIM_MAX = 30
INVOICE_CODE_MAX = 50
NAME_MAX = 150
STUDY_PROGRAM_MAX = 100
COHORT_MAX = 10
SEMESTER_MAX = 20
PAID_VIA_VALUES = ("siakad", "sipema")
# Amounts are stored as BIGINT rupiah; anything above this is a typo, not a tuition fee.
MAX_AMOUNT = 10**13
# Excel stores dates as day counts from 1899-12-30. Only numbers in this window (roughly the
# years 1954-2119) are read as dates, so ordinary numbers are not mistaken for them.
_EXCEL_SERIAL_MIN = 20_000
_EXCEL_SERIAL_MAX = 80_000
_EXCEL_EPOCH = date(1899, 12, 30)

_DATE_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?")
_DATE_DMY = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})")
_DIGITS_ONLY = re.compile(r"\d+(?:\.0+)?")
_AMOUNT_CHARS = re.compile(r"[\d.,]+")
_AMOUNT_PREFIX = re.compile(r"^rp\.?", re.IGNORECASE)
_THOUSANDS_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+|\d{1,3}(?:,\d{3})+")
_SEPARATORS = re.compile(r"[.,]")

_PAID = frozenset({"lunas", "sudah bayar", "terbayar", "paid", "1", "y", "ya", "true"})
_UNPAID = frozenset({"belum lunas", "belum bayar", "unpaid", "0", "n", "tidak", "false"})

_DATE_HINT = "(gunakan YYYY-MM-DD atau DD/MM/YYYY)"


@dataclass(frozen=True)
class RowIssue:
    """One problem found in one row; ``field`` is the canonical English field name."""

    row_number: int
    field: str
    message: str


@dataclass(frozen=True)
class ValidRow:
    """A fully validated row. Optional fields are ``None`` when the cell was empty.

    ``phone`` is the normalised number (``None`` when absent or not an Indonesian mobile number,
    which is not a row error); ``phone_raw`` is the trimmed original text so callers can tell a
    missing number from an invalid one. ``paid_via`` is ``None`` when the file has no value; the
    caller then falls back to the batch source.
    """

    row_number: int
    nim: str
    name: str | None
    study_program: str | None
    cohort: str | None
    phone: str | None
    invoice_code: str
    semester: str
    amount: int
    due_date: date
    status: Status
    paid_at: date | None
    paid_via: str | None
    phone_raw: str | None = None


def cell_text(value: object) -> str:
    """Trimmed text of a cell; whole floats lose their ``.0`` and ``None`` is empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value).replace("\ufeff", "").replace("\u00a0", " ").strip()


def parse_amount(value: object) -> int | None:
    """Whole rupiah > 0 from a number or text such as ``Rp 1.500.000,00``; otherwise ``None``.

    Both ``.`` and ``,`` may group thousands (``1.500.000`` / ``1,500,000``); a separator followed
    by exactly three digits is a thousands separator, followed by one or two digits a decimal
    mark, and the decimals must be zero (``,00``). Fractions, negatives and zero are rejected.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            return None
        return _checked_amount(int(value))

    text = _AMOUNT_PREFIX.sub("", cell_text(value).replace(" ", "").lower())
    if not _AMOUNT_CHARS.fullmatch(text):
        return None
    last = max(text.rfind("."), text.rfind(","))
    if last == -1:
        return _checked_amount(int(text))

    tail = text[last + 1 :]
    if len(tail) == 3:  # thousands separator
        integer_part = text
    elif len(tail) in (1, 2):  # decimal mark
        if int(tail) != 0:
            return None
        integer_part = text[:last]
    else:
        return None
    if _SEPARATORS.search(integer_part) and not _THOUSANDS_GROUPED.fullmatch(integer_part):
        return None
    return _checked_amount(int(_SEPARATORS.sub("", integer_part)))


def _checked_amount(amount: int) -> int | None:
    return amount if 0 < amount <= MAX_AMOUNT else None


def parse_date(value: object) -> date | None:
    """A calendar date from ``datetime``/``date`` cells or text; otherwise ``None``.

    Accepted text: ``YYYY-MM-DD`` (optionally followed by a time), ``DD/MM/YYYY`` and
    ``DD-MM-YYYY``; four-digit years only. An Excel serial number is accepted only when the cell
    is numeric (an int/float, or digits-only text as left by CSV exports from Indonesian Excel)
    and lies between 20000 and 80000; it is converted from the 1900 epoch (1899-12-30).
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float):
        return _excel_serial(value)

    text = cell_text(value)
    if _DIGITS_ONLY.fullmatch(text):
        return _excel_serial(float(text))
    if match := _DATE_ISO.fullmatch(text):
        year, month, day = (int(part) for part in match.groups())
    elif match := _DATE_DMY.fullmatch(text):
        day, month, year = (int(part) for part in match.groups())
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _excel_serial(number: float) -> date | None:
    if not math.isfinite(number) or not number.is_integer():
        return None
    if not _EXCEL_SERIAL_MIN <= number <= _EXCEL_SERIAL_MAX:
        return None
    return _EXCEL_EPOCH + timedelta(days=int(number))


def parse_status(value: object) -> Status | None:
    """``paid`` or ``unpaid`` from the many spellings used in exports; otherwise ``None``."""
    if value is None:
        return None
    text = " ".join(cell_text(value).lower().split())
    if text in _PAID:
        return "paid"
    if text in _UNPAID:
        return "unpaid"
    return None


def validate_row(
    row_number: int,
    cells: list[object],
    columns: dict[str, int],
    *,
    default_semester: str | None,
    source: str,
) -> ValidRow | list[RowIssue]:
    """Validate one data row; return the ``ValidRow`` or every ``RowIssue`` found (never both).

    ``columns`` maps canonical fields to cell indexes (see ``resolve_columns``); cells beyond the
    end of a short row count as empty. ``source`` is accepted so per-source rules can be added
    later; M1 validates both sources identically.
    """
    del source
    issues: list[RowIssue] = []

    def raw(field: str) -> object:
        index = columns.get(field)
        if index is None or index >= len(cells):
            return None
        return cells[index]

    def text(field: str) -> str:
        return cell_text(raw(field))

    def issue(field: str, message: str) -> None:
        issues.append(RowIssue(row_number, field, message))

    def required_text(field: str, label: str, max_length: int) -> str:
        value = text(field)
        if not value:
            issue(field, f"{label} wajib diisi.")
        elif len(value) > max_length:
            issue(field, f"{label} maksimal {max_length} karakter.")
        return value

    def optional_text(field: str, label: str, max_length: int) -> str | None:
        value = text(field)
        if len(value) > max_length:
            issue(field, f"{label} maksimal {max_length} karakter.")
        return value or None

    nim = required_text("nim", "NIM", NIM_MAX)
    invoice_code = required_text("invoice_code", "Kode tagihan", INVOICE_CODE_MAX)
    name = optional_text("name", "Nama", NAME_MAX)
    study_program = optional_text("study_program", "Program studi", STUDY_PROGRAM_MAX)
    cohort = optional_text("cohort", "Angkatan", COHORT_MAX)

    semester = text("semester") or (default_semester or "").strip()
    if not semester:
        issue(
            "semester",
            "Semester wajib diisi (isi kolom semester atau pilih semester saat unggah).",
        )
    elif len(semester) > SEMESTER_MAX:
        issue("semester", f"Semester maksimal {SEMESTER_MAX} karakter.")

    amount = parse_amount(raw("amount"))
    if amount is None:
        issue("amount", "Nominal tidak valid.")

    due_date = parse_date(raw("due_date"))
    if due_date is None:
        issue("due_date", f"Tanggal jatuh tempo tidak valid {_DATE_HINT}.")

    status = parse_status(raw("status"))
    if status is None:
        issue("status", "Status tidak dikenali (gunakan lunas atau belum lunas).")

    paid_at: date | None = None
    if text("paid_at"):
        paid_at = parse_date(raw("paid_at"))
        if paid_at is None:
            issue("paid_at", f"Tanggal bayar tidak valid {_DATE_HINT}.")

    paid_via = text("paid_via").lower() or None
    if paid_via is not None and paid_via not in PAID_VIA_VALUES:
        issue("paid_via", "Kanal pembayaran tidak dikenali (gunakan siakad atau sipema).")

    phone_raw = text("phone") or None
    phone_cell = raw("phone")
    phone = normalize_phone(phone_cell) if isinstance(phone_cell, str | int | float) else None

    if issues or amount is None or due_date is None or status is None:
        return issues
    return ValidRow(
        row_number=row_number,
        nim=nim,
        name=name,
        study_program=study_program,
        cohort=cohort,
        phone=phone,
        invoice_code=invoice_code,
        semester=semester,
        amount=amount,
        due_date=due_date,
        status=status,
        paid_at=paid_at if status == "paid" else None,
        paid_via=paid_via,
        phone_raw=phone_raw,
    )
