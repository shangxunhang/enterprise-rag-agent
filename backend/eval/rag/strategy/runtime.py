# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：ExperimentRuntime、ParentChildExperimentRuntime、DefaultExperimentRuntimeFactory。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Runtime adapter that executes one static retrieval specification."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Protocol

from rag.config.static_retrieval import StaticRetrievalSpecLoader
from rag.runtime.retrieval_runtime import RetrievalRuntime, RetrievalRuntimeConfig

from .schemas import ExperimentConfig, RAGEvalSample


# 阅读注释（类）：封装 experiment 运行时，负责驱动实际运行流程并维护执行状态。
class ExperimentRuntime(Protocol):
    """封装 experiment 运行时，负责驱动实际运行流程并维护执行状态。"""
    # 阅读注释（函数）：执行 ExperimentRuntime 的主流程。
    def run(self, sample: RAGEvalSample, experiment: ExperimentConfig) -> dict[str, Any]: ...
    # 阅读注释（函数）：处理 元数据 相关逻辑。
    def metadata(self) -> dict[str, Any]: ...
    # 阅读注释（函数）：释放 ExperimentRuntime 持有的资源。
    def close(self) -> None: ...


# 阅读注释（类）：封装 父块 子块 experiment 运行时，负责驱动实际运行流程并维护执行状态。
class ParentChildExperimentRuntime:
    """封装 父块 子块 experiment 运行时，负责驱动实际运行流程并维护执行状态。"""
    # 阅读注释（函数）：初始化 ParentChildExperimentRuntime，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        project_root: Path,
        experiment: ExperimentConfig,
    ) -> None:
        """初始化 ParentChildExperimentRuntime，保存运行所需的依赖、配置或状态。

        参数:
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。
            experiment: experiment，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：project_root.resolve, resolve_path, PipelineConfigLoader, load, RAGTool, RAGToolConfig, str。
        """
        self.project_root = project_root.resolve()
        self.experiment = experiment
        self.spec_path = StaticRetrievalSpecLoader().resolve_path(
            experiment.static_retrieval_spec_file,
            project_root=self.project_root,
        )
        self.spec = StaticRetrievalSpecLoader().load(
            self.spec_path,
            project_root=self.project_root,
        )
        self.runtime = RetrievalRuntime(
            RetrievalRuntimeConfig(
                static_retrieval_spec_file=str(self.spec_path),
            ),
            project_root=self.project_root,
        )

    # 阅读注释（函数）：执行 ParentChildExperimentRuntime 的主流程。
    def run(self, sample: RAGEvalSample, experiment: ExperimentConfig) -> dict[str, Any]:
        """执行 ParentChildExperimentRuntime 的主流程。

        参数:
            sample: sample，具体约束请结合类型标注和调用方确认。
            experiment: experiment，具体约束请结合类型标注和调用方确认。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：bool, experiment.runtime_params.get, payload.update, experiment.runtime_params.items, startswith, str, self.tool.run, response.get。
        """
        payload: dict[str, Any] = {
            "query": sample.query,
            "expected_doc_ids": sample.expected_doc_ids,
            "expected_parent_chunk_ids": sample.expected_parent_chunk_ids,
            "expected_child_chunk_ids": sample.expected_child_chunk_ids,
            "expected_keywords": sample.expected_keywords,
            "filter_expr": sample.filter_expr,
            "keyword_doc_ids": sample.keyword_doc_ids,
            "eval_top_k": experiment.top_k,
            "return_full_record": True,
            "extra_metadata": {
                "experiment_id": experiment.experiment_id,
                "sample_id": sample.sample_id,
                "mode": experiment.mode,
                "seed": experiment.seed,
                "static_retrieval_spec_file": (
                    experiment.static_retrieval_spec_file
                ),
                "retrieval_plan_overrides": dict(
                    experiment.retrieval_plan_overrides
                ),
                "warmup": bool(experiment.runtime_params.get("_experiment_warmup", False)),
            },
        }
        payload.update(
            {
                key: value
                for key, value in experiment.runtime_params.items()
                if not str(key).startswith("_")
            }
        )
        response = self.runtime.retrieve(payload)
        data = dict(response)
        data["runtime_metadata"] = dict(
            (response.get("run_record") or {}).get("metadata") or {}
        )
        return data

    # 阅读注释（函数）：处理 元数据 相关逻辑。
    def metadata(self) -> dict[str, Any]:
        """处理 元数据 相关逻辑。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：self.spec.canonical_dict, getattr, str, self.spec.config_hash。
        """
        configured_components = self.spec.canonical_dict()
        if getattr(self.runtime, "_initialized", False):
            runtime_config = getattr(self.runtime, "config", None)
            configured_components = getattr(
                runtime_config,
                "static_retrieval_component_metadata",
                configured_components,
            )
        return {
            "static_spec_id": self.spec.spec_id,
            "static_spec_version": self.spec.spec_version,
            "static_retrieval_spec_file": str(self.spec_path),
            "static_retrieval_spec_hash": self.spec.config_hash(),
            "components": configured_components,
        }

    # 阅读注释（函数）：释放 ParentChildExperimentRuntime 持有的资源。
    def close(self) -> None:
        """释放 ParentChildExperimentRuntime 持有的资源。

        返回:
            None

        阅读提示:
            主要直接调用：gc.collect, torch.cuda.is_available, torch.cuda.empty_cache。
        """
        self.runtime.close()
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


# 阅读注释（类）：封装 default experiment 运行时 工厂，负责根据配置装配并返回运行实例。
class DefaultExperimentRuntimeFactory:
    """封装 default experiment 运行时 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：构建 DefaultExperimentRuntimeFactory。
    def build(
        self,
        *,
        project_root: Path,
        experiment: ExperimentConfig,
    ) -> ExperimentRuntime:
        """构建 DefaultExperimentRuntimeFactory。

        参数:
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。
            experiment: experiment，具体约束请结合类型标注和调用方确认。

        返回:
            ExperimentRuntime

        阅读提示:
            主要直接调用：ParentChildExperimentRuntime。
        """
        return ParentChildExperimentRuntime(
            project_root=project_root,
            experiment=experiment,
        )
