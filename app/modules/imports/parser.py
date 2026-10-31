"""Read ``.csv`` / ``.xlsx`` uploads into a header row plus raw rows, and map headers to fields.

The parser never interprets values: cells keep their native type (openpyxl hands over numbers and
datetimes) and are only converted during row validation. Every failure surfaces as
``ImportFileError`` with a message that can be shown to staff as-is.
"""

import csv
import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from openpyxl import load_workbook

from app.modules.imports.columns import (
    ALIASES,
    FIELD_LABELS_ID,
    REQUIRED_FIELDS,
    normalize_header,
)

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


def parse_upload(filename: str, content: bytes) -> ParsedSheet:
    """Parse an uploaded file by its extension; raise ``ImportFileError`` when it is unusable."""
    name = filename.strip().lower()
    if not name.endswith(_SUPPORTED_EXTENSIONS):
        raise ImportFileError("Format berkas tidak didukung (gunakan .csv atau .xlsx).")
    if len(content) > MAX_FILE_BYTES:
        raise ImportFileError("Ukuran berkas melebihi batas 5 MB.")
    if not content.strip():
        raise ImportFileError("Berkas kosong.")

    records = _csv_records(content) if name.endswith(".csv") else _xlsx_records(content)
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
            for number, record in enumerate(
                workbook.worksheets[0].iter_rows(values_only=True), start=1
            ):
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
