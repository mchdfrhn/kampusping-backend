"""Pure parsing and validation of SIAKAD/SIPEMA export files (no database)."""

import io
import re
import zipfile
from collections.abc import Mapping, Sequence
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from app.modules.imports.columns import (
    ALIASES,
    CANONICAL_FIELDS,
    FIELD_LABELS_ID,
    REQUIRED_FIELDS,
    normalize_header,
)
from app.modules.imports.parser import (
    MAX_DATA_ROWS,
    MAX_FILE_BYTES,
    ImportFileError,
    ParsedSheet,
    parse_upload,
    resolve_columns,
)
from app.modules.imports.validation import (
    RowIssue,
    ValidRow,
    parse_amount,
    parse_date,
    parse_status,
    validate_row,
)

HEADER = ["NIM", "Nama", "Prodi", "Angkatan", "No WA", "Kode Tagihan", "Nominal", "Jatuh Tempo"]
FULL_HEADER = [*HEADER, "Status", "Tanggal Bayar", "Kanal Pembayaran", "Semester"]


def make_xlsx(rows: Sequence[Sequence[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def validated(sheet: ParsedSheet, index: int = 0) -> ValidRow | list[RowIssue]:
    columns = resolve_columns(sheet.headers, "siakad")
    return validate_row(
        sheet.row_numbers[index],
        sheet.rows[index],
        columns,
        default_semester="2026/1",
        source="siakad",
    )


# --- file parsing -----------------------------------------------------------------------------


def test_csv_with_bom_and_semicolon_delimiter() -> None:
    content = csv_bytes(
        "\ufeffNIM;Nama;Kode Tagihan;Nominal;Jatuh Tempo;Status\r\n"
        "20210001;Budi;INV-1;1.500.000;15/11/2026;Belum Lunas\r\n",
        "utf-8",
    )
    sheet = parse_upload("export.csv", content)
    assert sheet.headers[0] == "NIM"  # the BOM is gone
    assert sheet.headers == ["NIM", "Nama", "Kode Tagihan", "Nominal", "Jatuh Tempo", "Status"]
    assert sheet.rows == [["20210001", "Budi", "INV-1", "1.500.000", "15/11/2026", "Belum Lunas"]]
    assert sheet.row_numbers == [2]
    row = validated(sheet)
    assert isinstance(row, ValidRow)
    assert row.nim == "20210001"
    assert row.amount == 1_500_000
    assert row.due_date == date(2026, 11, 15)
    assert row.status == "unpaid"


@pytest.mark.parametrize("delimiter", [",", "\t"])
def test_csv_comma_and_tab_delimiters(delimiter: str) -> None:
    lines = [
        ["NIM", "Kode Tagihan", "Nominal", "Jatuh Tempo", "Status"],
        ["1", "A", "100", "2026-01-02", "lunas"],
    ]
    content = csv_bytes("\n".join(delimiter.join(line) for line in lines))
    sheet = parse_upload("export.CSV", content)
    assert sheet.headers == lines[0]
    assert sheet.rows == [lines[1]]


def test_csv_falls_back_to_cp1252_and_keeps_leading_zeros() -> None:
    content = csv_bytes(
        "NIM;Nama;Kode Tagihan;Nominal;Jatuh Tempo;Status\n0012;José;A;100;2026-01-02;lunas\n",
        "cp1252",
    )
    sheet = parse_upload("x.csv", content)
    assert sheet.rows[0][:2] == ["0012", "José"]


def test_csv_skips_blank_rows_but_keeps_line_numbers() -> None:
    content = csv_bytes("NIM,Kode Tagihan\n1,A\n,\n\n2,B\n")
    sheet = parse_upload("x.csv", content)
    assert sheet.rows == [["1", "A"], ["2", "B"]]
    assert sheet.row_numbers == [2, 5]


def test_xlsx_numeric_cells() -> None:
    content = make_xlsx(
        [
            ["NIM", "Nama", "No WA", "Kode Tagihan", "Nominal", "Jatuh Tempo", "Status"],
            [
                20210001.0,
                "Budi",
                6.28123456789e11,
                "INV-1",
                1500000.0,
                datetime(2026, 11, 15),
                "Lunas",
            ],
        ]
    )
    sheet = parse_upload("export.xlsx", content)
    assert sheet.row_numbers == [2]
    row = validated(sheet)
    assert isinstance(row, ValidRow)
    assert row.nim == "20210001"
    assert row.phone == "+628123456789"
    assert row.due_date == date(2026, 11, 15)
    assert row.amount == 1_500_000
    assert row.status == "paid"
    assert row.paid_at is None  # a paid row may come without a payment date


def test_xlsx_skips_blank_rows_and_reads_first_sheet_only() -> None:
    content = make_xlsx([["NIM", "Kode Tagihan"], ["1", "A"], [None, None], ["2", "B"]])
    sheet = parse_upload("x.xlsx", content)
    assert sheet.rows == [["1", "A"], ["2", "B"]]
    assert sheet.row_numbers == [2, 4]


def test_rejects_over_limits_and_bad_extension() -> None:
    with pytest.raises(ImportFileError, match=r"Format berkas tidak didukung"):
        parse_upload("data.xls", b"x")
    with pytest.raises(ImportFileError, match=r"Format berkas tidak didukung"):
        parse_upload("data", b"x")
    with pytest.raises(ImportFileError, match=r"5 MB"):
        parse_upload("big.csv", b"a" * (MAX_FILE_BYTES + 1))
    many = "NIM\n" + "1\n" * (MAX_DATA_ROWS + 1)
    with pytest.raises(ImportFileError, match=r"20\.000"):
        parse_upload("many.csv", csv_bytes(many))
    exactly = "NIM\n" + "1\n" * MAX_DATA_ROWS
    assert len(parse_upload("ok.csv", csv_bytes(exactly)).rows) == MAX_DATA_ROWS
    with pytest.raises(ImportFileError, match=r"20\.000"):
        parse_upload("many.xlsx", make_xlsx([["NIM"], *([["1"]] * (MAX_DATA_ROWS + 1))]))


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("empty.csv", b"", "Berkas kosong."),
        ("blank.csv", b"\n" + b"\n,,\n", "Berkas kosong."),
        ("header-only.csv", b"NIM,Nominal\n", "Berkas tidak berisi baris data."),
        (
            "header-only.xlsx",
            make_xlsx([["NIM", "Nominal"]]),
            "Berkas tidak berisi baris data.",
        ),
        ("corrupt.xlsx", b"this is not a zip file", "Berkas .xlsx tidak dapat dibaca."),
        pytest.param(
            "huge-field.csv",
            b"NIM\n" + b"x" * 200_000 + b"\n",
            "Berkas CSV tidak dapat dibaca.",
            id="huge-field",
        ),
    ],
)
def test_empty_or_unreadable_files_are_import_errors(
    filename: str, content: bytes, message: str
) -> None:
    with pytest.raises(ImportFileError) as excinfo:
        parse_upload(filename, content)
    assert str(excinfo.value) == message


def rewrite_sheet_dimension(content: bytes, ref: str) -> bytes:
    """Return the workbook with its first sheet's declared dimension replaced by ``ref``."""
    source = zipfile.ZipFile(io.BytesIO(content))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode("utf-8")
                assert "<dimension" in text
                data = re.sub(r'<dimension ref="[^"]*"', f'<dimension ref="{ref}"', text).encode()
            target.writestr(item, data)
    return buffer.getvalue()


@pytest.mark.parametrize("ref", ["A1:A1", "A1:B2", "A1"])
def test_xlsx_with_a_wrong_declared_dimension_is_still_read_completely(ref: str) -> None:
    content = make_xlsx(
        [["NIM", "Kode Tagihan", "Nominal"], ["1", "A", 100], ["2", "B", 200], ["3", "C", 300]]
    )
    sheet = parse_upload("x.xlsx", rewrite_sheet_dimension(content, ref))
    assert sheet.headers == ["NIM", "Kode Tagihan", "Nominal"]
    assert sheet.rows == [["1", "A", 100], ["2", "B", 200], ["3", "C", 300]]
    assert sheet.row_numbers == [2, 3, 4]


# --- headers ----------------------------------------------------------------------------------


def test_resolve_columns_matches_aliases_after_normalisation() -> None:
    headers = [
        "\ufeff NIM ",
        "Nama\u00a0Mahasiswa",
        "PROGRAM  STUDI",
        "no. hp",
        "Kode_Tagihan",
        "Total Tagihan",
        "Tanggal Jatuh Tempo",
        "Status Pembayaran",
        "Tgl Bayar",
        "Kanal",
        "Periode",
        "Angkatan",
    ]
    columns = resolve_columns(headers, "sipema")
    assert columns == {
        "nim": 0, "name": 1, "study_program": 2, "phone": 3, "invoice_code": 4, "amount": 5,
        "due_date": 6, "status": 7, "paid_at": 8, "paid_via": 9, "semester": 10, "cohort": 11,
    }  # fmt: skip


def test_resolve_columns_takes_the_first_match_per_field() -> None:
    columns = resolve_columns(
        ["NIM", "Kode Tagihan", "Nominal", "Jumlah", "Jatuh Tempo", "Status"], "siakad"
    )
    assert columns["amount"] == 2


def test_missing_required_header_names_the_field() -> None:
    with pytest.raises(ImportFileError) as excinfo:
        resolve_columns(["NIM", "Kode Tagihan", "Nominal"], "siakad")
    assert str(excinfo.value) == "Kolom wajib tidak ditemukan: jatuh tempo, status."


def test_unknown_source_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="source"):
        resolve_columns(["NIM"], "other")


def test_alias_tables_are_consistent() -> None:
    assert set(REQUIRED_FIELDS) <= set(CANONICAL_FIELDS)
    assert set(FIELD_LABELS_ID) == set(CANONICAL_FIELDS)
    for table in ALIASES.values():
        assert set(table) == set(CANONICAL_FIELDS)
        flat = [alias for aliases in table.values() for alias in aliases]
        assert len(flat) == len(set(flat)), "an alias must belong to one field only"
        assert all(alias == normalize_header(alias) for alias in flat)


# --- value parsers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["1.500.000", "Rp1.500.000", "Rp 1.500.000,00", "1,500,000", "1500000", 1500000.0, 1500000,
     "1.500.000,00", "1,500,000.00", "  Rp. 1.500.000 ", "1500000.00", "1500000,00"],
)  # fmt: skip
def test_parse_amount_accepts_common_formats(raw: object) -> None:
    assert parse_amount(raw) == 1_500_000


@pytest.mark.parametrize(
    "raw",
    ["0", "-5", "abc", "", "  ", None, 0, -3, 0.0, 1500000.5, "1500000,50", "1.50", "1.500,000",
     "1,5,5", "12.34.567", True, float("nan"), float("inf"), 10**30, "1e6", "Rp"],
)  # fmt: skip
def test_parse_amount_rejects_bad_values(raw: object) -> None:
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("1" * 5000, id="long-digits"),
        pytest.param("1." + "000." * 2000 + "000", id="long-grouped"),
        pytest.param("1," + "0" * 5000, id="long-decimals"),
        pytest.param(10**5000, id="huge-int"),
    ],
)
def test_parse_amount_never_raises_on_hostile_text(raw: object) -> None:
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("9" * 5000, id="long-digits"),
        pytest.param("1" * 5000 + ".0", id="long-digits-decimal"),
        pytest.param(10**5000, id="huge-int"),
        pytest.param(2.0**1000, id="huge-float"),
    ],
)
def test_parse_date_never_raises_on_hostile_numbers(raw: object) -> None:
    assert parse_date(raw) is None


def test_parse_amount_thousand_group_versus_decimals() -> None:
    assert parse_amount("1.500") == 1500
    assert parse_amount("1,500") == 1500
    assert parse_amount("500.000") == 500_000


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-15", date(2026, 11, 15)),
        ("15/11/2026", date(2026, 11, 15)),
        ("15-11-2026", date(2026, 11, 15)),
        ("5/1/2026", date(2026, 1, 5)),
        (" 2026-11-15 ", date(2026, 11, 15)),
        ("2026-11-15 00:00:00", date(2026, 11, 15)),
        (datetime(2026, 11, 15, 10, 30), date(2026, 11, 15)),
        (date(2026, 11, 15), date(2026, 11, 15)),
        (46341.0, date(2026, 11, 15)),
        (46341, date(2026, 11, 15)),
        ("46341", date(2026, 11, 15)),
    ],
)
def test_parse_date_accepts(raw: object, expected: date) -> None:
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["31/02/2026", "besok", "", None, "15/11/26", "2026-13-01", "11/15/2026", 5, 19999.0, 80001.0,
     12.5, True, "20261115", "2026/11/15", float("nan")],
)  # fmt: skip
def test_parse_date_rejects(raw: object) -> None:
    assert parse_date(raw) is None


@pytest.mark.parametrize(
    "raw",
    ["lunas", "Sudah Bayar", "paid", "  LUNAS ", "terbayar", "1", "y", "ya", "true", 1, 1.0, True],
)
def test_parse_status_paid(raw: object) -> None:
    assert parse_status(raw) == "paid"


@pytest.mark.parametrize(
    "raw",
    [
        "belum lunas",
        "Belum Bayar",
        "unpaid",
        "BELUM  LUNAS",
        "0",
        "n",
        "tidak",
        "false",
        0,
        0.0,
        False,
    ],
)
def test_parse_status_unpaid(raw: object) -> None:
    assert parse_status(raw) == "unpaid"


@pytest.mark.parametrize("raw", ["?", "", None, "mungkin", 2, "lunas?"])
def test_parse_status_unknown(raw: object) -> None:
    assert parse_status(raw) is None


# --- row validation ---------------------------------------------------------------------------


def row_from(cells: Mapping[str, object], default_semester: str | None = "2026/1") -> object:
    columns = {name: index for index, name in enumerate(cells)}
    return validate_row(
        7, list(cells.values()), columns, default_semester=default_semester, source="siakad"
    )


BASE = {
    "nim": "20210001",
    "invoice_code": "INV-1",
    "amount": "1.500.000",
    "due_date": "2026-11-15",
    "status": "belum lunas",
}


def test_valid_row_with_all_fields() -> None:
    result = row_from(
        {
            **BASE,
            "name": " Budi Santoso ",
            "study_program": "Teknik Informatika",
            "cohort": 2022.0,
            "phone": "0812-3456-789",
            "semester": "2026/2",
            "status": "Lunas",
            "paid_at": "10/11/2026",
            "paid_via": "SIPEMA",
        }
    )
    assert result == ValidRow(
        row_number=7,
        nim="20210001",
        name="Budi Santoso",
        study_program="Teknik Informatika",
        cohort="2022",
        phone="+628123456789",
        invoice_code="INV-1",
        semester="2026/2",
        amount=1_500_000,
        due_date=date(2026, 11, 15),
        status="paid",
        paid_at=date(2026, 11, 10),
        paid_via="sipema",
        phone_raw="0812-3456-789",
    )


def test_minimal_row_leaves_optional_fields_empty() -> None:
    result = row_from(BASE)
    assert isinstance(result, ValidRow)
    assert (result.name, result.study_program, result.cohort, result.phone) == (
        None,
        None,
        None,
        None,
    )
    assert (result.paid_at, result.paid_via, result.phone_raw) == (None, None, None)
    assert result.semester == "2026/1"


def test_leading_zeros_in_text_cells_are_preserved_and_floats_become_digits() -> None:
    result = row_from({**BASE, "nim": "0012345", "invoice_code": 998877.0})
    assert isinstance(result, ValidRow)
    assert (result.nim, result.invoice_code) == ("0012345", "998877")


def test_short_row_treats_missing_cells_as_empty() -> None:
    columns = {"nim": 0, "invoice_code": 1, "amount": 2, "due_date": 3, "status": 4, "name": 5}
    result = validate_row(
        3, ["1", "A", "100", "2026-01-02", "lunas"], columns, default_semester="S1", source="siakad"
    )
    assert isinstance(result, ValidRow)
    assert result.name is None


def test_row_without_semester_uses_default_or_errors() -> None:
    with_default = row_from(BASE, default_semester="2026/1")
    assert isinstance(with_default, ValidRow)
    assert with_default.semester == "2026/1"

    without = row_from(BASE, default_semester=None)
    assert isinstance(without, list)
    assert [(issue.field, issue.row_number) for issue in without] == [("semester", 7)]

    column_wins = row_from({**BASE, "semester": "2025/2"}, default_semester="2026/1")
    assert isinstance(column_wins, ValidRow)
    assert column_wins.semester == "2025/2"

    blank_cell_falls_back = row_from({**BASE, "semester": "  "}, default_semester="2026/1")
    assert isinstance(blank_cell_falls_back, ValidRow)
    assert blank_cell_falls_back.semester == "2026/1"


def test_row_issues_are_indonesian_and_per_field() -> None:
    result = row_from(
        {
            "nim": " ",
            "invoice_code": "",
            "amount": "abc",
            "due_date": "besok",
            "status": "mungkin",
            "paid_at": "kemarin",
            "paid_via": "bank",
        }
    )
    assert isinstance(result, list)
    messages = {issue.field: issue.message for issue in result}
    assert messages == {
        "nim": "NIM wajib diisi.",
        "invoice_code": "Kode tagihan wajib diisi.",
        "amount": "Nominal tidak valid.",
        "due_date": "Tanggal jatuh tempo tidak valid (gunakan YYYY-MM-DD atau DD/MM/YYYY).",
        "status": "Status tidak dikenali (gunakan lunas atau belum lunas).",
        "paid_at": "Tanggal bayar tidak valid (gunakan YYYY-MM-DD atau DD/MM/YYYY).",
        "paid_via": "Kanal pembayaran tidak dikenali (gunakan siakad atau sipema).",
    }
    assert all(issue.row_number == 7 for issue in result)


def test_hostile_cells_give_issues_instead_of_exceptions() -> None:
    result = row_from(
        {
            **BASE,
            "amount": "1" * 5000,
            "due_date": "9" * 5000,
            "nim": 10**5000,
            "phone": "9" * 5000,
            "status": "x" * 5000,
        }
    )
    assert isinstance(result, list)
    assert {issue.field for issue in result} == {"amount", "due_date", "nim", "status"}


@pytest.mark.parametrize("field", ["nim", "invoice_code", "cohort"])
def test_fractional_numbers_are_not_accepted_as_identifiers(field: str) -> None:
    result = row_from({**BASE, field: 20210001.5})
    assert isinstance(result, list)
    assert [issue.field for issue in result] == [field]
    assert "tidak valid" in result[0].message


def test_scientific_notation_phone_text_end_to_end() -> None:
    result = row_from({**BASE, "phone": "6.28123456789E+11"})
    assert isinstance(result, ValidRow)
    assert result.phone == "+628123456789"
    assert result.phone_raw == "6.28123456789E+11"


def test_over_long_fields_are_issues() -> None:
    result = row_from(
        {
            **BASE,
            "nim": "1" * 31,
            "invoice_code": "I" * 51,
            "name": "N" * 151,
            "study_program": "P" * 101,
            "cohort": "2" * 11,
            "semester": "S" * 21,
        }
    )
    assert isinstance(result, list)
    assert {issue.field for issue in result} == {
        "nim", "invoice_code", "name", "study_program", "cohort", "semester",
    }  # fmt: skip
    assert "maksimal 30 karakter" in next(i.message for i in result if i.field == "nim")


def test_boundary_lengths_are_accepted() -> None:
    result = row_from({**BASE, "nim": "1" * 30, "invoice_code": "I" * 50, "name": "N" * 150})
    assert isinstance(result, ValidRow)


def test_invalid_phone_is_not_a_row_error() -> None:
    result = row_from({**BASE, "phone": "12345"})
    assert isinstance(result, ValidRow)
    assert result.phone is None
    assert result.phone_raw == "12345"


def test_paid_at_with_invalid_value_is_an_issue_but_missing_is_fine() -> None:
    ok = row_from({**BASE, "status": "lunas", "paid_at": ""})
    assert isinstance(ok, ValidRow)
    assert ok.paid_at is None
    bad = row_from({**BASE, "status": "lunas", "paid_at": "n/a"})
    assert isinstance(bad, list)
    assert [issue.field for issue in bad] == ["paid_at"]


def test_paid_at_is_dropped_for_unpaid_rows() -> None:
    result = row_from({**BASE, "paid_at": "2026-11-10"})
    assert isinstance(result, ValidRow)
    assert result.paid_at is None


def test_paid_via_blank_means_not_provided() -> None:
    result = row_from({**BASE, "paid_via": " "})
    assert isinstance(result, ValidRow)
    assert result.paid_via is None


def test_row_issue_is_a_plain_value_object() -> None:
    issue = RowIssue(row_number=2, field="nim", message="x")
    assert (issue.row_number, issue.field, issue.message) == (2, "nim", "x")


def test_siakad_rekap_tunggakan_csv_parsing() -> None:
    csv_text = (
        '" Kode (-) | Angkatan (Semua) ",,,,,,,,,\n'
        ",,,,,,,,,\n"
        ",,,,,,,,,\n"
        ",,,,,,,,,\n"
        "No,PRODI,Jenis Kelas,NIM,Nama,No Hp,Tahun Akademik,Komponen Biaya,Rincian,Rekap\n"
        "1,22201 - Teknik Sipil,Kelas Pagi,201501545,AKBAR HARYO BIMO,,2022/2023 Genap,"
        "001 - DU,700000,700000\n"
        "2,22201 - Teknik Sipil,Kelas Sore,201701702,Adie Jati Kusumo,085959123666,"
        "2022/2023 Genap,001 - DU,200000,800000\n"
        ",,,,,,2022/2023 Genap,002 - SPP,500000,\n"
        ",,,,,,2022/2023 Genap,006 - Biaya Kegiatan Mahasiswa,100000,\n"
        "TOTAL,,,,,,,,1500000,1500000\n"
    )
    sheet = parse_upload("siakad_tunggakan.csv", csv_text.encode("utf-8"))
    assert len(sheet.rows) == 2
    assert sheet.row_numbers == [6, 7]
    assert sheet.rows[0][0] == "201501545"
    assert sheet.rows[0][1] == "AKBAR HARYO BIMO"
    assert sheet.rows[0][3] == "2015"  # cohort extracted from NIM
    assert sheet.rows[0][5] == "TGK-201501545-20222"  # invoice code
    assert float(str(sheet.rows[0][7])) == 700000.0  # amount
    assert sheet.rows[1][0] == "201701702"
    assert sheet.rows[1][4] == "085959123666"
    assert sheet.rows[1][5] == "TGK-201701702-20222"
    assert float(str(sheet.rows[1][7])) == 800000.0


def test_siakad_rekap_tunggakan_xlsx_parsing() -> None:
    rows = [
        ["Kode (-) | Angkatan (Semua)"],
        [None],
        [None],
        [None],
        [
            "No",
            "PRODI",
            "Jenis Kelas",
            "NIM",
            "Nama",
            "No Hp",
            "Tahun Akademik",
            "Komponen Biaya",
            "Rincian",
            "Rekap",
        ],
        [
            1,
            "55201 - Teknik Informatika",
            "Kelas Pagi",
            "52022012000",
            "SYAFITA KHAIRUNNISA",
            "08119486477",
            "2024/2025 Ganjil",
            "SPP",
            2700000,
            2700000,
        ],
        ["TOTAL", None, None, None, None, None, None, None, 2700000, 2700000],
    ]
    sheet = parse_upload("siakad.xlsx", make_xlsx(rows))
    assert len(sheet.rows) == 1
    assert sheet.row_numbers == [6]
    assert sheet.rows[0][0] == "52022012000"
    assert sheet.rows[0][1] == "SYAFITA KHAIRUNNISA"
    assert sheet.rows[0][3] == "2022"
    assert sheet.rows[0][5] == "TGK-52022012000-20241"
    assert sheet.rows[0][7] == 2700000
    assert sheet.rows[0][9] == "belum lunas"
