"""节点级与 Task 全局预算的强类型快照。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ._base import ContractSchema


class TaskBudgetLimitsSchema(ContractSchema):
    schema_version: str = "task_budget_limits_v1"
    max_model_calls: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    max_total_replans: int = Field(ge=0)
    max_total_rewrites: int = Field(ge=0)
    max_document_repair_tasks: int = Field(ge=0)
    max_wall_clock_seconds: int = Field(gt=0)


class BudgetVectorSchema(ContractSchema):
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    total_replans: int = Field(default=0, ge=0)
    total_rewrites: int = Field(default=0, ge=0)
    document_repair_tasks: int = Field(default=0, ge=0)


class BudgetReservationSchema(ContractSchema):
    schema_version: str = "budget_reservation_v1"
    reservation_id: str
    task_id: str
    requested: BudgetVectorSchema
    settled: BudgetVectorSchema = Field(default_factory=BudgetVectorSchema)
    status: Literal["active", "settled", "released"] = "active"


class BudgetSnapshotSchema(ContractSchema):
    schema_version: str = "budget_snapshot_v1"
    task_id: str
    limits: TaskBudgetLimitsSchema
    reserved: BudgetVectorSchema
    used: BudgetVectorSchema
    draining: bool = False
    elapsed_seconds: float = Field(default=0.0, ge=0)
