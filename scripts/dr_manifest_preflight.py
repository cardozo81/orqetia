#!/usr/bin/env python3
"""Offline, read-only integrity preflight for three operator-supplied DR archives.

This validates bytes and declared recovery metadata; it cannot prove off-host
custody, backup consistency, operational recovery, or measured RPO/RTO.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ARTIFACTS = ("postgres.dump", "local-data.tar", "caddy-data.tar")
ROOT_FIELDS = frozenset({
    "schema_version", "repository_sha", "alembic_revision", "runtime_image_digest",
    "compose_sha256", "caddyfile_sha256", "artifacts",
})
ARTIFACT_FIELDS = frozenset({"sha256", "size_bytes", "captured_at_utc"})
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ALEMBIC = re.compile(r"[a-z0-9_]{1,64}\Z")
UTC_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
MAX_MANIFEST_BYTES = 16_384
CHUNK_BYTES = 1024 * 1024


def _utc_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or UTC_STAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_metadata(
    raw: object,
    *,
    now: datetime,
    max_age_minutes: int | None = None,
    max_skew_minutes: int | None = None,
) -> list[str]:
    """Validate a strict, non-secret manifest. Never echo untrusted field values."""
    if not isinstance(raw, dict) or set(raw) != ROOT_FIELDS:
        return ["invalid manifest structure"]
    errors: list[str] = []
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        errors.append("unsupported manifest version")
    for key, regex in (
        ("repository_sha", COMMIT), ("alembic_revision", ALEMBIC),
        ("compose_sha256", SHA256), ("caddyfile_sha256", SHA256),
    ):
        if not isinstance(raw[key], str) or regex.fullmatch(raw[key]) is None:
            errors.append(f"invalid {key} metadata")
    image = raw["runtime_image_digest"]
    if not isinstance(image, str) or not image.startswith("sha256:") or (
        SHA256.fullmatch(image[7:]) is None
    ):
        errors.append("invalid runtime image digest")

    artifacts = raw["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != set(ARTIFACTS):
        errors.append("incomplete or unexpected artifact set")
        return errors
    captures: list[datetime] = []
    for name in ARTIFACTS:
        record = artifacts[name]
        if not isinstance(record, dict) or set(record) != ARTIFACT_FIELDS:
            errors.append(f"invalid {name} metadata")
            continue
        if not isinstance(record["sha256"], str) or SHA256.fullmatch(record["sha256"]) is None:
            errors.append(f"invalid {name} digest")
        if type(record["size_bytes"]) is not int or record["size_bytes"] <= 0:
            errors.append(f"invalid {name} size")
        captured = _utc_stamp(record["captured_at_utc"])
        if captured is None:
            errors.append(f"invalid {name} capture UTC timestamp")
            continue
        captures.append(captured)
        if captured > now + timedelta(minutes=5):
            errors.append(f"{name} capture timestamp is in the future")
        if max_age_minutes is not None and now - captured > timedelta(minutes=max_age_minutes):
            errors.append(f"{name} exceeds configured backup age proxy")
    if (
        max_skew_minutes is not None and len(captures) == len(ARTIFACTS)
        and max(captures) - min(captures) > timedelta(minutes=max_skew_minutes)
    ):
        errors.append("backup capture times exceed configured skew proxy")
    return errors


def _digest_file(root: Path, name: str) -> tuple[int, str] | None:
    """Read a fixed basename without following a final symlink; never disclose paths."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        if (root / name).is_symlink():
            return None
        with os.fdopen(os.open(root / name, flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                return None
            hasher = hashlib.sha256()
            size = 0
            while chunk := stream.read(CHUNK_BYTES):
                size += len(chunk)
                hasher.update(chunk)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
                after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev
            ):
                return None
            return size, hasher.hexdigest()
    except OSError:
        return None


def verify(
    manifest: object,
    root: Path,
    *,
    now: datetime,
    max_age_minutes: int | None = None,
    max_skew_minutes: int | None = None,
) -> list[str]:
    errors = validate_metadata(
        manifest, now=now, max_age_minutes=max_age_minutes,
        max_skew_minutes=max_skew_minutes,
    )
    if errors:
        return errors
    try:
        # A nominated directory is read-only input. Reject symlink roots explicitly.
        if root.is_symlink() or not root.is_dir():
            return ["invalid backup directory"]
    except OSError:
        return ["invalid backup directory"]
    assert isinstance(manifest, dict)  # validated above
    artifacts = manifest["artifacts"]
    for name in ARTIFACTS:
        record = artifacts[name]
        actual = _digest_file(root, name)
        if actual is None:
            errors.append(f"{name} missing, unsafe, or changed during inspection")
        elif (actual[0] != record["size_bytes"] or not hmac.compare_digest(
            actual[1], record["sha256"]
        )):
            errors.append(f"{name} byte integrity mismatch")
    return errors


def _read_manifest(path: Path) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        if path.is_symlink():
            raise ValueError("manifest must not be a symlink")
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("manifest is not a regular file")
            data = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(data) > MAX_MANIFEST_BYTES:
                raise ValueError("manifest exceeds maximum size")
            return json.loads(data)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("manifest unreadable or malformed") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="operator-private JSON path")
    parser.add_argument(
        "--backup-root", required=True, type=Path, help="read-only archive directory"
    )
    parser.add_argument("--max-age-minutes", type=int, default=None)
    parser.add_argument("--max-skew-minutes", type=int, default=None)
    args = parser.parse_args(argv)
    for name in ("max_age_minutes", "max_skew_minutes"):
        if getattr(args, name) is not None and getattr(args, name) <= 0:
            print("DR PREFLIGHT FAIL: invalid positive age/skew limit")
            return 2
    try:
        manifest = _read_manifest(args.manifest)
    except ValueError:
        print("DR PREFLIGHT FAIL: manifest unreadable or malformed")
        return 2
    errors = verify(
        manifest, args.backup_root, now=datetime.now(UTC),
        max_age_minutes=args.max_age_minutes, max_skew_minutes=args.max_skew_minutes,
    )
    if errors:
        print("DR PREFLIGHT FAIL (local inspection only):")
        for error in errors:
            print(f"- {error}")
        print("HOST-LOSS RECOVERY NOT PROVEN")
        return 1
    print("DR PREFLIGHT PASS: local metadata and archive bytes agree")
    print("HOST-LOSS RECOVERY NOT PROVEN: independent custody, restore, RPO/RTO unverified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

