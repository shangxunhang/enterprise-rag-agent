# =============================================================================
# 中文阅读说明：文档级确定性聚合服务。
# 只负责把章节、证据、引用和检索结果组装为完整 SchemeDraft 与 Hard Gate 所需聚合数据。
# 不调用 RAG、LLM，不写 SharedState，不执行 Capture 或 Document Hard Gate。
# =============================================================================
"""Deterministic document assembly for scheme generation."""

from __future__ import annotations

from apps.enterprise_document.schemas.scheme_writer_schema import (
    DocumentAssemblyRequestSchema,
    DocumentAssemblyResultSchema,
    SchemeDraftSchema,
)


class DocumentAssembler:
    """Own deterministic aggregation from completed sections to one document draft."""

    def assemble(
        self,
        request: DocumentAssemblyRequestSchema,
    ) -> DocumentAssemblyResultSchema:
        """Assemble completed section results without external I/O or side effects."""

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
            request.document_evidence_available
            or any(bundle.citations for bundle in request.section_evidence)
        )
        citation_required_titles = list(
            dict.fromkeys(request.citation_required_sections)
        )
        document_assessment_status = str(
            request.document_assessment_status or "not_assessed"
        ).strip().lower() or "not_assessed"
        final_section_statuses: dict[str, str] = {}
        for bundle in request.section_evidence:
            if bundle.section_title not in citation_required_titles:
                continue
            status = str(
                (bundle.metadata or {}).get(
                    "evidence_assessment_status",
                    "not_assessed",
                )
                or "not_assessed"
            ).strip().lower() or "not_assessed"
            final_section_statuses[bundle.section_title] = status

        effective_assessment_by_section: dict[str, str] = {}
        if citation_required_titles:
            for title in citation_required_titles:
                section_status = final_section_statuses.get(title, "not_assessed")
                effective_assessment_by_section[title] = (
                    section_status
                    if section_status != "not_assessed"
                    else document_assessment_status
                )
            assessed_statuses = list(effective_assessment_by_section.values())
        else:
            assessed_statuses = [document_assessment_status]

        if "insufficient" in assessed_statuses:
            semantic_evidence_sufficient = False
        elif "sufficient" in assessed_statuses:
            semantic_evidence_sufficient = True
        else:
            # Legacy/fake evidence contracts may still be not_assessed.
            semantic_evidence_sufficient = evidence_available

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
        )

        return DocumentAssemblyResultSchema(
            draft=draft,
            retrieved_chunks=deduplicated_chunks,
            citations=list(request.citations),
            citation_bindings=bindings,
            known_chunk_ids=known_chunk_ids,
            evidence_available=evidence_available,
            semantic_evidence_sufficient=semantic_evidence_sufficient,
        )
