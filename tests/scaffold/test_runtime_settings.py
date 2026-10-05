from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from orqetia.settings import (
    Environment,
    ProcessRole,
    RuntimeSettings,
    SecretStoreMode,
)

TEST_DSN = "postgresql+psycopg://runtime-user:synthetic-password@db:5432/orqetia"


class RuntimeSettingsTests(unittest.TestCase):
    def test_database_dsn_is_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                RuntimeSettings()

    def test_local_settings_accept_synthetic_runtime_configuration(self) -> None:
        settings = RuntimeSettings(
            database_dsn=TEST_DSN,
            environment=Environment.LOCAL,
            process_role=ProcessRole.API,
            debug=True,
            public_base_url="http://127.0.0.1:8000",
            cors_allowed_origins=("http://localhost:3000",),
            trusted_proxy_cidrs=("127.0.0.1/32",),
            secret_store_mode=SecretStoreMode.LOCAL,
        )
        self.assertEqual(settings.environment, Environment.LOCAL)
        self.assertEqual(settings.trusted_proxy_cidrs, ("127.0.0.1/32",))

    def test_settings_load_from_prefixed_environment(self) -> None:
        env = {
            "ORQETIA_ENVIRONMENT": "test",
            "ORQETIA_PROCESS_ROLE": "worker",
            "ORQETIA_DATABASE_DSN": TEST_DSN,
            "ORQETIA_WORKER_CONCURRENCY": "7",
            "ORQETIA_CORS_ALLOWED_ORIGINS": '["http://localhost:3000"]',
        }
        with patch.dict(os.environ, env, clear=True):
            settings = RuntimeSettings()
        self.assertEqual(settings.environment, Environment.TEST)
        self.assertEqual(settings.process_role, ProcessRole.WORKER)
        self.assertEqual(settings.worker_concurrency, 7)

    def test_database_dsn_rejects_non_postgresql(self) -> None:
        with self.assertRaises(ValidationError):
            RuntimeSettings(database_dsn="sqlite:///tmp/orqetia.db")

    def test_managed_secret_store_requires_reference(self) -> None:
        with self.assertRaises(ValidationError):
            RuntimeSettings(
                database_dsn=TEST_DSN,
                secret_store_mode=SecretStoreMode.MANAGED,
            )

    def test_envelope_secret_store_requires_root_key_reference(self) -> None:
        with self.assertRaises(ValidationError):
            RuntimeSettings(
                database_dsn=TEST_DSN,
                secret_store_mode=SecretStoreMode.ENVELOPE,
            )

    def test_secret_reference_is_opaque_identifier_not_secret_value(self) -> None:
        settings = RuntimeSettings(
            database_dsn=TEST_DSN,
            secret_store_mode=SecretStoreMode.MANAGED,
            secret_store_ref="vault://orqetia/provider-secrets",
        )
        self.assertEqual(settings.secret_store_ref, "vault://orqetia/provider-secrets")

        with self.assertRaises(ValidationError):
            RuntimeSettings(
                database_dsn=TEST_DSN,
                secret_store_mode=SecretStoreMode.MANAGED,
                secret_store_ref="plaintext-secret-value",
            )

    def test_secret_value_is_redacted_from_repr_and_safe_summary(self) -> None:
        settings = RuntimeSettings(database_dsn=TEST_DSN)
        rendered = repr(settings)
        summary = settings.safe_summary()

        self.assertNotIn("synthetic-password", rendered)
        self.assertNotIn("synthetic-password", repr(summary))
        self.assertEqual(summary["database_dsn"], "[REDACTED]")

    def test_production_rejects_debug(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(debug=True)

    def test_production_api_requires_https_public_base_url(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(public_base_url="http://orqetia.example")

        with self.assertRaises(ValidationError):
            RuntimeSettings(
                environment=Environment.PRODUCTION,
                process_role=ProcessRole.API,
                database_dsn=TEST_DSN,
            )

    def test_production_rejects_wildcard_or_http_cors(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(cors_allowed_origins=("*",))

        with self.assertRaises(ValidationError):
            self.production_settings(cors_allowed_origins=("http://client.example",))

    def test_production_rejects_universal_trusted_proxy(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(trusted_proxy_cidrs=("0.0.0.0/0",))

    def test_production_rejects_local_secret_store(self) -> None:
        with self.assertRaises(ValidationError):
            self.production_settings(secret_store_mode=SecretStoreMode.LOCAL)

    def test_production_secure_configuration_is_valid(self) -> None:
        settings = self.production_settings(
            cors_allowed_origins=("https://client.example",),
            trusted_proxy_cidrs=("10.0.0.0/24",),
            secret_store_mode=SecretStoreMode.MANAGED,
            secret_store_ref="vault://orqetia/provider-secrets",
        )
        self.assertEqual(settings.environment, Environment.PRODUCTION)
        self.assertFalse(settings.debug)

    @staticmethod
    def production_settings(**overrides: object) -> RuntimeSettings:
        values: dict[str, object] = {
            "environment": Environment.PRODUCTION,
            "process_role": ProcessRole.API,
            "database_dsn": TEST_DSN,
            "debug": False,
            "public_base_url": "https://orqetia.example",
            "secret_store_mode": SecretStoreMode.NONE,
        }
        values.update(overrides)
        return RuntimeSettings(**values)


if __name__ == "__main__":
    unittest.main()
