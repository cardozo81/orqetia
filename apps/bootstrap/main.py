"""One-shot LOCAL/TEST Docker bootstrap process."""

from __future__ import annotations

import asyncio
from pathlib import Path

from orqetia.infrastructure.local_bootstrap import (\n    bootstrap_local,\n    ensure_local_oidc_signing_key,\n)
from orqetia.infrastructure.local_secrets import LocalFileProviderSecretStore
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import (
    Environment,
    ProcessRole,
    RuntimeSettings,
    SecretStoreMode,
)

_LOCAL_SECRET_ROOT = Path("/var/lib/orqetia/provider-secrets")


async def amain() -> None:
    settings = RuntimeSettings()
    if settings.process_role is not ProcessRole.BOOTSTRAP:
        raise RuntimeError(
            "bootstrap entry point requires ORQETIA_PROCESS_ROLE=bootstrap"
        )
    if settings.environment not in {Environment.LOCAL, Environment.TEST}:
        raise RuntimeError("local bootstrap is restricted to LOCAL/TEST")
    if settings.secret_store_mode is not SecretStoreMode.LOCAL:
        raise RuntimeError(
            "local bootstrap requires ORQETIA_SECRET_STORE_MODE=local"
        )

    engine = create_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        result = await bootstrap_local(
            session_factory,
            secret_store=LocalFileProviderSecretStore(
                _LOCAL_SECRET_ROOT,
                environment=settings.environment.value,
            ),
            environment=settings.environment.value,
        )
        print(
            "ORQETIA local bootstrap complete; "
            f"manifest={result.manifest_path}"
        )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
