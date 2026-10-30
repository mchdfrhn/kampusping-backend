from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, StringConstraints

from app.core.security import MAX_PASSWORD_LENGTH, StrongPassword
from app.modules.users.schema import CurrentUserRead, FullName


class LoginRequest(BaseModel):
    email: EmailStr
    # Login only checks the credentials; strength rules apply when a password is set.
    password: Annotated[str, StringConstraints(min_length=1, max_length=MAX_PASSWORD_LENGTH)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type label, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds")
    user: CurrentUserRead


class ChangePasswordRequest(BaseModel):
    current_password: Annotated[
        str, StringConstraints(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    ]
    new_password: StrongPassword


class ProfileUpdate(BaseModel):
    full_name: FullName
