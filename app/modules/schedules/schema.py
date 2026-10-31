from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.templates.schema import TemplateSummary

from .constants import OFFSET_DAYS_MAX, OFFSET_DAYS_MIN, RULE_NAME_MAX_LENGTH

RuleName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=RULE_NAME_MAX_LENGTH)
]


class ScheduleRuleWrite(BaseModel):
    name: RuleName
    offset_days: int = Field(
        ge=OFFSET_DAYS_MIN,
        le=OFFSET_DAYS_MAX,
        description="Days relative to the due date: negative = before, 0 = on the day.",
    )
    template_id: int
    is_active: bool = True


class ScheduleRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    offset_days: int
    template: TemplateSummary
    is_active: bool
    created_at: datetime
    updated_at: datetime
