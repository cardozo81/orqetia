from __future__ import annotations

import json
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from orqetia.infrastructure import local_oidc as oidc
from orqetia.infrastructure.backoffice import OidcLoginRejected

ORIGIN = "https://localhost:8443"


@pytest.mark.asyncio
async def test_local_login_token_pkce_origin_state_and_durable_replay(tmp_path, monkeypatch):
    key, token = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    path = tmp_path / "login.json"
    path.write_text(json.dumps({"backoffice-admin": token}), encoding="utf-8")
    monkeypatch.setattr(oidc, "LOCAL_LOGIN_TOKENS_PATH", path)
    monkeypatch.setattr(oidc, "LOCAL_USED_CODES_PATH", tmp_path / "used")
    broker = oidc.LocalBackofficeOidcBroker(
        environment="test", signing_key=key, audience="backoffice",
        callback_url=ORIGIN + "/backoffice/callback", idp_public_url=ORIGIN + "/dev-idp",
    )
    app = oidc.create_local_oidc_app(environment="test", signing_key=key,
                                   allowed_callbacks=(ORIGIN + "/backoffice/callback",))
    start = await broker.begin_login()
    public = parse_qs(urlsplit(start.authorization_url).query)["transaction"][0]
    assert "code_verifier" not in oidc._verify(public, key.encode())
    assert "code_verifier" in oidc._verify(start.transaction_token, key.encode())
    form = {"transaction": public, "profile": "backoffice-admin", "access_token": token}
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
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
    with pytest.raises(OidcLoginRejected):
        await broker.complete_login(**{**args, "state": "invalid"})
    with pytest.raises(OidcLoginRejected):
        await broker.complete_login(**{**args, "transaction_token": public})
    context = await broker.complete_login(**args)
    assert context.subject == "local-backoffice-admin"
    with pytest.raises(OidcLoginRejected, match="already consumed"):
        await broker.complete_login(**args)
    start2 = await broker.begin_login()
    with pytest.raises(OidcLoginRejected):
        await broker.complete_login(**{**args, "transaction_token": start2.transaction_token})


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_local_idp_rejects_nonlocal_environment(environment):
    with pytest.raises(RuntimeError, match="LOCAL/TEST"):
        oidc.create_local_oidc_app(environment=environment, signing_key=secrets.token_urlsafe(48),
                                   allowed_callbacks=(ORIGIN + "/backoffice/callback",))


def test_local_verifier_rejects_untrusted_encoding():
    with pytest.raises(ValueError):
        oidc._verify("\u00e9.signature", secrets.token_bytes(32))
