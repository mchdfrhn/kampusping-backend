from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_primary_db
from app.core.role_catalog import (
    ROLE_TYPE_ADMIN,
    ROLE_TYPE_DEALER,
    ROLE_TYPE_EMPLOYEE,
    ROLE_TYPE_SUPER_ADMIN,
    RoleCatalog,
)

from .constants import ALLOWED_STATUSES
from .repository import ExampleRepository
from .schema import ExampleResponse, ExampleRolePolicyResponse
from .service import ExampleService

router = APIRouter()


def get_example_repository() -> ExampleRepository:
    return ExampleRepository()


def get_example_service(
    repository: Annotated[ExampleRepository, Depends(get_example_repository)],
) -> ExampleService:
    return ExampleService(repository)


@router.get("", response_model=ExampleResponse)
async def get_example(
    service: Annotated[ExampleService, Depends(get_example_service)],
) -> ExampleResponse:
    return await service.get_example()


@router.get("/role-policy", response_model=ExampleRolePolicyResponse)
def get_role_policy(db: Session = Depends(get_primary_db)) -> ExampleRolePolicyResponse:
    """Demonstrates reading IAM IDs from app_allowed_roles, never from this router."""
    catalog = RoleCatalog.from_db(db)
    return ExampleRolePolicyResponse(
        allowed_statuses=list(ALLOWED_STATUSES),
        role_types=[
            ROLE_TYPE_SUPER_ADMIN,
            ROLE_TYPE_ADMIN,
            ROLE_TYPE_DEALER,
            ROLE_TYPE_EMPLOYEE,
        ],
        iam_ids_by_type={
            ROLE_TYPE_SUPER_ADMIN: catalog.iam_ids_for_type(ROLE_TYPE_SUPER_ADMIN),
            ROLE_TYPE_ADMIN: catalog.iam_ids_for_type(ROLE_TYPE_ADMIN),
            ROLE_TYPE_DEALER: catalog.iam_ids_for_type(ROLE_TYPE_DEALER),
            ROLE_TYPE_EMPLOYEE: catalog.iam_ids_for_type(ROLE_TYPE_EMPLOYEE),
        },
    )
