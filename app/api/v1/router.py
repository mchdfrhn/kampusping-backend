from fastapi import APIRouter

from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.imports.router import router as imports_router
from app.modules.invoices.router import router as invoices_router
from app.modules.roles.router import router as roles_router
from app.modules.schedules.router import router as schedules_router
from app.modules.settings.router import router as settings_router
from app.modules.students.router import router as students_router
from app.modules.templates.router import router as templates_router
from app.modules.users.router import router as users_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(users_router)
router.include_router(roles_router)
router.include_router(students_router)
router.include_router(invoices_router)
router.include_router(imports_router)
router.include_router(templates_router)
router.include_router(schedules_router)
router.include_router(settings_router)
router.include_router(audit_router)

__all__ = ["router"]
