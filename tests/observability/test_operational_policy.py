from __future__ import annotations

import unittest

from orqetia.observability import (
    METRIC_CONTRACTS,
    OBSERVABILITY_POLICY_VERSION,
    TelemetryEventClass,
    TelemetrySignal,
    default_observability_policy,
    should_sample_trace,
)


class OperationalObservabilityPolicyTests(unittest.TestCase):
    def test_environment_defaults_are_versioned_and_security_retention_is_longer(self) -> None:
        for environment in ("local", "test", "staging", "production"):
            with self.subTest(environment=environment):
                policy = default_observability_policy(environment)
                self.assertEqual(policy.version, OBSERVABILITY_POLICY_VERSION)
                self.assertEqual(policy.environment, environment)
                self.assertGreaterEqual(
                    policy.retention.days_for(
                        TelemetrySignal.LOGS,
                        event_class=TelemetryEventClass.SECURITY,
                    ),
                    policy.retention.days_for(TelemetrySignal.LOGS),
                )
                self.assertIn("correlation_id", policy.correlation_fields)
                self.assertIn("trace_id", policy.correlation_fields)

    def test_metric_contracts_have_explicit_bounded_cardinality_budgets(self) -> None:
        policy = default_observability_policy("production")
        forbidden = {
            "tenant_id",
            "client_id",
            "session_id",
            "task_id",
            "attempt_id",
            "work_id",
            "trace_id",
            "correlation_id",
            "payload",
            "prompt",
            "secret",
            "credential",
        }

        for metric in METRIC_CONTRACTS:
            with self.subTest(metric=metric.name):
                policy.cardinality.validate_contract(metric)
                self.assertFalse(forbidden.intersection(metric.labels))
                for label in metric.labels:
                    self.assertGreater(
                        policy.cardinality.distinct_values_per_label[label],
                        0,
                    )

    def test_volume_budgets_expose_soft_hard_and_saturation_thresholds(self) -> None:
        policy = default_observability_policy("production")
        for signal in TelemetrySignal:
            with self.subTest(signal=signal):
                soft = policy.volume.soft_events_per_minute[signal]
                hard = policy.volume.hard_events_per_minute[signal]
                alert = policy.volume.saturation_threshold(signal)
                self.assertLessEqual(soft, hard)
                self.assertLess(alert, hard)
                self.assertGreater(alert, 0)

    def test_trace_sampling_is_deterministic_for_correlation(self) -> None:
        trace_id = "trace-stable-across-all-spans"
        decisions = {
            should_sample_trace(trace_id, 0.25)
            for _ in range(100)
        }
        self.assertEqual(len(decisions), 1)
        self.assertFalse(should_sample_trace(trace_id, 0.0))
        self.assertTrue(should_sample_trace(trace_id, 1.0))

    def test_invalid_sampling_and_environment_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            should_sample_trace("", 0.5)
        with self.assertRaises(ValueError):
            should_sample_trace("trace", 1.1)
        with self.assertRaises(ValueError):
            default_observability_policy("unknown")


if __name__ == "__main__":
    unittest.main()
