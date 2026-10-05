"""Provider-neutral registry and capability resolution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Protocol, runtime_checkable

from .protocol import ProviderTarget

INITIAL_PROVIDER_TAXONOMY = (
    "OpenAI",
    "DeepSeek",
    "Xiaomi MiMo",
    "xAI / Grok",
    "Alibaba Qwen",
    "Gemini",
    "Anthropic",
    "Mistral",
    "Cohere",
    "Kimi / Moonshot",
    "GitHub Copilot",
)


class ProviderCapability(StrEnum):
    STRUCTURED_OUTPUT = "structured_output"
    REASONING = "reasoning"
    MULTIMODAL = "multimodal"
    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"
    AGENTIC = "agentic"
    USAGE_REPORTING = "usage_reporting"
    TOKEN_PRICING = "token_pricing"
    REQUEST_PRICING = "request_pricing"
    CREDIT_USAGE = "credit_usage"
    TOOLS = "tools"
    REGION = "region"
    ENDPOINT = "endpoint"
    HEALTH = "health"


class RegistryEligibilityMode(StrEnum):
    AUTO = "AUTO"
    EXPLICIT_TARGET = "EXPLICIT_TARGET"


class RegistryFailureCode(StrEnum):
    UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
    PROVIDER_NOT_APPROVED = "PROVIDER_NOT_APPROVED"
    PROVIDER_NOT_ELIGIBLE = "PROVIDER_NOT_ELIGIBLE"
    UNKNOWN_MODEL = "UNKNOWN_MODEL"
    MODEL_NOT_APPROVED = "MODEL_NOT_APPROVED"
    MODEL_NOT_ELIGIBLE = "MODEL_NOT_ELIGIBLE"
    DEFAULT_MODEL_UNAVAILABLE = "DEFAULT_MODEL_UNAVAILABLE"
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"
    PROFILE_NOT_APPROVED = "PROFILE_NOT_APPROVED"
    PROFILE_NOT_ELIGIBLE = "PROFILE_NOT_ELIGIBLE"
    DEFAULT_PROFILE_UNAVAILABLE = "DEFAULT_PROFILE_UNAVAILABLE"
    CAPABILITY_NOT_OFFERED = "CAPABILITY_NOT_OFFERED"
    CAPABILITY_NOT_APPROVED = "CAPABILITY_NOT_APPROVED"
    TARGET_NOT_AUTHORIZED = "TARGET_NOT_AUTHORIZED"


class ProviderRegistryError(ValueError):
    def __init__(self, code: RegistryFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class TargetIdentity(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def reasoning_profile(self) -> str: ...


@runtime_checkable
class RequestedTargetIdentity(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str | None: ...

    @property
    def reasoning_profile(self) -> str | None: ...


@dataclass(frozen=True)
class AdapterResolution:
    adapter_key: str
    protocol_version: str = "v1"

    def __post_init__(self) -> None:
        if not self.adapter_key.strip() or len(self.adapter_key) > 200:
            raise ValueError("adapter_key must contain 1..200 characters")
        if not self.protocol_version.strip() or len(self.protocol_version) > 50:
            raise ValueError("protocol_version must contain 1..50 characters")


@dataclass(frozen=True)
class ReasoningProfileSpec:
    profile_id: str
    approved: bool = True
    auto_eligible: bool = True
    explicit_eligible: bool = True

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or len(self.profile_id) > 100:
            raise ValueError("profile_id must contain 1..100 characters")


@dataclass(frozen=True)
class ProviderModelSpec:
    model_id: str
    adapter: AdapterResolution
    offered_capabilities: frozenset[ProviderCapability]
    approved_capabilities: frozenset[ProviderCapability]
    reasoning_profiles: tuple[ReasoningProfileSpec, ...]
    default_reasoning_profile: str | None = None
    approved: bool = True
    auto_eligible: bool = True
    explicit_eligible: bool = True

    def __post_init__(self) -> None:
        if not self.model_id.strip() or len(self.model_id) > 200:
            raise ValueError("model_id must contain 1..200 characters")
        if not self.approved_capabilities <= self.offered_capabilities:
            raise ValueError("approved capabilities must be a subset of offered capabilities")
        profile_ids = [profile.profile_id for profile in self.reasoning_profiles]
        if len(set(profile_ids)) != len(profile_ids):
            raise ValueError("reasoning_profiles must not contain duplicate profile_id values")
        if self.default_reasoning_profile is not None:
            profile = self.profile(self.default_reasoning_profile)
            if profile is None or not profile.approved:
                raise ValueError("default reasoning profile must refer to an approved profile")

    def profile(self, profile_id: str) -> ReasoningProfileSpec | None:
        return next(
            (profile for profile in self.reasoning_profiles if profile.profile_id == profile_id),
            None,
        )


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    display_name: str
    models: tuple[ProviderModelSpec, ...]
    default_model_id: str | None = None
    approved: bool = True
    auto_eligible: bool = True
    explicit_eligible: bool = True

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or len(self.provider_id) > 100:
            raise ValueError("provider_id must contain 1..100 characters")
        if not self.display_name.strip() or len(self.display_name) > 200:
            raise ValueError("display_name must contain 1..200 characters")
        model_ids = [model.model_id for model in self.models]
        if len(set(model_ids)) != len(model_ids):
            raise ValueError("models must not contain duplicate model_id values")
        if self.default_model_id is not None:
            model = self.model(self.default_model_id)
            if model is None or not model.approved:
                raise ValueError("default model must refer to an approved model")

    def model(self, model_id: str) -> ProviderModelSpec | None:
        return next((model for model in self.models if model.model_id == model_id), None)


@dataclass(frozen=True)
class ResolvedProviderTarget:
    target: ProviderTarget
    approved_capabilities: frozenset[ProviderCapability]
    adapter: AdapterResolution


@dataclass(frozen=True)
class PublicProviderTargetMetadata:
    provider_id: str
    display_name: str
    model_id: str
    reasoning_profile: str
    capabilities: tuple[ProviderCapability, ...]


@runtime_checkable
class ProviderRegistryReader(Protocol):
    def resolve_explicit_target(
        self,
        *,
        requested: RequestedTargetIdentity,
        required_capabilities: Iterable[ProviderCapability],
        authorized_targets: Iterable[TargetIdentity],
    ) -> ResolvedProviderTarget:
        """Resolve one exact authorized target, applying approved defaults only when omitted."""
        ...

    def eligible_targets(
        self,
        *,
        mode: RegistryEligibilityMode,
        required_capabilities: Iterable[ProviderCapability],
        authorized_targets: Iterable[TargetIdentity],
    ) -> tuple[ResolvedProviderTarget, ...]:
        """Return deterministic registry-eligible targets within an authorized set."""
        ...

    def adapter_for(self, target: TargetIdentity) -> AdapterResolution:
        """Resolve adapter metadata without instantiating an adapter."""
        ...

    def public_metadata(
        self,
        *,
        authorized_targets: Iterable[TargetIdentity],
    ) -> tuple[PublicProviderTargetMetadata, ...]:
        """Return sanitized metadata only for already-authorized targets."""
        ...


class ProviderRegistry(ProviderRegistryReader):
    """Immutable provider/capability registry with fail-closed target resolution."""

    def __init__(self, providers: Iterable[ProviderSpec]) -> None:
        ordered = tuple(sorted(providers, key=lambda provider: provider.provider_id))
        provider_ids = [provider.provider_id for provider in ordered]
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("providers must not contain duplicate provider_id values")
        self._providers = ordered

    @property
    def providers(self) -> tuple[ProviderSpec, ...]:
        return self._providers

    def resolve_explicit_target(
        self,
        *,
        requested: RequestedTargetIdentity,
        required_capabilities: Iterable[ProviderCapability],
        authorized_targets: Iterable[TargetIdentity],
    ) -> ResolvedProviderTarget:
        provider = self._approved_provider(
            requested.provider_id,
            RegistryEligibilityMode.EXPLICIT_TARGET,
        )
        model_id = requested.model_id
        if model_id is None:
            model_id = provider.default_model_id
            if model_id is None:
                raise ProviderRegistryError(
                    RegistryFailureCode.DEFAULT_MODEL_UNAVAILABLE,
                    f"provider {provider.provider_id} has no approved default model",
                )
        model = self._approved_model(provider, model_id, RegistryEligibilityMode.EXPLICIT_TARGET)

        profile_id = requested.reasoning_profile
        if profile_id is None:
            profile_id = model.default_reasoning_profile
            if profile_id is None:
                raise ProviderRegistryError(
                    RegistryFailureCode.DEFAULT_PROFILE_UNAVAILABLE,
                    (
                        f"model {provider.provider_id}/{model.model_id} "
                        "has no approved default profile"
                    ),
                )
        profile = self._approved_profile(model, profile_id, RegistryEligibilityMode.EXPLICIT_TARGET)
        required = frozenset(required_capabilities)
        self._require_capabilities(model, required)

        target = ProviderTarget(provider.provider_id, model.model_id, profile.profile_id)
        if self._target_key(target) not in self._authorized_keys(authorized_targets):
            raise ProviderRegistryError(
                RegistryFailureCode.TARGET_NOT_AUTHORIZED,
                "resolved explicit target is outside the already-authorized target set",
            )
        return ResolvedProviderTarget(target, model.approved_capabilities, model.adapter)

    def eligible_targets(
        self,
        *,
        mode: RegistryEligibilityMode,
        required_capabilities: Iterable[ProviderCapability],
        authorized_targets: Iterable[TargetIdentity],
    ) -> tuple[ResolvedProviderTarget, ...]:
        required = frozenset(required_capabilities)
        authorized = self._authorized_keys(authorized_targets)
        resolved: list[ResolvedProviderTarget] = []

        for provider in self._providers:
            if not self._provider_is_eligible(provider, mode):
                continue
            for model in sorted(provider.models, key=lambda item: item.model_id):
                if not self._model_is_eligible(model, mode):
                    continue
                if not required <= model.approved_capabilities:
                    continue
                for profile in sorted(model.reasoning_profiles, key=lambda item: item.profile_id):
                    if not self._profile_is_eligible(profile, mode):
                        continue
                    target = ProviderTarget(
                        provider.provider_id, model.model_id, profile.profile_id
                    )
                    if self._target_key(target) not in authorized:
                        continue
                    resolved.append(
                        ResolvedProviderTarget(target, model.approved_capabilities, model.adapter)
                    )

        return tuple(resolved)

    def adapter_for(self, target: TargetIdentity) -> AdapterResolution:
        provider = self._approved_provider(target.provider_id, None)
        model = self._approved_model(provider, target.model_id, None)
        self._approved_profile(model, target.reasoning_profile, None)
        return model.adapter

    def public_metadata(
        self,
        *,
        authorized_targets: Iterable[TargetIdentity],
    ) -> tuple[PublicProviderTargetMetadata, ...]:
        authorized = self._authorized_keys(authorized_targets)
        public: list[PublicProviderTargetMetadata] = []
        for provider in self._providers:
            if not provider.approved:
                continue
            for model in sorted(provider.models, key=lambda item: item.model_id):
                if not model.approved:
                    continue
                for profile in sorted(model.reasoning_profiles, key=lambda item: item.profile_id):
                    if not profile.approved:
                        continue
                    target = ProviderTarget(
                        provider.provider_id, model.model_id, profile.profile_id
                    )
                    if self._target_key(target) not in authorized:
                        continue
                    public.append(
                        PublicProviderTargetMetadata(
                            provider_id=provider.provider_id,
                            display_name=provider.display_name,
                            model_id=model.model_id,
                            reasoning_profile=profile.profile_id,
                            capabilities=tuple(sorted(model.approved_capabilities, key=str)),
                        )
                    )
        return tuple(public)

    def _approved_provider(
        self,
        provider_id: str,
        mode: RegistryEligibilityMode | None,
    ) -> ProviderSpec:
        provider = next(
            (candidate for candidate in self._providers if candidate.provider_id == provider_id),
            None,
        )
        if provider is None:
            raise ProviderRegistryError(
                RegistryFailureCode.UNKNOWN_PROVIDER,
                f"unknown provider: {provider_id}",
            )
        if not provider.approved:
            raise ProviderRegistryError(
                RegistryFailureCode.PROVIDER_NOT_APPROVED,
                f"provider is not approved: {provider_id}",
            )
        if mode is not None and not self._provider_is_eligible(provider, mode):
            raise ProviderRegistryError(
                RegistryFailureCode.PROVIDER_NOT_ELIGIBLE,
                f"provider is not eligible for {mode.value}: {provider_id}",
            )
        return provider

    @staticmethod
    def _approved_model(
        provider: ProviderSpec,
        model_id: str,
        mode: RegistryEligibilityMode | None,
    ) -> ProviderModelSpec:
        model = provider.model(model_id)
        if model is None:
            raise ProviderRegistryError(
                RegistryFailureCode.UNKNOWN_MODEL,
                f"unknown model for {provider.provider_id}: {model_id}",
            )
        if not model.approved:
            raise ProviderRegistryError(
                RegistryFailureCode.MODEL_NOT_APPROVED,
                f"model is not approved: {provider.provider_id}/{model_id}",
            )
        if mode is not None and not ProviderRegistry._model_is_eligible(model, mode):
            raise ProviderRegistryError(
                RegistryFailureCode.MODEL_NOT_ELIGIBLE,
                f"model is not eligible for {mode.value}: {provider.provider_id}/{model_id}",
            )
        return model

    @staticmethod
    def _approved_profile(
        model: ProviderModelSpec,
        profile_id: str,
        mode: RegistryEligibilityMode | None,
    ) -> ReasoningProfileSpec:
        profile = model.profile(profile_id)
        if profile is None:
            raise ProviderRegistryError(
                RegistryFailureCode.UNKNOWN_PROFILE,
                f"unknown reasoning profile for {model.model_id}: {profile_id}",
            )
        if not profile.approved:
            raise ProviderRegistryError(
                RegistryFailureCode.PROFILE_NOT_APPROVED,
                f"reasoning profile is not approved: {model.model_id}/{profile_id}",
            )
        if mode is not None and not ProviderRegistry._profile_is_eligible(profile, mode):
            raise ProviderRegistryError(
                RegistryFailureCode.PROFILE_NOT_ELIGIBLE,
                (
                    f"reasoning profile is not eligible for {mode.value}: "
                    f"{model.model_id}/{profile_id}"
                ),
            )
        return profile

    @staticmethod
    def _require_capabilities(
        model: ProviderModelSpec,
        required: frozenset[ProviderCapability],
    ) -> None:
        not_offered = required - model.offered_capabilities
        if not_offered:
            names = ", ".join(sorted(capability.value for capability in not_offered))
            raise ProviderRegistryError(
                RegistryFailureCode.CAPABILITY_NOT_OFFERED,
                f"required capabilities are not offered by model {model.model_id}: {names}",
            )
        not_approved = required - model.approved_capabilities
        if not_approved:
            names = ", ".join(sorted(capability.value for capability in not_approved))
            raise ProviderRegistryError(
                RegistryFailureCode.CAPABILITY_NOT_APPROVED,
                f"required capabilities are not approved for model {model.model_id}: {names}",
            )

    @staticmethod
    def _provider_is_eligible(provider: ProviderSpec, mode: RegistryEligibilityMode) -> bool:
        mode_eligible = (
            provider.auto_eligible
            if mode is RegistryEligibilityMode.AUTO
            else provider.explicit_eligible
        )
        return provider.approved and mode_eligible

    @staticmethod
    def _model_is_eligible(model: ProviderModelSpec, mode: RegistryEligibilityMode) -> bool:
        mode_eligible = (
            model.auto_eligible
            if mode is RegistryEligibilityMode.AUTO
            else model.explicit_eligible
        )
        return model.approved and mode_eligible

    @staticmethod
    def _profile_is_eligible(profile: ReasoningProfileSpec, mode: RegistryEligibilityMode) -> bool:
        mode_eligible = (
            profile.auto_eligible
            if mode is RegistryEligibilityMode.AUTO
            else profile.explicit_eligible
        )
        return profile.approved and mode_eligible

    @staticmethod
    def _target_key(target: TargetIdentity) -> tuple[str, str, str]:
        return (target.provider_id, target.model_id, target.reasoning_profile)

    @classmethod
    def _authorized_keys(
        cls,
        targets: Iterable[TargetIdentity],
    ) -> frozenset[tuple[str, str, str]]:
        return frozenset(cls._target_key(target) for target in targets)
