from pydantic import BaseModel


class ExampleResponse(BaseModel):
    message: str
    flow: list[str]


class ExampleRolePolicyResponse(BaseModel):
    allowed_statuses: list[str]
    role_types: list[str]
    iam_ids_by_type: dict[str, list[int]]

