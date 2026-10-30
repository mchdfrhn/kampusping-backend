from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditedModel, Base, BigIntPK
from app.modules.roles.model import Role

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", BigIntPK, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", BigIntPK, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class User(AuditedModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[Role]] = relationship(
        secondary=user_roles, lazy="selectin", order_by=Role.name
    )

    @property
    def permission_codes(self) -> frozenset[str]:
        return frozenset(permission.code for role in self.roles for permission in role.permissions)
