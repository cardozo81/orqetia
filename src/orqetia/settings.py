"""Typed runtime configuration for ORQETIA process composition roots.

This module is infrastructure-facing. Bounded-context domain code must not read
environment variables directly.
"""

from __future__ import annotations

from enum import StrEnum
from ipaddress import ip_network
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ProcessRole(StrEnum):
    API = "api"
    WORKER = "worker"
    SCHEDULER = "scheduler"
    BACKOFFICE = "backoffice"
    PORTAL = "portal"
    LOCAL_IDP = "local_idp"
    BOOTSTRAP = "bootstrap"


class SecretStoreMode(StrEnum):
    NONE = "none"
    LOCAL = "local"
    MANAGED = "managed"
    ENVELOPE = "envelope"


class RuntimeSettings(BaseSettings):
    """Configuration shared by API/worker/scheduler composition roots.

    Provider credentials and encryption key material are deliberately absent.
    Only safe references to secret infrastructure belong here.
    """

    model_config = SettingsConfigDict(
        env_prefix="ORQETIA_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="forbid",
        validate_default=True,
    )

    environment: Environment = Environment.LOCAL
    process_role: ProcessRole = ProcessRole.API
    debug: bool = False

    public_base_url: AnyHttpUrl | None = None
    cors_allowed_origins: tuple[str, ...] = ()
    trusted_proxy_cidrs: tuple[str, ...] = ()

    database_dsn: SecretStr

    secret_store_mode: SecretStoreMode = SecretStoreMode.NONE
    secret_store_ref: str | None = None
    root_kek_ref: str | None = None
    local_oidc_signing_key: SecretStr | None = None

    worker_concurrency: int = Field(default=4, ge=1, le=256)
    work_lease_seconds: int = Field(default=60, ge=5, le=3600)
    queue_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    shutdown_grace_seconds: int = Field(default=30, ge=1, le=300)

    telemetry_trace_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    telemetry_log_payloads: bool = False

    maintenance_mode: Literal["normal", "draining", "maintenance"] = "normal"
    maintenance_retry_after_seconds: int = Field(default=60, ge=1, le=86_400)

    @field_validator("secret_store_ref", "root_kek_ref", mode="before")
    @classmethod
    def normalize_secret_reference(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            return None
        if "://" not in normalized or any(char.isspace() for char in normalized):
            raise ValueError("secret reference must be an opaque scheme://identifier")
        scheme, identifier = normalized.split("://", 1)
        if not scheme or not identifier:
            raise ValueError("secret reference must include scheme and identifier")
        return normalized

    @field_validator("database_dsn")
    @classmethod
    def validate_database_dsn(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("database_dsn must use PostgreSQL/Psycopg")
        if not parsed.hostname:
            raise ValueError("database_dsn must include a database host")
        if not parsed.path or parsed.path == "/":
            raise ValueError("database_dsn must include a database name")
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_cors_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            origin = value.strip()
            if not origin:
                raise ValueError("CORS origin cannot be empty")
            if origin == "*":
                normalized.append(origin)
                continue
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("CORS origins must be absolute HTTP(S) origins")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("CORS origin must not include path, query, or fragment")
            normalized.append(origin.rstrip("/"))
        if len(set(normalized)) != len(normalized):
            raise ValueError("CORS origins must be unique")
        return tuple(normalized)

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_proxy_cidrs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            network = ip_network(value.strip(), strict=False)
            normalized.append(str(network))
        if len(set(normalized)) != len(normalized):
            raise ValueError("trusted proxy CIDRs must be unique")
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_runtime_security(self) -> Self:
        if self.secret_store_mode is SecretStoreMode.MANAGED and self.secret_store_ref is None:
            raise ValueError("managed secret store requires secret_store_ref")
        if self.secret_store_mode is SecretStoreMode.ENVELOPE and self.root_kek_ref is None:
            raise ValueError("envelope secret store requires root_kek_ref")
        if self.telemetry_log_payloads:
            raise ValueError(
                "telemetry payload logging is disabled until an explicit data policy exists"
            )

        if self.environment not in {Environment.LOCAL, Environment.TEST}:
            if self.local_oidc_signing_key is not None:
                raise ValueError("local OIDC signing key is forbidden outside LOCAL/TEST")
            if self.process_role in {ProcessRole.BACKOFFICE, ProcessRole.PORTAL,
                                     ProcessRole.LOCAL_IDP, ProcessRole.BOOTSTRAP}:
                raise ValueError("local process composition is forbidden outside LOCAL/TEST")

        if self.environment is not Environment.PRODUCTION:
            return self

        if self.local_oidc_signing_key is not None:
            raise ValueError("local OIDC signing key is forbidden in production")

        if self.debug:
            raise ValueError("debug must be disabled in production")

        if self.public_base_url is not None and self.public_base_url.scheme != "https":
            raise ValueError("production public_base_url must use HTTPS")

        if self.process_role is ProcessRole.API and self.public_base_url is None:
            raise ValueError("production API requires public_base_url")

        if "*" in self.cors_allowed_origins:
            raise ValueError("production CORS cannot allow wildcard origin")

        for origin in self.cors_allowed_origins:
            if not origin.startswith("https://"):
                raise ValueError("production CORS origins must use HTTPS")

        if any(cidr in {"0.0.0.0/0", "::/0"} for cidr in self.trusted_proxy_cidrs):
            raise ValueError("production trusted proxy list cannot trust the whole Internet")

        if self.secret_store_mode is SecretStoreMode.LOCAL:
            raise ValueError("local secret store is forbidden in production")

        return self

    def safe_summary(self) -> dict[str, object]:
        """Return configuration metadata safe for startup diagnostics."""

        return {
            "environment": self.environment.value,
            "process_role": self.process_role.value,
            "debug": self.debug,
            "public_base_url": None if self.public_base_url is None else str(self.public_base_url),
            "cors_allowed_origins": self.cors_allowed_origins,
            "trusted_proxy_cidrs": self.trusted_proxy_cidrs,
            "database_dsn": "[REDACTED]",
            "secret_store_mode": self.secret_store_mode.value,
            "secret_store_ref": self.secret_store_ref,
            "root_kek_ref": self.root_kek_ref,
            "local_oidc_signing_key": (
                None if self.local_oidc_signing_key is None else "[REDACTED]"
            ),
            "worker_concurrency": self.worker_concurrency,
            "work_lease_seconds": self.work_lease_seconds,
            "queue_poll_interval_seconds": self.queue_poll_interval_seconds,
            "shutdown_grace_seconds": self.shutdown_grace_seconds,
            "telemetry_trace_sample_rate": self.telemetry_trace_sample_rate,
            "telemetry_log_payloads": self.telemetry_log_payloads,
            "maintenance_mode": self.maintenance_mode,
            "maintenance_retry_after_seconds": self.maintenance_retry_after_seconds,
        }
