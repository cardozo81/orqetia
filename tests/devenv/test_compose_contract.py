from __future__ import annotations

import unittest

from scripts.verify_compose_config import validate_compose


def valid_config() -> dict[str, object]:
    runtime = {
        "environment": {
            "ORQETIA_DATABASE_DSN": "postgresql+psycopg://orqetia@postgres:5432/orqetia",
            "ORQETIA_SECRET_STORE_MODE": "local",
        }
    }
    return {
        "services": {
            "api": dict(runtime),
            "worker": dict(runtime),
            "scheduler": dict(runtime),
            "migrate": dict(runtime),
            "postgres": {
                "ports": [{"host_ip": "127.0.0.1", "published": "5432", "target": 5432}],
                "volumes": [{"type": "volume", "source": "postgres_data", "target": "/var/lib/postgresql"}],
            },
        }
    }


class ComposeContractTests(unittest.TestCase):
    def test_valid_baseline_config_is_accepted(self) -> None:
        self.assertEqual(validate_compose(valid_config()), [])

    def test_public_postgres_binding_is_rejected(self) -> None:
        config = valid_config()
        config["services"]["postgres"]["ports"][0]["host_ip"] = "0.0.0.0"  # type: ignore[index]
        self.assertIn(
            "postgres host port must be loopback-only",
            validate_compose(config),
        )

    def test_privileged_runtime_service_is_rejected(self) -> None:
        config = valid_config()
        config["services"]["worker"]["privileged"] = True  # type: ignore[index]
        self.assertIn(
            "service worker must not be privileged",
            validate_compose(config),
        )

    def test_provider_secret_environment_is_rejected(self) -> None:
        config = valid_config()
        config["services"]["api"]["environment"]["PROVIDER_API_KEY"] = "synthetic"  # type: ignore[index]
        self.assertIn(
            "api must not receive provider credentials in baseline Compose",
            validate_compose(config),
        )


if __name__ == "__main__":
    unittest.main()
