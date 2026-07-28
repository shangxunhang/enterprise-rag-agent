"""把运行期原始交互和训练/评测候选记录写入分类 JSONL。

记录器只能创建 raw、candidate 和 runtime_rejected 三种初始阶段，不能直接把数据提升为
training_ready 或 eval_ready；正式生命周期晋级必须由 ``DataCaptureLifecycleStore``
完成。处于受控执行尝试时，外部写入会延迟到提交阶段，并使用语义指纹保证幂等。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from core.persistence.crash_safe_jsonl import (
    CrashSafeJsonlRecordSink,
    canonical_json_sha256,
)
from core.runtime.clock import Clock, SystemClock
from core.runtime.execution_control import current_execution_control
from core.runtime.ids import IdGenerator, UuidIdGenerator
from schemas.data_capture import DataCaptureRecordSchema


def data_capture_semantic_projection(record: dict[str, Any]) -> dict[str, Any]:
    """提取决定采集业务事实的稳定字段投影。

    Args:
        record: 完整采集记录字典。

    Returns:
        删除写入时间和执行尝试元数据后的浅层独立投影。

    ``created_at`` 和 ``metadata.execution_attempt`` 只描述何时、由哪个重试尝试执行了
    写入，不改变采集内容、来源血缘、模型输出或生命周期状态，因此不参与幂等冲突判断。
    """

    # 第一步：复制顶层字典，避免在计算指纹时修改调用方记录。
    projection = dict(record)

    # 第二步：删除每次写入都会变化、但不属于业务事实的创建时间。
    projection.pop("created_at", None)

    # 第三步：复制元数据并移除当前执行尝试信息。
    metadata = dict(projection.get("metadata") or {})
    metadata.pop("execution_attempt", None)
    projection["metadata"] = metadata

    return projection


class JsonlDataCaptureRecorder:
    """按采集类别持久化标准数据记录。

    Attributes:
        output_dir: 所有采集类别子目录的公共根目录。
        clock: 为采集记录生成创建时间的可替换时钟。
        id_generator: 在没有受控 side-effect ID 时生成记录标识的可替换生成器。

    每个类别和 run 对应一个 JSONL 文件，例如
    ``data/captures/sft_candidates/run_001_sft_candidates.jsonl``。
    """

    # 运行时允许创建的持久化类别。生命周期阶段与类别是两个维度：类别说明用途，
    # capture_stage 说明同一记录当前的数据治理状态。
    VALID_CAPTURE_TYPES = {
        "raw_interactions",
        "sft_candidates",
        "dpo_candidates",
        "eval_samples",
        "human_reviews",
        "rejected",
    }

    def __init__(
        self,
        output_dir: str | Path = "data/captures",
        *,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        """创建 JSONL 数据采集记录器并初始化分类目录。

        Args:
            output_dir: 所有采集文件的根目录。
            clock: 自定义时钟；未提供时使用系统 UTC 时钟。
            id_generator: 自定义标识生成器；未提供时使用 UUID 实现。
        """

        # 第一步：统一根目录类型并补齐可测试的时间与标识依赖。
        self.output_dir = Path(output_dir)
        self.clock = clock or SystemClock()
        self.id_generator = id_generator or UuidIdGenerator()

        # 第二步：创建公共根目录。
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 第三步：预先创建所有允许类别目录，使 record 只处理具体记录写入。
        for capture_type in self.VALID_CAPTURE_TYPES:
            (self.output_dir / capture_type).mkdir(parents=True, exist_ok=True)

    def _now_iso(self) -> str:
        """返回统一时钟生成的当前 UTC ISO 时间。"""

        return self.clock.now_iso()

    def _new_id(self, prefix: str) -> str:
        """生成带业务前缀的新标识。

        Args:
            prefix: 标识用途前缀。

        Returns:
            注入生成器创建的标识字符串。
        """

        return self.id_generator.new_id(prefix)

    def _capture_path(self, capture_type: str, run_id: str) -> Path:
        """返回指定类别和运行的标准采集文件路径。

        Args:
            capture_type: 已登记的数据采集类别。
            run_id: 单次运行标识。

        Returns:
            ``<output_dir>/<type>/<run_id>_<type>.jsonl``。

        Raises:
            ValueError: 类别不在允许集合中时抛出，防止任意路径或目录写入。
        """

        # 第一步：以固定白名单校验类别，目录名称不能由任意外部字符串决定。
        if capture_type not in self.VALID_CAPTURE_TYPES:
            raise ValueError(f"Unsupported capture_type: {capture_type}")

        # 第二步：按类别隔离不同用途数据，并按 run 隔离执行来源。
        return self.output_dir / capture_type / f"{run_id}_{capture_type}.jsonl"

    def record(
        self,
        capture_type: str,
        task_id: str,
        run_id: str,
        capture_stage: Literal[
            "raw", "candidate", "runtime_rejected"
        ] = "candidate",
        user_input: Optional[str] = None,
        rewritten_query: Optional[str] = None,
        rag_context: Optional[Dict[str, Any]] = None,
        structured_facts: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None,
        model_output: Optional[str] = None,
        generated_draft_output: Optional[str] = None,
        deliverable_output: Optional[str] = None,
        human_feedback: Optional[Dict[str, Any]] = None,
        label: Optional[Dict[str, Any]] = None,
        quality_score: Optional[float] = None,
        quality_flags: Optional[List[str]] = None,
        need_human_review: bool = True,
        trace_id: Optional[str] = None,
        source_component_type: Optional[str] = None,
        source_component_name: Optional[str] = None,
        source_event_type: Optional[str] = None,
        related_trace_event_ids: Optional[List[str]] = None,
        related_file_ids: Optional[List[str]] = None,
        lineage_refs: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
        retrieved_chunks: Optional[List[Dict[str, Any]]] = None,
        citations: Optional[List[Dict[str, Any]]] = None,
        rag_trace: Optional[Dict[str, Any]] = None,
        prompt_info: Optional[Dict[str, Any]] = None,
        model_info: Optional[Dict[str, Any]] = None,
        eval_sample: Optional[Dict[str, Any]] = None,
    ) -> DataCaptureRecordSchema:
        """构造并按当前执行提交策略持久化一条采集记录。

        Args:
            capture_type: 数据用途类别，例如 raw_interactions 或 sft_candidates。
            task_id: 产生数据的业务任务标识。
            run_id: 产生数据的单次运行标识。
            capture_stage: 运行时允许创建的初始治理阶段。
            user_input: 原始用户输入。
            rewritten_query: 改写后的检索查询。
            rag_context: RAG 上下文兼容快照。
            structured_facts: 从输入和证据确认的结构化事实。
            prompt: 实际发送给模型的提示词文本。
            model_output: 模型原始或规范化输出。
            generated_draft_output: 未通过最终门禁的草稿文本。
            deliverable_output: 已通过业务门禁的交付文本。
            human_feedback: 人工评分、意见和编辑信息。
            label: 监督标签或偏好标签。
            quality_score: 当前质量综合分数。
            quality_flags: 质量规则标记集合。
            need_human_review: 当前记录是否仍需人工判断。
            trace_id: 与运行轨迹关联的 trace 标识。
            source_component_type: 产生数据的组件类别。
            source_component_name: 产生数据的具体组件名称。
            source_event_type: 触发采集的事件类别。
            related_trace_event_ids: 与记录直接关联的轨迹事件标识。
            related_file_ids: 与记录关联的原始文件标识。
            lineage_refs: 原始输入、证据、调用和产物血缘。
            metadata: 数据集批次、规则版本等稳定元数据。
            extra: 尚未进入稳定契约的扩展字段。
            retrieved_chunks: 结构化召回和重排片段。
            citations: 证据来源和引用绑定信息。
            rag_trace: RAG 查询、召回、重排和质量轨迹。
            prompt_info: 模板 ID、版本和渲染信息。
            model_info: 模型角色、供应商、参数和用量信息。
            eval_sample: 面向离线评测器的标准样本结构。

        Returns:
            已立即写入或登记为延迟提交的 ``DataCaptureRecordSchema``。

        Raises:
            ValueError: 调用方尝试由运行时记录器直接创建晋级阶段时抛出。
        """

        # 第一步：运行时只允许创建初始或拒绝记录；晋级必须经过生命周期仓库校验。
        if capture_stage not in {"raw", "candidate", "runtime_rejected"}:
            raise ValueError(
                "runtime recorder cannot create promoted lifecycle state; "
                "use DataCaptureLifecycleStore.transition"
            )

        # 第二步：读取当前节点尝试的执行控制上下文；普通调用可能没有该上下文。
        execution_control = current_execution_control()

        # 第三步：受控执行中为本次外部副作用预留确定性标识，支持重试幂等。
        side_effect_id = (
            execution_control.reserve_side_effect_id(
                f"data_capture:{capture_type}:{capture_stage}"
            )
            if execution_control is not None
            else None
        )

        # 第四步：复制调用方元数据，并在受控执行中写入尝试与提交策略说明。
        effective_metadata = dict(metadata or {})
        if execution_control is not None:
            effective_metadata["execution_attempt"] = {
                "execution_id": execution_control.execution_id,
                "commit_scope_id": execution_control.commit_scope_id,
                "side_effect_id": side_effect_id,
                "external_commit_policy": "attempt_scoped_deferred_v1",
                # candidate 只有当前尝试成功才可提交，防止失败尝试污染训练候选。
                "requires_attempt_success": capture_stage == "candidate",
            }

        # 第五步：构造严格采集 Schema。运行时新记录始终不可直接用于训练或评测。
        record = DataCaptureRecordSchema(
            record_id=(
                f"capture_{side_effect_id.removeprefix('side_effect_')}"
                if side_effect_id is not None
                else self._new_id("capture")
            ),
            capture_type=capture_type,
            capture_stage=capture_stage,
            task_id=task_id,
            run_id=run_id,
            trace_id=trace_id,
            source_component_type=source_component_type,
            source_component_name=source_component_name,
            source_event_type=source_event_type,
            user_input=user_input,
            rewritten_query=rewritten_query,
            rag_context=rag_context or {},
            structured_facts=structured_facts or {},
            prompt=prompt,
            model_output=model_output,
            generated_draft_output=generated_draft_output,
            deliverable_output=deliverable_output,
            human_feedback=human_feedback or {},
            label=label or {},
            quality_score=quality_score,
            quality_flags=quality_flags or [],
            need_human_review=need_human_review,
            is_usable_for_training=False,
            is_usable_for_eval=False,
            related_trace_event_ids=related_trace_event_ids or [],
            related_file_ids=related_file_ids or [],
            lineage_refs=lineage_refs or {},
            created_at=self._now_iso(),
            metadata=effective_metadata,
            extra=extra or {},
            retrieved_chunks=retrieved_chunks or [],
            citations=citations or [],
            rag_trace=rag_trace or {},
            prompt_info=prompt_info or {},
            model_info=model_info or {},
            eval_sample=eval_sample or {},
        )

        # 第六步：定位分类文件并创建具备崩溃恢复和重复身份检查能力的 JSONL sink。
        path = self._capture_path(capture_type, run_id)
        sink = CrashSafeJsonlRecordSink(path)

        # 第七步：生成紧凑稳定 JSON 文本和只覆盖业务事实的语义指纹。
        record_data = record.model_dump()
        serialized = json.dumps(
            record_data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        semantic_fingerprint = canonical_json_sha256(
            data_capture_semantic_projection(record_data)
        )

        def append_record() -> None:
            """以记录身份和语义指纹执行一次幂等追加写入。"""

            # 同一 record_id 已存在且语义一致时不重复追加；语义冲突由 sink 拒绝。
            sink.append_record_once(
                serialized,
                id_field="record_id",
                identity=record.record_id,
                semantic_projection=data_capture_semantic_projection,
                semantic_fingerprint=semantic_fingerprint,
            )

        # 第八步：没有受控执行上下文时立即持久化。
        if execution_control is None:
            append_record()
        else:
            # 第九步：有执行上下文时登记延迟外部提交，由尝试终态决定是否真正落盘。
            execution_control.defer_external_commit(
                append_record,
                description=f"data_capture:{capture_type}:{record.record_id}",
                requires_success=capture_stage == "candidate",
                side_effect_id=side_effect_id,
                semantic_fingerprint=semantic_fingerprint,
            )

        # 第十步：返回规范记录；受控执行中此时可能尚未实际写入文件。
        return record
