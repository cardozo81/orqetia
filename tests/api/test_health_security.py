from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

import httpx

from tests.api.test_fastapi_shell import FakeAuthenticator
from orqetia.infrastructure.http import create_app


ROOT = Path(__file__).resolve().parents[2]
CANONICAL = json.loads(
    (ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json").read_text(encoding="utf-8")
)


class ReadyProbe:
    async def check(self) -> None:
        return None


class FailingProbe:
    async def check(self) -> None:
        raise RuntimeError("synthetic database outage")


async def request(
    app: object,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        return await client.request(method, path, headers=headers)


class HealthAndHttpSecurityTests(unittest.TestCase):
    def test_liveness_has_no_dependency_and_no_authentication(self) -> None:
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            readiness_probe=FailingProbe(),
        )
        response = asyncio.run(request(app, "GET", "/health/live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "live"})

    def test_readiness_reflects_local_database_dependency(self) -> None:
        ready_app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            readiness_probe=ReadyProbe(),
        )
        failing_app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            readiness_probe=FailingProbe(),
        )

        ready = asyncio.run(request(ready_app, "GET", "/health/ready"))
        failing = asyncio.run(request(failing_app, "GET", "/health/ready"))

        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["checks"]["database"], "ready")
        self.assertEqual(failing.status_code, 503)
        self.assertEqual(failing.json()["checks"]["database"], "unavailable")
        self.assertNotIn("synthetic database outage", failing.text)

    def test_health_routes_do_not_change_canonical_openapi(self) -> None:
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            readiness_probe=ReadyProbe(),
        )
        response = asyncio.run(request(app, "GET", "/openapi.json"))
        self.assertEqual(response.json(), CANONICAL)
        self.assertNotIn("/health/live", response.json()["paths"])

    def test_security_headers_are_present(self) -> None:
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
        )
        response = asyncio.run(request(app, "GET", "/health/live"))

        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])
        self.assertNotIn("strict-transport-security", response.headers)

    def test_hsts_is_explicitly_enabled_for_production_composition(self) -> None:
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            enable_hsts=True,
        )
        response = asyncio.run(request(app, "GET", "/health/live"))
        self.assertIn("max-age=31536000", response.headers["strict-transport-security"])

    def test_cors_is_allowlist_only_without_credentials(self) -> None:
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(),
            cors_allowed_origins=("https://client.example",),
        )

        allowed = asyncio.run(
            request(
                app,
                "OPTIONS",
                "/v1/providers",
                headers={
                    "Origin": "https://client.example",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )
        )
        blocked = asyncio.run(
            request(
                app,
                "OPTIONS",
                "/v1/providers",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "GET",
                },
            )
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(
            allowed.headers["access-control-allow-origin"],
            "https://client.example",
        )
        self.assertNotIn("access-control-allow-credentials", allowed.headers)
        self.assertNotIn("access-control-allow-origin", blocked.headers)


if __name__ == "__main__":
    unittest.main()
