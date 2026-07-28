"""把一份静态检索规范装配成可执行的策略评测运行时。"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Protocol

from rag.config.static_retrieval import StaticRetrievalSpecLoader
from rag.runtime.retrieval_runtime import RetrievalRuntime, RetrievalRuntimeConfig

from .schemas import ExperimentConfig, RAGEvalSample


class ExperimentRuntime(Protocol):
    """定义策略评测 Runner 所依赖的最小运行时端口。"""

    def run(
        self,
        sample: RAGEvalSample,
        experiment: ExperimentConfig,
    ) -> dict[str, Any]:
        """执行一个样本并返回完整运行记录。"""
        ...

    def metadata(self) -> dict[str, Any]:
        """返回本运行时实际使用的静态配置和组件血缘。"""
        ...

    def close(self) -> None:
        """释放模型、数据库连接和设备缓存等资源。"""
        ...


class ParentChildExperimentRuntime:
    """基于生产 ``RetrievalRuntime`` 执行父子块策略实验。

    构造阶段解析并加载静态检索规范，但底层资源仍由 ``RetrievalRuntime`` 按其自身
    生命周期延迟初始化。每个实验实例绑定一份静态规范，避免样本之间动态切换组件
    导致配置和实际执行不一致。
    """

    def __init__(
        self,
        *,
        project_root: Path,
        experiment: ExperimentConfig,
    ) -> None:
        """解析实验静态规范并创建生产检索运行时。

        Args:
            project_root: 用于解析相对配置路径的项目根目录。
            experiment: 当前实验配置。
        """

        # 第一步：固定绝对项目根目录和实验对象，后续样本共享同一实验上下文。
        self.project_root = project_root.resolve()
        self.experiment = experiment

        # 第二步：按生产规则解析并加载静态检索规范。
        spec_loader = StaticRetrievalSpecLoader()
        self.spec_path = spec_loader.resolve_path(
            experiment.static_retrieval_spec_file,
            project_root=self.project_root,
        )
        self.spec = spec_loader.load(
            self.spec_path,
            project_root=self.project_root,
        )

        # 第三步：把同一规范路径交给生产 RetrievalRuntime，避免评测层复制装配逻辑。
        self.runtime = RetrievalRuntime(
            RetrievalRuntimeConfig(
                static_retrieval_spec_file=str(self.spec_path),
            ),
            project_root=self.project_root,
        )

    def run(
        self,
        sample: RAGEvalSample,
        experiment: ExperimentConfig,
    ) -> dict[str, Any]:
        """使用当前实验配置执行一条黄金样本。

        Args:
            sample: 包含查询和黄金文档/块/关键词的评测样本。
            experiment: 当前实验配置；通常与构造时保存的对象相同。

        Returns:
            生产检索运行时的完整响应，并额外暴露 ``runtime_metadata`` 便于报告聚合。
        """

        # 第一步：构造评测专用请求，显式传递黄金标签、top_k 和完整记录开关。
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
                "warmup": bool(
                    experiment.runtime_params.get("_experiment_warmup", False)
                ),
            },
        }

        # 第二步：注入非私有 runtime_params；下划线前缀只用于评测控制，不下传生产链。
        payload.update(
            {
                key: value
                for key, value in experiment.runtime_params.items()
                if not str(key).startswith("_")
            }
        )

        # 第三步：调用生产检索入口并复制响应，避免直接修改运行时返回对象。
        response = self.runtime.retrieve(payload)
        data = dict(response)

        # 第四步：从完整 run_record 中提取运行时元数据，供实验报告审计实际组件配置。
        data["runtime_metadata"] = dict(
            (response.get("run_record") or {}).get("metadata") or {}
        )
        return data

    def metadata(self) -> dict[str, Any]:
        """返回静态规范身份、哈希和实际组件配置。

        在运行时尚未初始化时使用静态规范 canonical 内容；初始化后优先读取运行时
        已解析的组件元数据，使报告反映真正执行的配置而不是仅反映输入文件。
        """

        # 第一步：以静态规范 canonical 投影作为默认组件配置。
        configured_components = self.spec.canonical_dict()

        # 第二步：若运行时已初始化，优先使用其最终解析后的组件元数据。
        if getattr(self.runtime, "_initialized", False):
            runtime_config = getattr(self.runtime, "config", None)
            configured_components = getattr(
                runtime_config,
                "static_retrieval_component_metadata",
                configured_components,
            )

        # 第三步：返回可重复性所需的规范身份、路径、内容哈希和组件详情。
        return {
            "static_spec_id": self.spec.spec_id,
            "static_spec_version": self.spec.spec_version,
            "static_retrieval_spec_file": str(self.spec_path),
            "static_retrieval_spec_hash": self.spec.config_hash(),
            "components": configured_components,
        }

    def close(self) -> None:
        """关闭检索运行时并尽力释放 Python 与 CUDA 缓存。"""

        # 第一步：由生产运行时关闭其持有的索引、模型和连接资源。
        self.runtime.close()

        # 第二步：触发 Python 垃圾回收，尽早释放大对象引用。
        gc.collect()

        # 第三步：在 PyTorch 可用且存在 CUDA 设备时清空缓存；缺少依赖不影响关闭流程。
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


class DefaultExperimentRuntimeFactory:
    """为每个实验构建默认父子块检索运行时。"""

    def build(
        self,
        *,
        project_root: Path,
        experiment: ExperimentConfig,
    ) -> ExperimentRuntime:
        """根据项目根目录和实验配置创建独立运行时实例。"""

        # 每个实验独立持有运行时，便于在实验结束后完整关闭并隔离资源。
        return ParentChildExperimentRuntime(
            project_root=project_root,
            experiment=experiment,
        )
