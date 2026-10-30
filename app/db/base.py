from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.utils import utc_now

# SQLite only auto-increments INTEGER primary keys, so fall back to Integer there.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)


class AuditMixin:
    """Audit columns and soft delete. Rows are never hard-deleted."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(255))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(255))


class AuditedModel(Base, IdMixin, AuditMixin):
    """Base for business tables: ``id`` + audit columns + soft delete."""

    __abstract__ = True
