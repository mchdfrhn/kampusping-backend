"""Read ``.csv`` / ``.xlsx`` uploads into a header row plus raw rows, and map headers to fields.

The parser never interprets values: cells keep their native type (openpyxl hands over numbers and
datetimes) and are only converted during row validation. Every failure surfaces as
``ImportFileError`` with a message that can be shown to staff as-is.
"""

import csv
import io
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date

from openpyxl import load_workbook

from app.modules.imports.columns import (
    ALIASES,
    FIELD_LABELS_ID,
    REQUIRED_FIELDS,
    normalize_header,
)
from app.modules.imports.validation import cell_text

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DATA_ROWS = 20_000
# Blank rows are skipped, but an endless run of them must not keep the parser busy forever.
_MAX_SCANNED_RECORDS = MAX_DATA_ROWS * 5
_DELIMITERS = (",", ";", "\t")
_SUPPORTED_EXTENSIONS = (".csv", ".xlsx")


class ImportFileError(Exception):
    """The upload cannot be used at all (format, size, structure); the message is Indonesian."""


@dataclass(frozen=True)
class ParsedSheet:
    """Header texts and raw data rows; ``row_numbers[i]`` is the 1-based line of ``rows[i]``.

    Row numbers count every record of the file including the header and blank rows, so they match
    what staff see in a spreadsheet (the first data row is normally 2).
    """

    headers: list[str]
    rows: list[list[object]]
    row_numbers: list[int]


SIAKAD_REKAP_HEADERS = [
    "NIM",
    "Nama",
    "Program Studi",
    "Angkatan",
    "Nomor WA",
    "Kode Tagihan",
    "Semester",
    "Nominal",
    "Jatuh Tempo",
    "Status",
]


def _try_parse_siakad_rekap(records: Sequence[tuple[int, list[object]]]) -> ParsedSheet | None:
    header_idx = -1
    col_map: dict[str, int] = {}

    for i, (_, cells) in enumerate(records[:30]):
        if _is_blank(cells):
            continue
        norm_cells = [normalize_header(c) for c in cells]
        if "nim" in norm_cells and "rekap" in norm_cells:
            header_idx = i
            for idx, name in enumerate(norm_cells):
                if name == "nim":
                    col_map["nim"] = idx
                elif name in ("nama", "nama mahasiswa"):
                    col_map["nama"] = idx
                elif name in ("prodi", "program studi"):
                    col_map["prodi"] = idx
                elif name in ("no hp", "no wa", "telepon", "whatsapp", "nomor wa", "nomor hp"):
                    col_map["phone"] = idx
                elif name in ("tahun akademik", "semester"):
                    col_map["semester"] = idx
                elif name == "rekap":
                    col_map["rekap"] = idx
            break

    if header_idx == -1 or "nim" not in col_map or "rekap" not in col_map:
        return None

    rows: list[list[object]] = []
    row_numbers: list[int] = []
    today_iso = date.today().isoformat()

    nim_idx = col_map["nim"]
    rekap_idx = col_map["rekap"]
    nama_idx = col_map.get("nama")
    prodi_idx = col_map.get("prodi")
    phone_idx = col_map.get("phone")
    sem_idx = col_map.get("semester")

    for number, cells in records[header_idx + 1 :]:
        if number > _MAX_SCANNED_RECORDS or len(rows) >= MAX_DATA_ROWS:
            raise _too_many_rows()
        if _is_blank(cells):
            continue

        first_text = cell_text(cells[0] if cells else "").upper()
        if first_text == "TOTAL":
            break

        nim_val = cells[nim_idx] if nim_idx < len(cells) else None
        rekap_val = cells[rekap_idx] if rekap_idx < len(cells) else None

        if nim_val is None or rekap_val is None:
            continue

        nim = cell_text(nim_val)
        if not nim:
            continue

        nama = cell_text(cells[nama_idx]) if nama_idx is not None and nama_idx < len(cells) else ""
        prodi = (
            cell_text(cells[prodi_idx]) if prodi_idx is not None and prodi_idx < len(cells) else ""
        )
        phone = (
            cell_text(cells[phone_idx]) if phone_idx is not None and phone_idx < len(cells) else ""
        )
        ta = cell_text(cells[sem_idx]) if sem_idx is not None and sem_idx < len(cells) else ""

        m_cohort = re.search(r"20\d{2}", nim)
        cohort = m_cohort.group(0) if m_cohort else ""

        m_sem = re.match(r"(\d{4})/\d{4}\s+(Ganjil|Genap)", ta, re.IGNORECASE)
        if m_sem:
            sem_code = m_sem.group(1) + ("1" if m_sem.group(2).lower() == "ganjil" else "2")
        else:
            sem_code = re.sub(r"[^a-zA-Z0-9]", "", ta) or "1"

        code = f"TGK-{nim}-{sem_code}"

        row = [
            nim,
            nama,
            prodi,
            cohort,
            phone,
            code,
            ta,
            rekap_val,
            today_iso,
            "belum lunas",
        ]
        rows.append(row)
        row_numbers.append(number)

    if not rows:
        raise ImportFileError("Berkas tidak berisi baris data.")

    return ParsedSheet(headers=SIAKAD_REKAP_HEADERS, rows=rows, row_numbers=row_numbers)


def parse_upload(filename: str, content: bytes) -> ParsedSheet:
    """Parse an uploaded file by its extension; raise ``ImportFileError`` when it is unusable."""
    name = filename.strip().lower()
    if not name.endswith(_SUPPORTED_EXTENSIONS):
        raise ImportFileError("Format berkas tidak didukung (gunakan .csv atau .xlsx).")
    if len(content) > MAX_FILE_BYTES:
        raise ImportFileError("Ukuran berkas melebihi batas 5 MB.")
    if not content.strip():
        raise ImportFileError("Berkas kosong.")

    records = list(_csv_records(content) if name.endswith(".csv") else _xlsx_records(content))
    rekap_sheet = _try_parse_siakad_rekap(records)
    if rekap_sheet is not None:
        return rekap_sheet
    return _collect(records)


def _collect(records: Iterable[tuple[int, list[object]]]) -> ParsedSheet:
    headers: list[str] | None = None
    rows: list[list[object]] = []
    row_numbers: list[int] = []
    for number, cells in records:
        if number > _MAX_SCANNED_RECORDS:
            raise _too_many_rows()
        if _is_blank(cells):
            continue
        if headers is None:
            headers = ["" if cell is None else str(cell) for cell in cells]
            continue
        if len(rows) >= MAX_DATA_ROWS:
            raise _too_many_rows()
        rows.append(cells)
        row_numbers.append(number)

    if headers is None:
        raise ImportFileError("Berkas kosong.")
    if not rows:
        raise ImportFileError("Berkas tidak berisi baris data.")
    return ParsedSheet(headers=headers, rows=rows, row_numbers=row_numbers)


def _too_many_rows() -> ImportFileError:
    return ImportFileError("Jumlah baris melebihi batas 20.000 baris data.")


def _is_blank(cells: list[object]) -> bool:
    return all(cell is None or (isinstance(cell, str) and not cell.strip()) for cell in cells)


def _csv_records(content: bytes) -> Iterator[tuple[int, list[object]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252", errors="replace")

    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        for number, record in enumerate(reader, start=1):
            yield number, list(record)
    except csv.Error as exc:
        raise ImportFileError("Berkas CSV tidak dapat dibaca.") from exc


def _sniff_delimiter(text: str) -> str:
    """Pick the delimiter that occurs most in the first non-blank line; ``,`` when none does."""
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    best = ","
    best_count = first_line.count(best)
    for candidate in _DELIMITERS[1:]:
        if (count := first_line.count(candidate)) > best_count:
            best, best_count = candidate, count
    return best


def _xlsx_records(content: bytes) -> Iterator[tuple[int, list[object]]]:
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # corrupt zip, wrong format, malformed XML: all "unreadable"
        raise ImportFileError("Berkas .xlsx tidak dapat dibaca.") from exc
    try:
        if not workbook.worksheets:
            raise ImportFileError("Berkas .xlsx tidak memiliki lembar kerja.")
        try:
            sheet = workbook.worksheets[0]
            # Third-party exporters often write a wrong <dimension>; scan the real data instead.
            sheet.reset_dimensions()
            for number, record in enumerate(sheet.iter_rows(values_only=True), start=1):
                yield number, list(record)
        except ImportFileError:
            raise
        except Exception as exc:  # malformed sheet XML surfaces while iterating
            raise ImportFileError("Berkas .xlsx tidak dapat dibaca.") from exc
    finally:
        workbook.close()


def resolve_columns(headers: list[str], source: str) -> dict[str, int]:
    """Map canonical fields to the index of their first matching header.

    Headers are normalised before the alias lookup. Raises ``ImportFileError`` naming the missing
    required fields (by their Indonesian labels) when any is absent.
    """
    aliases = ALIASES.get(source)
    if aliases is None:
        raise ValueError(f"Unknown import source: {source!r}")

    normalised = [normalize_header(header) for header in headers]
    columns: dict[str, int] = {}
    for field, names in aliases.items():
        for index, header in enumerate(normalised):
            if header in names:
                columns[field] = index
                break

    missing = [FIELD_LABELS_ID[field] for field in REQUIRED_FIELDS if field not in columns]
    if missing:
        raise ImportFileError(f"Kolom wajib tidak ditemukan: {', '.join(missing)}.")
    return columns
