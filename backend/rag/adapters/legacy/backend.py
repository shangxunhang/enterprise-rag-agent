"""Lazy access to the existing rag-template implementation."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any, Callable

from rag.routing.runtime import AdaptiveProfileRouterRuntime
from rag.routing.schema import peek_config_schema_version


class LegacyRAGBackend:
    def __init__(
        self,
        rag_project_root: str | Path,
        *,
        generate_answer: bool = False,
        skip_rerank: bool = False,
        pipeline_config_file: str | Path | None = None,
        tool_builder: Callable[[Path], Any] | None = None,
    ) -> None:
        self.rag_project_root = Path(rag_project_root).resolve()
        self.backend_root = self.rag_project_root / "backend"
        self.generate_answer = generate_answer
        self.skip_rerank = skip_rerank
        if pipeline_config_file is None:
            self.pipeline_config_file = None
        else:
            candidate = Path(pipeline_config_file).expanduser()
            self.pipeline_config_file = (
                candidate.resolve()
                if candidate.is_absolute()
                else (self.rag_project_root / candidate).resolve()
            )
        self._rag_tool: Any | None = None
        self._routed_tools: dict[str, Any] = {}
        self._adaptive_runtime: AdaptiveProfileRouterRuntime | None = None
        self._tool_builder = tool_builder
        for path in (self.backend_root, self.rag_project_root):
            path_str = str(path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)

    def _resolved_pipeline_config_file(self) -> Path:
        raw = self.pipeline_config_file or os.getenv(
            "RAG_PIPELINE_CONFIG_FILE",
            "backend/rag/profiles/hybrid_v1.yaml",
        )
        candidate = Path(raw).expanduser()
        return (
            candidate.resolve()
            if candidate.is_absolute()
            else (self.rag_project_root / candidate).resolve()
        )

    def is_adaptive_profile_router(self) -> bool:
        return (
            peek_config_schema_version(self._resolved_pipeline_config_file())
            == "adaptive_profile_router_config_v1"
        )

    def adaptive_runtime(self) -> AdaptiveProfileRouterRuntime:
        if self._adaptive_runtime is None:
            self._adaptive_runtime = AdaptiveProfileRouterRuntime(
                config_file=self._resolved_pipeline_config_file(),
                project_root=self.rag_project_root,
            )
        return self._adaptive_runtime

    def _build_tool_for_profile(self, profile_file: Path) -> Any:
        if self._tool_builder is not None:
            return self._tool_builder(profile_file)
        from rag.tools.rag_tool import RAGTool, RAGToolConfig

        config = RAGToolConfig(
            parent_file="data/processed/parent_child_chunks/parent_chunks.jsonl",
            child_file="data/processed/parent_child_chunks/child_chunks.jsonl",
            db_file="data/processed/vector_store/milvus_parent_child.db",
            capture_output="data/processed/runs/rag_tool_runs.jsonl",
            pipeline_config_file=str(profile_file),
            enable_llm=self.generate_answer,
            skip_rerank=self.skip_rerank,
        )
        return RAGTool(config, project_root=self.rag_project_root)

    def _routed_tool(self, profile_id: str, profile_file: Path) -> Any:
        if profile_id not in self._routed_tools:
            self._routed_tools[profile_id] = self._build_tool_for_profile(profile_file)
        return self._routed_tools[profile_id]

    def tool(self) -> Any:
        if self.is_adaptive_profile_router():
            runtime = self.adaptive_runtime()
            return self._routed_tool(
                runtime.default_profile_id,
                runtime.profile_path(runtime.default_profile_id),
            )
        if self._rag_tool is None:
            self._rag_tool = self._build_tool_for_profile(
                self._resolved_pipeline_config_file()
            )
        return self._rag_tool

    @staticmethod
    def _attach_route_result(
        result: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        output = dict(result or {})
        data = dict(output.get("data") or {})
        metadata = dict(output.get("metadata") or {})
        data["adaptive_profile_router"] = decision
        data["original_retrieval_strategy"] = "adaptive_profile_router"
        data["effective_retrieval_strategy"] = decision.get("selected_profile_id")
        metadata["adaptive_profile_router"] = decision
        metadata["original_retrieval_strategy"] = "adaptive_profile_router"
        metadata["effective_retrieval_strategy"] = decision.get("selected_profile_id")
        output["data"] = data
        output["metadata"] = metadata
        return output

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_adaptive_profile_router():
            return self.tool().run(payload)

        runtime = self.adaptive_runtime()
        decision = runtime.route(payload)
        routed_payload = copy.deepcopy(payload)
        extra = dict(routed_payload.get("extra_metadata") or {})
        decision_dict = decision.to_dict()
        extra["adaptive_profile_router"] = decision_dict
        extra["adaptive_profile_router_validation"] = runtime.validation_report()
        routed_payload["extra_metadata"] = extra
        # The external router already selected the concrete profile. Avoid
        # invoking the legacy in-pipeline advisory router a second time.
        routed_payload["retrieval_strategy"] = decision.selected_profile_id

        selected_tool = self._routed_tool(
            decision.selected_profile_id,
            runtime.profile_path(decision.selected_profile_id),
        )
        result = selected_tool.run(routed_payload)
        return self._attach_route_result(result, decision_dict)
