"""Select a context packing strategy from real context requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from rag.context.context_packer import ContextPack, ContextPacker, PreparedPromptProjection


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
        """Apply caller constraints without allowing internal limits to expand."""

        raw = dict(value or {})

        def constrained_int(
            public_name: str,
            *,
            internal_default: int,
            legacy_name: str | None = None,
        ) -> int:
            requested = raw.get(public_name)
            if requested is None and legacy_name is not None:
                # TODO(Interface V2 post-Phase 2): retire this RAG-internal
                # compatibility key after all direct engine tests migrate.
                # It may only narrow the limit and never become public authority.
                requested = raw.get(legacy_name)
            if requested is None:
                return int(internal_default)
            return min(int(internal_default), max(1, int(requested)))

        return cls(
            # These two values are RAG internal config and are never caller-controlled.
            model_context_window=defaults.model_context_window,
            prompt_reserved_tokens=defaults.prompt_reserved_tokens,
            section_token_budget=constrained_int(
                "max_context_tokens",
                internal_default=defaults.section_token_budget,
                legacy_name="section_token_budget",
            ),
            max_evidence_items=constrained_int(
                "max_context_items",
                internal_default=defaults.max_evidence_items,
                legacy_name="max_evidence_items",
            ),
            max_context_chars=constrained_int(
                "max_context_chars",
                internal_default=defaults.max_context_chars,
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

    def pack(self, results: Iterable[dict[str, Any]], *,
             requirements: ContextRequirements | None = None,
             prepared_projection: PreparedPromptProjection | None = None) -> ContextPack:
        resolved = requirements or self.default_requirements
        owner = self.default_packer
        projection = (prepared_projection if prepared_projection is not None
                      else owner.prepare_projection(results, max_items=resolved.max_evidence_items))
        owner._assert_prepared_representation(projection, max_items=resolved.max_evidence_items)
        self.lost_in_middle_packer._assert_prepared_representation(
            projection, max_items=resolved.max_evidence_items)
        # Explicit common cap is resolved before admission. Both actors render
        # the same effective budget; a smaller actor default cannot silently
        # evict members after presentation selection or trigger readmission.
        char_budget = max(1, min(resolved.max_context_chars,
                                 owner.max_context_chars,
                                 self.lost_in_middle_packer.max_context_chars))
        admission = owner.admit_projection(projection,
            token_budget=resolved.evidence_token_budget,
            max_items=resolved.max_evidence_items, char_budget=char_budget)
        threshold = max(1, int(resolved.evidence_token_budget * self.long_context_threshold_ratio))
        packer = (self.lost_in_middle_packer
                  if admission.max_render_cost.tokens > threshold
                  else owner)
        return packer.pack((), token_budget=admission.token_budget,
            max_items=resolved.max_evidence_items, char_budget=admission.char_budget,
            prepared_projection=projection, prepared_admission=admission)

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "mode": "context_requirements_gate",
            "long_context_threshold_ratio": self.long_context_threshold_ratio,
            "default_requirements": self.default_requirements.to_dict(),
        }
