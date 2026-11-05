"""Text shared by the Excel and PDF reports, so both say the same thing."""

import re
from datetime import date

from app.core.config import settings
from app.modules.templates.rendering import format_rupiah
from app.shared.clock import local_now

from .constants import WEEKLY_WINDOW_DAYS, ReportPreset
from .schema import ReportData

# Control characters other than tab and newline: a paste from a PDF or Word file can leave one in a
# name, and openpyxl refuses to write them (the export would fail with a 500).
_CONTROL_CHARACTERS = re.compile(
    "[" + chr(0) + "-" + chr(8) + chr(11) + chr(12) + chr(14) + "-" + chr(31) + "]"
)


def clean_text(value: str) -> str:
    """Drop the control characters a worksheet or a PDF cannot carry."""
    return _CONTROL_CHARACTERS.sub("", value)


def format_short_date(value: date) -> str:
    return value.strftime("%d-%m-%Y")


def format_rupiah_id(amount: int) -> str:
    """``1500000`` -> ``"Rp1.500.000"`` (the template helper writes the digits without ``Rp``)."""
    return f"Rp{format_rupiah(amount)}"


def format_percent_id(value: float) -> str:
    """``85.5`` -> ``"85,5%"``."""
    return f"{value:.1f}".replace(".", ",") + "%"


def format_period(data: ReportData) -> str:
    if data.preset is ReportPreset.WEEKLY and data.period_start and data.period_end:
        start = format_short_date(data.period_start)
        end = format_short_date(data.period_end)
        return f"{WEEKLY_WINDOW_DAYS} hari terakhir ({start} s/d {end})"
    return f"Semester: {data.semester}"


def format_generated(data: ReportData) -> str:
    moment = local_now(data.generated_at, settings.app_timezone)
    return f"{moment:%d-%m-%Y %H:%M %Z} oleh {data.generated_by}"


GROUP_HEADERS = (
    "Prodi",
    "Angkatan",
    "Jumlah Tagihan",
    "Lunas",
    "Belum Lunas",
    "Lewat Jatuh Tempo",
    "Nominal Tunggakan",
    "% Lunas",
)
ARREARS_HEADERS = (
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
)
