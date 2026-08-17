from dataclasses import dataclass


@dataclass(frozen=True)
class ExampleReadModel:
    message: str
    flow: tuple[str, ...]
