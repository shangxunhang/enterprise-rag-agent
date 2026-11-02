"""The existing section recovery lengths, independent of writing preferences."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionTechnicalLengthPolicy:
    """Frozen technical bounds derived only from the section token budget."""

    technical_recovery_target_chars: int
    technical_max_section_chars: int
    compact_retry_target_chars: int
    compression_max_tokens: int
    continuation_max_tokens: int
    retry_max_tokens: int
    validation_rewrite_max_tokens: int

    @property
    def prefix_recovery_max_chars(self) -> int:
        return self.technical_max_section_chars

    @property
    def compression_trigger_chars(self) -> int:
        return self.technical_max_section_chars

    def continuation_remaining_chars(self, original_content: str) -> int:
        return max(160, self.technical_recovery_target_chars - len(original_content))


def build_section_technical_length_policy(
    max_tokens_per_section: int,
) -> SectionTechnicalLengthPolicy:
    """Preserve the start-HEAD recovery formulas without reading a soft target."""

    technical_tokens = max(256, int(max_tokens_per_section))
    base_chars = max(600, min(1200, technical_tokens))
    max_chars = int(base_chars * 1.5)
    return SectionTechnicalLengthPolicy(
        technical_recovery_target_chars=base_chars,
        technical_max_section_chars=max_chars,
        compact_retry_target_chars=min(800, max(480, int(base_chars * 0.68))),
        compression_max_tokens=min(
            640,
            max(384, int(base_chars * 0.65)),
            max_tokens_per_section,
        ),
        continuation_max_tokens=min(256, max_tokens_per_section),
        retry_max_tokens=min(768, max_tokens_per_section),
        validation_rewrite_max_tokens=min(768, max_tokens_per_section),
    )
