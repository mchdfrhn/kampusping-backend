"""The billing report as a PDF document (``ReportData`` in, PDF bytes out)."""

import io
from collections.abc import Sequence
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import Flowable

from .constants import MESSAGE_STATUS_LABELS, ReportPreset
from .formatting import (
    ARREARS_HEADERS,
    GROUP_HEADERS,
    clean_text,
    format_generated,
    format_percent_id,
    format_period,
    format_rupiah_id,
    format_short_date,
)
from .schema import ReportData

NOTICE = "Berisi data pribadi mahasiswa; jangan disebarluaskan."
MISSING = "-"
MARGIN = 15 * mm

# Widths in millimetres; each set adds up to at most the 267 mm of a landscape A4 page.
ARREARS_WIDTHS = (18, 38, 34, 15, 24, 22, 22, 20, 13, 13, 20, 18)
GROUP_WIDTHS = (70, 25, 30, 22, 28, 32, 40, 20)
SUMMARY_WIDTHS = (90, 40, 45)
# Columns holding numbers or amounts, right-aligned.
ARREARS_NUMERIC = frozenset({6, 8, 9})
GROUP_NUMERIC = frozenset({2, 3, 4, 5, 6, 7})
SUMMARY_NUMERIC = frozenset({1, 2})

_styles = getSampleStyleSheet()
CELL = ParagraphStyle("cell", parent=_styles["BodyText"], fontSize=7.5, leading=9)
CELL_RIGHT = ParagraphStyle("cell-right", parent=CELL, alignment=TA_RIGHT)
HEAD = ParagraphStyle("head", parent=CELL, fontName="Helvetica-Bold")
HEAD_RIGHT = ParagraphStyle("head-right", parent=HEAD, alignment=TA_RIGHT)
TITLE = ParagraphStyle("title", parent=_styles["Title"], fontSize=16, alignment=0, spaceAfter=4)
SECTION = ParagraphStyle(
    "section", parent=_styles["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=4
)
META = ParagraphStyle("meta", parent=_styles["BodyText"], fontSize=9, leading=11)


def _count(value: int) -> str:
    return str(value)


def summary_table(data: ReportData) -> list[list[str]]:
    invoices = data.summary.invoices
    rows: list[list[str]] = [["Keterangan", "Jumlah", "Nominal"]]
    for label, count, amount in (
        ("Total tagihan", invoices.total, invoices.total_amount),
        ("Lunas", invoices.paid, invoices.paid_amount),
        ("Belum lunas", invoices.unpaid, invoices.unpaid_amount),
        ("Lewat jatuh tempo", invoices.overdue, invoices.overdue_amount),
        ("Dikecualikan (keringanan)", invoices.exempt, invoices.exempt_amount),
    ):
        rows.append([label, _count(count), format_rupiah_id(amount)])
    rows.append(
        ["Persentase lunas (jumlah tagihan)", format_percent_id(invoices.paid_percent_count), ""]
    )
    rows.append(["Persentase lunas (nominal)", format_percent_id(invoices.paid_percent_amount), ""])
    rows.append(["Lunas setelah pesan", _count(data.summary.paid_after_message), ""])
    payments = data.summary.payments_in_period
    if data.preset is ReportPreset.WEEKLY and payments is not None:
        rows.append(
            [
                "Pembayaran dalam periode",
                _count(payments.count),
                format_rupiah_id(payments.amount),
            ]
        )
    counts = data.summary.messages.model_dump()
    for status, label in MESSAGE_STATUS_LABELS.items():
        rows.append([label, _count(counts[status]), ""])
    return rows


def groups_table(data: ReportData) -> list[list[str]]:
    rows: list[list[str]] = [list(GROUP_HEADERS)]
    for group in data.groups:
        rows.append(
            [
                group.study_program,
                group.cohort,
                _count(group.total),
                _count(group.paid),
                _count(group.unpaid),
                _count(group.overdue),
                format_rupiah_id(group.overdue_amount),
                format_percent_id(group.paid_percent),
            ]
        )
    return rows


def arrears_table(data: ReportData) -> list[list[str]]:
    rows: list[list[str]] = [list(ARREARS_HEADERS)]
    for item in data.arrears:
        status = item.last_message_status
        rows.append(
            [
                item.nim,
                item.name,
                item.study_program,
                item.cohort,
                item.semester,
                item.invoice_code,
                format_rupiah_id(item.amount),
                format_short_date(item.due_date),
                _count(item.days_overdue),
                _count(item.messages_sent),
                format_short_date(item.last_message_date) if item.last_message_date else MISSING,
                MESSAGE_STATUS_LABELS.get(status, status) if status else MISSING,
            ]
        )
    return rows


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    # Paragraph parses XML-like markup, and names come from imported files: escape everything.
    return Paragraph(escape(clean_text(text)), style)


def _table(rows: Sequence[Sequence[str]], widths: Sequence[int], numeric: frozenset[int]) -> Table:
    cells: list[list[Flowable]] = []
    for row_index, row in enumerate(rows):
        header = row_index == 0
        cells.append(
            [
                _paragraph(
                    value,
                    (HEAD_RIGHT if header else CELL_RIGHT)
                    if column in numeric
                    else (HEAD if header else CELL),
                )
                for column, value in enumerate(row)
            ]
        )
    table = Table(cells, colWidths=[width * mm for width in widths], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94A3B8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _footer(canvas: Canvas, document: Any) -> None:
    width, _ = landscape(A4)
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(MARGIN, 8 * mm, NOTICE)
    canvas.drawRightString(width - MARGIN, 8 * mm, f"Halaman {document.page}")
    canvas.restoreState()


def build_pdf(data: ReportData) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=16 * mm,
        title="Laporan Penagihan Uang Kuliah",
        author=data.generated_by,
    )
    story: list[Flowable] = [
        _paragraph("Laporan Penagihan Uang Kuliah", TITLE),
        _paragraph(f"Periode: {format_period(data)}", META),
        _paragraph(f"Dibuat: {format_generated(data)}", META),
        _paragraph("Ringkasan", SECTION),
        _table(summary_table(data), SUMMARY_WIDTHS, SUMMARY_NUMERIC),
        _paragraph("Per Prodi dan Angkatan", SECTION),
    ]
    if data.groups:
        story.append(_table(groups_table(data), GROUP_WIDTHS, GROUP_NUMERIC))
    else:
        story.append(_paragraph("Belum ada data tagihan.", META))
    story.append(_paragraph("Daftar Tunggakan", SECTION))
    if data.arrears:
        story.append(_table(arrears_table(data), ARREARS_WIDTHS, ARREARS_NUMERIC))
    else:
        story.append(_paragraph("Tidak ada tunggakan.", META))
    story.append(Spacer(1, 4 * mm))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
