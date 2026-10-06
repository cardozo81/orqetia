from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from orqetia.estimation import EstimateService
from orqetia.identity.backoffice_authz import HumanAuthenticationContext
from orqetia.infrastructure.customer_portal import (
    CustomerPortalOidcAuthorizationStart,
    build_customer_portal_app,
)
from orqetia.infrastructure.persistence import create_engine, create_session_factory
from orqetia.settings import RuntimeSettings


@dataclass
class _Oidc:
    async def begin_login(self) -> CustomerPortalOidcAuthorizationStart:
        return CustomerPortalOidcAuthorizationStart(
            authorization_url="https://idp.example/authorize",
            transaction_token="transaction",
        )

    async def complete_login(
        self,
        *,
        code: str,
        state: str,
        transaction_token: str,
    ) -> HumanAuthenticationContext:
        del code, state, transaction_token
        raise AssertionError("callback is outside this composition smoke")


class _Estimate(EstimateService):
    async def estimate(self, *, subject, spec):
        del subject, spec
        raise AssertionError("estimate is outside this composition smoke")


def test_postgres_portal_composition_serves_canonical_openapi_without_db_query() -> None:
    settings = RuntimeSettings()
    engine = create_engine(settings)
    try:
        app = build_customer_portal_app(
            session_factory=create_session_factory(engine),
            oidc=_Oidc(),
            allowed_origin="https://portal.example",
            client_openapi_document={
                "openapi": "3.1.0",
                "info": {"title": "ORQETIA", "version": "1"},
                "paths": {"/v1/sessions": {}},
            },
            estimation=_Estimate(),
        )
        client = TestClient(
            app,
            base_url="https://portal.example",
            follow_redirects=False,
        )
        contract = client.get("/openapi.json")
        assert contract.status_code == 200
        assert "/v1/sessions" in contract.json()["paths"]

        login = client.get("/portal/login")
        assert login.status_code == 302
        assert login.headers["location"] == "https://idp.example/authorize"
    finally:
        import asyncio

        asyncio.run(engine.dispose())
