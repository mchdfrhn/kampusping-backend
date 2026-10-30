from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditedModel

from .constants import STATUS_DRAFT


class ExampleItem(AuditedModel):
    __tablename__ = "example_items"
    __table_args__ = (Index("ix_example_items_status_updated_at", "status", "updated_at"),)

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_DRAFT)
