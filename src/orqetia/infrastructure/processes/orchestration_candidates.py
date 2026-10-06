"""Control-plane candidate resolution for durable task orchestration."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from orqetia.control_plane import (
    ProviderCatalogRepository,
    ProviderPricingCatalogRepository,
)
from orqetia.execution import (
    ExecutionMode,
    ExecutionSession,
    ExecutionTargetSnapshot,
    ExecutionTask,
    OrchestrationCandidate,
)
from orqetia.providers import RegistryEligibilityMode
from orqetia.usage_accounting import TechnicalUsage


class ControlPlaneOrchestrationCandidateResolver:
    """Resolve current catalog eligibility and conservative comparable pricing facts."""

    def __init__(
        self,
        *,
        catalogs: ProviderCatalogRepository,
        pricing: ProviderPricingCatalogRepository,
    ) -> None:
        self._catalogs = catalogs
        self._pricing = pricing

    async def resolve(
        self,
        *,
        task: ExecutionTask,
        session: ExecutionSession,
        occurred_at: datetime,
    ) -> tuple[OrchestrationCandidate, ...]:
        catalog = await self._catalogs.get_effective()
        if catalog is None:
            return ()
        pricing = await self._pricing.get_effective()

        mode = (
            RegistryEligibilityMode.AUTO
            if task.requested_execution_mode is ExecutionMode.AUTO
            else RegistryEligibilityMode.EXPLICIT_TARGET
        )
        resolved = catalog.registry.eligible_targets(
            mode=mode,
            required_capabilities=(),
            authorized_targets=session.policy.authorized_targets,
        )
        policy_rank = {
            target: index
            for index, target in enumerate(session.policy.authorized_targets)
        }

        raw: list[
            tuple[
                ExecutionTargetSnapshot,
                str,
                Decimal | None,
                str | None,
                str | None,
                int,
            ]
        ] = []
        for item in resolved:
            target = ExecutionTargetSnapshot(
                provider_id=item.target.provider_id,
                model_id=item.target.model_id,
                reasoning_profile=item.target.reasoning_profile,
            )
            if pricing is None:
                group = "UNPRICED"
                amount = None
                currency = None
                pricing_reference = None
            else:
                quote = pricing.resolver.quote(
                    provider_id=target.provider_id,
                    model_id=target.model_id,
                    reasoning_profile=target.reasoning_profile,
                    usage=TechnicalUsage(request_units=Decimal("1")),
                    occurred_at=occurred_at,
                )
                group = quote.comparison_group
                amount = quote.cost.amount
                currency = quote.cost.currency
                pricing_reference = quote.cost.pricing_reference
            raw.append(
                (
                    target,
                    group,
                    amount,
                    currency,
                    pricing_reference,
                    policy_rank[target],
                )
            )

        group_rank: dict[str, int] = {}
        for _target, group, _amount, _currency, _reference, rank in raw:
            if group == "UNPRICED":
                continue
            current = group_rank.get(group)
            group_rank[group] = rank if current is None else min(current, rank)
        unpriced_rank = len(session.policy.authorized_targets) + len(group_rank)

        return tuple(
            OrchestrationCandidate(
                target=target,
                comparison_group=group,
                comparison_group_rank=(
                    unpriced_rank if group == "UNPRICED" else group_rank[group]
                ),
                estimated_cost=amount,
                currency=currency,
                pricing_reference=reference,
                policy_rank=rank,
            )
            for target, group, amount, currency, reference, rank in raw
        )
