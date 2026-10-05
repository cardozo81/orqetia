"""Machine-verifiable incremental provider-adapter parity plan."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AdapterTransportFamily(StrEnum):
    RESPONSES_HTTP = "RESPONSES_HTTP"
    CHAT_COMPLETIONS_HTTP = "CHAT_COMPLETIONS_HTTP"
    GEMINI_INTERACTIONS_HTTP = "GEMINI_INTERACTIONS_HTTP"
    ANTHROPIC_MESSAGES_HTTP = "ANTHROPIC_MESSAGES_HTTP"
    COHERE_CHAT_V2_HTTP = "COHERE_CHAT_V2_HTTP"
    COPILOT_SDK = "COPILOT_SDK"


class AdapterParityGate(StrEnum):
    SINGLE_ATTEMPT = "SINGLE_ATTEMPT"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    ERROR_NORMALIZATION = "ERROR_NORMALIZATION"
    USAGE_NORMALIZATION = "USAGE_NORMALIZATION"
    CAPABILITY_ALIGNMENT = "CAPABILITY_ALIGNMENT"
    NO_RETRY_ORCHESTRATION = "NO_RETRY_ORCHESTRATION"
    NO_SECRET_CAPTURE = "NO_SECRET_CAPTURE"
    OFFLINE_CONTRACT_TESTS = "OFFLINE_CONTRACT_TESTS"
    AMBIGUOUS_EFFECT_BOUNDARY = "AMBIGUOUS_EFFECT_BOUNDARY"
    NO_PAID_CI = "NO_PAID_CI"


MANDATORY_ADAPTER_PARITY_GATES = frozenset(AdapterParityGate)


@dataclass(frozen=True)
class ProviderPortPlan:
    """One future provider port; this is planning metadata, not an adapter."""

    order: int
    provider_id: str
    display_name: str
    transport_family: AdapterTransportFamily
    required_gates: frozenset[AdapterParityGate] = MANDATORY_ADAPTER_PARITY_GATES

    def __post_init__(self) -> None:
        if self.order < 1:
            raise ValueError("order must be positive")
        if not self.provider_id.strip() or len(self.provider_id) > 100:
            raise ValueError("provider_id must contain 1..100 characters")
        if not self.display_name.strip() or len(self.display_name) > 200:
            raise ValueError("display_name must contain 1..200 characters")
        if not MANDATORY_ADAPTER_PARITY_GATES <= self.required_gates:
            raise ValueError("provider port plan is missing mandatory parity gates")


CANONICAL_PROVIDER_PORT_PLAN = (
    ProviderPortPlan(1, "openai", "OpenAI", AdapterTransportFamily.RESPONSES_HTTP),
    ProviderPortPlan(2, "deepseek", "DeepSeek", AdapterTransportFamily.RESPONSES_HTTP),
    ProviderPortPlan(3, "mimo", "Xiaomi MiMo", AdapterTransportFamily.RESPONSES_HTTP),
    ProviderPortPlan(4, "xai", "xAI / Grok", AdapterTransportFamily.RESPONSES_HTTP),
    ProviderPortPlan(
        5,
        "qwen",
        "Alibaba Qwen",
        AdapterTransportFamily.CHAT_COMPLETIONS_HTTP,
    ),
    ProviderPortPlan(
        6,
        "mistral",
        "Mistral",
        AdapterTransportFamily.CHAT_COMPLETIONS_HTTP,
    ),
    ProviderPortPlan(
        7,
        "kimi",
        "Kimi / Moonshot",
        AdapterTransportFamily.CHAT_COMPLETIONS_HTTP,
    ),
    ProviderPortPlan(
        8,
        "gemini",
        "Gemini",
        AdapterTransportFamily.GEMINI_INTERACTIONS_HTTP,
    ),
    ProviderPortPlan(
        9,
        "anthropic",
        "Anthropic",
        AdapterTransportFamily.ANTHROPIC_MESSAGES_HTTP,
    ),
    ProviderPortPlan(
        10,
        "cohere",
        "Cohere",
        AdapterTransportFamily.COHERE_CHAT_V2_HTTP,
    ),
    ProviderPortPlan(
        11,
        "copilot",
        "GitHub Copilot",
        AdapterTransportFamily.COPILOT_SDK,
    ),
)


def provider_port_plan(provider_id: str) -> ProviderPortPlan:
    """Return the canonical future port plan for one provider."""

    normalized = provider_id.strip().lower()
    for plan in CANONICAL_PROVIDER_PORT_PLAN:
        if plan.provider_id == normalized:
            return plan
    raise KeyError(f"unknown provider port plan: {provider_id}")
