"""把已完成章节、证据和引用确定性聚合为文档草稿及门禁输入。

装配器不调用 RAG 或 LLM，不写 SharedState，也不执行 Capture 或文档硬门禁。它只计算
缺失章节、正文顺序、引用绑定、Chunk 去重和逐章节证据要求，确保后续门禁看到完整且
可复现的文档级事实。
"""

from __future__ import annotations

from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentAssemblyRequestSchema,
    DocumentAssemblyResultSchema,
    SchemeDraftSchema,
    SectionEvidenceRequirementSchema,
)


class DocumentAssembler:
    """负责从章节结果到 ``SchemeDraft`` 的纯聚合，不决定是否允许交付。"""

    def assemble(
        self,
        request: DocumentAssemblyRequestSchema,
    ) -> DocumentAssemblyResultSchema:
        """无外部 I/O 地装配章节，并计算文档门禁所需的证据覆盖状态。

        检索 Chunk 按稳定标识去重，同时保留父子 Chunk 的全部已知 ID。引用必需章节采用
        全称量化：每个章节都必须独立达到 sufficient，或因证据不足而被明确 blocked；
        文档级证据判断不能替代缺失或未评估的章节结论。
        """

        sections = list(request.sections)
        required_sections = list(request.required_sections)

        generated_titles = {section.section_title for section in sections}
        missing_sections = [
            title for title in required_sections if title not in generated_titles
        ]
        full_text = "\n\n".join(
            f"{index}、{section.section_title}\n{section.content}"
            for index, section in enumerate(sections, start=1)
        )
        bindings = [
            binding
            for section in sections
            for binding in section.citation_bindings
        ]

        deduplicated_chunks = []
        seen_chunk_keys = set()
        for chunk in request.retrieved_chunks:
            chunk_key = (
                chunk.matched_chunk_id
                or chunk.context_chunk_id
                or chunk.child_chunk_id
                or chunk.parent_chunk_id
                or f"{chunk.doc_id}:{len(deduplicated_chunks)}"
            )
            if chunk_key in seen_chunk_keys:
                continue
            seen_chunk_keys.add(chunk_key)
            deduplicated_chunks.append(chunk)

        known_chunk_ids = {
            value
            for chunk in deduplicated_chunks
            for value in (
                chunk.matched_chunk_id,
                chunk.context_chunk_id,
                chunk.child_chunk_id,
                chunk.parent_chunk_id,
            )
            if value
        }
        for chunk in deduplicated_chunks:
            known_chunk_ids.update(
                str(item)
                for item in (
                    (chunk.metadata or {}).get("matched_child_chunk_ids") or []
                )
                if item
            )

        evidence_available = bool(
            (
                request.document_evidence_bundle is not None
                and request.document_evidence_bundle.assessment.evidence_available
            )
            or any(
                bundle.evidence_bundle is not None
                and bundle.evidence_bundle.assessment.evidence_available
                for bundle in request.section_evidence
            )
        )
        citation_required_titles = list(
            dict.fromkeys(request.citation_required_sections)
        )
        final_section_statuses: dict[str, str] = {}
        for bundle in request.section_evidence:
            if bundle.section_title not in citation_required_titles:
                continue
            if bundle.evidence_bundle is None:
                status = "not_assessed"
            else:
                raw_status = bundle.evidence_bundle.assessment.status
                status = (
                    raw_status.value
                    if raw_status is not None and hasattr(raw_status, "value")
                    else str(raw_status or "not_assessed")
                )
            status = status.strip().lower() or "not_assessed"
            final_section_statuses[bundle.section_title] = status

        section_by_title = {
            section.section_title: section for section in sections
        }
        section_requirements: list[SectionEvidenceRequirementSchema] = []
        for title in citation_required_titles:
            section = section_by_title.get(title)
            status = final_section_statuses.get(title, "not_assessed")
            generation_blocked = bool(
                section
                and (section.extra or {}).get("generation_blocked")
            )
            generation_block_reason = (
                str((section.extra or {}).get("generation_block_reason") or "")
                if section is not None
                else ""
            )
            writer_execution_status = (
                str(
                    (section.extra or {}).get("writer_execution_status")
                    or ""
                ).strip()
                if section is not None
                else ""
            )
            explicitly_blocked = bool(
                generation_blocked
                and (
                    generation_block_reason == "evidence_insufficient"
                    or generation_block_reason.startswith("writer_")
                    or writer_execution_status
                    in {"blocked", "human_review", "not_assessed"}
                )
            )
            if explicitly_blocked:
                effective_status = "blocked"
            elif status in {"sufficient", "insufficient"}:
                effective_status = status
            else:
                effective_status = "not_assessed"
            section_requirements.append(
                SectionEvidenceRequirementSchema(
                    section_title=title,
                    assessment_status=effective_status,
                    generation_blocked=generation_blocked,
                    generation_block_reason=(
                        generation_block_reason or None
                    ),
                    requirement_satisfied=(
                        effective_status in {"sufficient", "blocked"}
                    ),
                )
            )

        # Required evidence is universal quantification. A document-level
        # assessment cannot substitute for a missing/not_assessed section.
        semantic_evidence_sufficient = bool(section_requirements) and all(
            item.assessment_status == "sufficient"
            for item in section_requirements
        )
        evidence_requirements_satisfied = (
            all(item.requirement_satisfied for item in section_requirements)
            if citation_required_titles
            else True
        )

        degraded_sections: list[str] = []
        evidence_by_section = {
            bundle.section_id: (
                bundle.evidence_bundle.assessment.model_dump(mode="json")
                if bundle.evidence_bundle is not None
                else {}
            )
            for bundle in request.section_evidence
        }
        for section in sections:
            generation_check = dict((section.extra or {}).get("generation_check") or {})
            evidence_assessment = evidence_by_section.get(section.section_id, {})
            evidence_details = dict(evidence_assessment.get("details") or {})
            if bool(generation_check.get("quality_mode_degraded")) or bool(
                evidence_details.get("quality_mode_degraded")
            ):
                degraded_sections.append(section.section_id)
        document_assessment_details = (
            dict(request.document_evidence_bundle.assessment.details or {})
            if request.document_evidence_bundle is not None
            else {}
        )
        quality_mode_degraded = bool(
            degraded_sections
            or document_assessment_details.get("quality_mode_degraded")
        )

        draft = SchemeDraftSchema(
            draft_id=f"draft_{request.run_id}_scheme",
            document_id=request.document_id,
            task_id=request.task_id,
            run_id=request.run_id,
            title=request.document_title or "项目建设方案",
            full_text=full_text,
            sections=sections,
            required_sections=required_sections,
            missing_sections=missing_sections,
            citation_bindings=bindings,
            truncation_detected=any(
                item.truncation.truncated for item in sections
            ),
            summary=f"共生成{len(sections)}个章节。",
            created_at=request.created_at,
            updated_at=request.updated_at,
            extra={
                "quality_mode_degraded": quality_mode_degraded,
                "quality_degraded_section_ids": list(dict.fromkeys(degraded_sections)),
            },
        )

        return DocumentAssemblyResultSchema(
            draft=draft,
            retrieved_chunks=deduplicated_chunks,
            citations=list(request.citations),
            citation_bindings=bindings,
            known_chunk_ids=known_chunk_ids,
            evidence_available=evidence_available,
            semantic_evidence_sufficient=semantic_evidence_sufficient,
            evidence_requirements_satisfied=evidence_requirements_satisfied,
            section_evidence_requirements=section_requirements,
        )
