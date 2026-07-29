"""Task 全局预算的原子预留、结算与 draining 控制。"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Callable

from apps.enterprise_document.schemas.agent_rag_v1 import (
    BudgetReservationSchema,
    BudgetSnapshotSchema,
    BudgetVectorSchema,
    TaskBudgetLimitsSchema,
)


class TaskBudgetExhausted(RuntimeError):
    def __init__(self, resource: str, limit: int) -> None:
        self.resource = resource
        self.limit = limit
        super().__init__(f"task budget exhausted: {resource} limit={limit}")


_RESOURCE_LIMIT_FIELDS = {
    "model_calls": "max_model_calls",
    "tool_calls": "max_tool_calls",
    "total_replans": "max_total_replans",
    "total_rewrites": "max_total_rewrites",
    "document_repair_tasks": "max_document_repair_tasks",
}


def _add(left: BudgetVectorSchema, right: BudgetVectorSchema) -> BudgetVectorSchema:
    return BudgetVectorSchema(
        **{
            field: getattr(left, field) + getattr(right, field)
            for field in _RESOURCE_LIMIT_FIELDS
        }
    )


def _subtract(
    left: BudgetVectorSchema,
    right: BudgetVectorSchema,
) -> BudgetVectorSchema:
    values = {
        field: getattr(left, field) - getattr(right, field)
        for field in _RESOURCE_LIMIT_FIELDS
    }
    if any(value < 0 for value in values.values()):
        raise ValueError("budget vector subtraction cannot become negative")
    return BudgetVectorSchema(**values)


class BudgetManager:
    """并发调度前必须先通过本对象预留；耗尽后只允许结算和读取。"""

    def __init__(
        self,
        *,
        task_id: str,
        limits: TaskBudgetLimitsSchema,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.task_id = task_id
        self.limits = limits
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.started_at = self.clock()
        self._reserved = BudgetVectorSchema()
        self._used = BudgetVectorSchema()
        self._reservations: dict[str, BudgetReservationSchema] = {}
        self._draining = False
        self._lock = RLock()

    def reserve(
        self,
        reservation_id: str,
        requested: BudgetVectorSchema,
    ) -> BudgetReservationSchema:
        with self._lock:
            existing = self._reservations.get(reservation_id)
            if existing is not None:
                if existing.requested != requested:
                    raise ValueError(
                        "budget reservation id reused with different request"
                    )
                return existing.model_copy(deep=True)
            self._enforce_wall_clock()
            if self._draining:
                raise TaskBudgetExhausted("draining", 0)
            candidate = _add(_add(self._used, self._reserved), requested)
            for resource, limit_field in _RESOURCE_LIMIT_FIELDS.items():
                limit = int(getattr(self.limits, limit_field))
                if getattr(candidate, resource) > limit:
                    self._draining = True
                    raise TaskBudgetExhausted(resource, limit)
            reservation = BudgetReservationSchema(
                reservation_id=reservation_id,
                task_id=self.task_id,
                requested=requested,
            )
            self._reserved = _add(self._reserved, requested)
            self._reservations[reservation_id] = reservation
            return reservation.model_copy(deep=True)

    def settle(
        self,
        reservation_id: str,
        actual: BudgetVectorSchema,
    ) -> BudgetReservationSchema:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation.status == "settled":
                if reservation.settled != actual:
                    raise ValueError(
                        "settled reservation cannot change actual usage"
                    )
                return reservation.model_copy(deep=True)
            if reservation.status != "active":
                raise ValueError("released reservation cannot be settled")
            for resource in _RESOURCE_LIMIT_FIELDS:
                if getattr(actual, resource) > getattr(
                    reservation.requested,
                    resource,
                ):
                    raise ValueError(
                        f"actual usage exceeds reservation: {resource}"
                    )
            self._reserved = _subtract(
                self._reserved,
                reservation.requested,
            )
            self._used = _add(self._used, actual)
            reservation.settled = actual
            reservation.status = "settled"
            return reservation.model_copy(deep=True)

    def release(self, reservation_id: str) -> BudgetReservationSchema:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation.status == "active":
                self._reserved = _subtract(
                    self._reserved,
                    reservation.requested,
                )
                reservation.status = "released"
            return reservation.model_copy(deep=True)

    def start_draining(self) -> None:
        with self._lock:
            self._draining = True

    def snapshot(self) -> BudgetSnapshotSchema:
        with self._lock:
            elapsed = max(
                0.0,
                (self.clock() - self.started_at).total_seconds(),
            )
            if elapsed >= self.limits.max_wall_clock_seconds:
                self._draining = True
            return BudgetSnapshotSchema(
                task_id=self.task_id,
                limits=self.limits,
                reserved=self._reserved,
                used=self._used,
                draining=self._draining,
                elapsed_seconds=elapsed,
            )

    def _enforce_wall_clock(self) -> None:
        elapsed = (self.clock() - self.started_at).total_seconds()
        if elapsed >= self.limits.max_wall_clock_seconds:
            self._draining = True
            raise TaskBudgetExhausted(
                "wall_clock_seconds",
                self.limits.max_wall_clock_seconds,
            )
