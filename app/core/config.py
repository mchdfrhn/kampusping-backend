import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "change-me-development-only-secret-key-0000000000"  # noqa: S105 - dev placeholder, rejected in production
ASYNC_DRIVER_MARKERS = ("+asyncpg", "+asyncmy", "+aiomysql", "+aiosqlite")


class DatabaseEngine(StrEnum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SINGLESTORE = "singlestore"
    SQLITE = "sqlite"


_ALLOWED_URL_SCHEMES: dict[DatabaseEngine, tuple[str, ...]] = {
    DatabaseEngine.POSTGRESQL: ("postgresql",),
    DatabaseEngine.MYSQL: ("mysql",),
    # SingleStore speaks the MySQL wire protocol, so it uses the MySQL async driver.
    DatabaseEngine.SINGLESTORE: ("mysql", "singlestore"),
    DatabaseEngine.SQLITE: ("sqlite",),
}


class Settings(BaseSettings):
    # --- Application ---
    app_name: str = "KampusPing API"
    app_env: str = "development"
    app_debug: bool = False
    app_version: str = "1.0.0"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    # --- Database ---
    database_engine: DatabaseEngine = DatabaseEngine.POSTGRESQL
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/kampusping"
    database_echo: bool = False

    # --- Auth ---
    secret_key: str = INSECURE_DEFAULT_SECRET
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=15, ge=1)
    refresh_token_expire_days: int = Field(default=14, ge=1)
    refresh_cookie_name: str = "refresh_token"
    cookie_secure: bool | None = None
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    password_min_length: int = Field(default=10, ge=8)
    login_max_attempts: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)

    # --- Bootstrap (read only by `python -m app.cli create-admin`) ---
    initial_admin_email: str | None = None
    initial_admin_name: str = "Administrator"
    initial_admin_password: SecretStr | None = None

    # --- Rate limiting ---
    rate_limit_enabled: bool = True
    # "memory://" is per-process; use a shared store (e.g. redis://...) with several workers.
    rate_limit_storage_uri: str = "memory://"
    login_rate_limit: str = "10/minute"

    # --- CORS ---
    backend_cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        """Accept a JSON list or a comma-separated string."""
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        return value

    @field_validator("cookie_domain", mode="before")
    @classmethod
    def empty_cookie_domain_is_none(cls, value: Any) -> Any:
        return value or None

    @model_validator(mode="after")
    def validate_configuration(self) -> "Settings":
        url_scheme = self.database_url.split(":", 1)[0].lower()
        if not url_scheme.startswith(_ALLOWED_URL_SCHEMES[self.database_engine]):
            raise ValueError(
                "DATABASE_URL scheme does not match DATABASE_ENGINE: "
                f"engine={self.database_engine.value!r}, url_scheme={url_scheme!r}."
            )
        if not any(marker in self.database_url for marker in ASYNC_DRIVER_MARKERS):
            raise ValueError(
                "DATABASE_URL must use an async driver "
                "(postgresql+asyncpg, mysql+asyncmy, sqlite+aiosqlite)."
            )
        if self.cookie_samesite == "none" and not self.refresh_cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires secure cookies.")
        if self.is_production:
            self._validate_production()
        return self

    def _validate_production(self) -> None:
        problems: list[str] = []
        if self.secret_key == INSECURE_DEFAULT_SECRET or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be a unique random value of at least 32 characters")
        if self.app_debug:
            problems.append("APP_DEBUG must be false")
        if self.database_engine == DatabaseEngine.SQLITE:
            problems.append("DATABASE_ENGINE=sqlite is not allowed")
        if not self.refresh_cookie_secure:
            problems.append("COOKIE_SECURE must be true")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems) + ".")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def refresh_cookie_secure(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def refresh_cookie_path(self) -> str:
        return f"{self.api_v1_prefix}/auth"


settings = Settings()
