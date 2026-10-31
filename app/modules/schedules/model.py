from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditedModel
from app.modules.templates.model import MessageTemplate

from .constants import RULE_NAME_MAX_LENGTH


class ScheduleRule(AuditedModel):
    """Send ``template`` ``offset_days`` days from an invoice's due date (negative = before).

    ``offset_days`` is unique among non-deleted rules; the service enforces it.
    """

    __tablename__ = "schedule_rules"

    name: Mapped[str] = mapped_column(String(RULE_NAME_MAX_LENGTH), nullable=False)
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("message_templates.id"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    template: Mapped[MessageTemplate] = relationship(lazy="selectin")
