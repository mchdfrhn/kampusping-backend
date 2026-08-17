from .repository import ExampleRepository
from .schema import ExampleResponse


class ExampleService:
    def __init__(self, repository: ExampleRepository) -> None:
        self.repository = repository

    async def get_example(self) -> ExampleResponse:
        example = await self.repository.get_example()
        return ExampleResponse(message=example.message, flow=list(example.flow))
