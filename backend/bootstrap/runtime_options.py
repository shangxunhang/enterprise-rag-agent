"""Resolved runtime options; environment access is confined here."""

from __future__ import annotations

import os
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.config import AppSettings, _to_bool


def _optional_timeout_from_env(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc


def _optional_output_cap_from_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc


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
    section_max_concurrency: int = 1
    # Explicit per-execution capability declaration. Never inferred from DSN or
    # a global environment switch; callers lower their frozen profile here.
    durable_memory_required: bool = False
    # Explicit technical node guards; independent of the canonical Run deadline.
    execute_sections_timeout_seconds: float | None = None
    evaluate_document_gate_timeout_seconds: float | None = None
    # Prospective Verifier cognition only; absence preserves the 1024 default.
    verifier_max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        cap = self.verifier_max_output_tokens
        if cap is not None and (type(cap) is not int or cap <= 0):
            raise ValueError("VERIFIER_MAX_OUTPUT_TOKENS must be a positive integer")
        for name in (
            "execute_sections_timeout_seconds",
            "evaluate_document_gate_timeout_seconds",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            error = f"{name.upper()} must be a finite positive number"
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(error)
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite or value <= 0:
                raise ValueError(error)

    @classmethod
    def resolve(
        cls,
        settings: AppSettings,
        project_root: Path,
        *,
        enable_agent_self_rag: Optional[bool] = None,
        durable_memory_required: bool = False,
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
        raw_section_concurrency = os.getenv("SECTION_MAX_CONCURRENCY", "1")
        try:
            section_max_concurrency = int(raw_section_concurrency)
        except ValueError as exc:
            raise ValueError(
                "SECTION_MAX_CONCURRENCY must be an integer from 1 to 32"
            ) from exc
        if not 1 <= section_max_concurrency <= 32:
            raise ValueError(
                "SECTION_MAX_CONCURRENCY must be an integer from 1 to 32"
            )
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
            section_max_concurrency=section_max_concurrency,
            durable_memory_required=durable_memory_required,
            execute_sections_timeout_seconds=_optional_timeout_from_env(
                "EXECUTE_SECTIONS_TIMEOUT_SECONDS"
            ),
            evaluate_document_gate_timeout_seconds=_optional_timeout_from_env(
                "EVALUATE_DOCUMENT_GATE_TIMEOUT_SECONDS"
            ),
            verifier_max_output_tokens=_optional_output_cap_from_env(
                "VERIFIER_MAX_OUTPUT_TOKENS"
            ),
        )
