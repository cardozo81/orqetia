#!/usr/bin/env python3
"""Validate security/reproducibility invariants of rendered Compose JSON."""

from __future__ import annotations

import json
import sys
from typing import Any

EXPECTED_SERVICES = {"api", "worker", "scheduler", "migrate", "postgres"}
RUNTIME_SERVICES = {"api", "worker", "scheduler", "migrate"}


def validate_compose(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return ["compose config has no services mapping"]

    names = set(services)
    if names != EXPECTED_SERVICES:
        errors.append(f"unexpected service set: {sorted(names)}")

    for name, service in services.items():
        if not isinstance(service, dict):
            errors.append(f"service {name} is not an object")
            continue
        if service.get("privileged") is True:
            errors.append(f"service {name} must not be privileged")
        if service.get("network_mode") == "host":
            errors.append(f"service {name} must not use host networking")

    postgres = services.get("postgres", {})
    ports = postgres.get("ports", []) if isinstance(postgres, dict) else []
    if not ports:
        errors.append("postgres must bind a loopback-only development port")
    for port in ports:
        if not isinstance(port, dict):
            errors.append("postgres port must render as structured mapping")
            continue
        if port.get("host_ip") not in {"127.0.0.1", "::1"}:
            errors.append("postgres host port must be loopback-only")

    api = services.get("api", {})
    api_ports = api.get("ports", []) if isinstance(api, dict) else []
    if not api_ports:
        errors.append("api must bind a loopback-only development port")
    for port in api_ports:
        if not isinstance(port, dict):
            errors.append("api port must render as structured mapping")
            continue
        if port.get("host_ip") not in {"127.0.0.1", "::1"}:
            errors.append("api host port must be loopback-only")
        if int(port.get("target", 0)) != 8000:
            errors.append("api container port must be 8000")

    volumes = postgres.get("volumes", []) if isinstance(postgres, dict) else []
    if not any(
        isinstance(item, dict) and str(item.get("target", "")).startswith("/var/lib/postgresql")
        for item in volumes
    ):
        errors.append("postgres must persist data under /var/lib/postgresql")

    for name in RUNTIME_SERVICES:
        service = services.get(name, {})
        environment = service.get("environment", {}) if isinstance(service, dict) else {}
        if environment.get("ORQETIA_DATABASE_DSN") is None:
            errors.append(f"{name} is missing ORQETIA_DATABASE_DSN")
        if environment.get("ORQETIA_SECRET_STORE_MODE") != "local":
            errors.append(f"{name} must use synthetic local secret-store mode")
        if any(key.startswith("PROVIDER_") for key in environment):
            errors.append(f"{name} must not receive provider credentials in baseline Compose")

    return errors


def main() -> int:
    config = json.load(sys.stdin)
    errors = validate_compose(config)
    if errors:
        print("Compose contract failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Compose contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
