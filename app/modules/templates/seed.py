"""Default templates and schedule rules (BRD section 11, rule AB-02).

Called from the CLI only (``python -m app.cli seed|create-admin``), never from
``seed_reference_data``: staff own these rows once they exist.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schedules.model import ScheduleRule

from .constants import (
    DEFAULT_ARREARS_TEMPLATE_NAME,
    DEFAULT_REMINDER_TEMPLATE_NAME,
    DEFAULT_ROUTINE_TEMPLATE_NAME,
)
from .model import MessageTemplate

SEED_ACTOR = "system"

# The opt-out footer is appended when sending and is not part of the stored body.
# "cara_bayar" / "link_pembayaran" follow the invoice source (SIAKAD or SIPEMA).
REMINDER_BODY = (
    "Selamat siang, {{nama}} (NIM {{nim}}).\n"
    "\n"
    "Kami mengingatkan bahwa tagihan uang kuliah semester {{semester}} sebesar "
    "Rp{{nominal}} akan jatuh tempo pada {{tanggal_jatuh_tempo}}.\n"
    "\n"
    "Pembayaran dapat dilakukan melalui {{cara_bayar}}: {{link_pembayaran}}\n"
    "\n"
    "Bila sudah membayar, abaikan pesan ini. Pertanyaan? Hubungi Bagian Keuangan di "
    "{{kontak_keuangan}}.\n"
    "\n"
    "Terima kasih.\n"
    "Bagian Keuangan {{nama_kampus}}"
)

ARREARS_BODY = (
    "Selamat siang, {{nama}} (NIM {{nim}}).\n"
    "\n"
    "Tagihan uang kuliah semester {{semester}} sebesar Rp{{nominal}} telah melewati jatuh "
    "tempo ({{tanggal_jatuh_tempo}}).\n"
    "\n"
    "Mohon segera melakukan pembayaran melalui {{cara_bayar}}: {{link_pembayaran}}\n"
    "\n"
    "Bila Anda mengalami kesulitan, silakan menghubungi Bagian Keuangan di "
    "{{kontak_keuangan}} untuk berdiskusi.\n"
    "\n"
    "Terima kasih.\n"
    "Bagian Keuangan {{nama_kampus}}"
)

# Regular (periodic) billing: not tied to the due date, so the wording claims neither "will be
# due" nor "is overdue". No default rule uses it; staff send it manually or attach a rule later.
ROUTINE_BODY = (
    "Selamat siang, {{nama}} (NIM {{nim}}).\n"
    "\n"
    "Berikut informasi tagihan uang kuliah Anda yang menurut data kami belum lunas:\n"
    "\n"
    "Semester: {{semester}}\n"
    "Nominal: Rp{{nominal}}\n"
    "Jatuh tempo: {{tanggal_jatuh_tempo}}\n"
    "\n"
    "Pembayaran dapat dilakukan melalui {{cara_bayar}}: {{link_pembayaran}}\n"
    "\n"
    "Bila sudah membayar, abaikan pesan ini. Bila ada kendala atau ingin berdiskusi, silakan "
    "hubungi Bagian Keuangan di {{kontak_keuangan}}.\n"
    "\n"
    "Terima kasih.\n"
    "Bagian Keuangan {{nama_kampus}}"
)

# (rule name, days from the due date, uses the reminder template?)
DEFAULT_RULES: tuple[tuple[str, int, bool], ...] = (
    ("H-7", -7, True),
    ("H-3", -3, True),
    ("H-1", -1, True),
    ("H", 0, True),
    ("H+3", 3, False),
    ("H+7", 7, False),
)


async def seed_default_templates_and_rules(db: AsyncSession) -> bool:
    """Create the three default templates and six standard rules when no template exists at all.

    Soft-deleted templates count as existing, so staff who removed the defaults do not get them
    back. Existing rows are never changed. Returns whether anything was created.
    """
    existing = await db.scalar(select(func.count()).select_from(MessageTemplate))
    if existing:
        return False

    reminder = MessageTemplate(
        name=DEFAULT_REMINDER_TEMPLATE_NAME,
        body=REMINDER_BODY,
        is_active=True,
        created_by=SEED_ACTOR,
        updated_by=SEED_ACTOR,
    )
    arrears = MessageTemplate(
        name=DEFAULT_ARREARS_TEMPLATE_NAME,
        body=ARREARS_BODY,
        is_active=True,
        created_by=SEED_ACTOR,
        updated_by=SEED_ACTOR,
    )
    routine = MessageTemplate(
        name=DEFAULT_ROUTINE_TEMPLATE_NAME,
        body=ROUTINE_BODY,
        is_active=True,
        created_by=SEED_ACTOR,
        updated_by=SEED_ACTOR,
    )
    db.add_all([reminder, arrears, routine])
    await db.flush()

    for name, offset_days, before_due in DEFAULT_RULES:
        db.add(
            ScheduleRule(
                name=name,
                offset_days=offset_days,
                template=reminder if before_due else arrears,
                is_active=True,
                created_by=SEED_ACTOR,
                updated_by=SEED_ACTOR,
            )
        )
    await db.commit()
    return True
