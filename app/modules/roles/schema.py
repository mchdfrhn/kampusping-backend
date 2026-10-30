from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

RoleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=50)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    description: str


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    is_system: bool
    permissions: list[str]
    created_at: datetime
    updated_at: datetime

    @field_validator("permissions", mode="before")
    @classmethod
    def permissions_to_codes(cls, value: Any) -> Any:
        return [getattr(item, "code", item) for item in value]


class RoleCreate(BaseModel):
    name: RoleName
    description: Description = ""
    permissions: list[str] = Field(default_factory=list, description="Permission codes")


class RoleUpdate(BaseModel):
    name: RoleName
    description: Description = ""
    permissions: list[str] = Field(default_factory=list, description="Permission codes")
