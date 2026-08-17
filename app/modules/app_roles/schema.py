from pydantic import BaseModel, Field, field_validator


class AppAllowedRoleResponse(BaseModel):
    id: int
    iam_role_id: int
    display_name: str
    auth_slug: str | None = None
    role_type: str
    sort_order: int
    is_active: bool

    model_config = {"from_attributes": True}

    @field_validator("is_active", mode="before")
    @classmethod
    def coerce_active(cls, value: object) -> bool:
        return bool(int(value)) if value is not None else False


class AppAllowedRoleCreate(BaseModel):
    iam_role_id: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=255)
    auth_slug: str | None = Field(default=None, max_length=128)
    role_type: str = Field(default="admin", min_length=1, max_length=64)
    sort_order: int = 0
    is_active: bool = True


class AppAllowedRoleUpdate(BaseModel):
    iam_role_id: int | None = Field(default=None, gt=0)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    auth_slug: str | None = Field(default=None, max_length=128)
    role_type: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = None
    is_active: bool | None = None
