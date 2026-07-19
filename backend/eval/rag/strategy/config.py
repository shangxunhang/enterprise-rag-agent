# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：ExperimentConfigLoader。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Load strict YAML/JSON experiment matrices relative to the project root."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from rag.config.static_retrieval import StaticRetrievalSpecLoader

from .schemas import ExperimentMatrixConfig


# 阅读注释（类）：封装 experiment 配置 loader，集中封装相关状态、依赖和行为。
class ExperimentConfigLoader:
    """封装 experiment 配置 loader，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 ExperimentConfigLoader，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 ExperimentConfigLoader，保存运行所需的依赖、配置或状态。

        返回:
            None

        阅读提示:
            主要直接调用：PipelineConfigLoader。
        """
        self._path_loader = StaticRetrievalSpecLoader()

    # 阅读注释（函数）：解析并确定 路径。
    def resolve_path(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> Path:
        """解析并确定 路径。

        参数:
            path: 目标文件或目录路径。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            Path

        阅读提示:
            主要直接调用：self._path_loader.resolve_path。
        """
        return self._path_loader.resolve_path(path, project_root=project_root)

    # 阅读注释（函数）：加载 ExperimentConfigLoader。
    def load(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> tuple[ExperimentMatrixConfig, Path, str]:
        """加载 ExperimentConfigLoader。

        参数:
            path: 目标文件或目录路径。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            tuple[ExperimentMatrixConfig, Path, str]

        阅读提示:
            主要直接调用：self.resolve_path, config_path.is_file, FileNotFoundError, config_path.read_text, config_path.suffix.lower, yaml.safe_load, json.loads, ValueError。
        """
        config_path = self.resolve_path(path, project_root=project_root)
        if not config_path.is_file():
            raise FileNotFoundError(f"experiment config not found: {config_path}")
        raw = config_path.read_text(encoding="utf-8-sig")
        if config_path.suffix.lower() in {".yaml", ".yml"}:
            payload: Any = yaml.safe_load(raw)
        elif config_path.suffix.lower() == ".json":
            payload = json.loads(raw)
        else:
            raise ValueError("experiment config must use .yaml, .yml or .json")
        if not isinstance(payload, dict):
            raise ValueError("experiment config root must be an object")
        matrix = ExperimentMatrixConfig.model_validate(payload)
        canonical = json.dumps(
            matrix.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return matrix, config_path, hashlib.sha256(canonical).hexdigest()
