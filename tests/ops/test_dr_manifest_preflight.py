from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.dr_manifest_preflight import ARTIFACTS, main, validate_metadata, verify

NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixture(root: Path) -> dict[str, object]:
    artifacts: dict[str, dict[str, object]] = {}
    for name in ARTIFACTS:
        data = (f"offline synthetic data: {name}\n").encode()
        (root / name).write_bytes(data)
        artifacts[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "captured_at_utc": _stamp(NOW - timedelta(minutes=3)),
        }
    return {
        "schema_version": 1,
        "repository_sha": "a" * 40,
        "alembic_revision": "revision_123",
        "runtime_image_digest": "sha256:" + "b" * 64,
        "compose_sha256": "c" * 64,
        "caddyfile_sha256": "d" * 64,
        "artifacts": artifacts,
    }


def test_valid_synthetic_three_archive_manifest(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    assert verify(manifest, tmp_path, now=NOW, max_age_minutes=15, max_skew_minutes=5) == []


def test_digest_and_size_mismatch_fail_closed(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    target = tmp_path / "local-data.tar"
    target.write_bytes(target.read_bytes().replace(b"synthetic", b"modified "))
    assert "local-data.tar byte integrity mismatch" in verify(manifest, tmp_path, now=NOW)
    target.write_bytes(b"a")
    assert "local-data.tar byte integrity mismatch" in verify(manifest, tmp_path, now=NOW)


def test_missing_archive_fails_closed(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    (tmp_path / "caddy-data.tar").unlink()
    assert any("caddy-data.tar missing" in error for error in verify(manifest, tmp_path, now=NOW))


def test_untrusted_fields_do_not_leak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _fixture(tmp_path)
    leak = "NEVER_PRINT_RAW_SECRET"
    manifest["secret_token"] = leak
    assert validate_metadata(manifest, now=NOW) == ["invalid manifest structure"]
    manifest_path = tmp_path / "private.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["--manifest", str(manifest_path), "--backup-root", str(tmp_path)]) == 1
    assert leak not in capsys.readouterr().out


def test_reject_artifact_path_escape_and_unknown_artifacts(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    entries = manifest["artifacts"]
    assert isinstance(entries, dict)
    entries["../secret.key"] = entries.pop("caddy-data.tar")
    assert validate_metadata(manifest, now=NOW) == ["incomplete or unexpected artifact set"]


def test_reject_archive_symlink(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    (tmp_path / "postgres.dump").unlink()
    try:
        (tmp_path / "postgres.dump").symlink_to(tmp_path / "local-data.tar")
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks is restricted on this host")
    assert any("postgres.dump missing" in error for error in verify(manifest, tmp_path, now=NOW))


def test_reject_backup_root_symlink(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    link = tmp_path.parent / f"{tmp_path.name}-link"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks is restricted on this host")
    try:
        assert verify(manifest, link, now=NOW) == ["invalid backup directory"]
    finally:
        link.unlink()


def test_reject_bad_utc_capture_freshness_and_skew(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    entries = manifest["artifacts"]
    assert isinstance(entries, dict)
    entries["postgres.dump"]["captured_at_utc"] = "2026-10-13T10:00:00+03:00"
    assert any("UTC timestamp" in err for err in validate_metadata(manifest, now=NOW))
    entries["postgres.dump"]["captured_at_utc"] = _stamp(NOW - timedelta(hours=2))
    errors = validate_metadata(manifest, now=NOW, max_age_minutes=15, max_skew_minutes=5)
    assert any("backup age proxy" in err for err in errors)
    assert "backup capture times exceed configured skew proxy" in errors
    entries["postgres.dump"]["captured_at_utc"] = _stamp(NOW + timedelta(minutes=7))
    assert any("in the future" in err for err in validate_metadata(manifest, now=NOW))


def test_reject_invalid_metadata_types_and_version(tmp_path: Path) -> None:
    manifest = _fixture(tmp_path)
    manifest["schema_version"] = True
    manifest["runtime_image_digest"] = "sha256:relative/path"
    entries = manifest["artifacts"]
    assert isinstance(entries, dict)
    entries["postgres.dump"]["size_bytes"] = True
    errors = validate_metadata(manifest, now=NOW)
    assert "unsupported manifest version" in errors
    assert "invalid runtime image digest" in errors
    assert "invalid postgres.dump size" in errors


def test_cli_reports_safe_pass_and_disclaimer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _fixture(tmp_path)
    # CLI uses wall clock: generate a current synthetic capture time only here.
    entries = manifest["artifacts"]
    assert isinstance(entries, dict)
    for record in entries.values():
        record["captured_at_utc"] = _stamp(datetime.now(UTC))
    path = tmp_path / "operator-private.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = main(["--manifest", str(path), "--backup-root", str(tmp_path)])
    output = capsys.readouterr().out
    assert result == 0
    assert "PREFLIGHT PASS" in output
    assert "HOST-LOSS RECOVERY NOT PROVEN" in output
    assert str(tmp_path) not in output


def test_manifest_file_size_bounded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b"x" * 17_000)
    assert main(["--manifest", str(path), "--backup-root", str(tmp_path)]) == 2
    assert "manifest unreadable or malformed" in capsys.readouterr().out

