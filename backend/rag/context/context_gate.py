"""Select a context packing strategy from real context requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from context_manager.token_estimator import DeterministicTokenEstimator
from rag.context.context_packer import ContextPack, ContextPacker


@dataclass(frozen=True)
class ContextRequirements:
    model_context_window: int
    prompt_reserved_tokens: int
    section_token_budget: int
    max_evidence_items: int
    max_context_chars: int = 12000

    @property
    def evidence_token_budget(self) -> int:
        model_available = max(
            1,
            int(self.model_context_window) - int(self.prompt_reserved_tokens),
        )
        return max(1, min(model_available, int(self.section_token_budget)))

    @classmethod
    def from_mapping(
        cls,
        value: dict[str, Any] | None,
        *,
        defaults: "ContextRequirements",
    ) -> "ContextRequirements":
        raw = dict(value or {})
        return cls(
            model_context_window=max(
                512,
                int(raw.get("model_context_window", defaults.model_context_window)),
            ),
            prompt_reserved_tokens=max(
                0,
                int(raw.get("prompt_reserved_tokens", defaults.prompt_reserved_tokens)),
            ),
            section_token_budget=max(
                256,
                int(raw.get("section_token_budget", defaults.section_token_budget)),
            ),
            max_evidence_items=max(
                1,
                int(raw.get("max_evidence_items", defaults.max_evidence_items)),
            ),
            max_context_chars=max(
                256,
                int(raw.get("max_context_chars", defaults.max_context_chars)),
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "model_context_window": self.model_context_window,
            "prompt_reserved_tokens": self.prompt_reserved_tokens,
            "section_token_budget": self.section_token_budget,
            "max_evidence_items": self.max_evidence_items,
            "max_context_chars": self.max_context_chars,
            "evidence_token_budget": self.evidence_token_budget,
        }


class ContextGate:
    """Choose default or lost-in-middle packing from measured context size."""

    def __init__(
        self,
        *,
        default_packer: ContextPacker,
        lost_in_middle_packer: ContextPacker,
        default_requirements: ContextRequirements,
        long_context_threshold_ratio: float = 0.65,
    ) -> None:
        self.default_packer = default_packer
        self.lost_in_middle_packer = lost_in_middle_packer
        self.default_requirements = default_requirements
        self.long_context_threshold_ratio = max(
            0.05, min(1.0, float(long_context_threshold_ratio))
        )

    @staticmethod
    def _candidate_text(item: dict[str, Any]) -> str:
        return str(
            item.get("parent_text")
            or item.get("text")
            or item.get("child_text")
            or ""
        )

    def pack(
        self,
        results: Iterable[dict[str, Any]],
        *,
        requirements: ContextRequirements | None = None,
    ) -> ContextPack:
        resolved = requirements or self.default_requirements
        materialized = list(results)
        estimated_tokens = sum(
            DeterministicTokenEstimator.estimate(self._candidate_text(item))
            for item in materialized[: resolved.max_evidence_items]
        )
        threshold = max(
            1,
            int(resolved.evidence_token_budget * self.long_context_threshold_ratio),
        )
        packer = (
            self.lost_in_middle_packer
            if estimated_tokens > threshold
            else self.default_packer
        )
        pack = packer.pack(
            materialized,
            token_budget=resolved.evidence_token_budget,
            max_items=resolved.max_evidence_items,
            char_budget=resolved.max_context_chars,
        )
        return pack

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "mode": "context_requirements_gate",
            "long_context_threshold_ratio": self.long_context_threshold_ratio,
            "default_requirements": self.default_requirements.to_dict(),
        }
