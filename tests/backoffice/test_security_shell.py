from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from orqetia.identity import (
    BackofficeAuthorizationService,
    BackofficeBindingStatus,
    BackofficeRole,
    BackofficeWebSessionService,
    HumanAuthenticationContext,
    InMemoryBackofficeAuthzAuditSink,
    InMemoryBackofficeBindingRepository,
    InMemoryBackofficeWebSessionStore,
)
from orqetia.infrastructure.backoffice import (
    OidcAuthorizationStart,
    OidcLoginRejected,
    create_backoffice_app,
)

ISSUER = "https://idp.example.com"


class FakeOidcBroker:
    def __init__(self, *, mfa: bool = True) -> None:
        self.mfa = mfa

    async def begin_login(self) -> OidcAuthorizationStart:
        return OidcAuthorizationStart(
            authorization_url="https://idp.example.com/authorize",
            transaction_token="opaque-transaction",
        )

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        if (
            code != "valid-code"
            or state != "valid-state"
            or transaction_token != "opaque-transaction"
        ):
            raise OidcLoginRejected()
        return HumanAuthenticationContext(
            issuer=ISSUER,
            subject="admin-subject",
            authenticated_at=datetime.now(UTC),
            mfa_satisfied=self.mfa,
            amr=("pwd", "webauthn") if self.mfa else ("pwd",),
            acr="urn:mfa" if self.mfa else None,
        )


async def _fixture(*, mfa: bool = True):
    repository = InMemoryBackofficeBindingRepository()
    authorization = BackofficeAuthorizationService(
        repository=repository,
        audit=InMemoryBackofficeAuthzAuditSink(),
    )
    binding = await authorization.create_binding(
        issuer=ISSUER,
        subject="admin-subject",
        roles=(BackofficeRole.ADMIN,),
        occurred_at=datetime.now(UTC),
    )
    sessions = BackofficeWebSessionService(
        store=InMemoryBackofficeWebSessionStore(),
        authorization=authorization,
    )
    app = create_backoffice_app(
        oidc=FakeOidcBroker(mfa=mfa),
        authorization=authorization,
        sessions=sessions,
        allowed_origin="https://test",
        enable_hsts=True,
    )
    return app, authorization, binding


async def _login(client: httpx.AsyncClient) -> httpx.Response:
    start = await client.get("/backoffice/login", follow_redirects=False)
    assert start.status_code == 302
    assert start.headers["location"] == "https://idp.example.com/authorize"
    callback = await client.get(
        "/backoffice/callback?code=valid-code&state=valid-state",
        follow_redirects=False,
    )
    return callback


@pytest.mark.asyncio
async def test_oidc_login_creates_secure_server_side_session_and_home() -> None:
    app, _authorization, _binding = await _fixture()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
    ) as client:
        callback = await _login(client)
        assert callback.status_code == 303
        assert callback.headers["location"] == "/backoffice"
        cookies = callback.headers.get_list("set-cookie")
        joined = "\n".join(cookies).lower()
        assert "__host-orqetia-bo-session=" in joined
        assert "httponly" in joined
        assert "secure" in joined
        assert "__host-orqetia-bo-csrf=" in joined

        home = await client.get("/backoffice")
        assert home.status_code == 200
        assert "admin-subject" in home.text
        assert "default-src 'none'" in home.headers["content-security-policy"]
        assert home.headers["cache-control"] == "no-store"
        assert home.headers["referrer-policy"] == "strict-origin"
        assert "max-age=31536000" in home.headers["strict-transport-security"]


@pytest.mark.asyncio
async def test_logout_requires_same_origin_and_bound_csrf() -> None:
    app, _authorization, _binding = await _fixture()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
    ) as client:
        await _login(client)
        csrf = client.cookies.get("__Host-orqetia-bo-csrf")
        assert csrf

        rejected = await client.post(
            "/backoffice/logout",
            headers={"Origin": "https://evil.example"},
            content=f"csrf_token={csrf}",
            follow_redirects=False,
        )
        assert rejected.status_code == 401

        null_origin = await client.post(
            "/backoffice/logout",
            headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert null_origin.status_code == 401

        rejected_csrf = await client.post(
            "/backoffice/logout",
            headers={"Origin": "https://test"},
            data={"csrf_token": "wrong"},
            follow_redirects=False,
        )
        assert rejected_csrf.status_code == 401

        logout = await client.post(
            "/backoffice/logout",
            headers={"Origin": "https://test"},
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert logout.status_code == 303

        home = await client.get("/backoffice")
        assert home.status_code == 401


@pytest.mark.asyncio
async def test_local_disable_revokes_authorization_on_next_request() -> None:
    app, authorization, binding = await _fixture()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
    ) as client:
        await _login(client)
        assert (await client.get("/backoffice")).status_code == 200

        await authorization.set_status(
            binding_id=binding.binding_id,
            status=BackofficeBindingStatus.DISABLED,
            occurred_at=datetime.now(UTC),
        )
        assert (await client.get("/backoffice")).status_code == 401


@pytest.mark.asyncio
async def test_backoffice_login_requires_mfa() -> None:
    app, _authorization, _binding = await _fixture(mfa=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
    ) as client:
        callback = await _login(client)
        assert callback.status_code == 401
        assert "__Host-orqetia-bo-session" not in client.cookies
