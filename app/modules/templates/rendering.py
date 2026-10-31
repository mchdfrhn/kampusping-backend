"""Pure message rendering: no database access, no I/O.

Template bodies are plain WhatsApp text, so values are inserted as-is (no escaping).
"""

import re
from collections.abc import Mapping
from datetime import date

from app.modules.invoices.constants import InvoiceSource
from app.modules.invoices.model import Invoice
from app.modules.settings.model import AppSettings
from app.modules.students.model import Student

from .model import MessageTemplate

ALLOWED_VARIABLES: tuple[str, ...] = (
    "nama",
    "nim",
    "semester",
    "nominal",
    "tanggal_jatuh_tempo",
    "cara_bayar",
    "link_pembayaran",
    "kontak_keuangan",
    "nama_kampus",
)

# Appended to every outgoing message (NFR-03). Never part of the stored template body.
OPT_OUT_FOOTER = "Balas STOP untuk berhenti."

MONTH_NAMES_ID: tuple[str, ...] = (
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
)

PAYMENT_CHANNEL_SIAKAD = "SIAKAD/Civitas"
PAYMENT_CHANNEL_SIPEMA = "SIPEMA"

# Fixed example data for template previews.
SAMPLE_CONTEXT: dict[str, str] = {
    "nama": "Budi Santoso",
    "nim": "2021001",
    "semester": "Ganjil 2026/2027",
    "nominal": "1.250.000",
    "tanggal_jatuh_tempo": "15 November 2026",
    "cara_bayar": "SIAKAD/Civitas",
    "link_pembayaran": "https://siakad.contoh.ac.id/bayar",
    "kontak_keuangan": "0812-0000-0000",
    "nama_kampus": "Kampus Contoh",
}

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def unknown_variables(body: str) -> list[str]:
    """Placeholder names not in ``ALLOWED_VARIABLES``: unique, in order of first appearance."""
    found: list[str] = []
    for name in _PLACEHOLDER.findall(body):
        if name not in ALLOWED_VARIABLES and name not in found:
            found.append(name)
    return found


def format_rupiah(amount: int) -> str:
    """``1250000`` -> ``"1.250.000"`` (no currency symbol; templates write ``Rp`` themselves)."""
    return f"{amount:,}".replace(",", ".")


def format_date_id(value: date) -> str:
    """``date(2026, 11, 15)`` -> ``"15 November 2026"``."""
    return f"{value.day} {MONTH_NAMES_ID[value.month - 1]} {value.year}"


def render_body(body: str, context: Mapping[str, str]) -> str:
    """Replace ``{{name}}`` placeholders. Unknown names stay untouched; callers validate first."""

    def substitute(match: re.Match[str]) -> str:
        return context.get(match.group(1), match.group(0))

    return _PLACEHOLDER.sub(substitute, body)


def with_opt_out_footer(text: str) -> str:
    """Exactly one footer at the end: trailing copies are stripped, then one is appended."""
    text = text.rstrip()
    while text.endswith(OPT_OUT_FOOTER):
        text = text.removesuffix(OPT_OUT_FOOTER).rstrip()
    return f"{text}\n\n{OPT_OUT_FOOTER}" if text else OPT_OUT_FOOTER


def build_context(*, student: Student, invoice: Invoice, settings: AppSettings) -> dict[str, str]:
    is_siakad = invoice.source == InvoiceSource.SIAKAD.value
    return {
        "nama": student.name,
        "nim": student.nim,
        "semester": invoice.semester,
        "nominal": format_rupiah(invoice.amount),
        "tanggal_jatuh_tempo": format_date_id(invoice.due_date),
        "cara_bayar": PAYMENT_CHANNEL_SIAKAD if is_siakad else PAYMENT_CHANNEL_SIPEMA,
        "link_pembayaran": (
            settings.payment_link_siakad if is_siakad else settings.payment_guide_sipema
        )
        or "",
        "kontak_keuangan": settings.finance_contact or "",
        "nama_kampus": settings.campus_name or "",
    }


def build_message_body(
    template: MessageTemplate, *, student: Student, invoice: Invoice, settings: AppSettings
) -> str:
    """The final WhatsApp text for one student and invoice, always ending in the opt-out footer."""
    context = build_context(student=student, invoice=invoice, settings=settings)
    return with_opt_out_footer(render_body(template.body, context))
