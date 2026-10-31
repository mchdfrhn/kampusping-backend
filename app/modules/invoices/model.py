from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditedModel
from app.modules.students.model import Student

from .constants import InvoiceStatus


class Invoice(AuditedModel):
    __tablename__ = "invoices"
    __table_args__ = (Index("ix_invoices_status_due_date", "status", "due_date"),)

    invoice_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    semester: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # rupiah
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=InvoiceStatus.UNPAID.value
    )
    paid_at: Mapped[date | None] = mapped_column(Date)
    paid_via: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    exempt_reason: Mapped[str | None] = mapped_column(String(500))
    exempt_until: Mapped[date | None] = mapped_column(Date)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    student: Mapped[Student] = relationship(lazy="selectin")
