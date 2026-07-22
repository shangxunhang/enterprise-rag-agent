"""Recover one generated section from truncation or excessive length."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import TruncationCheckSchema
from apps.enterprise_document.services.output_validation import detect_truncation
from schemas.citation import CitationSchema
from schemas.model import ModelResponseSchema
from schemas.rag import RAGContextSchema

from .model_service import SectionModelService
from .prompt_service import SectionPromptService


@dataclass
class SectionContentRecoveryResult:
    """Recovered content plus complete recovery lineage for one section."""

    content: str
    truncation: TruncationCheckSchema
    truncation_retry_responses: List[ModelResponseSchema]
    truncation_recovery_strategy: Optional[str]
    compression_response: Optional[ModelResponseSchema]
    compression_fallback_strategy: Optional[str]
    target_section_chars: int
    max_section_chars: int
    overlong: bool


class SectionContentRecovery:
    """Own truncation retry, complete-prefix recovery and overlong compression."""

    def __init__(
        self,
        *,
        model_service: SectionModelService,
        prompt_service: SectionPromptService,
    ) -> None:
        self.model_service = model_service
        self.prompt_service = prompt_service

    def recover(
        self,
        shared_state: SharedStateSchema,
        *,
        response: ModelResponseSchema,
        model_section_id: str,
        section_title: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
        rag_context: RAGContextSchema,
    ) -> SectionContentRecoveryResult:
        """Return the best complete section text available within recovery policy."""

        content = response.content.strip()
        truncation = detect_truncation(
            content,
            response.finish_reason,
            project_input.generation_requirements.min_section_chars,
        )
        truncation_retry_responses: list[ModelResponseSchema] = []
        truncation_recovery_strategy: Optional[str] = None
        remaining_retries = max(
            0, project_input.generation_requirements.max_section_retries
        )
        target_section_chars = self.prompt_service.target_section_chars(project_input)
        max_section_chars = int(target_section_chars * 1.5)
        overlong = len(content) > max_section_chars
        retry_index = 1

        # Token-limit recovery uses a fresh compact generation. Do not append a
        # continuation: small local models tend to keep expanding and truncate
        # again, producing a longer but still incomplete section.
        while truncation.truncated and remaining_retries > 0:
            retry_response = self.model_service.retry_truncated_section(
                shared_state,
                section_id=model_section_id,
                section_title=section_title,
                project_input=project_input,
                citations=citations,
                rag_context=rag_context,
                retry_index=retry_index,
            )
            truncation_retry_responses.append(retry_response)
            remaining_retries -= 1
            retry_index += 1
            if not retry_response.success or not retry_response.content.strip():
                continue
            candidate = retry_response.content.strip()
            candidate_truncation = detect_truncation(
                candidate,
                retry_response.finish_reason,
                project_input.generation_requirements.min_section_chars,
            )
            content = candidate
            truncation = candidate_truncation
            overlong = len(content) > max_section_chars

        # If the compact retry still reaches the model limit, retain only a
        # sufficiently long prefix ending at a complete sentence/list item.
        if truncation.truncated:
            recovered = self.model_service.recover_complete_prefix(
                content,
                min_chars=project_input.generation_requirements.min_section_chars,
                max_chars=max_section_chars,
            )
            if recovered:
                content = recovered
                truncation = detect_truncation(
                    content,
                    "stop",
                    project_input.generation_requirements.min_section_chars,
                )
                overlong = len(content) > max_section_chars
                truncation_recovery_strategy = "complete_sentence_prefix"
                print(
                    f"[TruncationRecovery] section={section_title} "
                    f"strategy={truncation_recovery_strategy} chars={len(content)}",
                    flush=True,
                )

        compression_response: Optional[ModelResponseSchema] = None
        compression_fallback_strategy: Optional[str] = None
        if overlong and not truncation.truncated:
            print(
                f"[SectionCompression] START section={section_title} chars={len(content)} "
                f"limit={max_section_chars}",
                flush=True,
            )
            compression_response = self.model_service.compress_overlong_section(
                shared_state,
                original_content=content,
                section_id=model_section_id,
                section_title=section_title,
                project_input=project_input,
                citations=citations,
            )
            if compression_response.success and compression_response.content.strip():
                candidate = compression_response.content.strip()
                candidate_truncation = detect_truncation(
                    candidate,
                    compression_response.finish_reason,
                    project_input.generation_requirements.min_section_chars,
                )
                if not candidate_truncation.truncated and len(candidate) < len(content):
                    content = candidate
                    truncation = candidate_truncation
                    overlong = len(content) > max_section_chars
                elif candidate_truncation.truncated:
                    recovered_candidate = self.model_service.recover_complete_prefix(
                        candidate,
                        min_chars=project_input.generation_requirements.min_section_chars,
                        max_chars=max_section_chars,
                    )
                    if recovered_candidate and len(recovered_candidate) < len(content):
                        content = recovered_candidate
                        truncation = detect_truncation(
                            content,
                            "stop",
                            project_input.generation_requirements.min_section_chars,
                        )
                        overlong = len(content) > max_section_chars
                        compression_fallback_strategy = (
                            "compressed_complete_sentence_prefix"
                        )
            if overlong:
                deterministic = self.model_service.recover_complete_prefix(
                    content,
                    min_chars=project_input.generation_requirements.min_section_chars,
                    max_chars=max_section_chars,
                )
                if deterministic and len(deterministic) < len(content):
                    content = deterministic
                    truncation = detect_truncation(
                        content,
                        "stop",
                        project_input.generation_requirements.min_section_chars,
                    )
                    overlong = len(content) > max_section_chars
                    compression_fallback_strategy = (
                        compression_fallback_strategy
                        or "deterministic_complete_sentence_prefix"
                    )
            print(
                f"[SectionCompression] END   section={section_title} chars={len(content)} "
                f"overlong={overlong}",
                flush=True,
            )

        return SectionContentRecoveryResult(
            content=content,
            truncation=truncation,
            truncation_retry_responses=truncation_retry_responses,
            truncation_recovery_strategy=truncation_recovery_strategy,
            compression_response=compression_response,
            compression_fallback_strategy=compression_fallback_strategy,
            target_section_chars=target_section_chars,
            max_section_chars=max_section_chars,
            overlong=overlong,
        )
