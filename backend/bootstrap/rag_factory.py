"""RAG Tool composition."""

from __future__ import annotations

from bootstrap.runtime_options import RuntimeOptions
from rag.services.legacy_rag_service import LegacyRAGService
from tools.fake_rag_tool import FakeRAGTool
from tools.real_rag_tool import RealRAGTool
from tools.tool_registry import ToolRegistry


class RAGToolFactory:
    def build_registry(self, options: RuntimeOptions) -> tuple[ToolRegistry, str]:
        print(
            "[RAG Runtime] "
            f"use_real_rag={options.use_real_rag}, "
            f"RAG_PROJECT_ROOT={str(options.rag_project_root)!r}"
        )
        registry = ToolRegistry()
        if options.use_real_rag:
            registry.register(
                RealRAGTool(
                    rag_service=LegacyRAGService(
                        rag_project_root=options.rag_project_root,
                        generate_answer=False,
                        skip_rerank=options.rag_skip_rerank,
                        pipeline_config_file=options.rag_pipeline_config_file,
                    )
                )
            )
            return registry, "RealRAGTool"
        registry.register(FakeRAGTool())
        return registry, "FakeRAGTool"
