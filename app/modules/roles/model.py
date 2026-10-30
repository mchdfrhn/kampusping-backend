from sqlalchemy import Boolean, Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditedModel, Base, BigIntPK, IdMixin

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", BigIntPK, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        BigIntPK,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Permission(Base, IdMixin):
    """Reference data synced from ``app.core.permissions`` by the seed command."""

    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class Role(AuditedModel):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, lazy="selectin", order_by=Permission.code
    )
