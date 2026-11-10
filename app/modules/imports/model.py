from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditedModel

from .constants import ImportStatus


class ImportBatch(AuditedModel):
    """One uploaded file: previewed first, then applied or discarded."""

    __tablename__ = "import_batches"

    source: Mapped[str] = mapped_column(String(10), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    semester: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=ImportStatus.PREVIEW.value, index=True
    )
    summary: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    # Validated rows kept between preview and apply; SQL NULL once applied or discarded. Deferred
    # because list queries never need it (it can be megabytes).
    rows: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON(none_as_null=True), deferred=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # True when applying wrote ``import_changes`` rows, i.e. the batch can be rolled back. Batches
    # applied before the change log existed stay False.
    has_change_log: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRowError(AuditedModel):
    __tablename__ = "import_row_errors"
    __table_args__ = (Index("ix_import_row_errors_batch_id_row_number", "batch_id", "row_number"),)

    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)


class ImportChange(AuditedModel):
    """One student or invoice created or changed by an applied import (basis of rollback).

    ``before`` / ``after`` hold only the tracked fields that changed (all of them for a
    ``created`` row, where ``before`` is NULL).
    """

    __tablename__ = "import_changes"
    __table_args__ = (Index("ix_import_changes_batch_id_entity", "batch_id", "entity"),)

    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    entity: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
