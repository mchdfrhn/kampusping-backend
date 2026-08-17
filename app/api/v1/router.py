from fastapi import APIRouter

from ...modules.app_roles.router import router as app_roles_router
from ...modules.auth_context.router import router as auth_context_router
from ...modules.example.router import router as example_router
from ...modules.menu_mapping.router import router as menu_mapping_router

router = APIRouter()

router.include_router(example_router, prefix="/examples", tags=["Examples"])
router.include_router(auth_context_router, tags=["Auth"])
router.include_router(app_roles_router, tags=["App Roles"])
router.include_router(menu_mapping_router, tags=["Menu Mapping"])

__all__ = ["router"]
