"""Resolved runtime options; environment access is confined here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.config import AppSettings, _to_bool


@dataclass(frozen=True)
class RuntimeOptions:
    """Runtime settings resolved once at the application boundary."""

    use_real_rag: bool
    rag_project_root: Path
    enable_agent_self_rag: bool
    enable_semantic_gate: bool
    semantic_gate_model_name: str
    rag_static_retrieval_spec_file: Path
    rag_intent_policy_file: Path
    rag_retrieval_gate_policy_file: Path
    grounded_generation_policy_file: Path = Path(
        "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
    )

    @classmethod
    def resolve(
        cls,
        settings: AppSettings,
        project_root: Path,
        *,
        enable_agent_self_rag: Optional[bool] = None,
    ) -> "RuntimeOptions":
        """Resolve environment-backed paths relative to ``project_root``."""
        raw_static_spec = Path(
            os.getenv(
                "RAG_STATIC_RETRIEVAL_SPEC_FILE",
                "backend/rag/config/static_retrieval_v1.yaml",
            )
        ).expanduser()
        static_retrieval_spec_file = (
            raw_static_spec
            if raw_static_spec.is_absolute()
            else project_root / raw_static_spec
        ).resolve()
        raw_intent_policy = Path(
            os.getenv(
                "RAG_INTENT_POLICY_FILE",
                "backend/rag/config/intent_policy_v1.yaml",
            )
        ).expanduser()
        intent_policy_file = (
            raw_intent_policy
            if raw_intent_policy.is_absolute()
            else project_root / raw_intent_policy
        ).resolve()
        raw_gate_policy = Path(
            os.getenv(
                "RAG_RETRIEVAL_GATE_POLICY_FILE",
                "backend/rag/config/retrieval_gate_policy_v1.yaml",
            )
        ).expanduser()
        retrieval_gate_policy_file = (
            raw_gate_policy
            if raw_gate_policy.is_absolute()
            else project_root / raw_gate_policy
        ).resolve()
        raw_generation_policy = Path(
            os.getenv(
                "GROUNDED_GENERATION_POLICY_FILE",
                "backend/apps/enterprise_document/config/grounded_generation_v1.yaml",
            )
        ).expanduser()
        grounded_generation_policy_file = (
            raw_generation_policy
            if raw_generation_policy.is_absolute()
            else project_root / raw_generation_policy
        ).resolve()
        return cls(
            use_real_rag=_to_bool(os.getenv("USE_REAL_RAG_TOOL", "true")),
            rag_project_root=Path(
                os.getenv("RAG_PROJECT_ROOT", str(project_root))
            ).resolve(),
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
            rag_static_retrieval_spec_file=static_retrieval_spec_file,
            rag_intent_policy_file=intent_policy_file,
            rag_retrieval_gate_policy_file=retrieval_gate_policy_file,
            grounded_generation_policy_file=grounded_generation_policy_file,
        )
