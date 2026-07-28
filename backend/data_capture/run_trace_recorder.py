"""把 Trace v2 事件按运行写入 JSONL 文件。

每个 ``run_id`` 对应一个 ``<run_id>_trace.jsonl`` 文件。记录器在进程内为事件分配
单调序号，并继承当前活动 span 的 trace/span 关系；每行都是独立
``RunTraceEventSchema`` JSON 对象，便于追加写入、流式读取和局部损坏隔离。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.runtime.clock import Clock, SystemClock
from core.runtime.ids import IdGenerator, UuidIdGenerator
from observability.trace_context import current_span
from observability.trace_summary import bounded_summary
from schemas.trace import RunTraceEventSchema


class JsonlRunTraceRecorder:
    """以线程安全追加方式持久化结构化 Trace v2 事件。

    Attributes:
        output_dir: 每个运行轨迹文件所在目录。
        clock: 为事件和 span 阶段生成时间戳的可替换时钟。
        id_generator: 为事件和缺省 span 生成标识的可替换生成器。
        _lock: 保护序号初始化、递增和文件追加写入的可重入锁。
        _sequence_by_run: 当前进程内每个 run 已分配的最后事件序号。
    """

    def __init__(
        self,
        output_dir: str | Path = "data/runs",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        """创建 JSONL 运行轨迹记录器。

        Args:
            output_dir: 运行轨迹输出目录。
            clock: 自定义时钟；未提供时使用系统 UTC 时钟。
            id_generator: 自定义标识生成器；未提供时使用 UUID 实现。
        """

        # 第一步：统一把输出目录转换为 Path。
        self.output_dir = Path(output_dir)

        # 第二步：补齐可替换时间和标识依赖，支持确定性测试。
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()

        # 第三步：初始化时创建目录，使后续 record 只处理事件和文件追加。
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 第四步：使用可重入锁同时保护序号和文件写入；_next_sequence 会在 record 锁外调用。
        self._lock = threading.RLock()

        # 第五步：序号缓存按 run 隔离，首次记录时从现有文件行数恢复。
        self._sequence_by_run: Dict[str, int] = {}

    def _now_iso(self) -> str:
        """返回统一时钟生成的当前 UTC ISO 时间。"""

        return self.clock.now_iso()

    def _new_id(self, prefix: str) -> str:
        """生成带给定业务前缀的新标识。

        Args:
            prefix: 标识用途前缀，例如 event 或 span。

        Returns:
            由注入标识生成器创建的字符串。
        """

        return self.id_generator.new_id(prefix)

    def _trace_path(self, run_id: str) -> Path:
        """返回指定运行的标准轨迹文件路径。

        Args:
            run_id: 单次运行标识。

        Returns:
            ``<output_dir>/<run_id>_trace.jsonl``。
        """

        return self.output_dir / f"{run_id}_trace.jsonl"

    def _next_sequence(self, run_id: str) -> int:
        """线程安全地分配当前运行的下一个事件序号。

        Args:
            run_id: 要分配序号的单次运行标识。

        Returns:
            从 1 开始、在当前文件已有非空行基础上递增的序号。
        """

        # 第一步：锁住首次恢复和递增过程，防止并发事件获得相同序号。
        with self._lock:
            if run_id not in self._sequence_by_run:
                # 第二步：首次见到该 run 时定位已有文件，支持进程重启后继续追加。
                path = self._trace_path(run_id)
                count = 0

                # 第三步：以非空 JSONL 行数作为已有最大序号的兼容恢复值。
                if path.exists():
                    with path.open("r", encoding="utf-8") as handle:
                        count = sum(1 for line in handle if line.strip())

                # 第四步：缓存恢复结果，后续事件不再重复扫描文件。
                self._sequence_by_run[run_id] = count

            # 第五步：递增后返回当前事件序号。
            self._sequence_by_run[run_id] += 1
            return self._sequence_by_run[run_id]

    def record(
        self,
        task_id: str,
        run_id: str,
        event_type: str,
        component_type: str,
        component_name: str,
        payload: Optional[Dict[str, Any]] = None,
        input_payload: Optional[Dict[str, Any]] = None,
        output_payload: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
        workflow_version: Optional[str] = None,
        step_id: Optional[str] = None,
        step_name: Optional[str] = None,
        step_order: Optional[int] = None,
        call_id: Optional[str] = None,
        caller: Optional[str] = None,
        callee: Optional[str] = None,
        status: Optional[str] = None,
        error_message: Optional[str] = None,
        latency_ms: Optional[int] = None,
        token_usage: Optional[Dict[str, Any]] = None,
        cost: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        agent_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        span_name: Optional[str] = None,
        span_kind: str = "internal",
        phase: str = "event",
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        input_summary: Optional[Dict[str, Any]] = None,
        output_summary: Optional[Dict[str, Any]] = None,
        lineage: Optional[Dict[str, Any]] = None,
    ) -> RunTraceEventSchema:
        """构造、校验并追加写入一个结构化运行事件。

        Args:
            task_id: 事件所属业务任务标识。
            run_id: 事件所属单次运行标识。
            event_type: 稳定事件类别。
            component_type: 产生事件的组件类型。
            component_name: 产生事件的具体组件名称。
            payload: 迁移期通用结构化载荷。
            input_payload: 完整或脱敏输入载荷。
            output_payload: 完整或脱敏输出载荷。
            workflow_id: 业务工作流标识。
            workflow_version: 业务工作流版本。
            step_id: 工作流步骤标识。
            step_name: 工作流步骤名称。
            step_order: 工作流步骤默认顺序。
            call_id: 当前跨组件调用标识。
            caller: 调用方组件名称。
            callee: 被调用组件名称。
            status: 事件发生时的执行状态。
            error_message: 已脱敏错误摘要。
            latency_ms: 当前调用或 span 毫秒耗时。
            token_usage: 模型 token 用量。
            cost: 当前调用成本信息。
            metrics: 组件特有指标。
            model_name: 实际模型名称。
            tool_name: 实际工具名称。
            agent_name: 实际 Agent 名称。
            tags: 检索和聚合标签。
            metadata: 稳定事件元数据。
            extra: 尚未进入稳定契约的扩展字段。
            trace_id: 显式整条调用链标识；为空时继承活动 span 或按 run 生成。
            span_id: 显式当前 span 标识；为空时继承活动 span 或生成新值。
            parent_span_id: 显式父 span 标识。
            span_name: 当前 span 可读名称。
            span_kind: span 角色，默认 internal。
            phase: 生命周期阶段，例如 start、end、error 或 event。
            started_at: 显式开始时间；start 阶段缺省为当前时间。
            finished_at: 显式结束时间；end/error 阶段缺省为当前时间。
            input_summary: 调用方提供的有限输入摘要。
            output_summary: 调用方提供的有限输出摘要。
            lineage: 数据和调用血缘。

        Returns:
            已写入文件的标准 ``RunTraceEventSchema``。
        """

        # 第一步：为事件创建时间快照，并读取当前活动 span 上下文。
        now = self._now_iso()
        active = current_span()

        # 第二步：显式 trace_id 优先；否则继承活动 span，最外层事件按 run 创建稳定值。
        resolved_trace_id = trace_id or (
            active.trace_id if active else f"trace_{run_id}"
        )

        # 第三步：显式 span_id 优先；否则复用活动 span，完全无上下文时生成新 span。
        resolved_span_id = span_id or (
            active.span_id if active else self._new_id("span")
        )

        # 第四步：显式父级优先；自动复用活动 span 本身时继承其父级，不额外制造自环。
        resolved_parent_span_id = (
            parent_span_id
            if parent_span_id is not None
            else (active.parent_span_id if active and span_id is None else None)
        )

        # 第五步：把空输入输出规范为空字典，随后可生成有界摘要。
        raw_input = input_payload or {}
        raw_output = output_payload or {}

        # 第六步：一次性构造并由 Pydantic 校验完整 Trace v2 事件。
        event = RunTraceEventSchema(
            event_id=self._new_id("event"),
            event_sequence=self._next_sequence(run_id),
            trace_id=resolved_trace_id,
            task_id=task_id,
            run_id=run_id,
            span_id=resolved_span_id,
            parent_span_id=resolved_parent_span_id,
            span_name=span_name or component_name,
            span_kind=span_kind,
            phase=phase,
            event_type=event_type,
            component_type=component_type,
            component_name=component_name,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            step_id=step_id,
            step_name=step_name,
            step_order=step_order,
            call_id=call_id,
            caller=caller,
            callee=callee,
            status=status,
            error_message=error_message,
            input_payload=raw_input,
            output_payload=raw_output,
            # 调用方未提供摘要时，对完整载荷执行统一有界摘要。
            input_summary=input_summary or bounded_summary(raw_input),
            output_summary=output_summary or bounded_summary(raw_output),
            lineage=lineage or {},
            payload=payload or {},
            latency_ms=latency_ms,
            token_usage=token_usage or {},
            cost=cost or {},
            metrics=metrics or {},
            # 生命周期时间默认值只在对应阶段补齐，普通 event 不伪造开始或结束时间。
            started_at=started_at or (now if phase == "start" else None),
            finished_at=finished_at or (
                now if phase in {"end", "error"} else None
            ),
            created_at=now,
            model_name=model_name,
            tool_name=tool_name,
            agent_name=agent_name,
            tags=tags or [],
            metadata=metadata or {},
            extra=extra or {},
        )

        # 第七步：将已校验事件转为单行 UTF-8 JSON，保留中文。
        path = self._trace_path(run_id)
        row = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)

        # 第八步：锁住单次追加写，防止同一进程多线程事件内容交错。
        with self._lock:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(row + "\n")

        # 第九步：返回实际落盘对象，供调用方获得 event_id 和最终关联字段。
        return event
