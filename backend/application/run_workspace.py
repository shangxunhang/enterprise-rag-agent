"""定义单次任务运行所使用的文件系统工作区。

本模块根据 ``task_id``、``run_id`` 和输出根目录推导标准产物路径，并负责模型
用量快照的原子读写以及本次运行产物的清理。它不创建任务、不执行工作流，也不
解释产物中的业务内容。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RunWorkspace:
    """描述一次运行在本地文件系统中的标准目录与产物位置。

    Attributes:
        output_root: 所有任务状态、运行轨迹和采集产物的公共输出根目录。
        task_id: 业务任务标识，用于定位可跨多次运行共享的任务状态文件。
        run_id: 单次执行标识，用于隔离本次运行的轨迹、用量和采集文件。

    对象被声明为不可变，保证一次执行期间所有路径推导结果保持稳定。各路径属性
    只负责计算路径，不会提前创建目录；真正写入文件时再按需创建父目录。
    """

    output_root: Path
    task_id: str
    run_id: str

    @property
    def tasks_dir(self) -> Path:
        """返回存放任务级持久状态的目录。"""

        # 任务状态可能跨多个 run 复用，因此按任务维度独立保存。
        return self.output_root / "tasks"

    @property
    def runs_dir(self) -> Path:
        """返回存放单次运行轨迹和聚合产物的目录。"""

        # 目录中的文件通过 run_id 命名，避免同一任务的多次执行互相覆盖。
        return self.output_root / "runs"

    @property
    def captures_dir(self) -> Path:
        """返回存放训练与评测数据采集结果的目录。"""

        # 原始交互、SFT 候选和评测样本会在该目录下继续按用途分层。
        return self.output_root / "captures"

    @property
    def task_state_path(self) -> Path:
        """返回当前任务的状态事件流文件路径。"""

        # 文件按 task_id 命名，使任务状态能够跨 run 追踪和恢复。
        return self.tasks_dir / f"{self.task_id}_state.jsonl"

    @property
    def trace_path(self) -> Path:
        """返回当前运行的执行轨迹文件路径。"""

        # 轨迹按 run_id 隔离，便于定位一次具体执行的节点、分支和异常。
        return self.runs_dir / f"{self.run_id}_trace.jsonl"

    @property
    def model_usage_path(self) -> Path:
        """返回当前运行的模型用量聚合快照路径。"""

        # 用量快照是单个 JSON 对象，而不是追加写入的 JSONL 事件流。
        return self.runs_dir / f"{self.run_id}_model_usage.json"

    def write_model_usage(self, payload: dict[str, Any]) -> None:
        """以原子替换方式发布当前运行的模型用量快照。

        Args:
            payload: 可 JSON 序列化的完整快照，通常包含任务标识、运行标识和
                聚合后的 ``usage`` 字段。

        数据先完整写入同目录临时文件，再通过 ``os.replace`` 原子替换正式文件，
        避免进程中断时读取方看到只写入一部分的快照。
        """

        # 第一步：确定正式产物路径并确保父目录存在。
        target = self.model_usage_path
        target.parent.mkdir(parents=True, exist_ok=True)

        # 第二步：为临时文件附加随机后缀，避免并发运行发生名称冲突。
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            # 第三步：完整序列化快照；保留中文并格式化，便于人工检查。
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            # 第四步：原子替换正式文件，读取方只会看到旧版本或完整新版本。
            os.replace(temporary, target)
        finally:
            # 第五步：序列化或替换失败时，删除可能残留的临时文件。
            if temporary.exists():
                temporary.unlink()

    def read_model_usage(self) -> dict[str, Any]:
        """读取并校验可用于持久化恢复的模型用量数据。

        Returns:
            ``usage`` 字典的浅拷贝；不存在历史快照时返回空字典。

        Raises:
            ValueError: 顶层不是 JSON 对象、任务或运行标识不匹配，或者
                ``usage`` 字段不是对象时抛出。
        """

        # 第一步：定位快照；没有文件表示当前运行无需恢复历史用量。
        target = self.model_usage_path
        if not target.is_file():
            return {}

        # 第二步：解析完整 JSON 文档并校验顶层数据契约。
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("model usage artifact must contain a JSON object")

        # 第三步：核对任务和运行身份，防止恢复了其他执行的用量数据。
        if (
            payload.get("task_id") != self.task_id
            or payload.get("run_id") != self.run_id
        ):
            raise ValueError("model usage artifact identity mismatch")

        # 第四步：提取聚合用量并保证返回类型稳定。
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("model usage artifact must contain a usage object")

        # 第五步：返回浅拷贝，避免调用方直接修改解析后的内部字典引用。
        return dict(usage)

    @property
    def raw_capture_path(self) -> Path:
        """返回当前运行的原始模型交互采集文件路径。"""

        # 原始交互单独保存，供审计、重放和后续数据清洗使用。
        return (
            self.captures_dir
            / "raw_interactions"
            / f"{self.run_id}_raw_interactions.jsonl"
        )

    @property
    def sft_capture_path(self) -> Path:
        """返回当前运行的监督微调候选样本文件路径。"""

        # SFT 候选与原始交互隔离，表明其已经经过候选样本构造步骤。
        return (
            self.captures_dir
            / "sft_candidates"
            / f"{self.run_id}_sft_candidates.jsonl"
        )

    @property
    def eval_capture_path(self) -> Path:
        """返回当前运行的评测样本采集文件路径。"""

        # 评测样本独立存放，避免与训练候选混用造成数据泄漏。
        return (
            self.captures_dir
            / "eval_samples"
            / f"{self.run_id}_eval_samples.jsonl"
        )

    def clean(self) -> None:
        """删除当前运行可安全重建的产物文件。

        任务状态文件不在清理范围内，因为它可能承载跨运行恢复所需的持久状态。
        不存在的文件会被直接跳过，因此该操作可以重复执行。
        """

        # 第一步：枚举仅属于当前 run、能够通过重新执行生成的全部产物。
        run_artifacts = (
            self.trace_path,
            self.model_usage_path,
            self.raw_capture_path,
            self.sft_capture_path,
            self.eval_capture_path,
        )

        # 第二步：逐个检查并删除，避免对不存在的路径调用 unlink。
        for path in run_artifacts:
            if path.exists():
                path.unlink()

    def paths(self) -> dict[str, str]:
        """返回当前工作区关键产物的可序列化路径映射。

        Returns:
            键为稳定产物名称、值为字符串路径的字典，可直接用于日志、接口响应
            或端到端报告。
        """

        # 将 Path 统一转为字符串，避免上层序列化器依赖 Path 的特殊处理。
        return {
            "trace": str(self.trace_path),
            "model_usage": str(self.model_usage_path),
            "task_state": str(self.task_state_path),
            "raw_interactions": str(self.raw_capture_path),
            "sft_candidates": str(self.sft_capture_path),
            "eval_samples": str(self.eval_capture_path),
        }
