from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from orqetia.identity.authentication import AuthenticatedPrincipal, AuthenticationRejected
from orqetia.infrastructure.http import create_app


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json"
CANONICAL = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
ALL_SCOPES = frozenset(
    {
        "sessions:write",
        "sessions:read",
        "tasks:write",
        "tasks:read",
        "tasks:cancel",
        "tasks:target",
        "estimates:write",
        "catalog:read",
        "usage:read",
    }
)


class FakeAuthenticator:
    def __init__(self, scopes: frozenset[str] = ALL_SCOPES) -> None:
        self.scopes = scopes

    async def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal:
        if token != "synthetic-token":
            raise AuthenticationRejected()
        return AuthenticatedPrincipal(
            subject_type="SERVICE_CLIENT",
            subject_id="subject-1",
            tenant_id="tenant-1",
            client_id="client-1",
            scopes=self.scopes,
        )


def canonical_route_methods() -> set[tuple[str, str]]:
    methods = {"get", "post", "put", "patch", "delete"}
    return {
        (path, method.upper())
        for path, item in CANONICAL["paths"].items()
        for method in item
        if method in methods
    }


def app_route_methods(app: Any) -> set[tuple[str, str]]:
    allowed = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    output: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/v1"):
            continue
        for method in getattr(route, "methods", set()):
            if method in allowed:
                output.add((path, method))
    return output


async def request(
    app: Any,
    method: str,
    path: str,
    *,
    token: str | None = "synthetic-token",
    json_body: object | None = None,
    correlation_id: str | None = None,
) -> httpx.Response:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers=headers, json=json_body)


class FastApiShellTests(unittest.TestCase):
    def test_v1_route_method_parity_matches_canonical_contract(self) -> None:
        app = create_app(openapi_document=CANONICAL, authenticator=FakeAuthenticator())
        self.assertEqual(app_route_methods(app), canonical_route_methods())

    def test_openapi_endpoint_serves_exact_canonical_document(self) -> None:
        app = create_app(openapi_document=CANONICAL, authenticator=FakeAuthenticator())
        response = asyncio.run(request(app, "GET", "/openapi.json", token=None))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), CANONICAL)

    def test_authenticated_route_returns_not_implemented_not_fake_success(self) -> None:
        app = create_app(openapi_document=CANONICAL, authenticator=FakeAuthenticator())
        response = asyncio.run(
            request(
                app,
                "POST",
                "/v1/sessions",
                json_body={},
                correlation_id="client-correlation-1",
            )
        )
        self.assertEqual(response.status_code, 501)
        self.assertEqual(response.json()["code"], "NOT_IMPLEMENTED")
        self.assertEqual(response.json()["correlation_id"], "client-correlation-1")
        self.assertEqual(response.headers["x-correlation-id"], "client-correlation-1")

    def test_missing_authentication_is_safe_401(self) -> None:
        app = create_app(openapi_document=CANONICAL, authenticator=FakeAuthenticator())
        response = asyncio.run(request(app, "GET", "/v1/providers", token=None))
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["code"], "AUTHENTICATION_REQUIRED")
        self.assertNotIn("traceback", response.text.lower())

    def test_invalid_bearer_is_safe_401(self) -> None:
        app = create_app(openapi_document=CANONICAL, authenticator=FakeAuthenticator())
        response = asyncio.run(request(app, "GET", "/v1/providers", token="wrong"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTHENTICATION_REJECTED")
        self.assertNotIn("wrong", response.text)

    def test_required_scope_is_enforced(self) -> None:
        app = create_app(openapi_document=CANONICAL, authenticator=FakeAuthenticator(frozenset()))
        response = asyncio.run(request(app, "GET", "/v1/providers"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "FORBIDDEN")

    def test_explicit_target_requires_conditional_scope(self) -> None:
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(frozenset({"tasks:write"})),
        )
        session_id = UUID("0199b39a-9bf1-7000-8000-000000000001")
        response = asyncio.run(
            request(
                app,
                "POST",
                f"/v1/sessions/{session_id}/tasks",
                json_body={
                    "operation": "TASK_EXECUTION",
                    "input": {},
                    "execution": {
                        "mode": "EXPLICIT_TARGET",
                        "target": {"provider": "OPENAI"},
                    },
                },
            )
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "FORBIDDEN")

    def test_auto_task_creation_with_base_scope_reaches_stub(self) -> None:
        app = create_app(
            openapi_document=CANONICAL,
            authenticator=FakeAuthenticator(frozenset({"tasks:write"})),
        )
        session_id = UUID("0199b39a-9bf1-7000-8000-000000000001")
        response = asyncio.run(
            request(
                app,
                "POST",
                f"/v1/sessions/{session_id}/tasks",
                json_body={"operation": "TASK_EXECUTION", "input": {}},
            )
        )
        self.assertEqual(response.status_code, 501)

    def test_validation_error_does_not_echo_sensitive_value(self) -> None:
        app = create_app(openapi_document=CANONICAL, authenticator=FakeAuthenticator())
        response = asyncio.run(
            request(
                app,
                "POST",
                "/v1/sessions",
                json_body={"provider_secret": "must-not-be-reflected"},
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "INVALID_REQUEST")
        self.assertNotIn("must-not-be-reflected", response.text)

    def test_invalid_correlation_id_is_replaced(self) -> None:
        app = create_app(openapi_document=CANONICAL, authenticator=FakeAuthenticator())
        response = asyncio.run(
            request(
                app,
                "GET",
                "/v1/providers",
                correlation_id="contains spaces and is invalid",
            )
        )
        self.assertEqual(response.status_code, 501)
        returned = response.headers["x-correlation-id"]
        self.assertNotEqual(returned, "contains spaces and is invalid")
        UUID(returned)


if __name__ == "__main__":
    unittest.main()
