from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditedModel

from .constants import TEMPLATE_NAME_MAX_LENGTH


class MessageTemplate(AuditedModel):
    """A reusable WhatsApp message body with ``{{variable}}`` placeholders.

    ``name`` is unique among non-deleted rows; the service enforces it (no DB constraint, so a
    soft-deleted template never blocks reuse of its name). The opt-out footer is not stored here.
    """

    __tablename__ = "message_templates"

    name: Mapped[str] = mapped_column(String(TEMPLATE_NAME_MAX_LENGTH), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
