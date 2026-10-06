from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from orqetia.estimation import (
    EstimateResult,
    EstimateSubject,
    EstimateTarget,
    EstimatedTechnicalUsage,
)
from orqetia.identity.authentication import AuthenticatedPrincipal
from orqetia.infrastructure.http import create_app

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = json.loads(
    (ROOT / "contracts" / "openapi" / "orqetia-v1.openapi.json").read_text(
        encoding="utf-8"
    )
)


class FakeAuthenticator:
    def __init__(self, scopes: frozenset[str]) -> None:
        self.scopes = scopes

    async def authenticate_bearer(self, _token: str) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            subject_type="SERVICE_CLIENT",
            subject_id="subject-1",
            tenant_id="tenant-1",
            client_id="client-1",
            scopes=self.scopes,
        )


class FakeEstimationService:
    async def estimate(self, *, subject, spec):
        assert subject == EstimateSubject("tenant-1", "client-1")
        target = spec.target or EstimateTarget("openai", "gpt-auto", "medium")
        return EstimateResult(
            usage=EstimatedTechnicalUsage(
                input_tokens=12,
                cached_input_tokens=2,
                output_tokens=30,
                reasoning_tokens=5,
                total_tokens=42,
            ),
            requested_execution_mode=spec.execution_mode,
            effective_execution_mode=spec.execution_mode,
            effective_target=target,
            reference_scope=spec.reference_scope,
            estimation_method="SYNTHETIC",
            methodology_version="v1",
            benchmark_version="b1",
            as_of=datetime(2026, 10, 5, 20, tzinfo=UTC),
            sample_size=40,
            cohort_size=5,
            confidence="MEDIUM",
            fallback_available=None,
            limitations=(),
        )


async def _post(app, body):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/v1/estimates",
            headers={"Authorization": "Bearer synthetic"},
            json=body,
        )


def test_auto_estimate_returns_technical_fields_only() -> None:
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=FakeAuthenticator(frozenset({"estimates:write"})),
        estimation_service=FakeEstimationService(),
    )
    response = asyncio.run(
        _post(
            app,
            {
                "operation": "TASK_EXECUTION",
                "input": {"message": "hello"},
                "reference_scope": "GLOBAL_PUBLIC",
            },
        )
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_execution_mode"] == "AUTO"
    assert payload["effective_target"]["provider_id"] == "openai"
    forbidden = ("cost", "currency", "price", "charge", "credit")
    assert not any(token in response.text.lower() for token in forbidden)


def test_explicit_estimate_requires_target_scope() -> None:
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=FakeAuthenticator(frozenset({"estimates:write"})),
        estimation_service=FakeEstimationService(),
    )
    response = asyncio.run(
        _post(
            app,
            {
                "operation": "TASK_EXECUTION",
                "input": {},
                "execution": {
                    "mode": "EXPLICIT_TARGET",
                    "target": {
                        "provider_id": "anthropic",
                        "model_id": "claude",
                        "reasoning_profile": "standard",
                    },
                },
            },
        )
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_explicit_estimate_preserves_requested_target() -> None:
    app = create_app(
        openapi_document=CANONICAL,
        authenticator=FakeAuthenticator(
            frozenset({"estimates:write", "tasks:target"})
        ),
        estimation_service=FakeEstimationService(),
    )
    response = asyncio.run(
        _post(
            app,
            {
                "operation": "TASK_EXECUTION",
                "input": {},
                "execution": {
                    "mode": "EXPLICIT_TARGET",
                    "target": {
                        "provider_id": "anthropic",
                        "model_id": "claude",
                        "reasoning_profile": "standard",
                    },
                },
            },
        )
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_execution_mode"] == "EXPLICIT_TARGET"
    assert payload["effective_target"]["provider_id"] == "anthropic"
