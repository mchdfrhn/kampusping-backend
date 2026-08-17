from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.shared.utils import app_now


class Base(DeclarativeBase):
    pass


def _tablename_from_class(model_cls: type) -> str:
    name = model_cls.__name__
    return "".join(f"_{char.lower()}" if char.isupper() else char for char in name).lstrip("_")


class BaseModelMixin:
    """Generic primary-key and timestamp mixin."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=app_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=app_now,
        onupdate=app_now,
        server_default=func.now(),
        nullable=False,
    )

    @declared_attr.directive
    def __tablename__(cls) -> str:
        model_cls = cls if isinstance(cls, type) else type(cls)
        return _tablename_from_class(model_cls)


class AuditMixin:
    """Audit columns used by HIS business tables."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=app_now)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
