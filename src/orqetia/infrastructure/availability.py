"""Versioned availability, maintenance and controlled-degradation policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

AVAILABILITY_POLICY_VERSION = "availability-v1"


class AvailabilityState(StrEnum):
    HEALTHY = "HEALTHY"
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class DependencyKind(StrEnum):
    DATABASE = "database"
    AUTHENTICATION = "authentication"
    SECRET_STORE = "secret_store"
    PROVIDER = "provider"
    READ_MODEL = "read_model"


class DependencyStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class MaintenanceMode(StrEnum):
    NORMAL = "normal"
    DRAINING = "draining"
    MAINTENANCE = "maintenance"


@dataclass(frozen=True)
class AvailabilitySnapshot:
    policy_version: str
    process_role: str
    liveness: AvailabilityState
    readiness: AvailabilityState
    maintenance_mode: MaintenanceMode
    checks: Mapping[str, str]
    retry_after_seconds: int

    @property
    def ready(self) -> bool:
        return self.readiness in {
            AvailabilityState.READY,
            AvailabilityState.DEGRADED,
        }


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None
    retry_after_seconds: int | None = None


_CRITICAL_DEPENDENCIES = MappingProxyType(
    {
        "api": frozenset(
            {DependencyKind.DATABASE, DependencyKind.AUTHENTICATION}
        ),
        "worker": frozenset(
            {DependencyKind.DATABASE, DependencyKind.SECRET_STORE}
        ),
        "scheduler": frozenset({DependencyKind.DATABASE}),
    }
)

_RELEVANT_DEPENDENCIES = MappingProxyType(
    {
        "api": frozenset(
            {
                DependencyKind.DATABASE,
                DependencyKind.AUTHENTICATION,
                DependencyKind.SECRET_STORE,
                DependencyKind.PROVIDER,
                DependencyKind.READ_MODEL,
            }
        ),
        "worker": frozenset(
            {
                DependencyKind.DATABASE,
                DependencyKind.SECRET_STORE,
                DependencyKind.PROVIDER,
            }
        ),
        "scheduler": frozenset({DependencyKind.DATABASE}),
    }
)


class OperationalAvailabilityController:
    """In-process policy state for API/worker/scheduler composition roots."""

    def __init__(
        self,
        *,
        process_role: str,
        maintenance_mode: MaintenanceMode = MaintenanceMode.NORMAL,
        retry_after_seconds: int = 60,
        dependencies: Mapping[DependencyKind, DependencyStatus] | None = None,
    ) -> None:
        role = process_role.strip().lower()
        if role not in _CRITICAL_DEPENDENCIES:
            raise ValueError("unsupported process_role")
        if not 1 <= retry_after_seconds <= 86_400:
            raise ValueError("retry_after_seconds must be between 1 and 86400")
        self._process_role = role
        self._maintenance_mode = maintenance_mode
        self._retry_after_seconds = retry_after_seconds
        initial = {
            dependency: (
                DependencyStatus.READY
                if dependency in _RELEVANT_DEPENDENCIES[role]
                else DependencyStatus.NOT_APPLICABLE
            )
            for dependency in DependencyKind
        }
        if dependencies:
            initial.update(dependencies)
        self._dependencies = initial

    @property
    def policy_version(self) -> str:
        return AVAILABILITY_POLICY_VERSION

    @property
    def maintenance_mode(self) -> MaintenanceMode:
        return self._maintenance_mode

    def set_maintenance_mode(self, mode: MaintenanceMode) -> None:
        self._maintenance_mode = mode

    def set_dependency(
        self,
        dependency: DependencyKind,
        status: DependencyStatus,
    ) -> None:
        self._dependencies[dependency] = status

    def snapshot(
        self,
        *,
        overrides: Mapping[DependencyKind, DependencyStatus] | None = None,
    ) -> AvailabilitySnapshot:
        statuses = dict(self._dependencies)
        if overrides:
            statuses.update(overrides)

        readiness = AvailabilityState.READY
        if self._maintenance_mode is MaintenanceMode.MAINTENANCE:
            readiness = AvailabilityState.UNAVAILABLE
        elif self._maintenance_mode is MaintenanceMode.DRAINING:
            readiness = AvailabilityState.DEGRADED

        for dependency in _RELEVANT_DEPENDENCIES[self._process_role]:
            status = statuses[dependency]
            if (
                status is DependencyStatus.UNAVAILABLE
                and dependency in _CRITICAL_DEPENDENCIES[self._process_role]
            ):
                readiness = AvailabilityState.UNAVAILABLE
                break
            if status in {
                DependencyStatus.DEGRADED,
                DependencyStatus.UNAVAILABLE,
            } and readiness is AvailabilityState.READY:
                readiness = AvailabilityState.DEGRADED

        return AvailabilitySnapshot(
            policy_version=AVAILABILITY_POLICY_VERSION,
            process_role=self._process_role,
            liveness=AvailabilityState.HEALTHY,
            readiness=readiness,
            maintenance_mode=self._maintenance_mode,
            checks=MappingProxyType(
                {
                    dependency.value: statuses[dependency].value
                    for dependency in DependencyKind
                    if statuses[dependency] is not DependencyStatus.NOT_APPLICABLE
                }
            ),
            retry_after_seconds=self._retry_after_seconds,
        )

    def can_claim_work(self) -> bool:
        if self._maintenance_mode is not MaintenanceMode.NORMAL:
            return False
        return self.snapshot().readiness is not AvailabilityState.UNAVAILABLE

    def admit_http(self, *, method: str, path: str) -> AdmissionDecision:
        if not path.startswith("/v1/"):
            return AdmissionDecision(True)

        snapshot = self.snapshot()
        if snapshot.readiness is AvailabilityState.UNAVAILABLE:
            return AdmissionDecision(
                False,
                code="SERVICE_UNAVAILABLE",
                message="Service is temporarily unavailable.",
                retry_after_seconds=self._retry_after_seconds,
            )

        if self._maintenance_mode is MaintenanceMode.DRAINING and method.upper() in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            return AdmissionDecision(
                False,
                code="MAINTENANCE_DRAINING",
                message="Service is draining for planned maintenance.",
                retry_after_seconds=self._retry_after_seconds,
            )

        return AdmissionDecision(True)
