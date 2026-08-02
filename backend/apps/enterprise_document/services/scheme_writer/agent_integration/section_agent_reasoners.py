"""Round-5 production reasoners for Section business Agents.

Research uses the authorized Agent tool session. Writer and Verifier use the
already composed ``ModelGatewayPort`` through ``ModelCallBoundary``. They never
construct model clients, vector stores, RAG services or provider sessions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from apps.enterprise_document.quality.model_adapter import (
    reserve_current_workflow_budget,
)
from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    ResearchEvidencePackageSchema,
    ResearchInvocationInputSchema,
    ResearchRecommendedAction,
    ResearchResultPayloadSchema,
    ResearchStatus,
    RejectedEvidenceSchema,
    ToolResultStatus,
    VerifierAction,
    VerifierExecutionMode,
    VerifierInvocationInputSchema,
    VerifierResultPayloadSchema,
    WriterInvocationInputSchema,
    WriterResultPayloadSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1._base import ContractSchema
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from contracts.model_gateway import ModelGatewayPort
from model_gateway.call_boundary import ModelCallBoundary
from model_gateway.model_contract import ModelRole
from schemas.status import ExecutionStatus


def _json_object(text: str) -> dict[str, Any] | None:
    """Extract the first valid JSON object without accepting partial objects."""

    source = str(text or "").strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        source = "\n".join(lines).strip()
    try:
        value = json.loads(source)
    except Exception:
        value = None
    if isinstance(value, dict):
        return value
    decoder = json.JSONDecoder()
    for index, character in enumerate(source):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(source[index:])
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


def _normalized_gaps(assessment: Mapping[str, Any]) -> list[str]:
    details = assessment.get("details")
    details = dict(details) if isinstance(details, Mapping) else {}
    values: list[Any] = []
    for key in (
        "gaps",
        "evidence_gaps",
        "missing_topics",
        "reasons",
        "reason_codes",
    ):
        item = details.get(key)
        if isinstance(item, list):
            values.extend(item)
        elif item not in (None, ""):
            values.append(item)
    reason = assessment.get("reason")
    if reason not in (None, ""):
        values.append(reason)
    normalized = [str(item).strip() for item in values if str(item).strip()]
    return list(dict.fromkeys(normalized))


class MainlineResearchReasoner:
    """Run authorized retrieval or summarize precomputed document evidence."""

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools,
    ) -> ResearchResultPayloadSchema:
        del context
        invocation = ResearchInvocationInputSchema.model_validate(invocation_input)
        tool_error_statuses = {
            ToolResultStatus.RETRYABLE_ERROR,
            ToolResultStatus.PERMANENT_ERROR,
            ToolResultStatus.REJECTED,
            ToolResultStatus.STALE,
        }
        if invocation.query.strip():
            result = tools.call(
                "knowledge.retrieve",
                {
                    "query": invocation.query,
                    "scope": invocation.retrieval_scope,
                    "section_id": invocation.section_id,
                    "section_title": invocation.section_title,
                    "research_round": invocation.research_round,
                    "research_mode": invocation.research_mode,
                },
            )
            data = dict(result.data)
            tool_status = result.status
            tool_error_code = result.error.error_code
            tool_gateway_name = "MainlineResearchToolGateway"
        else:
            data = {
                "retrieval_succeeded": invocation.retrieval_succeeded,
                "assessment": dict(invocation.evidence_assessment),
                "accepted_evidence_ids": list(invocation.accepted_evidence_ids),
                "rejected_evidence_ids": list(invocation.rejected_evidence_ids),
                "tool_call_ids": list(invocation.tool_call_ids),
                "evidence_contract_sha256": invocation.evidence_contract_sha256,
            }
            tool_status = (
                ToolResultStatus.SUCCESS
                if invocation.retrieval_succeeded
                else ToolResultStatus.EMPTY
            )
            tool_error_code = None
            tool_gateway_name = "precomputed_document_evidence"

        assessment = data.get("assessment")
        assessment = dict(assessment) if isinstance(assessment, Mapping) else {}
        raw_status = str(assessment.get("status") or "not_assessed").strip().lower()
        gaps = _normalized_gaps(assessment)
        retrieval_succeeded = bool(data.get("retrieval_succeeded"))
        accepted_ids = [
            str(value).strip()
            for value in data.get("accepted_evidence_ids", [])
            if str(value).strip()
        ]
        rejected_ids = [
            str(value).strip()
            for value in data.get("rejected_evidence_ids", [])
            if str(value).strip()
        ]

        if tool_status in tool_error_statuses:
            research_status = ResearchStatus.CONFLICTED
            action = ResearchRecommendedAction.HUMAN_REVIEW
            if tool_error_code and tool_error_code not in gaps:
                gaps.append(tool_error_code)
        elif not retrieval_succeeded:
            research_status = ResearchStatus.INSUFFICIENT
            action = (
                ResearchRecommendedAction.PARTIAL_DELIVERY
                if invocation.citation_required
                else ResearchRecommendedAction.WRITE
            )
            if not gaps:
                gaps.append(
                    str(data.get("error_message") or "retrieval_unavailable")
                )
        elif raw_status == "insufficient":
            research_status = ResearchStatus.INSUFFICIENT
            action = ResearchRecommendedAction.PARTIAL_DELIVERY
            if not gaps:
                gaps.append("evidence_insufficient")
        elif raw_status == "sufficient" or accepted_ids:
            research_status = ResearchStatus.SUFFICIENT
            action = ResearchRecommendedAction.WRITE
        elif not invocation.citation_required:
            research_status = ResearchStatus.SUFFICIENT
            action = ResearchRecommendedAction.WRITE
        else:
            research_status = ResearchStatus.INSUFFICIENT
            action = ResearchRecommendedAction.PARTIAL_DELIVERY
            if not gaps:
                gaps.append("evidence_not_assessed")

        rejected = [
            RejectedEvidenceSchema(
                evidence_id=evidence_id,
                reason_code="not_selected_by_canonical_retrieval",
            )
            for evidence_id in rejected_ids
        ]
        tool_call_ids = [
            str(value).strip()
            for value in data.get("tool_call_ids", [])
            if str(value).strip()
        ]
        contract_sha = str(
            data.get("evidence_contract_sha256")
            or invocation.evidence_contract_sha256
            or ""
        ).strip() or None
        return ResearchResultPayloadSchema(
            section_id=invocation.section_id,
            evidence_package_ref=(
                f"state://section-evidence/{invocation.section_id}/"
                f"{invocation.retrieval_scope}/r{invocation.research_round}"
            ),
            evidence_summary=ResearchEvidencePackageSchema(
                section_id=invocation.section_id,
                accepted_evidence_ids=list(dict.fromkeys(accepted_ids)),
                rejected_evidence=rejected,
                supported_topics=list(invocation.evidence_topics),
                evidence_gaps=list(dict.fromkeys(gaps)),
                conflicts=(
                    [
                        {
                            "error_code": tool_error_code,
                            "tool_status": tool_status.value,
                        }
                    ]
                    if tool_status in tool_error_statuses
                    else []
                ),
                research_status=research_status,
                recommended_action=action,
            ),
            evidence_contract_sha256=contract_sha,
            tool_call_ids=list(dict.fromkeys(tool_call_ids)),
            research_round=invocation.research_round,
            research_mode=invocation.research_mode,
            agent_metadata={
                "reasoner": "mainline_research_tool_reasoner",
                "tool_gateway": tool_gateway_name,
                "tool_name": (
                    "knowledge.retrieve" if invocation.query.strip() else None
                ),
                "tool_result_status": tool_status.value,
                "canonical_assessment_status": raw_status,
                "retrieval_scope": invocation.retrieval_scope,
                "fake_payload_only": False,
                "production_adapter": True,
            },
        )


class _ModelReasonerBase:
    def __init__(self, *, model_gateway: ModelGatewayPort) -> None:
        self.model_gateway = model_gateway

    def _call(
        self,
        *,
        context: AgentExecutionContextSchema,
        purpose: str,
        model_role: ModelRole,
        prompt: str,
        max_tokens: int,
        model_call_id: str,
    ):
        boundary = ModelCallBoundary(
            model_gateway=self.model_gateway,
            model_role=model_role,
            runtime_context={
                "task_id": context.task_id,
                "workflow_run_id": context.run_id,
                "section_id": context.section_id,
                "caller_agent": f"{context.agent_role.value.title()}Agent",
                "generation_attempt": context.attempt_id,
            },
            default_purpose=purpose,
            call_suffix=context.invocation_id,
            budget_hook=reserve_current_workflow_budget,
        )
        response = boundary.generate_response(
            prompt,
            temperature=0.0,
            max_new_tokens=max_tokens,
            model_call_id=model_call_id,
            model_extra={
                "agent_invocation_id": context.invocation_id,
                "input_snapshot_version": context.input_snapshot_version,
                "reasoner_contract": "mainline_section_agent_v1",
            },
        )
        if not response.success:
            message = response.error_message or (
                response.error.message if response.error is not None else ""
            )
            raise RuntimeError(message or f"{purpose} model call failed")
        return response


class MainlineWriterModelReasoner(_ModelReasonerBase):
    """Use ModelGateway for Writer control output over the canonical draft."""

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools,
    ) -> WriterResultPayloadSchema:
        del tools
        invocation = WriterInvocationInputSchema.model_validate(invocation_input)
        section = invocation.section_snapshot
        if section is None:
            raise ValueError("Writer model reasoner requires section_snapshot")
        canonical = SchemeSectionSchema.model_validate(
            section.model_dump(mode="python", round_trip=True)
        )
        prompt = (
            "You are the WriterAgent control reasoner. The production writing "
            "service has already produced the canonical section below. Do not "
            "rewrite it. Return JSON only with keys generation_complete "
            "(boolean), generation_cursor (string or null), and note (short "
            "string). generation_complete may be true only when the section has "
            "non-empty complete content.\n\n"
            + json.dumps(
                {
                    "section_id": canonical.section_id,
                    "section_title": canonical.section_title,
                    "status": canonical.status.value,
                    "content": canonical.content,
                    "truncated": canonical.truncation.truncated,
                    "generation_check": invocation.generation_check,
                    "citation_required": invocation.citation_required,
                },
                ensure_ascii=False,
            )
        )
        response = self._call(
            context=context,
            purpose="writer_agent_control",
            model_role=ModelRole.SECTION_GENERATION,
            prompt=prompt,
            max_tokens=160,
            model_call_id=(
                f"model_call_{context.run_id}_{invocation.section_id}_"
                f"writer_agent_v{invocation.generation_attempt}"
            ),
        )
        parsed = _json_object(response.content)
        fallback_used = parsed is None
        canonical_complete = bool(
            canonical.content.strip() and not canonical.truncation.truncated
        )
        proposed_complete = (
            bool(parsed.get("generation_complete"))
            if parsed is not None
            else canonical_complete
        )
        generation_complete = bool(canonical_complete and proposed_complete)
        cursor = None
        if parsed is not None and parsed.get("generation_cursor") not in (None, ""):
            cursor = str(parsed.get("generation_cursor"))
        return WriterResultPayloadSchema(
            section_id=invocation.section_id,
            draft_ref=(
                f"state://section-draft/{invocation.section_id}/"
                f"v{invocation.generation_attempt}"
            ),
            draft_version=invocation.generation_attempt,
            paragraphs=[],
            generation_complete=generation_complete,
            expected_paragraph_ids=[],
            generation_cursor=cursor or invocation.generation_cursor,
            section_snapshot=canonical,
            writer_metadata={
                "reasoner": "mainline_writer_model_gateway_reasoner",
                "model_gateway_adapter": "ModelCallBoundary",
                "model_call_id": response.model_call_id,
                "model_name": response.model_name,
                "finish_reason": response.finish_reason,
                "parse_fallback_used": fallback_used,
                "model_decision": parsed or {},
                "canonical_section_status": canonical.status.value,
                "content_chars": len(canonical.content),
                "fake_payload_only": False,
                "production_adapter": True,
                "paragraph_pipeline_attached": False,
            },
        )


class MainlineVerifierModelReasoner(_ModelReasonerBase):
    """Use ModelGateway for an advisory decision with deterministic safety."""

    @staticmethod
    def _issue_id(section_id: str, message: str) -> str:
        digest = hashlib.sha256(
            f"{section_id}|{message}".encode("utf-8")
        ).hexdigest()[:16]
        return f"issue:{section_id}:{digest}"

    @staticmethod
    def _deterministic_action(
        invocation: VerifierInvocationInputSchema,
        canonical: SchemeSectionSchema,
    ) -> tuple[VerifierAction, str | None]:
        need_more = bool(invocation.generation_check.get("need_retrieve_more"))
        citation_bound = bool(
            canonical.eval_result
            and canonical.eval_result.checks.get("citation_bound", False)
        )
        if need_more:
            return VerifierAction.RESEARCH_MORE, "retrieve_more"
        if (
            invocation.citation_required
            and not invocation.evidence_blocked
            and not citation_bound
            and invocation.corrective_retrieval_enabled
        ):
            return VerifierAction.RESEARCH_MORE, "corrective_retrieval"
        if canonical.status == ExecutionStatus.SUCCESS and (
            canonical.eval_result is None or canonical.eval_result.passed
        ):
            return VerifierAction.ACCEPT, None
        if canonical.status == ExecutionStatus.PARTIAL_SUCCESS:
            return VerifierAction.PARTIAL_ACCEPT, None
        return VerifierAction.REJECT, None

    def invoke(
        self,
        *,
        context: AgentExecutionContextSchema,
        invocation_input: ContractSchema,
        tools,
    ) -> VerifierResultPayloadSchema:
        del tools
        invocation = VerifierInvocationInputSchema.model_validate(invocation_input)
        section = invocation.section_snapshot
        if section is None or invocation.section_id is None:
            raise ValueError("Verifier model reasoner requires section_snapshot")
        canonical = SchemeSectionSchema.model_validate(
            section.model_dump(mode="python", round_trip=True)
        )
        checks = {
            str(name): bool(value)
            for name, value in invocation.generation_check.items()
        }
        failures: list[str] = []
        warnings: list[str] = []
        if canonical.eval_result is not None:
            checks = dict(canonical.eval_result.checks)
            failures = list(canonical.eval_result.failures)
            warnings = list(canonical.eval_result.warnings)
        baseline, baseline_route = self._deterministic_action(invocation, canonical)
        prompt = (
            "You are the VerifierAgent for one generated section. Return JSON "
            "only with keys action, retrieval_route, and reason. action must be "
            "one of accept, partial_accept, reject, research_more, rewrite. "
            "Never accept when deterministic checks fail or a required citation "
            "is unbound.\n\n"
            + json.dumps(
                {
                    "section_id": invocation.section_id,
                    "section_title": canonical.section_title,
                    "status": canonical.status.value,
                    "checks": checks,
                    "failures": failures,
                    "warnings": warnings,
                    "citation_required": invocation.citation_required,
                    "evidence_blocked": invocation.evidence_blocked,
                    "deterministic_baseline": baseline.value,
                    "deterministic_retrieval_route": baseline_route,
                },
                ensure_ascii=False,
            )
        )
        response = self._call(
            context=context,
            purpose="verifier_agent_section_verify",
            model_role=ModelRole.SEMANTIC_GATE,
            prompt=prompt,
            max_tokens=160,
            model_call_id=(
                f"model_call_{context.run_id}_{invocation.section_id}_"
                f"verifier_agent_v{invocation.generation_attempt}"
            ),
        )
        parsed = _json_object(response.content)
        fallback_used = parsed is None
        model_action: VerifierAction | None = None
        model_route: str | None = None
        if parsed is not None:
            try:
                model_action = VerifierAction(str(parsed.get("action") or ""))
            except Exception:
                model_action = None
            candidate_route = str(parsed.get("retrieval_route") or "").strip()
            if candidate_route in {"retrieve_more", "corrective_retrieval"}:
                model_route = candidate_route

        # Deterministic rules remain the safety authority. Model advice can make
        # an already clean section more conservative, but cannot weaken a hard
        # retrieval/rejection decision.
        action = baseline
        retrieval_route = baseline_route
        if baseline == VerifierAction.ACCEPT and model_action in {
            VerifierAction.ACCEPT,
            VerifierAction.PARTIAL_ACCEPT,
            VerifierAction.REJECT,
            VerifierAction.RESEARCH_MORE,
            VerifierAction.REWRITE,
        }:
            action = model_action
            retrieval_route = (
                model_route or "retrieve_more"
                if action == VerifierAction.RESEARCH_MORE
                else None
            )

        issue_messages = list(dict.fromkeys([*failures, *warnings]))
        issue_ids = [
            self._issue_id(invocation.section_id, message)
            for message in issue_messages
        ]
        return VerifierResultPayloadSchema(
            execution_mode=VerifierExecutionMode.SECTION_VERIFY,
            action=action,
            issue_ids=issue_ids,
            accepted_paragraph_ids=[],
            rejected_paragraph_ids=[],
            verification_ref=(
                f"state://section-verification/{invocation.section_id}/"
                f"v{invocation.generation_attempt}"
            ),
            checks=checks,
            decision_metadata={
                "reasoner": "mainline_verifier_model_gateway_reasoner",
                "model_gateway_adapter": "ModelCallBoundary",
                "model_call_id": response.model_call_id,
                "model_name": response.model_name,
                "finish_reason": response.finish_reason,
                "parse_fallback_used": fallback_used,
                "model_decision": parsed or {},
                "model_action": model_action.value if model_action else None,
                "deterministic_baseline": baseline.value,
                "retrieval_route": retrieval_route,
                "canonical_section_status": canonical.status.value,
                "failure_count": len(failures),
                "warning_count": len(warnings),
                "fake_payload_only": False,
                "production_adapter": True,
            },
        )
