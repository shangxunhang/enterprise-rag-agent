"""SIF-13 local filesystem Disk-budget Guard.

The guard is storage-capacity policy only.  It is not SIF-12 Budget authority,
Gate authority, provider-fallback authority, or business lifecycle truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import os
import shutil
from typing import Iterable

from schemas.failure import FailureCategory, FailureOrigin


GIB = 1024 ** 3


class LocalStorageAdmissionError(RuntimeError):
    """CRITICAL SIF-13 pressure rejected a new durable-storage-heavy Run."""

    failure_category = FailureCategory.RESOURCE_EXHAUSTED
    failure_origin = FailureOrigin.LOCAL_STORAGE
    provider_fallback_allowed = False

    def __init__(self, *, scope_ref: str, roots: tuple[Path, ...]) -> None:
        self.scope_ref = str(scope_ref)
        self.filesystem_roots = tuple(Path(item).resolve() for item in roots)
        super().__init__(
            "critical local-storage pressure rejects new durable Run: "
            + self.scope_ref
        )


class DiskPressureState(str, Enum):
    NORMAL = "NORMAL"
    SOFT_PRESSURE = "SOFT_PRESSURE"
    HARD_PRESSURE = "HARD_PRESSURE"
    CRITICAL_PRESSURE = "CRITICAL_PRESSURE"


@dataclass(frozen=True)
class DiskMeasurement:
    filesystem_root: Path
    total_bytes: int
    used_bytes: int
    free_bytes: int
    free_ratio: float
    filesystem_identity: str


@dataclass(frozen=True)
class DiskPressurePolicyResult:
    state: DiskPressureState
    scope_ref: str
    filesystem_root: Path
    allow_new_durable_run: bool
    suppress_optional_capture: bool
    safe_gc_allowed: bool
    safe_compression_allowed: bool
    required_canonical_writes_allowed: bool
    required_recovery_writes_allowed: bool
    required_release_writes_allowed: bool
    failure_origin: str
    provider_fallback_allowed: bool
    gate_route_changed: bool
    sif12_budget_changed: bool
    observability_status: str
    actions: tuple[str, ...]


class DiskBudgetGuard:
    """Measure local storage and apply frozen SIF-13 pressure policy."""

    def __init__(
        self,
        *,
        soft_free_ratio: float = 0.15,
        soft_free_gib: float = 20.0,
        hard_free_ratio: float = 0.10,
        hard_free_gib: float = 10.0,
        critical_free_ratio: float = 0.05,
        critical_free_gib: float = 5.0,
    ) -> None:
        values = (
            soft_free_ratio,
            hard_free_ratio,
            critical_free_ratio,
        )
        if not all(0.0 < float(value) < 1.0 for value in values):
            raise ValueError("disk free-ratio thresholds must be between 0 and 1")
        if not (
            float(critical_free_ratio)
            < float(hard_free_ratio)
            < float(soft_free_ratio)
        ):
            raise ValueError(
                "disk pressure ratio ordering must be critical < hard < soft"
            )
        gib_values = (
            soft_free_gib,
            hard_free_gib,
            critical_free_gib,
        )
        if not all(float(value) > 0 for value in gib_values):
            raise ValueError("disk free-space thresholds must be positive")
        if not (
            float(critical_free_gib)
            < float(hard_free_gib)
            < float(soft_free_gib)
        ):
            raise ValueError(
                "disk pressure byte ordering must be critical < hard < soft"
            )
        self.soft_free_ratio = float(soft_free_ratio)
        self.soft_free_bytes = int(float(soft_free_gib) * GIB)
        self.hard_free_ratio = float(hard_free_ratio)
        self.hard_free_bytes = int(float(hard_free_gib) * GIB)
        self.critical_free_ratio = float(critical_free_ratio)
        self.critical_free_bytes = int(float(critical_free_gib) * GIB)

    @classmethod
    def from_settings(cls, settings) -> "DiskBudgetGuard":
        return cls(
            soft_free_ratio=settings.disk_soft_free_ratio,
            soft_free_gib=settings.disk_soft_free_gib,
            hard_free_ratio=settings.disk_hard_free_ratio,
            hard_free_gib=settings.disk_hard_free_gib,
            critical_free_ratio=settings.disk_critical_free_ratio,
            critical_free_gib=settings.disk_critical_free_gib,
        )

    @staticmethod
    def _filesystem_identity(root: Path) -> str:
        resolved = root.expanduser().resolve()
        try:
            stat = resolved.stat()
            device = getattr(stat, "st_dev", None)
        except OSError:
            device = None
        if device is not None:
            return f"device:{device}"
        anchor = resolved.anchor or str(resolved)
        return f"anchor:{anchor.casefold()}"

    def measure(self, filesystem_root: str | Path) -> DiskMeasurement:
        root = Path(filesystem_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(root)
        total = int(usage.total)
        free = int(usage.free)
        used = int(usage.used)
        if total <= 0:
            raise RuntimeError("filesystem total bytes must be positive")
        return DiskMeasurement(
            filesystem_root=root,
            total_bytes=total,
            used_bytes=used,
            free_bytes=free,
            free_ratio=free / total,
            filesystem_identity=self._filesystem_identity(root),
        )

    def measure_distinct_filesystems(
        self,
        roots: Iterable[str | Path],
    ) -> tuple[DiskMeasurement, ...]:
        by_identity: dict[str, DiskMeasurement] = {}
        for root in roots:
            measurement = self.measure(root)
            by_identity.setdefault(
                measurement.filesystem_identity,
                measurement,
            )
        return tuple(
            by_identity[key]
            for key in sorted(by_identity)
        )

    def classify_pressure(
        self,
        measurement: DiskMeasurement,
    ) -> DiskPressureState:
        free_ratio = float(measurement.free_ratio)
        free_bytes = int(measurement.free_bytes)
        if (
            free_ratio < self.critical_free_ratio
            and free_bytes < self.critical_free_bytes
        ):
            return DiskPressureState.CRITICAL_PRESSURE
        if (
            free_ratio < self.hard_free_ratio
            and free_bytes < self.hard_free_bytes
        ):
            return DiskPressureState.HARD_PRESSURE
        if (
            free_ratio < self.soft_free_ratio
            and free_bytes < self.soft_free_bytes
        ):
            return DiskPressureState.SOFT_PRESSURE
        return DiskPressureState.NORMAL

    def apply_pressure_policy(
        self,
        state: DiskPressureState,
        scope_ref: str,
        *,
        filesystem_root: str | Path,
    ) -> DiskPressurePolicyResult:
        root = Path(filesystem_root).expanduser().resolve()
        actions: list[str] = ["MEASURE_AND_REPORT"]
        safe_gc = state is not DiskPressureState.NORMAL
        safe_compression = state is not DiskPressureState.NORMAL
        suppress_capture = state in {
            DiskPressureState.HARD_PRESSURE,
            DiskPressureState.CRITICAL_PRESSURE,
        }
        allow_new_run = state is not DiskPressureState.CRITICAL_PRESSURE
        if safe_gc:
            actions.append("RUN_ALREADY_ELIGIBLE_SAFE_GC")
            actions.append("LOSSLESS_SAFE_COMPRESSION")
        if suppress_capture:
            actions.append("SUPPRESS_NEW_OPTIONAL_R3_CAPTURE")
        if not allow_new_run:
            actions.append("REJECT_NEW_DURABLE_STORAGE_HEAVY_RUN")

        return DiskPressurePolicyResult(
            state=state,
            scope_ref=str(scope_ref),
            filesystem_root=root,
            allow_new_durable_run=allow_new_run,
            suppress_optional_capture=suppress_capture,
            safe_gc_allowed=safe_gc,
            safe_compression_allowed=safe_compression,
            required_canonical_writes_allowed=True,
            required_recovery_writes_allowed=True,
            required_release_writes_allowed=True,
            failure_origin="local_storage",
            provider_fallback_allowed=False,
            gate_route_changed=False,
            sif12_budget_changed=False,
            observability_status=(
                "OBSERVABILITY_DEGRADED"
                if suppress_capture
                else "NORMAL"
            ),
            actions=tuple(actions),
        )

    def evaluate_distinct_filesystems(
        self,
        roots: Iterable[str | Path],
        scope_ref: str,
    ) -> tuple[tuple[DiskMeasurement, DiskPressurePolicyResult], ...]:
        results: list[tuple[DiskMeasurement, DiskPressurePolicyResult]] = []
        for measurement in self.measure_distinct_filesystems(roots):
            state = self.classify_pressure(measurement)
            results.append(
                (
                    measurement,
                    self.apply_pressure_policy(
                        state,
                        scope_ref,
                        filesystem_root=measurement.filesystem_root,
                    ),
                )
            )
        return tuple(results)

    def assert_new_durable_run_allowed(
        self,
        roots: Iterable[str | Path],
        scope_ref: str,
    ) -> tuple[tuple[DiskMeasurement, DiskPressurePolicyResult], ...]:
        evaluated = self.evaluate_distinct_filesystems(roots, scope_ref)
        blocked = tuple(
            policy.filesystem_root
            for _measurement, policy in evaluated
            if not policy.allow_new_durable_run
        )
        if blocked:
            raise LocalStorageAdmissionError(scope_ref=scope_ref, roots=blocked)
        return evaluated

    def evaluate(
        self,
        filesystem_root: str | Path,
        scope_ref: str,
    ) -> tuple[DiskMeasurement, DiskPressurePolicyResult]:
        measurement = self.measure(filesystem_root)
        state = self.classify_pressure(measurement)
        return measurement, self.apply_pressure_policy(
            state,
            scope_ref,
            filesystem_root=measurement.filesystem_root,
        )


__all__ = [
    "DiskBudgetGuard",
    "DiskMeasurement",
    "DiskPressurePolicyResult",
    "DiskPressureState",
    "GIB",
    "LocalStorageAdmissionError",
]
