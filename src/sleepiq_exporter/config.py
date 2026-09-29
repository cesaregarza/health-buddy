"""Environment-backed application configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ConfigurationError

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value.strip():
        raise ConfigurationError(f"required environment variable {name} is missing")
    return value


def _integer(
    env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _optional_integer(
    env: Mapping[str, str], name: str, minimum: int, maximum: int
) -> int | None:
    raw = env.get(name, "").strip()
    if not raw or raw == "0":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}, or 0 to disable"
        )
    return value


def _number(
    env: Mapping[str, str], name: str, default: float, minimum: float, maximum: float
) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, str(default)).strip().casefold()
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime settings.

    Credentials are excluded from ``repr`` so accidental object logging cannot
    disclose them.
    """

    email: str | None = field(repr=False)
    password: str | None = field(repr=False)
    timezone_name: str
    timezone: ZoneInfo
    lookback_days: int
    request_delay_seconds: float
    max_retries: int
    include_names: bool
    database_url: str | None = field(repr=False)
    sqlite_path: Path
    database_timeout_seconds: float
    retention_days: int | None
    log_level: str
    log_format: str

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        require_credentials: bool = True,
    ) -> Settings:
        source = os.environ if env is None else env
        email = (
            _required(source, "SLEEPIQ_EMAIL").strip() if require_credentials else None
        )
        password = (
            _required(source, "SLEEPIQ_PASSWORD") if require_credentials else None
        )
        if email is not None and (
            "@" not in email or any(character.isspace() for character in email)
        ):
            raise ConfigurationError("SLEEPIQ_EMAIL is malformed")

        timezone_name = source.get("SLEEPIQ_TIMEZONE", "UTC").strip()
        try:
            timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigurationError(
                "SLEEPIQ_TIMEZONE is not a valid timezone"
            ) from exc

        database_url = source.get("DATABASE_URL", "").strip() or None
        if database_url and not database_url.startswith(
            ("postgresql://", "postgresql+psycopg://", "postgres://")
        ):
            raise ConfigurationError(
                "DATABASE_URL must use PostgreSQL; omit it to use SQLite"
            )

        sqlite_raw = source.get("SQLITE_PATH", "/data/sleepiq.db").strip()
        if not sqlite_raw:
            raise ConfigurationError("SQLITE_PATH must not be empty")

        log_level = source.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL is invalid")
        log_format = source.get("LOG_FORMAT", "json").strip().casefold()
        if log_format not in {"json", "text"}:
            raise ConfigurationError("LOG_FORMAT must be json or text")

        return cls(
            email=email,
            password=password,
            timezone_name=timezone_name,
            timezone=timezone,
            lookback_days=_integer(
                source, "SLEEPIQ_LOOKBACK_DAYS", 7, minimum=1, maximum=366
            ),
            request_delay_seconds=_number(
                source,
                "SLEEPIQ_REQUEST_DELAY_SECONDS",
                1.0,
                minimum=0.0,
                maximum=60.0,
            ),
            max_retries=_integer(
                source, "SLEEPIQ_MAX_RETRIES", 4, minimum=0, maximum=10
            ),
            include_names=_boolean(source, "SLEEPIQ_INCLUDE_NAMES", default=False),
            database_url=database_url,
            sqlite_path=Path(sqlite_raw).expanduser(),
            database_timeout_seconds=_number(
                source,
                "DATABASE_TIMEOUT_SECONDS",
                30.0,
                minimum=1.0,
                maximum=300.0,
            ),
            retention_days=_optional_integer(
                source, "SLEEPIQ_RETENTION_DAYS", minimum=1, maximum=36500
            ),
            log_level=log_level,
            log_format=log_format,
        )

    @property
    def effective_database_url(self) -> str:
        """Return a SQLAlchemy URL without exposing it to logs."""

        if self.database_url:
            if self.database_url.startswith("postgres://"):
                return "postgresql+psycopg://" + self.database_url.removeprefix(
                    "postgres://"
                )
            if self.database_url.startswith("postgresql://"):
                return "postgresql+psycopg://" + self.database_url.removeprefix(
                    "postgresql://"
                )
            return self.database_url
        return f"sqlite+pysqlite:///{self.sqlite_path.resolve()}"

    @property
    def secret_values(self) -> tuple[str, ...]:
        return tuple(
            value for value in (self.email, self.password, self.database_url) if value
        )
