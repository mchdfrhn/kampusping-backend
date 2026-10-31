"""Import every model here so Alembic and ``Base.metadata`` see all tables."""

from app.modules.audit.model import AuditEvent
from app.modules.auth.model import RefreshToken
from app.modules.invoices.model import Invoice
from app.modules.roles.model import Permission, Role, role_permissions
from app.modules.settings.model import AppSettings
from app.modules.students.model import Student
from app.modules.users.model import User, user_roles

__all__ = [
    "AppSettings",
    "AuditEvent",
    "Invoice",
    "Permission",
    "RefreshToken",
    "Role",
    "Student",
    "User",
    "role_permissions",
    "user_roles",
]
