"""Development-only durable audit sink for local Docker evidence."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from uuid import UUID


class LocalJsonlAuditSink:
    """Append safe domain audit events to a LOCAL/TEST-only JSONL file."""

    def __init__(self, path: Path, *, environment: str) -> None:
        normalized = environment.strip().lower()
        if normalized not in {"local", "test"}:
            raise RuntimeError("local audit sink is restricted to LOCAL/TEST")
        if not path.is_absolute():
            raise ValueError("local audit path must be absolute")
        self._path = path

    async def record(self, event: object) -> None:
        payload = {
            "event_type": type(event).__name__,
            "event": self._json_value(event),
        }
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        await asyncio.to_thread(self._append, encoded)

    def _append(self, encoded: bytes) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            self._path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _json_value(cls, value: object) -> object:
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: cls._json_value(getattr(value, field.name))
                for field in dataclasses.fields(value)
            }
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, tuple):
            return [cls._json_value(item) for item in value]
        if isinstance(value, frozenset):
            return sorted(str(item) for item in value)
        if isinstance(value, dict):
            return {
                str(key): cls._json_value(item)
                for key, item in value.items()
            }
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)
