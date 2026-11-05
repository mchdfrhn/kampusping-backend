"""The PDF document of the billing report."""

import re

from app.modules.reports.constants import ReportPreset
from app.modules.reports.pdf_builder import (
    arrears_table,
    build_pdf,
    groups_table,
    summary_table,
)

from .reports_helpers import make_arrears_row, make_group, make_report

PAGE_OBJECT = re.compile(rb"/Type\s*/Page\b")


def page_count(pdf: bytes) -> int:
    return len(PAGE_OBJECT.findall(pdf))


def test_build_pdf_returns_a_valid_document() -> None:
    pdf = build_pdf(make_report())

    assert pdf.startswith(b"%PDF")
    assert b"%%EOF" in pdf[-1024:]
    assert page_count(pdf) >= 1


def test_pdf_with_many_arrears_spans_several_pages() -> None:
    rows = [
        make_arrears_row(nim=f"22{index:05d}", invoice_code=f"INV-{index}") for index in range(300)
    ]

    pdf = build_pdf(make_report(arrears=rows))

    assert pdf.startswith(b"%PDF")
    assert page_count(pdf) >= 4


def test_markup_characters_in_names_do_not_break_the_pdf() -> None:
    rows = [
        make_arrears_row(nim="1", name="Budi & Co <b>Tebal</b>", invoice_code="A"),
        make_arrears_row(nim="2", name="A < B > C &amp; <unclosed", invoice_code="B"),
    ]
    groups = [make_group(study_program="Prodi <i>Miring</i> & Lain")]

    pdf = build_pdf(make_report(arrears=rows, groups=groups))

    assert pdf.startswith(b"%PDF")


def test_very_long_names_are_wrapped_not_rejected() -> None:
    rows = [make_arrears_row(name="Nama Sangat Panjang " * 30)]

    assert build_pdf(make_report(arrears=rows)).startswith(b"%PDF")


def test_empty_report_builds() -> None:
    pdf = build_pdf(make_report(empty=True))

    assert pdf.startswith(b"%PDF")
    assert page_count(pdf) == 1


def test_semester_report_builds() -> None:
    data = make_report(preset=ReportPreset.SEMESTER, semester="Ganjil 2026/2027")

    assert build_pdf(data).startswith(b"%PDF")


def test_arrears_table_formats_values_and_has_no_phone() -> None:
    rows = arrears_table(make_report())

    assert rows[0] == [
        "NIM",
        "Nama",
        "Prodi",
        "Angkatan",
        "Semester",
        "Kode Tagihan",
        "Nominal",
        "Jatuh Tempo",
        "Hari Terlambat",
        "Pesan Terkirim",
        "Pesan Terakhir (Tanggal)",
        "Pesan Terakhir (Status)",
    ]
    assert rows[1] == [
        "2201001",
        "Budi Santoso",
        "Teknik Informatika",
        "2022",
        "Ganjil 2026/2027",
        "INV-001",
        "Rp1.500.000",
        "20-10-2026",
        "13",
        "2",
        "25-10-2026",
        "Diterima",
    ]
    headers = [header.lower() for header in rows[0]]
    assert not [h for h in headers if any(w in h for w in ("hp", "wa ", "telepon", "phone"))]


def test_arrears_table_leaves_missing_message_columns_blank() -> None:
    row = arrears_table(
        make_report(arrears=[make_arrears_row(last_message_date=None, last_message_status=None)])
    )[1]

    assert row[10:] == ["-", "-"]


def test_groups_table_formats_money_and_percent_with_a_comma() -> None:
    rows = groups_table(make_report(groups=[make_group(paid_percent=85.5)]))

    assert rows[0][:2] == ["Prodi", "Angkatan"]
    assert rows[1] == ["Teknik Informatika", "2022", "4", "1", "3", "2", "Rp3.000.000", "85,5%"]


def test_summary_table_lists_the_figures() -> None:
    rows = summary_table(make_report())

    assert rows[0] == ["Keterangan", "Jumlah", "Nominal"]
    figures = {row[0]: row[1:] for row in rows[1:]}
    assert figures["Total tagihan"] == ["10", "Rp10.000.000"]
    assert figures["Lewat jatuh tempo"] == ["3", "Rp3.000.000"]
    assert figures["Persentase lunas (jumlah tagihan)"] == ["40,0%", ""]
    assert figures["Lunas setelah pesan"] == ["3", ""]
    assert figures["Pembayaran dalam periode"] == ["2", "Rp1.500.000"]
    assert figures["Diterima"] == ["3", ""]


def test_summary_table_has_no_payment_window_row_for_a_semester() -> None:
    data = make_report(preset=ReportPreset.SEMESTER, semester="Ganjil 2026/2027")

    assert "Pembayaran dalam periode" not in {row[0] for row in summary_table(data)}
