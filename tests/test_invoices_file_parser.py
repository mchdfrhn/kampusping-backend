"""Tests for parsing invoice codes from CSV/XLSX files (used by delete_invoices script)."""

import io

import pytest
from openpyxl import Workbook

from app.modules.invoices.file_parser import extract_invoice_codes_from_file


def make_xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_extract_invoice_codes_csv() -> None:
    content = b"kode tagihan,nama\nINV-001,Budi\nINV-002,Ani\n"
    codes = extract_invoice_codes_from_file("test.csv", content)
    assert codes == ["INV-001", "INV-002"]


def test_extract_invoice_codes_single_column_csv() -> None:
    content = b"INV-101\nINV-102\nINV-103\n"
    codes = extract_invoice_codes_from_file("codes.csv", content)
    assert codes == ["INV-101", "INV-102", "INV-103"]


def test_extract_invoice_codes_xlsx() -> None:
    content = make_xlsx([["NIM", "Kode Tagihan", "Nominal"], ["2021001", "INV-999", 500000]])
    codes = extract_invoice_codes_from_file("test.xlsx", content)
    assert codes == ["INV-999"]


def test_extract_invoice_codes_missing_column() -> None:
    content = b"nama,prodi\nBudi,Informatika\n"
    with pytest.raises(Exception, match="Kolom kode tagihan tidak ditemukan"):
        extract_invoice_codes_from_file("test.csv", content)

