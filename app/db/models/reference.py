from sqlalchemy import Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MenuMapping(Base):
    __tablename__ = "menus_mapping"
    __table_args__ = (UniqueConstraint("menu_id", name="uq_menus_mapping_menu_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    menu_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)


class AppAllowedRole(Base):
    __tablename__ = "app_allowed_roles"
    __table_args__ = (UniqueConstraint("iam_role_id", name="uq_app_allowed_roles_iam_role_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iam_role_id: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role_type: Mapped[str] = mapped_column(String(64), default="admin", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
