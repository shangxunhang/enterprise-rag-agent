"""方案生成中的 RAG 调用与标准证据投影边界。

本服务只构造 ``RAGToolInputSchema``、调用 ``RAGServicePort`` 并返回 canonical
``EvidenceBundleSchema``。章节 Query、文档级引用注册与 remap、旧 ToolResult 升级均由
独立协作者负责，防止检索、授权和兼容逻辑重新聚合成巨型服务。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple


from agent.runtime.shared_state_schema import SharedStateSchema
from apps.enterprise_document.schemas.agent_rag_v1.research import (
    CanonicalEvidenceBundleBindingSchema,
)
from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceRequirementsSnapshotSchema as CanonicalEvidenceRequirementsSnapshotSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from contracts.evidence import evidence_bundle_sha256, project_evidence_bundle
from contracts.rag import RAGServicePort
from rag.evidence.eligibility_policy import (
    resolve_evidence_use_eligibility_policy,
)
from rag.store.evidence_bundle_store import EvidenceBundleSnapshotStore
from schemas.citation import CitationSchema
from schemas.evidence_assessment import EvidenceAssessmentV2Schema
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceRequirementSchema as RAGEvidenceRequirementSchema,
    EvidenceRequirementsSchema,
    RAGContextSchema,
    RAGDocumentContextSchema,
    RAGToolInputSchema,
    RetrievalAccessScopeSchema,
    RetrievedChunkSchema,
)
from schemas.tool import ToolResultSchema

from .legacy_evidence_adapter import LegacyEvidenceAdapter


class SchemeEvidenceService:
    """执行一次受授权范围约束的检索，并向调用方暴露统一证据契约。"""

    def __init__(
        self,
        *,
        rag_service: RAGServicePort | None,
        agent_name: str,
        retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
        evidence_bundle_store: EvidenceBundleSnapshotStore | None = None,
    ) -> None:
        """注入 RAG 端口、调用 Agent 名称和可信检索授权范围。

        ``retrieval_access_scope`` 必须来自认证后的应用/安全边界；ProjectInput 只能提供
        缩小范围的材料和 KB 请求，不能被提升为授权事实。
        """
        self.rag_service = rag_service
        self.agent_name = agent_name
        # The composition root injects this object from an authenticated
        # application/security boundary. Business input is never promoted into
        # this authorization truth.
        self.retrieval_access_scope = retrieval_access_scope
        self.evidence_bundle_store = evidence_bundle_store

    def retrieve(
        self,
        shared_state: SharedStateSchema,
        project_input: ProjectInputSchema,
        *,
        query: Optional[str] = None,
        scope: str = "document",
        section_id: Optional[str] = None,
        section_title: Optional[str] = None,
        call_suffix: Optional[str] = None,
        canonical_evidence_requirements: (
            CanonicalEvidenceRequirementsSnapshotSchema | None
        ) = None,
        canonical_evidence_requirement_ids: tuple[str, ...] = (),
    ) -> Optional[EvidenceBundleSchema]:
        """构造并执行一次文档、章节或恢复范围的 RAG 请求。

        方法汇总 ProjectInput 的文件、文档和知识库定位信息，校验租户与可信授权范围一致，
        再构造 typed ``RAGToolInputSchema`` 并调用 ``RAGServicePort``。调用方预算只作为
        public constraint 传入 RAG，内部安全限制由 RAG 自己执行。未配置 RAG 服务时返回
        ``None``，由上层决定降级策略。
        """

        if self.rag_service is None:
            return None

        requirements = project_input.generation_requirements
        resolved_query = str(query or project_input.user_query).strip()
        source_doc_ids = list(
            dict.fromkeys(
                doc_id
                for material in project_input.source_materials
                for doc_id in material.doc_ids
                if doc_id
            )
        )
        source_file_ids = list(
            dict.fromkeys(
                file_id
                for material in project_input.source_materials
                for file_id in material.file_ids
                if file_id
            )
        )
        source_kb_ids = list(
            dict.fromkeys(
                str(kb_id)
                for material in project_input.source_materials
                for kb_id in (
                    material.metadata.get("kb_ids")
                    or (
                        [material.metadata.get("kb_id")]
                        if material.metadata.get("kb_id")
                        else []
                    )
                )
                if str(kb_id).strip()
            )
        )
        task_kb_ids = (shared_state.task or {}).get("kb_ids") or []
        requested_kb_ids = list(dict.fromkeys([*task_kb_ids, *source_kb_ids]))
        access_scope = self.retrieval_access_scope
        if (
            access_scope is not None
            and project_input.tenant_id != access_scope.tenant_id
        ):
            raise ValueError(
                "ProjectInput tenant_id conflicts with authenticated retrieval tenant"
            )
        raw_suffix = call_suffix or scope
        safe_suffix = (
            re.sub(r"[^0-9A-Za-z_\-]+", "_", raw_suffix).strip("_")
            or "document"
        )
        retrieval_trace_id = f"rag_{shared_state.run_id}_{safe_suffix}"
        if canonical_evidence_requirements is not None:
            selected_requirement_ids = tuple(
                str(item).strip()
                for item in canonical_evidence_requirement_ids
                if str(item).strip()
            )
            if len(selected_requirement_ids) != len(set(selected_requirement_ids)):
                raise ValueError(
                    "canonical Evidence requirement ids must be ordered-unique"
                )
            by_id = {
                item.requirement_id: item
                for item in canonical_evidence_requirements.requirements
            }
            unknown = set(selected_requirement_ids) - set(by_id)
            if unknown:
                raise ValueError(
                    "canonical Evidence requirement ids are absent from snapshot: "
                    + ", ".join(sorted(unknown))
                )
            if not selected_requirement_ids:
                raise ValueError(
                    "canonical Research retrieval requires selected Evidence requirement ids"
                )
            projected_requirements = []
            for requirement_id in selected_requirement_ids:
                requirement = by_id[requirement_id]
                topic = "；".join(
                    criterion.criterion.strip()
                    for criterion in requirement.criteria
                    if criterion.criterion.strip()
                )
                if not topic:
                    raise ValueError(
                        "canonical Evidence requirement criteria cannot project empty topic"
                    )
                projected_requirements.append(
                    RAGEvidenceRequirementSchema(
                        requirement_id=requirement.requirement_id,
                        topic=topic,
                        importance="required",
                    )
                )
            evidence_requirements = EvidenceRequirementsSchema(
                section_id=section_id or scope,
                section_title=section_title or resolved_query,
                section_type=section_id or scope,
                requirements=projected_requirements,
            )
        else:
            if canonical_evidence_requirement_ids:
                raise ValueError(
                    "canonical Evidence requirement ids require exact snapshot"
                )
            evidence_requirements = EvidenceRequirementsSchema.from_topics(
                section_id=section_id or scope,
                section_title=section_title or resolved_query,
                section_type=section_id or scope,
                topics=[section_title or resolved_query],
            )
        document_context = RAGDocumentContextSchema(
            project_name=project_input.project_name,
            document_title=project_input.output_schema.document_title,
            required_sections=list(
                project_input.generation_requirements.required_sections
                or project_input.output_schema.required_sections
            ),
            citation_required_sections=list(
                project_input.generation_requirements.citation_required_sections
            ),
        )
        request = RAGToolInputSchema(
            task_id=shared_state.task_id,
            run_id=shared_state.run_id,
            agent_name=self.agent_name,
            query=resolved_query,
            kb_ids=requested_kb_ids,
            access_scope=access_scope,
            filters={
                "tenant_id": project_input.tenant_id,
                "doc_ids": source_doc_ids,
                "file_ids": source_file_ids,
            },
            need_citation=requirements.need_citation,
            max_context_chars=requirements.max_context_chars,
            max_context_items=int(requirements.extra.get("max_evidence_items", 5)),
            max_context_tokens=int(
                requirements.extra.get("max_evidence_context_tokens", 4096)
            ),
            retrieval_trace_id=retrieval_trace_id,
            retrieval_scope=scope,
            task_type=project_input.task_type,
            evidence_requirements=evidence_requirements,
            document_context=document_context,
            extra={
                "project_type": project_input.project_type,
                "target_documents": list(project_input.target_documents),
                "target_templates": list(project_input.target_templates),
            },
        )
        result = self.rag_service.retrieve(request)
        if result is None:
            return None
        bundle = (
            result
            if isinstance(result, EvidenceBundleSchema)
            else LegacyEvidenceAdapter.upgrade(shared_state, result)
        )
        if bundle.success and self.evidence_bundle_store is not None:
            # Canonical RAG handoff becomes crash-resolvable before any downstream
            # Research/EvidencePackage consumer can observe the successful result.
            self.evidence_bundle_store.persist(bundle)
        return bundle

    def canonical_bundle_binding(
        self,
        bundle: EvidenceBundleSchema,
    ) -> CanonicalEvidenceBundleBindingSchema:
        """Materialize and return the exact durable RAG source binding.

        Hash-only or retrieval-trace-only provenance is intentionally rejected:
        SIF-05/SIF-08 require an opaque resolver-backed immutable handle plus the
        exact canonical Bundle hash.
        """

        if self.evidence_bundle_store is None:
            raise RuntimeError(
                "canonical EvidenceBundle snapshot store is not configured"
            )
        snapshot = self.evidence_bundle_store.persist(bundle)
        return CanonicalEvidenceBundleBindingSchema(
            evidence_bundle_handle=snapshot.handle,
            evidence_bundle_sha256=snapshot.evidence_bundle_sha256,
        )

    def resolve_canonical_bundle(
        self,
        evidence_bundle_handle: str,
    ) -> EvidenceBundleSchema:
        """Resolve one exact immutable RAG EvidenceBundle by canonical handle."""

        if self.evidence_bundle_store is None:
            raise RuntimeError(
                "canonical EvidenceBundle snapshot store is not configured"
            )
        return self.evidence_bundle_store.resolve(evidence_bundle_handle)

    @staticmethod
    def resolve_item_assessment(
        bundle: EvidenceBundleSchema,
        evidence_id: str,
    ) -> EvidenceAssessmentV2Schema:
        """Resolve exact typed RAG Eligibility truth without metadata reconstruction."""

        normalized_id = str(evidence_id or "").strip()
        if not normalized_id:
            raise ValueError("evidence_id is required")
        matches = [
            assessment
            for assessment in bundle.item_assessments
            if assessment.evidence_id == normalized_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "canonical item assessment must resolve exactly once for "
                f"evidence_id={normalized_id}"
            )
        if normalized_id not in {item.evidence_id for item in bundle.items}:
            raise ValueError("item assessment references non-member Evidence")
        assessment = matches[0]
        resolve_evidence_use_eligibility_policy(
            assessment.eligibility_semantic_policy_id,
            assessment.eligibility_semantic_policy_version,
        )
        return assessment

    @staticmethod
    def extract_rag_output(
        shared_state: SharedStateSchema,
        result: Optional[EvidenceBundleSchema | ToolResultSchema],
    ) -> Tuple[
        RAGContextSchema,
        List[RetrievedChunkSchema],
        List[CitationSchema],
        Dict[str, Any],
    ]:
        """Project typed canonical Evidence into compatibility-safe lightweight views.

        Legacy ToolResult values are first upgraded at the single compatibility ingress,
        then follow the same typed Bundle -> projection path as normal RAG results.  The
        returned projections deliberately contain no reconstructable full Bundle copy.
        """
        if isinstance(result, EvidenceBundleSchema):
            contract = result
        else:
            contract = LegacyEvidenceAdapter.upgrade(shared_state, result)

        context, chunks, citations = project_evidence_bundle(contract)
        normalized = {
            "schema_version": "rag_tool_output_projection_v2",
            "task_id": contract.task_id or shared_state.task_id,
            "run_id": contract.run_id or shared_state.run_id,
            "status": (
                contract.status.value
                if hasattr(contract.status, "value")
                else str(contract.status)
            ),
            "query": contract.query,
            "rewritten_queries": list(contract.rewritten_queries),
            "retrieval_scope": contract.retrieval_scope,
            "retrieval_trace_id": contract.retrieval_trace_id,
            "evidence_contract_sha256": evidence_bundle_sha256(contract),
            "lineage": contract.lineage.model_dump(),
            "context": context.model_dump(),
            "retrieved_chunks": [item.model_dump() for item in chunks],
            "citations": [item.model_dump() for item in citations],
            "trace": contract.trace.model_dump() if contract.trace else None,
            "extra": dict(contract.extra or {}),
        }
        return context, chunks, citations, normalized

    @staticmethod
    def retrieval_history_projection(
        normalized: Mapping[str, Any],
        *,
        scope: str | None = None,
        evidence_bundle: EvidenceBundleSchema | None = None,
    ) -> Dict[str, Any]:
        """Return a lightweight retrieval audit record without persisting Evidence copies.

        Phase 4B keeps retrieval history as trace/ref metadata only. Canonical Evidence lives
        in ``document_evidence_bundle`` / ``active_evidence_bundle``; this projection must
        never contain the full evidence/context/chunk/citation payloads.
        """

        if evidence_bundle is not None:
            raw_status = evidence_bundle.status
            status = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
            return {
                "scope": scope or evidence_bundle.retrieval_scope,
                "retrieval_scope": evidence_bundle.retrieval_scope,
                "query": evidence_bundle.query,
                "retrieval_trace_id": evidence_bundle.retrieval_trace_id,
                "evidence_contract_sha256": evidence_bundle_sha256(evidence_bundle),
                "status": status,
                "retrieved_chunk_count": len(evidence_bundle.selected_evidence_ids),
                "citation_count": len(evidence_bundle.citations),
            }

        retrieved_chunks = normalized.get("retrieved_chunks")
        citations = normalized.get("citations")
        return {
            "scope": scope or normalized.get("retrieval_scope") or "legacy",
            "retrieval_scope": normalized.get("retrieval_scope") or scope,
            "query": normalized.get("query"),
            "retrieval_trace_id": (
                normalized.get("retrieval_trace_id")
                or normalized.get("trace_id")
            ),
            "evidence_contract_sha256": normalized.get("evidence_contract_sha256"),
            "status": normalized.get("status"),
            "retrieved_chunk_count": (
                len(retrieved_chunks) if isinstance(retrieved_chunks, list) else 0
            ),
            "citation_count": len(citations) if isinstance(citations, list) else 0,
        }
