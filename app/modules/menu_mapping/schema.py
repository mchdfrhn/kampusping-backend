from pydantic import BaseModel, Field


class MenuMappingResponse(BaseModel):
    id: int
    menu_id: int
    slug: str

    model_config = {"from_attributes": True}


class MenuMappingCreate(BaseModel):
    menu_id: int = Field(gt=0)
    slug: str = Field(min_length=1, max_length=255)


class MenuMappingUpdate(BaseModel):
    menu_id: int | None = Field(default=None, gt=0)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
