"""Runtime adapter that executes one configured RAG profile."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Protocol

from rag.config.pipeline_config import PipelineConfigLoader
from rag.tools.rag_tool import RAGTool, RAGToolConfig

from .schemas import ExperimentConfig, RAGEvalSample


class ExperimentRuntime(Protocol):
    def run(self, sample: RAGEvalSample, experiment: ExperimentConfig) -> dict[str, Any]: ...
    def metadata(self) -> dict[str, Any]: ...
    def close(self) -> None: ...


class ParentChildExperimentRuntime:
    def __init__(
        self,
        *,
        project_root: Path,
        experiment: ExperimentConfig,
    ) -> None:
        self.project_root = project_root.resolve()
        self.experiment = experiment
        self.profile_path = PipelineConfigLoader().resolve_path(
            experiment.pipeline_config_file,
            project_root=self.project_root,
        )
        self.profile = PipelineConfigLoader().load(
            self.profile_path,
            project_root=self.project_root,
        )
        self.tool = RAGTool(
            RAGToolConfig(
                pipeline_config_file=str(self.profile_path),
                enable_llm=experiment.mode == "rag_answer",
            ),
            project_root=self.project_root,
        )

    def run(self, sample: RAGEvalSample, experiment: ExperimentConfig) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": sample.query,
            "expected_doc_ids": sample.expected_doc_ids,
            "expected_parent_chunk_ids": sample.expected_parent_chunk_ids,
            "expected_child_chunk_ids": sample.expected_child_chunk_ids,
            "expected_keywords": sample.expected_keywords,
            "filter_expr": sample.filter_expr,
            "keyword_doc_ids": sample.keyword_doc_ids,
            "eval_top_k": experiment.top_k,
            "generate_answer": experiment.mode == "rag_answer",
            "generation_params": experiment.generation_params,
            "return_full_record": True,
            "extra_metadata": {
                "experiment_id": experiment.experiment_id,
                "sample_id": sample.sample_id,
                "mode": experiment.mode,
                "seed": experiment.seed,
                "pipeline_config_file": experiment.pipeline_config_file,
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
        response = self.tool.run(payload)
        if not response.get("success"):
            raise RuntimeError(str(response.get("error") or "RAG tool failed"))
        data = dict(response.get("data") or {})
        data["runtime_metadata"] = dict(response.get("metadata") or {})
        return data

    def metadata(self) -> dict[str, Any]:
        configured_components = self.profile.canonical_dict()
        if getattr(self.tool, "_initialized", False):
            runtime_config = getattr(self.tool, "config", None)
            configured_components = getattr(
                runtime_config,
                "pipeline_component_metadata",
                configured_components,
            )
        return {
            "profile_id": self.profile.profile_id,
            "profile_version": self.profile.profile_version,
            "pipeline_config_file": str(self.profile_path),
            "pipeline_config_hash": self.profile.config_hash(),
            "components": configured_components,
        }

    def close(self) -> None:
        self.tool.engine = None
        self.tool = None  # type: ignore[assignment]
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


class DefaultExperimentRuntimeFactory:
    def build(
        self,
        *,
        project_root: Path,
        experiment: ExperimentConfig,
    ) -> ExperimentRuntime:
        return ParentChildExperimentRuntime(
            project_root=project_root,
            experiment=experiment,
        )
