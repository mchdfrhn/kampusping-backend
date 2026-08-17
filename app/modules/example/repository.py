from .model import ExampleReadModel


class ExampleRepository:
    async def get_example(self) -> ExampleReadModel:
        return ExampleReadModel(
            message="Example module is ready.",
            flow=(
                "router.py",
                "schema.py",
                "service.py",
                "repository.py",
                "model.py",
                "database",
            ),
        )
