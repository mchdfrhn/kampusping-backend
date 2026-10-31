from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditedModel
from app.shared.utils import utc_now

from .constants import MessageStatus


class Message(AuditedModel):
    """One WhatsApp message in the outbound queue (see ADR 0003).

    ``body`` is rendered when the message is planned and never re-rendered. A NULL ``rule_id``
    marks a manual message; NULLs are distinct in the unique key, so several manual messages may
    exist for one invoice, while an (invoice, rule) pair is planned at most once.
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("invoice_id", "rule_id", name="uq_messages_invoice_id_rule_id"),
        Index("ix_messages_status_scheduled_date", "status", "scheduled_date"),
        Index("ix_messages_student_id_sent_at", "student_id", "sent_at"),
    )

    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("schedule_rules.id"))
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    batch_ref: Mapped[str | None] = mapped_column(String(50))
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=MessageStatus.QUEUED.value
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_message_id: Mapped[str | None] = mapped_column(String(100), index=True)
    error: Mapped[str | None] = mapped_column(String(500))
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
