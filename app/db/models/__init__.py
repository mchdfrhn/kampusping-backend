"""Import every model here so Alembic and ``Base.metadata`` see all tables."""

from app.modules.audit.model import AuditEvent
from app.modules.auth.model import RefreshToken
from app.modules.imports.model import ImportBatch, ImportRowError
from app.modules.invoices.model import Invoice
from app.modules.messages.model import Message
from app.modules.roles.model import Permission, Role, role_permissions
from app.modules.schedules.model import ScheduleRule
from app.modules.settings.model import AppSettings
from app.modules.students.model import Student
from app.modules.templates.model import MessageTemplate
from app.modules.users.model import User, user_roles

__all__ = [
    "AppSettings",
    "AuditEvent",
    "ImportBatch",
    "ImportRowError",
    "Invoice",
    "Message",
    "MessageTemplate",
    "Permission",
    "RefreshToken",
    "Role",
    "ScheduleRule",
    "Student",
    "User",
    "role_permissions",
    "user_roles",
]
