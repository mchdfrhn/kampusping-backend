from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.core.security import StrongPassword

from .model import User

FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]


class RoleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    is_active: bool
    roles: list[RoleSummary]
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CurrentUserRead(UserRead):
    """The signed-in user plus the flattened permission codes the UI uses for display logic."""

    permissions: list[str]

    @classmethod
    def from_user(cls, user: User) -> Self:
        base = UserRead.model_validate(user).model_dump()
        return cls(**base, permissions=sorted(user.permission_codes))


class UserCreate(BaseModel):
    email: EmailStr
    full_name: FullName
    password: StrongPassword
    is_active: bool = True
    role_ids: list[int] = Field(default_factory=list)


class UserUpdate(BaseModel):
    full_name: FullName
    is_active: bool = True
    role_ids: list[int] = Field(default_factory=list)
    password: StrongPassword | None = Field(
        default=None, description="Set to reset the user's password; omit to keep it."
    )
