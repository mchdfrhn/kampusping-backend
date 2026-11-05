"""The Excel workbook of the billing report."""

import io
from datetime import date, datetime
from typing import cast

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.modules.reports.constants import ReportPreset
from app.modules.reports.xlsx_builder import build_xlsx

from .reports_helpers import make_arrears_row, make_group, make_report

ARREARS_HEADERS = [
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
GROUP_HEADERS = [
    "Prodi",
    "Angkatan",
    "Jumlah Tagihan",
    "Lunas",
    "Belum Lunas",
    "Lewat Jatuh Tempo",
    "Nominal Tunggakan",
    "% Lunas",
]


def open_workbook(data: bytes):
    return load_workbook(io.BytesIO(data))


def header_row(sheet: Worksheet) -> list[object]:
    return [cell.value for cell in sheet[1]]


def label_value_pairs(sheet: Worksheet) -> dict[object, object]:
    return {
        label.value: value.value
        for label, value in sheet.iter_rows(min_col=1, max_col=2)
        if label.value is not None
    }


def test_sheet_names_and_headers() -> None:
    workbook = open_workbook(build_xlsx(make_report()))

    assert workbook.sheetnames == ["Ringkasan", "Per Prodi-Angkatan", "Tunggakan"]
    assert header_row(workbook["Per Prodi-Angkatan"]) == GROUP_HEADERS
    assert header_row(workbook["Tunggakan"]) == ARREARS_HEADERS


def test_header_rows_are_bold_frozen_and_filterable() -> None:
    workbook = open_workbook(build_xlsx(make_report()))

    for name in ("Per Prodi-Angkatan", "Tunggakan"):
        sheet = workbook[name]
        assert sheet["A1"].font.bold is True
        assert sheet.freeze_panes == "A2"
    assert workbook["Tunggakan"].auto_filter.ref == "A1:L2"


def test_summary_sheet_shows_period_author_and_figures() -> None:
    workbook = open_workbook(build_xlsx(make_report()))

    pairs = label_value_pairs(workbook["Ringkasan"])

    assert pairs["Periode"] == "7 hari terakhir (27-10-2026 s/d 02-11-2026)"
    assert pairs["Dibuat"] == "02-11-2026 10:00 WIB oleh staf@example.com"
    assert pairs["Total tagihan"] == 10
    assert pairs["Lunas"] == 4
    assert pairs["Belum lunas"] == 6
    assert pairs["Lewat jatuh tempo"] == 3
    assert pairs["Dikecualikan (keringanan)"] == 1
    assert pairs["Persentase lunas (jumlah tagihan)"] == 40.0
    assert pairs["Lunas setelah pesan"] == 3
    assert pairs["Pembayaran dalam periode"] == 2
    assert pairs["Terkirim"] == 5
    assert pairs["Gagal"] == 1


def test_semester_report_names_the_semester_and_has_no_payment_window() -> None:
    data = make_report(preset=ReportPreset.SEMESTER, semester="Ganjil 2026/2027")

    pairs = label_value_pairs(open_workbook(build_xlsx(data))["Ringkasan"])

    assert pairs["Periode"] == "Semester: Ganjil 2026/2027"
    assert "Pembayaran dalam periode" not in pairs


def test_arrears_values_and_number_formats() -> None:
    sheet = open_workbook(build_xlsx(make_report()))["Tunggakan"]

    row = [cell.value for cell in sheet[2]]

    assert row[:7] == [
        "2201001",
        "Budi Santoso",
        "Teknik Informatika",
        "2022",
        "Ganjil 2026/2027",
        "INV-001",
        1_500_000,
    ]
    assert cast(datetime, row[7]).date() == date(2026, 10, 20)
    assert row[8:10] == [13, 2]
    assert cast(datetime, row[10]).date() == date(2026, 10, 25)
    assert row[11] == "Diterima"
    assert sheet["G2"].number_format == '"Rp"#,##0'
    assert sheet["H2"].number_format.lower() == "dd-mm-yyyy"


def test_text_starting_with_a_formula_character_stays_a_string() -> None:
    rows = [
        make_arrears_row(nim="1", name="=1+1", invoice_code="A"),
        make_arrears_row(nim="2", name="@SUM(A1:A2)", invoice_code="B"),
        make_arrears_row(nim="3", name="+62812", invoice_code="C"),
        make_arrears_row(nim="4", name="-5", invoice_code="D"),
    ]
    sheet = open_workbook(build_xlsx(make_report(arrears=rows)))["Tunggakan"]

    names = [sheet.cell(row=index, column=2) for index in range(2, 6)]

    assert [cell.value for cell in names] == ["=1+1", "@SUM(A1:A2)", "+62812", "-5"]
    assert {cell.data_type for cell in names} == {"s"}


def test_group_values() -> None:
    data = make_report(groups=[make_group(), make_group(study_program="Akuntansi", cohort="2023")])
    sheet = open_workbook(build_xlsx(data))["Per Prodi-Angkatan"]

    first = [cell.value for cell in sheet[2]]

    assert first == ["Teknik Informatika", "2022", 4, 1, 3, 2, 3_000_000, 25.0]
    assert sheet["G2"].number_format == '"Rp"#,##0'
    assert sheet.max_row == 3


def test_empty_report_is_valid() -> None:
    workbook = open_workbook(build_xlsx(make_report(empty=True)))

    assert workbook["Tunggakan"].max_row == 1
    assert workbook["Per Prodi-Angkatan"].max_row == 1
    assert label_value_pairs(workbook["Ringkasan"])["Total tagihan"] == 0


def test_no_sheet_has_a_phone_column() -> None:
    workbook = open_workbook(build_xlsx(make_report()))

    headers = [
        str(value).lower()
        for name in ("Per Prodi-Angkatan", "Tunggakan")
        for value in header_row(workbook[name])
    ]

    assert not [h for h in headers if any(w in h for w in ("hp", "wa ", "telepon", "phone"))]
