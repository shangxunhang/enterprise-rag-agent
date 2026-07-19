# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：JsonlTaskManager。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""JSONL task lifecycle manager with canonical statuses and structured errors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from schemas.agent import AgentResultSchema
from schemas.common import ErrorSchema
from schemas.status import ExecutionStatus
from schemas.task import TaskSchema
from schemas.task_state import TaskStateEventSchema, TaskStateRecordSchema


# 阅读注释（类）：封装 jsonl 任务 管理器，集中封装相关状态、依赖和行为。
class JsonlTaskManager:
    """封装 jsonl 任务 管理器，集中封装相关状态、依赖和行为。"""
    VALID_STATUSES = {status.value for status in ExecutionStatus}

    # 阅读注释（函数）：初始化 JsonlTaskManager，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        output_dir: str | Path = "data/tasks",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        """初始化 JsonlTaskManager，保存运行所需的依赖、配置或状态。

        参数:
            output_dir: 输出 dir，具体约束请结合类型标注和调用方确认。
            clock: clock，具体约束请结合类型标注和调用方确认。
            id_generator: 标识 generator，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：Path, SystemClock, UuidIdGenerator, self.output_dir.mkdir。
        """
        self.output_dir = Path(output_dir)
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._records: Dict[str, TaskStateRecordSchema] = {}

    # 阅读注释（函数）：处理 now iso 相关逻辑。
    def _now_iso(self) -> str:
        """处理 now iso 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：self.clock.now_iso。
        """
        return self.clock.now_iso()

    # 阅读注释（函数）：处理 new 标识 相关逻辑。
    def _new_id(self, prefix: str) -> str:
        """处理 new 标识 相关逻辑。

        参数:
            prefix: prefix，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：self.id_generator.new_id。
        """
        return self.id_generator.new_id(prefix)

    # 阅读注释（函数）：处理 任务 路径 相关逻辑。
    def _task_path(self, task_id: str) -> Path:
        """处理 任务 路径 相关逻辑。

        参数:
            task_id: 任务唯一标识。

        返回:
            Path
        """
        return self.output_dir / f"{task_id}_state.jsonl"

    # 阅读注释（函数）：写入 事件。
    def _write_event(self, event: TaskStateEventSchema) -> None:
        """写入 事件。

        参数:
            event: 事件，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：open, self._task_path, f.write, json.dumps, event.model_dump。
        """
        with self._task_path(event.task_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")

    # 阅读注释（函数）：处理 append 事件 相关逻辑。
    def _append_event(
        self,
        record: TaskStateRecordSchema,
        event_type: str,
        current_status: ExecutionStatus,
        previous_status: Optional[ExecutionStatus] = None,
        current_step: Optional[str] = None,
        result_id: Optional[str] = None,
        error: Optional[ErrorSchema] = None,
    ) -> TaskStateEventSchema:
        """处理 append 事件 相关逻辑。

        参数:
            record: 记录，具体约束请结合类型标注和调用方确认。
            event_type: 事件 类型，具体约束请结合类型标注和调用方确认。
            current_status: current 状态，具体约束请结合类型标注和调用方确认。
            previous_status: previous 状态，具体约束请结合类型标注和调用方确认。
            current_step: current step，具体约束请结合类型标注和调用方确认。
            result_id: 结果 标识，具体约束请结合类型标注和调用方确认。
            error: 错误，具体约束请结合类型标注和调用方确认。

        返回:
            TaskStateEventSchema

        阅读提示:
            主要直接调用：TaskStateEventSchema, self._new_id, self._now_iso, record.events.append, self._write_event。
        """
        event = TaskStateEventSchema(
            event_id=self._new_id("task_event"),
            task_id=record.task_id,
            run_id=record.run_id,
            task_type=record.task_type,
            event_type=event_type,
            previous_status=previous_status,
            current_status=current_status,
            current_step=current_step,
            result_id=result_id,
            error=error,
            error_message=error.message if error else None,
            created_at=self._now_iso(),
        )
        record.events.append(event)
        self._write_event(event)
        return event

    # 阅读注释（函数）：创建 任务。
    def create_task(self, task: TaskSchema) -> TaskStateRecordSchema:
        """创建 任务。

        参数:
            task: 待执行的任务对象。

        返回:
            TaskStateRecordSchema

        阅读提示:
            主要直接调用：self._now_iso, TaskStateRecordSchema, self._append_event。
        """
        if task.task_id in self._records:
            return self._records[task.task_id]
        now = self._now_iso()
        record = TaskStateRecordSchema(
            task_id=task.task_id,
            run_id=task.run_id,
            task_type=task.task_type,
            tenant_id=task.tenant_id,
            task_name=task.task_name,
            project_name=task.project_name,
            user_input=task.user_input,
            status=ExecutionStatus.PENDING,
            current_step="created",
            created_at=now,
            updated_at=now,
            metadata=task.metadata,
        )
        self._records[task.task_id] = record
        self._append_event(
            record,
            "task_created",
            ExecutionStatus.PENDING,
            current_step="created",
        )
        return record

    # 阅读注释（函数）：处理 mark 相关逻辑。
    def _mark(
        self,
        task: TaskSchema,
        *,
        status: ExecutionStatus,
        event_type: str,
        current_step: str,
        result: Optional[AgentResultSchema] = None,
        error: Optional[ErrorSchema] = None,
    ) -> TaskStateRecordSchema:
        """处理 mark 相关逻辑。

        参数:
            task: 待执行的任务对象。
            status: 状态，具体约束请结合类型标注和调用方确认。
            event_type: 事件 类型，具体约束请结合类型标注和调用方确认。
            current_step: current step，具体约束请结合类型标注和调用方确认。
            result: 待处理的结果对象。
            error: 错误，具体约束请结合类型标注和调用方确认。

        返回:
            TaskStateRecordSchema

        阅读提示:
            主要直接调用：self.create_task, self._now_iso, self._append_event。
        """
        record = self.create_task(task)
        previous = record.status
        now = self._now_iso()
        record.status = status
        record.current_step = current_step
        record.updated_at = now
        if status == ExecutionStatus.RUNNING:
            record.started_at = record.started_at or now
        if status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYABLE_FAILED,
            ExecutionStatus.CANCELLED,
        }:
            record.finished_at = now
        if result:
            record.result_id = result.result_id
        if error:
            record.error = error
            record.error_message = error.message
        self._append_event(
            record,
            event_type,
            status,
            previous_status=previous,
            current_step=current_step,
            result_id=result.result_id if result else None,
            error=error,
        )
        return record

    # 阅读注释（函数）：处理 mark running 相关逻辑。
    def mark_running(self, task: TaskSchema, current_step: str = "workflow_started") -> TaskStateRecordSchema:
        """处理 mark running 相关逻辑。

        参数:
            task: 待执行的任务对象。
            current_step: current step，具体约束请结合类型标注和调用方确认。

        返回:
            TaskStateRecordSchema

        阅读提示:
            主要直接调用：self._mark。
        """
        return self._mark(
            task,
            status=ExecutionStatus.RUNNING,
            event_type="task_started",
            current_step=current_step,
        )

    # 阅读注释（函数）：处理 mark success 相关逻辑。
    def mark_success(self, task: TaskSchema, result: AgentResultSchema, current_step: str = "workflow_finished") -> TaskStateRecordSchema:
        """处理 mark success 相关逻辑。

        参数:
            task: 待执行的任务对象。
            result: 待处理的结果对象。
            current_step: current step，具体约束请结合类型标注和调用方确认。

        返回:
            TaskStateRecordSchema

        阅读提示:
            主要直接调用：self._mark。
        """
        return self._mark(
            task,
            status=ExecutionStatus.SUCCESS,
            event_type="task_succeeded",
            current_step=current_step,
            result=result,
        )

    # 阅读注释（函数）：处理 mark partial success 相关逻辑。
    def mark_partial_success(self, task: TaskSchema, result: AgentResultSchema, current_step: str = "workflow_finished") -> TaskStateRecordSchema:
        """处理 mark partial success 相关逻辑。

        参数:
            task: 待执行的任务对象。
            result: 待处理的结果对象。
            current_step: current step，具体约束请结合类型标注和调用方确认。

        返回:
            TaskStateRecordSchema

        阅读提示:
            主要直接调用：self._mark。
        """
        return self._mark(
            task,
            status=ExecutionStatus.PARTIAL_SUCCESS,
            event_type="task_partial_succeeded",
            current_step=current_step,
            result=result,
            error=result.error,
        )

    # 阅读注释（函数）：处理 mark failed 相关逻辑。
    def mark_failed(
        self,
        task: TaskSchema,
        error: ErrorSchema,
        current_step: str = "workflow_failed",
        retryable: bool = False,
        result: Optional[AgentResultSchema] = None,
    ) -> TaskStateRecordSchema:
        """处理 mark failed 相关逻辑。

        参数:
            task: 待执行的任务对象。
            error: 错误，具体约束请结合类型标注和调用方确认。
            current_step: current step，具体约束请结合类型标注和调用方确认。
            retryable: retryable，具体约束请结合类型标注和调用方确认。
            result: 待处理的结果对象。

        返回:
            TaskStateRecordSchema

        阅读提示:
            主要直接调用：self._mark。
        """
        status = ExecutionStatus.RETRYABLE_FAILED if retryable else ExecutionStatus.FAILED
        return self._mark(
            task,
            status=status,
            event_type="task_retryable_failed" if retryable else "task_failed",
            current_step=current_step,
            result=result,
            error=error,
        )

    # 阅读注释（函数）：获取 任务。
    def get_task(self, task_id: str) -> TaskStateRecordSchema:
        """获取 任务。

        参数:
            task_id: 任务唯一标识。

        返回:
            TaskStateRecordSchema

        阅读提示:
            主要直接调用：KeyError。
        """
        if task_id not in self._records:
            raise KeyError(f"Task state not found: {task_id}")
        return self._records[task_id]

    # 阅读注释（函数）：处理 exists 相关逻辑。
    def exists(self, task_id: str) -> bool:
        """处理 exists 相关逻辑。

        参数:
            task_id: 任务唯一标识。

        返回:
            bool
        """
        return task_id in self._records

    # 阅读注释（函数）：获取 任务 路径。
    def get_task_path(self, task_id: str) -> Path:
        """获取 任务 路径。

        参数:
            task_id: 任务唯一标识。

        返回:
            Path

        阅读提示:
            主要直接调用：self._task_path。
        """
        return self._task_path(task_id)
