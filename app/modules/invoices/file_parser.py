"""Extract invoice codes from an uploaded CSV or Excel file."""

from app.core.constants import VALIDATION_ERROR
from app.core.exceptions import AppException
from app.modules.imports.columns import normalize_header
from app.modules.imports.parser import ImportFileError, parse_upload
from app.modules.imports.validation import cell_text

INVOICE_CODE_ALIASES: frozenset[str] = frozenset(
    {
        "kode tagihan",
        "kode_tagihan",
        "kodetagihan",
        "invoice_code",
        "invoice code",
        "invoicecode",
        "no tagihan",
        "nomor tagihan",
        "notagihan",
        "nomortagihan",
        "id tagihan",
        "idtagihan",
        "no invoice",
        "nomor invoice",
        "kode bayar",
        "kodebayar",
        "va",
        "virtual account",
        "virtualaccount",
        "bill id",
        "billid",
        "billing id",
        "billingid",
        "kode",
    }
)


def extract_invoice_codes_from_file(filename: str, content: bytes) -> list[str]:
    """Parse upload and extract unique invoice codes.

    Accepts .csv or .xlsx up to 5 MB. Looks for a header matching invoice code aliases,
    or uses the single column if only one column exists.
    """
    try:
        sheet = parse_upload(filename, content)
    except ImportFileError as exc:
        raise AppException(str(exc), status_code=422, code=VALIDATION_ERROR) from exc

    norm_headers = [normalize_header(h) for h in sheet.headers]
    target_idx: int | None = None
    for idx, norm_h in enumerate(norm_headers):
        if norm_h in INVOICE_CODE_ALIASES:
            target_idx = idx
            break

    codes: list[str] = []
    seen: set[str] = set()

    if target_idx is not None:
        for row in sheet.rows:
            if target_idx < len(row):
                val = cell_text(row[target_idx]).strip()
                if val and val not in seen:
                    seen.add(val)
                    codes.append(val)
    elif len(sheet.headers) == 1:
        # Single column file: check if header row itself is an invoice code rather than a label
        header_val = cell_text(sheet.headers[0]).strip()
        if header_val and normalize_header(header_val) not in INVOICE_CODE_ALIASES:
            seen.add(header_val)
            codes.append(header_val)
        for row in sheet.rows:
            if row:
                val = cell_text(row[0]).strip()
                if val and val not in seen:
                    seen.add(val)
                    codes.append(val)
    else:
        raise AppException(
            "Kolom kode tagihan tidak ditemukan. Pastikan berkas memiliki kolom "
            "'kode tagihan' atau 'invoice_code'.",
            status_code=422,
            code=VALIDATION_ERROR,
        )

    if not codes:
        raise AppException(
            "Berkas tidak berisi kode tagihan yang dapat diproses.",
            status_code=422,
            code=VALIDATION_ERROR,
        )

    return codes

