from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from .constants import ExampleStatus

Code = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._-]+$"
    ),
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]


class ExampleWrite(BaseModel):
    code: Code
    name: Name
    status: ExampleStatus = "draft"


class ExampleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
