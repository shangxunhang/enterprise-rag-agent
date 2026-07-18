"""Resolved runtime options; environment access is confined here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.config import AppSettings, _to_bool


@dataclass(frozen=True)
class RuntimeOptions:
    use_real_rag: bool
    rag_project_root: Path
    rag_skip_rerank: bool
    retrieval_strategy: str
    enable_agent_self_rag: bool
    enable_semantic_gate: bool
    semantic_gate_model_name: str
    rag_pipeline_config_file: Path

    @classmethod
    def resolve(
        cls,
        settings: AppSettings,
        project_root: Path,
        *,
        retrieval_strategy: Optional[str] = None,
        enable_agent_self_rag: Optional[bool] = None,
    ) -> "RuntimeOptions":
        raw_pipeline_config = Path(
            os.getenv(
                "RAG_PIPELINE_CONFIG_FILE",
                "backend/rag/profiles/hybrid_v1.yaml",
            )
        ).expanduser()
        pipeline_config_file = (
            raw_pipeline_config
            if raw_pipeline_config.is_absolute()
            else project_root / raw_pipeline_config
        ).resolve()
        return cls(
            use_real_rag=_to_bool(os.getenv("USE_REAL_RAG_TOOL", "true")),
            rag_project_root=Path(
                os.getenv("RAG_PROJECT_ROOT", str(project_root))
            ).resolve(),
            rag_skip_rerank=_to_bool(os.getenv("RAG_SKIP_RERANK", "false")),
            retrieval_strategy=(
                retrieval_strategy
                or os.getenv("RAG_RETRIEVAL_STRATEGY")
                or os.getenv("RAG_RETRIEVAL_MODE")
                or "hybrid"
            ),
            enable_agent_self_rag=(
                enable_agent_self_rag
                if enable_agent_self_rag is not None
                else _to_bool(os.getenv("ENABLE_AGENT_SELF_RAG", "true"))
            ),
            enable_semantic_gate=_to_bool(
                os.getenv("ENABLE_SEMANTIC_GATE", "false")
            ),
            semantic_gate_model_name=(
                os.getenv("SEMANTIC_GATE_MODEL_NAME")
                or settings.default_model_name
            ),
            rag_pipeline_config_file=pipeline_config_file,
        )
