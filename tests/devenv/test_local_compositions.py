from __future__ import annotations

import os
import secrets
import subprocess
import sys

import pytest


@pytest.mark.parametrize("role", ["api", "backoffice", "portal", "local_idp", "bootstrap"])
def test_deployable_composition_imports(role):
    environment = {**os.environ,
        "ORQETIA_ENVIRONMENT": "test", "ORQETIA_PROCESS_ROLE": role,
        "ORQETIA_DATABASE_DSN": "postgresql+psycopg://orqetia@127.0.0.1/orqetia_test",
        "ORQETIA_SECRET_STORE_MODE": "local", "ORQETIA_PUBLIC_BASE_URL": "https://localhost:8443",
        "ORQETIA_LOCAL_OIDC_SIGNING_KEY": secrets.token_urlsafe(48),
    }
    module = "local_oidc" if role == "local_idp" else role
    result = subprocess.run(
        [sys.executable, "-c", f"import apps.{module}.main"],
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("role", ["backoffice", "portal", "local_idp", "bootstrap"])
@pytest.mark.parametrize("environment", ["staging", "production"])
def test_local_compositions_fail_closed(role, environment):
    from pydantic import ValidationError

    from orqetia.settings import RuntimeSettings

    with pytest.raises(ValidationError, match="outside LOCAL/TEST"):
        RuntimeSettings(
            environment=environment, process_role=role,
            database_dsn="postgresql+psycopg://orqetia@127.0.0.1/orqetia_test",
        )
