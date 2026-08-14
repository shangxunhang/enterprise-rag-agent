"""Map retrieval runtime output directly to the public evidence bundle."""

from __future__ import annotations

import hashlib
from typing import Any

from rag.common.coercion import as_str_list
from rag.evidence.contract import RAGEvidenceContractBuilder
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.evidence_mapper import EvidenceMapper
from rag.mapping.request_mapper import RAGInvocation
from rag.schema.prompt_evidence_candidate_v1 import (
    PromptEvidenceCandidateV1Schema,
    build_prompt_evidence_candidate_v1,
)
from schemas.rag import (
    EvidenceBundleSchema,
    EvidenceStatus,
    RAGEvidenceAssessmentSchema,
    RAGToolInputSchema,
    RAGTraceSchema,
)
from schemas.status import ExecutionStatus


class RAGResultMapper:
    """Own the single dictionary-to-schema conversion at the runtime boundary."""

    def __init__(
        self,
        evidence: EvidenceMapper | None = None,
        evidence_governance_v2: OnlineEvidenceGovernanceV2 | None = None,
    ) -> None:
        self.evidence = evidence or EvidenceMapper()
        self.evidence_governance_v2 = (
            evidence_governance_v2 or OnlineEvidenceGovernanceV2()
        )

    @staticmethod
    def _query_expansion(
        result: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        metadata = result.get("metadata") or {}
        nested = metadata.get("metadata")
        for candidate in (
            data.get("query_expansion"),
            metadata.get("query_expansion"),
            nested.get("query_expansion") if isinstance(nested, dict) else None,
        ):
            if isinstance(candidate, dict):
                return candidate
        return {}

    @staticmethod
    def _runtime_metadata(
        result: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        run_record = data.get("run_record") or {}
        metadata = result.get("metadata") or run_record.get("metadata") or {}
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _record_key(item: dict[str, Any]) -> tuple[str, str]:
        return (
            str(
                item.get("child_chunk_id")
                or item.get("matched_chunk_id")
                or item.get("chunk_id")
                or ""
            ),
            str(
                item.get("parent_chunk_id")
                or item.get("context_chunk_id")
                or item.get("chunk_id")
                or ""
            ),
        )

    def _selected_and_dropped_records(
        self,
        *,
        data: dict[str, Any],
        context_pack: dict[str, Any],
        max_context_items: int,
        strict_prompt_contract: bool,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        str,
        bool,
    ]:
        full = data.get("retrieval_results") or []
        compact = data.get("contexts") or []
        dropped = context_pack.get("dropped_results") or []
        selected_present = "selected_results" in context_pack
        prompt_contract_missing = False

        if selected_present:
            selected = context_pack.get("selected_results")
            if not isinstance(selected, list):
                raise ValueError("context_pack.selected_results must be a list")
            selected_records = [
                dict(item) for item in selected if isinstance(item, dict)
            ]
            if (
                not strict_prompt_contract
                and any("prompt_visible_text" not in item for item in selected_records)
            ):
                prompt_evidence_source = "legacy_fallback"
            else:
                prompt_evidence_source = "context_pack_selected_results"
        elif strict_prompt_contract:
            # Strict prompt-contract mode never reconstructs prompt evidence
            # from the broader retrieval set. The public assessment exposes
            # the explicit missing-contract fact.
            selected_records = []
            prompt_evidence_source = "missing_blocked"
            prompt_contract_missing = True
        elif isinstance(full, list) and full:
            selected_records = [
                dict(item)
                for item in full[:max_context_items]
                if isinstance(item, dict)
            ]
            prompt_evidence_source = "legacy_fallback"
        elif isinstance(compact, list):
            selected_records = [
                dict(item)
                for item in compact[:max_context_items]
                if isinstance(item, dict)
            ]
            prompt_evidence_source = "legacy_fallback"
        else:
            selected_records = []
            prompt_evidence_source = "legacy_fallback"

        packed_context = str(
            context_pack.get("context")
            or context_pack.get("rendered_text")
            or ""
        )
        if selected_records and not packed_context and prompt_evidence_source != "legacy_fallback":
            raise ValueError(
                "context_pack selected_results is non-empty but rendered context is empty"
            )
        if not selected_records and packed_context and selected_present:
            raise ValueError(
                "context_pack rendered context is non-empty while selected_results is empty"
            )

        dropped_records = (
            [dict(item) for item in dropped if isinstance(item, dict)]
            if isinstance(dropped, list)
            else []
        )
        selected_keys = {self._record_key(item) for item in selected_records}
        dropped_keys = {self._record_key(item) for item in dropped_records}
        governance = data.get("evidence_governance_v2")
        governance = governance if isinstance(governance, dict) else {}
        rejected_candidate_ids = {
            str(item)
            for item in governance.get("rejected_candidate_ids", [])
            if str(item).strip()
        }

        if isinstance(full, list):
            for item in full:
                if not isinstance(item, dict):
                    continue
                key = self._record_key(item)
                if key in selected_keys or key in dropped_keys:
                    continue
                normalized = dict(item)
                metadata = dict(normalized.get("metadata") or {})
                candidate_id = str(
                    normalized.get("child_chunk_id")
                    or normalized.get("chunk_id")
                    or ""
                )
                if prompt_contract_missing:
                    drop_reason = "prompt_evidence_contract_missing"
                elif candidate_id in rejected_candidate_ids:
                    drop_reason = "evidence_ineligible_v2"
                else:
                    drop_reason = "not_selected_by_context_packer"
                metadata.setdefault("context_drop_reason", drop_reason)
                normalized["metadata"] = metadata
                dropped_records.append(normalized)
                dropped_keys.add(key)
        return (
            selected_records,
            dropped_records,
            prompt_evidence_source,
            prompt_contract_missing,
        )

    @staticmethod
    def _correction_rounds(
        evidence_quality: dict[str, Any]
    ) -> list[dict[str, Any]]:
        corrective = evidence_quality.get("corrective_retrieval")
        if not isinstance(corrective, dict):
            return []
        rounds = corrective.get("rounds")
        if not isinstance(rounds, list):
            return []
        return [dict(item) for item in rounds if isinstance(item, dict)]

    @staticmethod
    def _assessment_from_quality(
        evidence_quality: dict[str, Any],
    ) -> RAGEvidenceAssessmentSchema:
        """Project the retrieval grader result into the canonical evidence contract."""

        corrective = evidence_quality.get("corrective_retrieval")
        corrective = corrective if isinstance(corrective, dict) else {}
        final_assessment = (
            corrective.get("final_assessment")
            or evidence_quality.get("final_assessment")
            or evidence_quality.get("evidence_assessment")
            or {}
        )
        final_assessment = (
            dict(final_assessment) if isinstance(final_assessment, dict) else {}
        )
        sufficient = final_assessment.get("sufficient")
        if sufficient is True:
            status = EvidenceStatus.SUFFICIENT
            reason_codes = ["retrieval_evidence_sufficient"]
        elif sufficient is False:
            status = EvidenceStatus.INSUFFICIENT
            reason_codes = ["retrieval_evidence_insufficient"]
        else:
            status = None
            reason_codes = []

        raw_score = final_assessment.get("confidence")
        try:
            score = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None
        metadata = evidence_quality.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        final_report = final_assessment.get("report")
        final_report = final_report if isinstance(final_report, dict) else {}
        verification_mode = (
            evidence_quality.get("verification_mode")
            or final_report.get("verification_mode")
        )
        quality_mode_degraded = bool(
            evidence_quality.get("quality_mode_degraded")
            or final_report.get("quality_mode_degraded")
        )
        return RAGEvidenceAssessmentSchema(
            assessment_state="completed" if status is not None else "pending",
            status=status,
            judge_name=str(metadata.get("judge") or "") or None,
            judge_version=str(metadata.get("version") or "") or None,
            score=score,
            reason_codes=reason_codes,
            details={
                "final_assessment": final_assessment,
                "verification_mode": verification_mode,
                "quality_mode_degraded": quality_mode_degraded,
                "corrective_retrieval_triggered": bool(
                    corrective.get("triggered")
                ),
                "correction_rounds": len(
                    corrective.get("rounds")
                    if isinstance(corrective.get("rounds"), list)
                    else []
                ),
            },
        )

    @staticmethod
    def _assessment_from_governance(
        governance: dict[str, Any],
        *,
        legacy_assessment: RAGEvidenceAssessmentSchema,
    ) -> RAGEvidenceAssessmentSchema:
        set_assessment = governance.get("set_assessment")
        set_assessment = set_assessment if isinstance(set_assessment, dict) else {}
        public_payload = governance.get("public_assessment")
        if isinstance(public_payload, dict):
            assessment = RAGEvidenceAssessmentSchema.model_validate(public_payload)
        else:
            # Compatibility for pre-Phase-3A internal governance payloads. This
            # projects set facts only and never recovers business actions.
            assessment = RAGEvidenceAssessmentSchema.model_validate(
                {
                    "status": set_assessment.get("status"),
                    "coverage_ratio": set_assessment.get("coverage_ratio"),
                    "score": set_assessment.get("coverage_ratio"),
                    "required_topics": set_assessment.get("required_topics") or [],
                    "covered_topics": set_assessment.get("covered_topics") or [],
                    "missing_topics": set_assessment.get("missing_topics") or [],
                    "conflicts": set_assessment.get("conflicts") or [],
                    "reason_codes": set_assessment.get("reason_codes") or [],
                }
            )
        return assessment.model_copy(
            update={"details": {
                **dict(assessment.details),
                "evidence_governance_v2": governance,
                "legacy_corrective_assessment": legacy_assessment.model_dump(),
                "presence_is_not_semantic_sufficiency": True,
            }},
            deep=True,
        )

    def map(
        self,
        *,
        request: RAGToolInputSchema,
        invocation: RAGInvocation,
        raw_result: dict[str, Any],
        latency_ms: int,
        rag_project_root: str,
        service_name: str,
    ) -> EvidenceBundleSchema:
        if raw_result.get("success") is False:
            raise RuntimeError(raw_result.get("error") or "RAG retrieval failed")

        data = raw_result.get("data") or raw_result
        metadata = self._runtime_metadata(raw_result, data)
        query_expansion = self._query_expansion(raw_result, data)
        rewritten_queries = as_str_list(query_expansion.get("rewritten_queries"))
        if not rewritten_queries:
            rewritten_queries = as_str_list(
                invocation.request_data.get("rewritten_queries")
            )

        retrieval_plan = data.get("retrieval_plan") or metadata.get("retrieval_plan") or {}
        retrieval_plan = (
            dict(retrieval_plan) if isinstance(retrieval_plan, dict) else {}
        )
        evidence_quality = (
            data.get("evidence_quality") or metadata.get("evidence_quality") or {}
        )
        evidence_quality = (
            dict(evidence_quality) if isinstance(evidence_quality, dict) else {}
        )
        context_pack = data.get("context_pack") or {}
        context_pack = dict(context_pack) if isinstance(context_pack, dict) else {}
        runtime_context = dict(invocation.payload.get("extra_metadata") or {})
        strict_prompt_contract = bool(
            invocation.payload.get("access_scope_enforced")
            or isinstance(runtime_context.get("evidence_requirements"), dict)
        )
        (
            selected_records,
            dropped_records,
            prompt_evidence_source,
            prompt_contract_missing,
        ) = self._selected_and_dropped_records(
            data=data,
            context_pack=context_pack,
            max_context_items=invocation.max_context_items,
            strict_prompt_contract=strict_prompt_contract,
        )

        effective_context_pack = dict(context_pack)
        if prompt_contract_missing:
            effective_context_pack.update(
                {
                    "context": "",
                    "rendered_text": "",
                    "selected_results": [],
                    "selected_count": 0,
                    "used_chars": 0,
                    "tokens_used": 0,
                }
            )

        prompt_candidates: list[PromptEvidenceCandidateV1Schema] = []
        prompt_candidate_errors: list[dict[str, str]] = []
        packed_context = str(
            effective_context_pack.get("context")
            or effective_context_pack.get("rendered_text")
            or ""
        )
        allow_legacy_prompt = prompt_evidence_source == "legacy_fallback"
        normalized_selected_records: list[dict[str, Any]] = []
        for raw in selected_records:
            normalized = dict(raw)
            try:
                prompt_candidate = build_prompt_evidence_candidate_v1(
                    normalized,
                    allow_legacy_fallback=allow_legacy_prompt,
                )
            except Exception as exc:
                if not allow_legacy_prompt:
                    raise
                candidate_id = str(
                    normalized.get("child_chunk_id")
                    or normalized.get("chunk_id")
                    or "unknown"
                )
                error = {
                    "candidate_id": candidate_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
                prompt_candidate_errors.append(error)
                metadata_for_item = dict(normalized.get("metadata") or {})
                metadata_for_item["prompt_evidence_candidate_error"] = error
                normalized["metadata"] = metadata_for_item
                normalized_selected_records.append(normalized)
                continue
            if (
                not allow_legacy_prompt
                and prompt_candidate.prompt_visible_text
                and prompt_candidate.prompt_visible_text not in packed_context
            ):
                raise ValueError(
                    "prompt_visible_text is not present in context_pack.context"
                )
            metadata_for_item = dict(normalized.get("metadata") or {})
            metadata_for_item["prompt_evidence_candidate_v1"] = (
                prompt_candidate.model_dump(mode="json", exclude_none=True)
            )
            normalized["metadata"] = metadata_for_item
            normalized["prompt_visible_text"] = (
                prompt_candidate.prompt_visible_text
            )
            normalized["context_text"] = prompt_candidate.prompt_visible_text
            prompt_candidates.append(prompt_candidate)
            normalized_selected_records.append(normalized)
        selected_records = normalized_selected_records

        governance_prompt_contract_missing = bool(
            prompt_contract_missing or prompt_candidate_errors
        )
        prompt_context = {
            **runtime_context,
            "prompt_evidence_contract_missing": governance_prompt_contract_missing,
            "prompt_evidence_source": prompt_evidence_source,
        }
        final_governance = self.evidence_governance_v2.evaluate_prompt(
            query=invocation.query,
            prompt_candidates=prompt_candidates,
            runtime_context=prompt_context,
            prompt_evidence_source=prompt_evidence_source,
        )
        governance_payload = final_governance.to_dict()
        prompt_contract_payload = {
            "schema_version": "prompt_evidence_contract_v1",
            "source": prompt_evidence_source,
            "contract_missing": governance_prompt_contract_missing,
            "candidate_count": len(prompt_candidates),
            "candidate_errors": prompt_candidate_errors,
            "candidates": [
                item.model_dump(mode="json", exclude_none=True)
                for item in prompt_candidates
            ],
            "context_sha256": hashlib.sha256(
                packed_context.encode("utf-8")
            ).hexdigest(),
        }
        selected_chunks = self.evidence.chunks(selected_records)
        dropped_chunks = self.evidence.chunks(dropped_records)
        citations = self.evidence.citations(selected_chunks)

        offline_index = metadata.get("offline_index")
        offline_index = offline_index if isinstance(offline_index, dict) else {}
        plan_id = str(retrieval_plan.get("plan_id") or "adaptive")
        trace = RAGTraceSchema(
            retrieval_mode=plan_id,
            query=invocation.query,
            rewritten_queries=rewritten_queries,
            embedding_model=str(
                offline_index.get("embedding_model") or "runtime-config"
            ),
            embedding_version=str(offline_index.get("embedding_version") or "unknown"),
            reranker_model="runtime-config",
            reranker_version="unknown",
            index_name=str(
                offline_index.get("collection_name") or "rag_child_chunks"
            ),
            index_version=str(
                offline_index.get("index_version") or "unversioned_index"
            ),
            vector_db=str(offline_index.get("backend") or "milvus-lite"),
            max_context_chars=invocation.max_context_chars,
            retrieved_count=len(selected_chunks) + len(dropped_chunks),
            reranked_count=len(selected_chunks) + len(dropped_chunks),
            context_item_count=len(selected_chunks),
            latency_ms=latency_ms,
            extra={
                "rag_project_root": rag_project_root,
                "rag_result_metadata": metadata,
                "query_expansion": query_expansion,
                "retrieval_plan": retrieval_plan,
                "evidence_quality": evidence_quality,
                "rag_run_id": data.get("run_id"),
                "context_source": "rag_evidence_contract_v1",
                "packing_strategy": effective_context_pack.get("packing_strategy"),
                "context_pack_selected_count": len(selected_records),
                "context_pack_dropped_count": len(dropped_records),
                "context_token_budget": effective_context_pack.get("token_budget"),
                "context_tokens_used": effective_context_pack.get("tokens_used"),
                "context_truncated_item_ids": effective_context_pack.get(
                    "truncated_item_ids"
                ),
                "prompt_evidence_source": prompt_evidence_source,
                "prompt_evidence_contract_missing": governance_prompt_contract_missing,
            },
        )
        bundle = RAGEvidenceContractBuilder.build(
            query=invocation.query,
            rewritten_queries=rewritten_queries,
            selected_chunks=selected_chunks,
            dropped_chunks=dropped_chunks,
            citations=citations,
            trace=trace,
            max_context_chars=invocation.max_context_chars,
            context_pack=effective_context_pack,
            extra={
                "source": "parent_child_retrieval",
                "packing_strategy": effective_context_pack.get("packing_strategy"),
                "selected_count": len(selected_records),
                "dropped_count": len(dropped_records),
                "token_budget": effective_context_pack.get("token_budget"),
                "tokens_used": effective_context_pack.get("tokens_used"),
                "prompt_evidence_contract": prompt_contract_payload,
                "prompt_evidence_source": prompt_evidence_source,
            },
        )
        correction_rounds = self._correction_rounds(evidence_quality)
        legacy_assessment = self._assessment_from_quality(evidence_quality)
        assessment = self._assessment_from_governance(
            governance_payload,
            legacy_assessment=legacy_assessment,
        )
        assessment.evidence_available = bool(bundle.selected_evidence_ids)
        assessment.selected_evidence_count = len(bundle.selected_evidence_ids)
        assessment.dropped_evidence_count = len(bundle.dropped_evidence_ids)
        assessment.citation_count = len(bundle.citations)
        trace_id = str(
            request.retrieval_trace_id
            or f"rag_{request.run_id}_{request.retrieval_scope or 'document'}"
        )
        return bundle.model_copy(
            update={
                "task_id": request.task_id,
                "run_id": request.run_id,
                "status": ExecutionStatus.SUCCESS,
                "retrieval_trace_id": trace_id,
                "correction_trace": correction_rounds,
                "assessment": assessment,
                "budget_usage": {
                    "retrieval_rounds": 1 + len(correction_rounds),
                    "queries_executed": 1 + len(rewritten_queries),
                    "rerank_calls": 1 + len(correction_rounds),
                },
                "trace": trace,
                "extra": {
                    **dict(bundle.extra),
                    "rag_service": service_name,
                    "retrieval_plan": retrieval_plan,
                    "evidence_quality": evidence_quality,
                    "evidence_governance_v2": governance_payload,
                    "prompt_evidence_contract": prompt_contract_payload,
                    "prompt_evidence_source": prompt_evidence_source,
                },
            }
        )
