from __future__ import annotations

import unittest
from dataclasses import asdict, dataclass

from orqetia.providers import (
    AdapterResolution,
    INITIAL_PROVIDER_TAXONOMY,
    ProviderCapability,
    ProviderModelSpec,
    ProviderRegistry,
    ProviderRegistryError,
    ProviderRegistryReader,
    ProviderSpec,
    ProviderTarget,
    ReasoningProfileSpec,
    RegistryEligibilityMode,
    RegistryFailureCode,
)


@dataclass(frozen=True)
class RequestedTarget:
    provider_id: str
    model_id: str | None = None
    reasoning_profile: str | None = None


class ProviderRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.standard = ReasoningProfileSpec("standard")
        self.deep = ReasoningProfileSpec("deep", auto_eligible=False)
        self.unapproved_profile = ReasoningProfileSpec("experimental", approved=False)
        self.alpha_model = ProviderModelSpec(
            model_id="alpha-1",
            adapter=AdapterResolution("alpha.adapter"),
            offered_capabilities=frozenset(
                {
                    ProviderCapability.STRUCTURED_OUTPUT,
                    ProviderCapability.REASONING,
                    ProviderCapability.MULTIMODAL,
                    ProviderCapability.TOOLS,
                }
            ),
            approved_capabilities=frozenset(
                {
                    ProviderCapability.STRUCTURED_OUTPUT,
                    ProviderCapability.REASONING,
                    ProviderCapability.TOOLS,
                }
            ),
            reasoning_profiles=(self.standard, self.deep, self.unapproved_profile),
            default_reasoning_profile="standard",
        )
        self.explicit_only_model = ProviderModelSpec(
            model_id="alpha-explicit",
            adapter=AdapterResolution("alpha.explicit.adapter"),
            offered_capabilities=frozenset({ProviderCapability.REASONING}),
            approved_capabilities=frozenset({ProviderCapability.REASONING}),
            reasoning_profiles=(ReasoningProfileSpec("standard"),),
            default_reasoning_profile="standard",
            auto_eligible=False,
        )
        self.unapproved_model = ProviderModelSpec(
            model_id="alpha-preview",
            adapter=AdapterResolution("alpha.preview.adapter"),
            offered_capabilities=frozenset({ProviderCapability.MULTIMODAL}),
            approved_capabilities=frozenset(),
            reasoning_profiles=(ReasoningProfileSpec("standard"),),
            default_reasoning_profile="standard",
            approved=False,
        )
        self.alpha = ProviderSpec(
            provider_id="alpha",
            display_name="Alpha Provider",
            models=(self.unapproved_model, self.explicit_only_model, self.alpha_model),
            default_model_id="alpha-1",
        )
        self.beta = ProviderSpec(
            provider_id="beta",
            display_name="Beta Provider",
            models=(
                ProviderModelSpec(
                    model_id="beta-1",
                    adapter=AdapterResolution("beta.adapter"),
                    offered_capabilities=frozenset({ProviderCapability.STRUCTURED_OUTPUT}),
                    approved_capabilities=frozenset({ProviderCapability.STRUCTURED_OUTPUT}),
                    reasoning_profiles=(ReasoningProfileSpec("standard"),),
                    default_reasoning_profile="standard",
                ),
            ),
            default_model_id="beta-1",
        )
        self.inventory_only = ProviderSpec(
            provider_id="inventory-only",
            display_name="Inventory Only",
            models=(),
            approved=False,
        )
        self.registry = ProviderRegistry((self.beta, self.inventory_only, self.alpha))
        self.alpha_standard = ProviderTarget("alpha", "alpha-1", "standard")
        self.alpha_deep = ProviderTarget("alpha", "alpha-1", "deep")
        self.alpha_explicit = ProviderTarget("alpha", "alpha-explicit", "standard")
        self.beta_standard = ProviderTarget("beta", "beta-1", "standard")
        self.authorized = (
            self.beta_standard,
            self.alpha_deep,
            self.alpha_standard,
            self.alpha_explicit,
        )

    def test_resolves_known_approved_provider_model_profile(self) -> None:
        result = self.registry.resolve_explicit_target(
            requested=RequestedTarget("alpha", "alpha-1", "deep"),
            required_capabilities=(ProviderCapability.REASONING,),
            authorized_targets=self.authorized,
        )

        self.assertEqual(result.target, self.alpha_deep)
        self.assertEqual(result.adapter.adapter_key, "alpha.adapter")

    def test_unknown_provider_fails_closed(self) -> None:
        self._assert_resolution_error(
            RegistryFailureCode.UNKNOWN_PROVIDER,
            RequestedTarget("missing", "model", "standard"),
        )

    def test_unapproved_provider_fails_closed(self) -> None:
        self._assert_resolution_error(
            RegistryFailureCode.PROVIDER_NOT_APPROVED,
            RequestedTarget("inventory-only", "model", "standard"),
        )

    def test_unknown_model_fails_closed_without_default_fallback(self) -> None:
        self._assert_resolution_error(
            RegistryFailureCode.UNKNOWN_MODEL,
            RequestedTarget("alpha", "missing", "standard"),
        )

    def test_unapproved_model_fails_closed(self) -> None:
        self._assert_resolution_error(
            RegistryFailureCode.MODEL_NOT_APPROVED,
            RequestedTarget("alpha", "alpha-preview", "standard"),
        )

    def test_unknown_profile_fails_closed_without_default_fallback(self) -> None:
        self._assert_resolution_error(
            RegistryFailureCode.UNKNOWN_PROFILE,
            RequestedTarget("alpha", "alpha-1", "missing"),
        )

    def test_unapproved_profile_fails_closed(self) -> None:
        self._assert_resolution_error(
            RegistryFailureCode.PROFILE_NOT_APPROVED,
            RequestedTarget("alpha", "alpha-1", "experimental"),
        )

    def test_capability_not_offered_fails_closed(self) -> None:
        with self.assertRaises(ProviderRegistryError) as caught:
            self.registry.resolve_explicit_target(
                requested=RequestedTarget("alpha", "alpha-1", "standard"),
                required_capabilities=(ProviderCapability.ASYNCHRONOUS,),
                authorized_targets=self.authorized,
            )
        self.assertEqual(caught.exception.code, RegistryFailureCode.CAPABILITY_NOT_OFFERED)

    def test_offered_but_not_approved_capability_remains_ineligible(self) -> None:
        with self.assertRaises(ProviderRegistryError) as caught:
            self.registry.resolve_explicit_target(
                requested=RequestedTarget("alpha", "alpha-1", "standard"),
                required_capabilities=(ProviderCapability.MULTIMODAL,),
                authorized_targets=self.authorized,
            )
        self.assertEqual(caught.exception.code, RegistryFailureCode.CAPABILITY_NOT_APPROVED)

    def test_defaults_resolve_only_approved_model_and_profile(self) -> None:
        result = self.registry.resolve_explicit_target(
            requested=RequestedTarget("alpha"),
            required_capabilities=(ProviderCapability.STRUCTURED_OUTPUT,),
            authorized_targets=self.authorized,
        )
        self.assertEqual(result.target, self.alpha_standard)

    def test_invalid_default_model_is_rejected_at_registry_construction_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "default model"):
            ProviderSpec(
                provider_id="bad",
                display_name="Bad",
                models=(self.unapproved_model,),
                default_model_id="alpha-preview",
            )

    def test_invalid_default_profile_is_rejected_at_model_construction_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "default reasoning profile"):
            ProviderModelSpec(
                model_id="bad",
                adapter=AdapterResolution("bad.adapter"),
                offered_capabilities=frozenset(),
                approved_capabilities=frozenset(),
                reasoning_profiles=(ReasoningProfileSpec("preview", approved=False),),
                default_reasoning_profile="preview",
            )

    def test_auto_eligibility_is_deterministic_and_contains_no_pricing_input(self) -> None:
        first = self.registry.eligible_targets(
            mode=RegistryEligibilityMode.AUTO,
            required_capabilities=(ProviderCapability.STRUCTURED_OUTPUT,),
            authorized_targets=reversed(self.authorized),
        )
        second = self.registry.eligible_targets(
            mode=RegistryEligibilityMode.AUTO,
            required_capabilities=(ProviderCapability.STRUCTURED_OUTPUT,),
            authorized_targets=self.authorized,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            tuple(item.target for item in first),
            (self.alpha_standard, self.beta_standard),
        )

    def test_auto_excludes_explicit_only_model_and_profile(self) -> None:
        eligible = self.registry.eligible_targets(
            mode=RegistryEligibilityMode.AUTO,
            required_capabilities=(ProviderCapability.REASONING,),
            authorized_targets=self.authorized,
        )
        self.assertEqual(tuple(item.target for item in eligible), (self.alpha_standard,))

    def test_explicit_target_preserves_exact_requested_target(self) -> None:
        result = self.registry.resolve_explicit_target(
            requested=RequestedTarget("alpha", "alpha-explicit", "standard"),
            required_capabilities=(ProviderCapability.REASONING,),
            authorized_targets=self.authorized,
        )
        self.assertEqual(result.target, self.alpha_explicit)

    def test_explicit_target_never_silently_falls_back_to_authorized_default(self) -> None:
        with self.assertRaises(ProviderRegistryError) as caught:
            self.registry.resolve_explicit_target(
                requested=RequestedTarget("alpha", "missing", "standard"),
                required_capabilities=(),
                authorized_targets=(self.alpha_standard,),
            )
        self.assertEqual(caught.exception.code, RegistryFailureCode.UNKNOWN_MODEL)

    def test_explicit_target_requires_existing_authorization_snapshot(self) -> None:
        with self.assertRaises(ProviderRegistryError) as caught:
            self.registry.resolve_explicit_target(
                requested=RequestedTarget("alpha", "alpha-1", "deep"),
                required_capabilities=(ProviderCapability.REASONING,),
                authorized_targets=(self.alpha_standard,),
            )
        self.assertEqual(caught.exception.code, RegistryFailureCode.TARGET_NOT_AUTHORIZED)

    def test_consumer_can_depend_on_registry_contract_not_adapter_class(self) -> None:
        self.assertIsInstance(self.registry, ProviderRegistryReader)
        resolved = self.registry.adapter_for(self.alpha_standard)
        self.assertEqual(resolved, AdapterResolution("alpha.adapter"))
        self.assertIsInstance(resolved.adapter_key, str)

    def test_public_metadata_is_authorization_scoped_and_has_no_sensitive_fields(self) -> None:
        metadata = self.registry.public_metadata(authorized_targets=(self.alpha_standard,))
        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0].provider_id, "alpha")
        self.assertEqual(metadata[0].model_id, "alpha-1")

        serialized = asdict(metadata[0])
        forbidden_fragments = ("secret", "credential", "account", "balance", "cost", "price")
        self.assertFalse(
            any(fragment in key.lower() for key in serialized for fragment in forbidden_fragments)
        )
        self.assertNotIn("beta", repr(metadata))
        self.assertNotIn("inventory-only", repr(metadata))

    def test_initial_taxonomy_is_inventory_only_and_contains_expected_names(self) -> None:
        self.assertIn("OpenAI", INITIAL_PROVIDER_TAXONOMY)
        self.assertIn("GitHub Copilot", INITIAL_PROVIDER_TAXONOMY)
        self.assertNotIn("Perplexity", INITIAL_PROVIDER_TAXONOMY)
        self.assertNotIn("Manus", INITIAL_PROVIDER_TAXONOMY)

    def test_approved_capabilities_must_be_subset_of_offered_capabilities(self) -> None:
        with self.assertRaisesRegex(ValueError, "subset"):
            ProviderModelSpec(
                model_id="invalid",
                adapter=AdapterResolution("invalid.adapter"),
                offered_capabilities=frozenset(),
                approved_capabilities=frozenset({ProviderCapability.REASONING}),
                reasoning_profiles=(ReasoningProfileSpec("standard"),),
            )

    def _assert_resolution_error(
        self,
        code: RegistryFailureCode,
        requested: RequestedTarget,
    ) -> None:
        with self.assertRaises(ProviderRegistryError) as caught:
            self.registry.resolve_explicit_target(
                requested=requested,
                required_capabilities=(),
                authorized_targets=self.authorized,
            )
        self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
