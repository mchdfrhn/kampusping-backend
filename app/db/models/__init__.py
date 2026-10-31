"""Import every model here so Alembic and ``Base.metadata`` see all tables."""

from app.modules.audit.model import AuditEvent
from app.modules.auth.model import RefreshToken
from app.modules.roles.model import Permission, Role, role_permissions
from app.modules.settings.model import AppSettings
from app.modules.users.model import User, user_roles

__all__ = [
    "AppSettings",
    "AuditEvent",
    "Permission",
    "RefreshToken",
    "Role",
    "User",
    "role_permissions",
    "user_roles",
]
