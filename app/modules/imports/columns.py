"""Header vocabulary of import files: canonical fields, required fields and header aliases.

SIAKAD and SIPEMA export formats are not known yet (open BRD question), so both sources share one
alias table. Adjust ``ALIASES`` per source when the real formats are available; no logic changes.
"""

SOURCES = ("siakad", "sipema")

CANONICAL_FIELDS = (
    "nim",
    "name",
    "study_program",
    "cohort",
    "phone",
    "invoice_code",
    "amount",
    "due_date",
    "status",
    "paid_at",
    "paid_via",
    "semester",
)
REQUIRED_FIELDS = ("nim", "invoice_code", "amount", "due_date", "status")

# Indonesian names, used in messages shown to staff.
FIELD_LABELS_ID: dict[str, str] = {
    "nim": "NIM",
    "name": "nama",
    "study_program": "program studi",
    "cohort": "angkatan",
    "phone": "nomor WA",
    "invoice_code": "kode tagihan",
    "amount": "nominal",
    "due_date": "jatuh tempo",
    "status": "status",
    "paid_at": "tanggal bayar",
    "paid_via": "kanal pembayaran",
    "semester": "semester",
}

# Aliases are stored already normalised (see ``normalize_header``).
_COMMON_ALIASES: dict[str, tuple[str, ...]] = {
    "nim": ("nim", "nomor induk mahasiswa", "no induk", "npm", "student id"),
    "name": ("nama", "name", "nama mahasiswa", "nama lengkap", "student name"),
    "study_program": ("prodi", "program studi", "study program", "jurusan"),
    "cohort": ("angkatan", "cohort", "tahun masuk", "tahun angkatan"),
    "phone": (
        "no wa",
        "no. wa",
        "nomor wa",
        "no whatsapp",
        "whatsapp",
        "wa",
        "hp",
        "no hp",
        "no. hp",
        "nomor hp",
        "no handphone",
        "telepon",
        "no telepon",
        "phone",
        "phone number",
    ),
    "invoice_code": (
        "kode tagihan",
        "invoice code",
        "no tagihan",
        "no. tagihan",
        "nomor tagihan",
        "id tagihan",
        "invoice id",
        "invoice number",
        "invoice no",
    ),
    "amount": (
        "nominal",
        "jumlah",
        "amount",
        "total tagihan",
        "tagihan",
        "jumlah tagihan",
        "nominal tagihan",
    ),
    "due_date": ("jatuh tempo", "tanggal jatuh tempo", "tgl jatuh tempo", "due date", "due"),
    "status": ("status", "status pembayaran", "status bayar", "payment status"),
    "paid_at": (
        "tanggal bayar",
        "tgl bayar",
        "tanggal pembayaran",
        "tgl pembayaran",
        "paid at",
        "payment date",
    ),
    "paid_via": ("kanal pembayaran", "kanal", "kanal bayar", "channel", "payment channel"),
    "semester": ("semester", "periode"),
}

ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    source: dict(_COMMON_ALIASES) for source in SOURCES
}


def normalize_header(value: object) -> str:
    """Lower-case, trim, collapse whitespace; drop BOM/zero-width marks; ``_`` is a space."""
    text = "" if value is None else str(value)
    text = text.replace("\ufeff", "").replace("\u200b", "").replace("\u00a0", " ")
    return " ".join(text.replace("_", " ").lower().split())
