"""The billing report as an Excel workbook (``ReportData`` in, ``.xlsx`` bytes out)."""

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .constants import MESSAGE_STATUS_LABELS, ReportPreset
from .formatting import (
    ARREARS_HEADERS,
    GROUP_HEADERS,
    clean_text,
    format_generated,
    format_period,
)
from .schema import ReportData

RUPIAH_FORMAT = '"Rp"#,##0'
DATE_FORMAT = "dd-mm-yyyy"
PERCENT_FORMAT = '0.0"%"'
HEADER_FILL = PatternFill("solid", fgColor="E2E8F0")

GROUP_WIDTHS = (28, 12, 16, 10, 14, 18, 20, 10)
ARREARS_WIDTHS = (14, 30, 28, 11, 20, 18, 16, 14, 15, 15, 24, 22)


def _text(sheet: Worksheet, row: int, column: int, value: str, *, bold: bool = False) -> Cell:
    cell = sheet.cell(row=row, column=column, value=clean_text(value))
    # openpyxl turns text that starts with "=" into a formula; names from imported files must stay
    # text, so the cell type is forced back to string.
    cell.data_type = "s"
    if bold:
        cell.font = Font(bold=True)
    return cell


def _number(
    sheet: Worksheet, row: int, column: int, value: float, number_format: str = "General"
) -> None:
    sheet.cell(row=row, column=column, value=value).number_format = number_format


def _date(sheet: Worksheet, row: int, column: int, value: date | None) -> None:
    if value is not None:
        sheet.cell(row=row, column=column, value=value).number_format = DATE_FORMAT


def _header(sheet: Worksheet, headers: tuple[str, ...], widths: tuple[int, ...]) -> None:
    for index, (title, width) in enumerate(zip(headers, widths, strict=True), start=1):
        cell = _text(sheet, 1, index, title, bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"


def _summary_sheet(sheet: Worksheet, data: ReportData) -> None:
    sheet.title = "Ringkasan"
    sheet.column_dimensions["A"].width = 40
    sheet.column_dimensions["B"].width = 46
    sheet.column_dimensions["C"].width = 20
    title = _text(sheet, 1, 1, "Laporan Penagihan Uang Kuliah", bold=True)
    title.font = Font(bold=True, size=14)
    _text(sheet, 3, 1, "Periode", bold=True)
    _text(sheet, 3, 2, format_period(data))
    _text(sheet, 4, 1, "Dibuat", bold=True)
    _text(sheet, 4, 2, format_generated(data))

    invoices = data.summary.invoices
    row = 6
    _text(sheet, row, 1, "Tagihan", bold=True)
    _text(sheet, row, 2, "Jumlah", bold=True)
    _text(sheet, row, 3, "Nominal", bold=True)
    amounts = (
        ("Total tagihan", invoices.total, invoices.total_amount),
        ("Lunas", invoices.paid, invoices.paid_amount),
        ("Belum lunas", invoices.unpaid, invoices.unpaid_amount),
        ("Lewat jatuh tempo", invoices.overdue, invoices.overdue_amount),
        ("Dikecualikan (keringanan)", invoices.exempt, invoices.exempt_amount),
    )
    for label, count, amount in amounts:
        row += 1
        _text(sheet, row, 1, label)
        _number(sheet, row, 2, count)
        _number(sheet, row, 3, amount, RUPIAH_FORMAT)
    for label, value in (
        ("Persentase lunas (jumlah tagihan)", invoices.paid_percent_count),
        ("Persentase lunas (nominal)", invoices.paid_percent_amount),
    ):
        row += 1
        _text(sheet, row, 1, label)
        _number(sheet, row, 2, value, PERCENT_FORMAT)
    row += 1
    _text(sheet, row, 1, "Lunas setelah pesan")
    _number(sheet, row, 2, data.summary.paid_after_message)
    payments = data.summary.payments_in_period
    if data.preset is ReportPreset.WEEKLY and payments is not None:
        row += 1
        _text(sheet, row, 1, "Pembayaran dalam periode")
        _number(sheet, row, 2, payments.count)
        _number(sheet, row, 3, payments.amount, RUPIAH_FORMAT)

    row += 2
    _text(sheet, row, 1, "Pesan", bold=True)
    _text(sheet, row, 2, "Jumlah", bold=True)
    counts = data.summary.messages.model_dump()
    for status, label in MESSAGE_STATUS_LABELS.items():
        row += 1
        _text(sheet, row, 1, label)
        _number(sheet, row, 2, counts[status])


def _groups_sheet(sheet: Worksheet, data: ReportData) -> None:
    sheet.title = "Per Prodi-Angkatan"
    _header(sheet, GROUP_HEADERS, GROUP_WIDTHS)
    for row, group in enumerate(data.groups, start=2):
        _text(sheet, row, 1, group.study_program)
        _text(sheet, row, 2, group.cohort)
        for column, value in enumerate(
            (group.total, group.paid, group.unpaid, group.overdue), start=3
        ):
            _number(sheet, row, column, value)
        _number(sheet, row, 7, group.overdue_amount, RUPIAH_FORMAT)
        _number(sheet, row, 8, group.paid_percent, PERCENT_FORMAT)
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(GROUP_HEADERS))}{max(len(data.groups) + 1, 1)}"
    )


def _arrears_sheet(sheet: Worksheet, data: ReportData) -> None:
    sheet.title = "Tunggakan"
    _header(sheet, ARREARS_HEADERS, ARREARS_WIDTHS)
    for row, item in enumerate(data.arrears, start=2):
        for column, value in enumerate(
            (
                item.nim,
                item.name,
                item.study_program,
                item.cohort,
                item.semester,
                item.invoice_code,
            ),
            start=1,
        ):
            _text(sheet, row, column, value)
        _number(sheet, row, 7, item.amount, RUPIAH_FORMAT)
        _date(sheet, row, 8, item.due_date)
        _number(sheet, row, 9, item.days_overdue)
        _number(sheet, row, 10, item.messages_sent)
        _date(sheet, row, 11, item.last_message_date)
        if item.last_message_status is not None:
            status = item.last_message_status
            _text(sheet, row, 12, MESSAGE_STATUS_LABELS.get(status, status))
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(ARREARS_HEADERS))}{max(len(data.arrears) + 1, 1)}"
    )


def build_xlsx(data: ReportData) -> bytes:
    workbook = Workbook()
    _summary_sheet(workbook.active or workbook.create_sheet(), data)
    _groups_sheet(workbook.create_sheet(), data)
    _arrears_sheet(workbook.create_sheet(), data)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
