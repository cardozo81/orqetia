from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from orqetia.identity import (
    AuthenticatedPrincipal,
    AuthenticationRejected,
    ClientAccessCredentialService,
    InMemoryClientCredentialStore,
    StoredClientCredentialAuthenticator,
)
from orqetia.infrastructure.http import create_app

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = json.loads(
    (ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json").read_text(
        encoding="utf-8"
    )
)
TENANT_ID = UUID("0199b39a-9bf1-7000-8000-000000000020")
CLIENT_ID = UUID("0199b39a-9bf1-7000-8000-000000000021")


class ManagementAuthenticator:
    async def authenticate_bearer(self, token: str) -> AuthenticatedPrincipal:
        assert token == "management"
        return AuthenticatedPrincipal(
            subject_type="SERVICE_CLIENT",
            subject_id="management",
            tenant_id=str(TENANT_ID),
            client_id=str(CLIENT_ID),
            scopes=frozenset(
                {
                    "credentials:read",
                    "credentials:write",
                    "usage:read",
                    "tasks:write",
                }
            ),
        )


async def _request(
    app,
    method: str,
    path: str,
    *,
    body: object | None = None,
    key: str | None = None,
) -> httpx.Response:
    headers = {"Authorization": "Bearer management"}
    if key is not None:
        headers["Idempotency-Key"] = key
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(
            method,
            path,
            json=body,
            headers=headers,
        )


def test_issue_replay_list_rotate_and_revoke_http_contract() -> None:
    store = InMemoryClientCredentialStore()
    service = ClientAccessCredentialService(store)
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=ManagementAuthenticator(),
        client_credential_service=service,
    )

    issued = asyncio.run(
        _request(
            app,
            "POST",
            "/v1/credentials",
            body={
                "display_label": "developer",
                "scopes": ["usage:read"],
            },
            key="issue-http-1",
        )
    )
    assert issued.status_code == 200
    first = issued.json()
    assert first["secret_available"] is True
    assert first["replayed"] is False
    assert first["secret"]
    credential_id = first["credential"]["credential_id"]

    replay = asyncio.run(
        _request(
            app,
            "POST",
            "/v1/credentials",
            body={
                "display_label": "developer",
                "scopes": ["usage:read"],
            },
            key="issue-http-1",
        )
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["secret"] is None

    listed = asyncio.run(_request(app, "GET", "/v1/credentials"))
    assert listed.status_code == 200
    text = listed.text.lower()
    assert "secret_hash" not in text
    assert "secret_salt" not in text
    assert first["secret"].lower() not in text

    rotated = asyncio.run(
        _request(
            app,
            "POST",
            f"/v1/credentials/{credential_id}/rotate",
            key="rotate-http-1",
        )
    )
    assert rotated.status_code == 200
    second_secret = rotated.json()["secret"]
    assert second_secret
    assert second_secret != first["secret"]

    stored_auth = StoredClientCredentialAuthenticator(store=store)
    with pytest.raises(AuthenticationRejected):
        asyncio.run(stored_auth.authenticate_bearer(first["secret"]))
    principal = asyncio.run(stored_auth.authenticate_bearer(second_secret))
    assert principal.credential_id == credential_id

    revoked = asyncio.run(
        _request(
            app,
            "POST",
            f"/v1/credentials/{credential_id}/revoke",
            key="revoke-http-1",
        )
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    with pytest.raises(AuthenticationRejected):
        asyncio.run(stored_auth.authenticate_bearer(second_secret))


def test_client_cannot_mint_scope_it_does_not_hold() -> None:
    store = InMemoryClientCredentialStore()
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=ManagementAuthenticator(),
        client_credential_service=ClientAccessCredentialService(store),
    )
    response = asyncio.run(
        _request(
            app,
            "POST",
            "/v1/credentials",
            body={
                "display_label": "escalation",
                "scopes": ["admin:write"],
            },
            key="issue-http-forbidden",
        )
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_credential_mutations_require_idempotency_key() -> None:
    store = InMemoryClientCredentialStore()
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=ManagementAuthenticator(),
        client_credential_service=ClientAccessCredentialService(store),
    )
    response = asyncio.run(
        _request(
            app,
            "POST",
            "/v1/credentials",
            body={
                "display_label": "missing-key",
                "scopes": ["usage:read"],
            },
        )
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
