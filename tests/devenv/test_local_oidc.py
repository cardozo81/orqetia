from __future__ import annotations

import json
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from orqetia.infrastructure import local_oidc as oidc
from orqetia.infrastructure.backoffice import OidcLoginRejected
from orqetia.infrastructure.customer_portal.app import CustomerPortalOidcRejected

ORIGIN = "https://localhost:8443"


@pytest.mark.asyncio
@pytest.mark.parametrize("audience,profile", [
    ("backoffice", "backoffice-admin"), ("portal", "client-admin"),
])
async def test_local_login_token_pkce_origin_state_and_epoch_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audience: str, profile: str,
) -> None:
    key, token = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    path = tmp_path / "login.json"
    path.write_text(json.dumps({profile: token}), encoding="utf-8")
    monkeypatch.setattr(oidc, "LOCAL_LOGIN_TOKENS_PATH", path)
    monkeypatch.setattr(oidc, "LOCAL_USED_CODES_PATH", tmp_path / "used")
    cls = (oidc.LocalBackofficeOidcBroker if audience == "backoffice"
           else oidc.LocalCustomerPortalOidcBroker)
    rejected = OidcLoginRejected if audience == "backoffice" else CustomerPortalOidcRejected
    broker = cls(
        environment="test", signing_key=key, audience=audience,
        callback_url=ORIGIN + f"/{audience}/callback", idp_public_url=ORIGIN + "/dev-idp",
    )
    app = oidc.create_local_oidc_app(environment="test", signing_key=key,
                                   allowed_callbacks=(ORIGIN + f"/{audience}/callback",))
    start = await broker.begin_login()
    public = parse_qs(urlsplit(start.authorization_url).query)["transaction"][0]
    assert "code_verifier" not in oidc._verify(public, key.encode())
    assert "code_verifier" in oidc._verify(start.transaction_token, key.encode())
    form = {"transaction": public, "profile": profile, "access_token": token}
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        page = client.get("/authorize", params={"transaction": public})
        assert page.status_code == 200
        assert page.headers["referrer-policy"] == "strict-origin"
        assert client.post("/select", data=form, headers={"Origin": "null"}).status_code == 403
        assert client.get("/select", params=form).status_code == 405
        assert client.post("/select", data=form).status_code == 403
        assert client.post("/select", data=form,
                           headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert client.post("/select", data={**form, "access_token": "invalid"},
                           headers={"Origin": ORIGIN}).status_code == 403
        response = client.post("/select", data=form, headers={"Origin": ORIGIN})
    assert response.status_code == 303
    assert token not in response.text + response.headers["location"]
    query = parse_qs(urlsplit(response.headers["location"]).query)
    args = {"code": query["code"][0], "state": query["state"][0],
            "transaction_token": start.transaction_token}
    with pytest.raises(rejected):
        await broker.complete_login(**{**args, "state": "invalid"})
    with pytest.raises(rejected):
        await broker.complete_login(**{**args, "transaction_token": public})
    context = await broker.complete_login(**args)
    assert context.subject == f"local-{profile}"
    assert not (tmp_path / "used").exists()
    with pytest.raises(rejected, match="already consumed"):
        await broker.complete_login(**args)
    start2 = await broker.begin_login()
    with pytest.raises(rejected):
        await broker.complete_login(**{**args, "transaction_token": start2.transaction_token})


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_local_idp_rejects_nonlocal_environment(environment: str) -> None:
    with pytest.raises(RuntimeError, match="LOCAL/TEST"):
        oidc.create_local_oidc_app(environment=environment, signing_key=secrets.token_urlsafe(48),
                                   allowed_callbacks=(ORIGIN + "/backoffice/callback",))


def test_local_verifier_rejects_untrusted_encoding() -> None:
    with pytest.raises(ValueError):
        oidc._verify("\u00e9.signature", secrets.token_bytes(32))
