# =============================================================================
# 中文阅读说明：应用层主链编排模块。
# 主要定义：RunWorkspace。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Filesystem paths associated with one runtime execution."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


# 阅读注释（类）：封装 run workspace，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class RunWorkspace:
    """封装 run workspace，集中封装相关状态、依赖和行为。"""
    output_root: Path
    task_id: str
    run_id: str

    # 阅读注释（函数）：处理 tasks dir 相关逻辑。
    @property
    def tasks_dir(self) -> Path:
        """处理 tasks dir 相关逻辑。

        返回:
            Path
        """
        return self.output_root / "tasks"

    # 阅读注释（函数）：处理 runs dir 相关逻辑。
    @property
    def runs_dir(self) -> Path:
        """处理 runs dir 相关逻辑。

        返回:
            Path
        """
        return self.output_root / "runs"

    # 阅读注释（函数）：处理 captures dir 相关逻辑。
    @property
    def captures_dir(self) -> Path:
        """处理 captures dir 相关逻辑。

        返回:
            Path
        """
        return self.output_root / "captures"

    # 阅读注释（函数）：处理 任务 状态 路径 相关逻辑。
    @property
    def task_state_path(self) -> Path:
        """处理 任务 状态 路径 相关逻辑。

        返回:
            Path
        """
        return self.tasks_dir / f"{self.task_id}_state.jsonl"

    # 阅读注释（函数）：处理 Trace 路径 相关逻辑。
    @property
    def trace_path(self) -> Path:
        """处理 Trace 路径 相关逻辑。

        返回:
            Path
        """
        return self.runs_dir / f"{self.run_id}_trace.jsonl"

    @property
    def model_usage_path(self) -> Path:
        """Canonical aggregate model-usage artifact for this run."""

        return self.runs_dir / f"{self.run_id}_model_usage.json"

    def write_model_usage(self, payload: dict[str, Any]) -> None:
        """Atomically publish the completed run's model-usage snapshot."""

        target = self.model_usage_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    # 阅读注释（函数）：处理 raw capture 路径 相关逻辑。
    @property
    def raw_capture_path(self) -> Path:
        """处理 raw capture 路径 相关逻辑。

        返回:
            Path
        """
        return (
            self.captures_dir
            / "raw_interactions"
            / f"{self.run_id}_raw_interactions.jsonl"
        )

    # 阅读注释（函数）：处理 sft capture 路径 相关逻辑。
    @property
    def sft_capture_path(self) -> Path:
        """处理 sft capture 路径 相关逻辑。

        返回:
            Path
        """
        return (
            self.captures_dir
            / "sft_candidates"
            / f"{self.run_id}_sft_candidates.jsonl"
        )

    # 阅读注释（函数）：处理 评测 capture 路径 相关逻辑。
    @property
    def eval_capture_path(self) -> Path:
        """处理 评测 capture 路径 相关逻辑。

        返回:
            Path
        """
        return (
            self.captures_dir
            / "eval_samples"
            / f"{self.run_id}_eval_samples.jsonl"
        )

    # 阅读注释（函数）：处理 clean 相关逻辑。
    def clean(self) -> None:
        """处理 clean 相关逻辑。

        返回:
            None

        阅读提示:
            主要直接调用：path.exists, path.unlink。
        """
        for path in (
            self.trace_path,
            self.model_usage_path,
            self.raw_capture_path,
            self.sft_capture_path,
            self.eval_capture_path,
            self.task_state_path,
        ):
            if path.exists():
                path.unlink()

    # 阅读注释（函数）：处理 paths 相关逻辑。
    def paths(self) -> dict[str, str]:
        """处理 paths 相关逻辑。

        返回:
            dict[str, str]

        阅读提示:
            主要直接调用：str。
        """
        return {
            "trace": str(self.trace_path),
            "model_usage": str(self.model_usage_path),
            "task_state": str(self.task_state_path),
            "raw_interactions": str(self.raw_capture_path),
            "sft_candidates": str(self.sft_capture_path),
            "eval_samples": str(self.eval_capture_path),
        }
