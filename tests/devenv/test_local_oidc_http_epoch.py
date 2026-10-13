"""Real IdP/BFF callback and independent session store across verifier restart."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid7

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orqetia.identity import (
    BackofficeAuthorizationService,
    BackofficeRole,
    BackofficeWebSessionService,
    InMemoryBackofficeAuthzAuditSink,
    InMemoryBackofficeBindingRepository,
    InMemoryBackofficeWebSessionStore,
)
from orqetia.identity.customer_authz import (
    CustomerAuthorizationService,
    CustomerRole,
    InMemoryCustomerAuthzAuditSink,
    InMemoryCustomerIdentityRepository,
)
from orqetia.identity.customer_web_sessions import (
    CustomerPortalWebSessionService,
    InMemoryCustomerPortalWebSessionStore,
)
from orqetia.infrastructure import local_oidc as oidc
from orqetia.infrastructure.backoffice import create_backoffice_app
from orqetia.infrastructure.customer_portal import create_customer_portal_app
from tests.portal.test_customer_portal_security_shell import _OwnerResolver

ORIGIN = "https://synthetic.invalid"


async def app_factory(audience: str, key: str) -> Callable[[], FastAPI]:
    """Recreate only the verifier/app; retain the independently owned session store."""
    kwargs = dict(environment="test", signing_key=key, audience=audience,
                  callback_url=f"{ORIGIN}/{audience}/callback",
                  idp_public_url=f"{ORIGIN}/dev-idp")
    if audience == "backoffice":
        authorization = BackofficeAuthorizationService(
            repository=InMemoryBackofficeBindingRepository(),
            audit=InMemoryBackofficeAuthzAuditSink(),
        )
        await authorization.create_binding(
            issuer=oidc.LOCAL_OIDC_ISSUER, subject="local-backoffice-admin",
            roles=(BackofficeRole.ADMIN,), occurred_at=datetime.now(UTC),
        )
        sessions = BackofficeWebSessionService(
            store=InMemoryBackofficeWebSessionStore(), authorization=authorization,
        )
        return lambda: create_backoffice_app(
            oidc=oidc.LocalBackofficeOidcBroker(**kwargs), authorization=authorization,
            sessions=sessions, allowed_origin=ORIGIN,
        )
    customer_authorization = CustomerAuthorizationService(
        repository=InMemoryCustomerIdentityRepository(), owner_resolver=_OwnerResolver(),
        audit=InMemoryCustomerAuthzAuditSink(),
    )
    identity = await customer_authorization.create_identity(
        issuer=oidc.LOCAL_OIDC_ISSUER, subject="local-client-admin",
        occurred_at=datetime.now(UTC),
    )
    await customer_authorization.add_membership(
        identity_id=identity.identity_id, tenant_id=uuid7(), client_id=uuid7(),
        roles=(CustomerRole.OWNER,), occurred_at=datetime.now(UTC),
    )
    customer_sessions = CustomerPortalWebSessionService(
        store=InMemoryCustomerPortalWebSessionStore(), authorization=customer_authorization,
    )
    return lambda: create_customer_portal_app(
        oidc=oidc.LocalCustomerPortalOidcBroker(**kwargs), authorization=customer_authorization,
        sessions=customer_sessions, allowed_origin=ORIGIN,
    )


@pytest.mark.parametrize("audience,profile", [
    ("backoffice", "backoffice-admin"), ("portal", "client-admin"),
])
def test_http_login_replay_restart_and_established_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audience: str, profile: str,
) -> None:
    key, token = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    tokens = tmp_path / "synthetic-tokens.json"
    tokens.write_text(json.dumps({profile: token}))
    monkeypatch.setattr(oidc, "LOCAL_LOGIN_TOKENS_PATH", tokens)
    monkeypatch.setattr(oidc, "LOCAL_USED_CODES_PATH", tmp_path / "legacy")
    factory = asyncio.run(app_factory(audience, key))
    idp_app = oidc.create_local_oidc_app(
        environment="test", signing_key=key,
        allowed_callbacks=(f"{ORIGIN}/{audience}/callback",),
    )
    with (TestClient(factory(), base_url=ORIGIN, follow_redirects=False) as bff,
          TestClient(idp_app, base_url=ORIGIN, follow_redirects=False) as idp):

        def start() -> tuple[str, str]:
            response = bff.get(f"/{audience}/login")
            assert response.status_code == 302
            public = parse_qs(urlsplit(response.headers["location"]).query)["transaction"][0]
            selected = idp.post("/select", headers={"Origin": ORIGIN}, data={
                "transaction": public, "profile": profile, "access_token": token,
            })
            assert selected.status_code == 303
            # Preserve the original cookie deliberately to test replay, not missing-cookie denial.
            return selected.headers["location"], "; ".join(
                f"{c.name}={c.value}" for c in bff.cookies.jar if "oidc" in c.name
            )

        callback, cookie = start()
        assert cookie
        established = bff.get(callback)
        assert established.status_code == 303
        assert "HttpOnly" in established.headers["set-cookie"]
        assert bff.get(f"/{audience}").status_code == 200
        assert bff.get(callback, headers={"Cookie": cookie}).status_code == 401
        session_cookies = dict(bff.cookies)
        pending, pending_cookie = start()
        with TestClient(factory(), base_url=ORIGIN, follow_redirects=False) as restarted:
            assert restarted.get(pending, headers={"Cookie": pending_cookie}).status_code == 401
            assert restarted.get(callback, headers={"Cookie": cookie}).status_code == 401
            restarted.cookies.update(session_cookies)
            assert restarted.get(f"/{audience}").status_code == 200
    assert not (tmp_path / "legacy").exists()
