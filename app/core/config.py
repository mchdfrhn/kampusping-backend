from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseEngine(StrEnum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SINGLESTORE = "singlestore"


class Settings(BaseSettings):
    app_name: str = "HIS Standard Backend"
    app_env: str = "development"
    app_debug: bool = True
    app_version: str = "1.0.0"

    api_v1_prefix: str = "/api/v1"

    database_engine: DatabaseEngine = DatabaseEngine.POSTGRESQL
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:password@localhost:5432/his_standard"
    )

    # Optional named connections (leave empty if unused).
    db_url_primary: str = ""
    db_url_iam: str = ""
    db_url_reference: str = ""

    iam_api_base_url: str = ""
    group_admin_role_id: int = 1

    backend_cors_origins: list[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> list[str] | str:
        if value is None or value == "":
            return []
        if isinstance(value, str) and not value.strip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def validate_database_url_matches_engine(self) -> "Settings":
        url_scheme = self.database_url.split(":", 1)[0].lower()
        allowed_prefixes = {
            DatabaseEngine.POSTGRESQL: ("postgresql",),
            DatabaseEngine.MYSQL: ("mysql",),
            DatabaseEngine.SINGLESTORE: ("singlestore", "singlestoredb"),
        }
        if not url_scheme.startswith(allowed_prefixes[self.database_engine]):
            message = (
                "DATABASE_URL scheme does not match DATABASE_ENGINE. "
                f"Got engine={self.database_engine!r}, url_scheme={url_scheme!r}."
            )
            raise ValueError(message)
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_async_database_url(self) -> bool:
        async_markers = ("+asyncpg", "+asyncmy", "+aiomysql", "+aiosqlite")
        return any(marker in self.database_url for marker in async_markers)

    @property
    def database_urls(self) -> dict[str, str]:
        primary = self.db_url_primary or ("" if self.is_async_database_url else self.database_url)
        urls = {
            "PRIMARY": primary,
            "IAM": self.db_url_iam,
            "REFERENCE": self.db_url_reference,
        }
        return {key: value for key, value in urls.items() if value}


settings = Settings()
