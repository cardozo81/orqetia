"""Development-only file-backed provider secret store.

This adapter exists exclusively for LOCAL/TEST compositions where multiple
processes need to share synthetic provider credentials. It is intentionally
unavailable in staging/production and never logs or returns raw material except
through the ProviderSecretStore contract.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from uuid import uuid7

from orqetia.control_plane import SecretReference, SecretValue

_IDENTIFIER = re.compile(r"^[0-9a-f]{32}$")
_PREFIX = "local-file://"


class LocalFileProviderSecretStore:
    """Small shared-volume secret store restricted to LOCAL/TEST."""

    def __init__(self, root: Path, *, environment: str) -> None:
        normalized = environment.strip().lower()
        if normalized not in {"local", "test"}:
            raise RuntimeError(
                "local file secret store is restricted to LOCAL/TEST"
            )
        if not root.is_absolute():
            raise ValueError("local secret store root must be an absolute path")
        self._root = root

    async def put(self, secret: SecretValue) -> SecretReference:
        identifier = uuid7().hex
        value = secret.reveal()
        await asyncio.to_thread(self._write_secret, identifier, value)
        return SecretReference(f"{_PREFIX}{identifier}")

    async def get(self, reference: SecretReference) -> SecretValue:
        identifier = self._identifier(reference)
        try:
            value = await asyncio.to_thread(
                self._path(identifier).read_text,
                encoding="utf-8",
            )
        except FileNotFoundError as error:
            raise LookupError("secret reference not found") from error
        return SecretValue(value)

    async def delete(self, reference: SecretReference) -> None:
        identifier = self._identifier(reference)
        await asyncio.to_thread(self._path(identifier).unlink, missing_ok=True)

    def _write_secret(self, identifier: str, value: str) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._path(identifier)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            encoded = value.encode("utf-8")
            written = 0
            while written < len(encoded):
                written += os.write(descriptor, encoded[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _path(self, identifier: str) -> Path:
        return self._root / identifier

    @staticmethod
    def _identifier(reference: SecretReference) -> str:
        if not reference.value.startswith(_PREFIX):
            raise LookupError("secret reference is not owned by local file store")
        identifier = reference.value.removeprefix(_PREFIX)
        if _IDENTIFIER.fullmatch(identifier) is None:
            raise LookupError("local secret reference is invalid")
        return identifier
