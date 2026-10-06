#!/usr/bin/env python3
"""Zero-dependency repository security baseline checks."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b"),
)

ACTION_USE = re.compile(r"^\s*-\s+uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def check_env_files(paths: list[Path], errors: list[str]) -> None:
    allowed = {".env.example", ".env.template", ".env.sample"}
    for path in paths:
        name = path.name
        if (name == ".env" or name.startswith(".env.")) and name not in allowed:
            errors.append(f"SEC-013 committed environment file: {path.relative_to(ROOT)}")


def check_secrets(paths: list[Path], errors: list[str]) -> None:
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError, UnicodeError:
            continue
        if len(data) > 2_000_000:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(data):
                errors.append(
                    f"SEC-013 likely secret/private key material: {path.relative_to(ROOT)}"
                )
                break


def check_minimal_permissions(
    *,
    path: Path,
    content: str,
    errors: list[str],
) -> None:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("permissions:"):
            continue
        indent = len(line) - len(stripped)
        inline = stripped.split(":", 1)[1].split("#", 1)[0].strip()
        if inline in {"read-all", "write-all"}:
            errors.append(
                f"SEC-013 broad workflow permissions prohibited: "
                f"{path.relative_to(ROOT)} -> {inline}"
            )
            continue
        if inline:
            continue

        for child in lines[index + 1 :]:
            child_stripped = child.lstrip()
            if not child_stripped or child_stripped.startswith("#"):
                continue
            child_indent = len(child) - len(child_stripped)
            if child_indent <= indent:
                break
            match = re.match(
                r"([A-Za-z0-9_-]+):\s*(read|write|none)\b",
                child_stripped,
            )
            if match is not None and match.group(2) == "write":
                errors.append(
                    f"SEC-013 workflow write permission requires explicit "
                    f"security exception: {path.relative_to(ROOT)} -> "
                    f"{match.group(1)}: write"
                )


def check_workflows(errors: list[str]) -> None:
    directory = ROOT / ".github" / "workflows"
    if not directory.exists():
        return
    for path in sorted(directory.glob("*.y*ml")):
        content = path.read_text(encoding="utf-8")
        if "pull_request_target:" in content:
            errors.append(f"SEC-013 pull_request_target prohibited: {path.relative_to(ROOT)}")
        if "permissions:" not in content:
            errors.append(f"SEC-013 workflow lacks explicit permissions: {path.relative_to(ROOT)}")
        check_minimal_permissions(path=path, content=content, errors=errors)
        if re.search(r"(?:curl|wget).*[|]\s*(?:sh|bash)\b", content):
            errors.append(f"SEC-013 pipe-to-shell in workflow: {path.relative_to(ROOT)}")
        for action, ref in ACTION_USE.findall(content):
            if action.startswith("./"):
                continue
            if not FULL_SHA.fullmatch(ref):
                errors.append(
                    f"SEC-013 external action not pinned to full SHA: "
                    f"{path.relative_to(ROOT)} -> {action}@{ref}"
                )


def main() -> int:
    errors: list[str] = []
    paths = tracked_files()
    check_env_files(paths, errors)
    check_secrets(paths, errors)
    check_workflows(errors)

    if errors:
        print("Security baseline failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Security baseline passed for {len(paths)} tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
