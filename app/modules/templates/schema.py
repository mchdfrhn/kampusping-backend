from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .constants import TEMPLATE_BODY_MAX_LENGTH, TEMPLATE_NAME_MAX_LENGTH

TemplateName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TEMPLATE_NAME_MAX_LENGTH)
]
TemplateBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TEMPLATE_BODY_MAX_LENGTH)
]


class TemplateWrite(BaseModel):
    name: TemplateName
    body: TemplateBody = Field(
        description="Message text with {{variable}} placeholders; the opt-out footer is added "
        "automatically when sending."
    )
    is_active: bool = True


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    body: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TemplateSummary(BaseModel):
    """Compact template reference embedded in other resources."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class TemplatePreviewRequest(BaseModel):
    body: TemplateBody


class TemplatePreviewResponse(BaseModel):
    rendered: str
    unknown_variables: list[str]
