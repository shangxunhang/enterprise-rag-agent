"""Production recovery guards for the Mainline document graphs.

This module deliberately owns no checkpoint, router, task status, or business
state.  It validates identities already persisted by the top-level and Section
LangGraph checkpoints and delegates the mutually-exclusive running invocation
policy to the existing deterministic service.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from apps.enterprise_document.schemas.agent_rag_v1 import (
    InvocationRecoveryAction,
    InvocationRecoveryDecisionSchema,
    RunningInvocationRecoveryContextSchema,
    RunningInvocationRecoveryInputSchema,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.planning import SectionPlanSchema
from apps.enterprise_document.schemas.scheme_writer.state_projection import (
    AttemptStateSchema,
    AttemptStatus,
    SectionLifecycleStatus,
    SectionStateSchema,
)
from apps.enterprise_document.schemas.section_execution_graph import (
    SectionExecutionGraphPayloadSchema,
)
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import (
    RunAttemptLifecycleService,
)
from apps.enterprise_document.services.agent_rag_v1.running_invocation_recovery import (
    RunningInvocationRecoveryService,
)
from contracts.evidence import evidence_bundle_sha256, project_evidence_bundle
from core.hashing import canonical_sha256


class StaleMainlineResultError(ValueError):
    """Raised before a stale Agent result can mutate production graph state."""


class MainlineAttemptGuard:
    """Validate one Agent result against the active Section attempt snapshot."""

    @staticmethod
    def snapshot_version(
        project_input: ProjectInputSchema,
        section_plan: SectionPlanSchema,
    ) -> str:
        serialized = json.dumps(
            {
                "project_input": project_input.model_dump(
                    mode="json",
                    round_trip=True,
                ),
                "section_plan": section_plan.model_dump(
                    mode="json",
                    round_trip=True,
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def attempt_scope_ref(section_id: str) -> str:
        normalized = str(section_id or "").strip()
        if not normalized:
            raise ValueError("section_id is required")
        return f"section:{normalized}"

    @classmethod
    def active_identity(
        cls,
        *,
        run_id: str,
        section_id: str,
        section_state: SectionStateSchema,
        project_input: ProjectInputSchema,
        section_plan: SectionPlanSchema,
        lifecycle_service: RunAttemptLifecycleService | None = None,
    ) -> dict[str, Any]:
        if lifecycle_service is not None:
            scope_ref = cls.attempt_scope_ref(section_id)
            lineage = lifecycle_service.resolve_active_lineage(run_id, scope_ref)
            current_attempt = lifecycle_service.load_active_attempt(
                run_id,
                scope_ref,
            )
            attempt_number = current_attempt.identity.attempt_ordinal
            attempt_id = lineage.attempt_id
            snapshot = lineage.input_snapshot_version
        else:
            # Compatibility projection only. Target commit/currentness paths
            # inject RunAttemptLifecycleService and never self-authorize from State.
            attempt_number = max(1, int(section_state.attempt.attempt_number or 0))
            attempt_id = str(section_state.attempt.attempt_id or "").strip()
            if not attempt_id:
                raise ValueError(
                    "active Section attempt_id is required as canonical write_instance_id"
                )
            snapshot = (
                str(section_state.attempt.input_snapshot_version or "").strip()
                or cls.snapshot_version(project_input, section_plan)
            )
        return {
            "attempt_number": attempt_number,
            "attempt_id": attempt_id,
            "input_snapshot_version": snapshot,
            "invocation_prefix": f"{run_id}:{section_id}:",
            "invocation_suffix": f":a{attempt_number}",
        }

    @classmethod
    def validate_result_metadata(
        cls,
        *,
        run_id: str,
        section_id: str,
        section_state: SectionStateSchema,
        project_input: ProjectInputSchema,
        section_plan: SectionPlanSchema,
        metadata: Mapping[str, Any],
        lifecycle_service: RunAttemptLifecycleService | None = None,
    ) -> dict[str, Any]:
        active = cls.active_identity(
            run_id=run_id,
            section_id=section_id,
            section_state=section_state,
            project_input=project_input,
            section_plan=section_plan,
            lifecycle_service=lifecycle_service,
        )
        invocation_id = str(metadata.get("invocation_id") or "").strip()
        execution_id = str(metadata.get("execution_id") or "").strip()
        attempt_id = str(metadata.get("attempt_id") or "").strip()
        snapshot = str(metadata.get("input_snapshot_version") or "").strip()
        missing = [
            name
            for name, value in (
                ("invocation_id", invocation_id),
                ("execution_id", execution_id),
                ("attempt_id", attempt_id),
                ("input_snapshot_version", snapshot),
            )
            if not value
        ]
        if missing:
            raise StaleMainlineResultError(
                "agent result identity is incomplete: " + ", ".join(missing)
            )
        violations: list[str] = []
        if attempt_id != active["attempt_id"]:
            violations.append("attempt_id")
        if snapshot != active["input_snapshot_version"]:
            violations.append("input_snapshot_version")
        if not invocation_id.startswith(active["invocation_prefix"]):
            violations.append("invocation_scope")
        if not invocation_id.endswith(active["invocation_suffix"]):
            violations.append("attempt_number")
        if execution_id != f"{invocation_id}:execution":
            violations.append("execution_id")
        if violations:
            raise StaleMainlineResultError(
                "stale agent result rejected: " + ", ".join(violations)
            )
        return {
            **active,
            "invocation_id": invocation_id,
            "execution_id": execution_id,
            "stale_result_rejected": False,
        }


class MainlineReplanAttempt:
    """Create one fresh Section attempt while retaining supersession lineage."""

    @classmethod
    def prepare(
        cls,
        *,
        payload: SectionExecutionGraphPayloadSchema,
        revised_plan: SectionPlanSchema,
        lifecycle_service: RunAttemptLifecycleService | None = None,
    ) -> SectionExecutionGraphPayloadSchema:
        section_id = str(payload.section_id or payload.section_plan.section_id)
        if revised_plan.section_id != section_id:
            raise ValueError("replan cannot change section identity")
        if revised_plan.section_order != payload.section_plan.section_order:
            raise ValueError("replan cannot change section order")
        if payload.section_state is None:
            raise ValueError("replan requires persisted Section state")

        previous = payload.section_state.attempt
        next_attempt = max(
            int(previous.attempt_number or 0),
            int(payload.generation_attempt or 1),
        ) + 1
        snapshot = MainlineAttemptGuard.snapshot_version(
            payload.project_input,
            revised_plan,
        )
        canonical_attempt_id: str | None = None
        if lifecycle_service is not None:
            opened = lifecycle_service.open_attempt(
                payload.run_id,
                MainlineAttemptGuard.attempt_scope_ref(section_id),
                snapshot,
            )
            next_attempt = opened.identity.attempt_ordinal
            canonical_attempt_id = opened.identity.attempt_id
        superseded = list(previous.metadata.get("superseded_attempts") or [])
        superseded.append(
            {
                "attempt_id": previous.attempt_id,
                "attempt_number": previous.attempt_number,
                "execution_id": previous.execution_id,
                "plan_revision": previous.plan_revision,
                "input_snapshot_version": previous.input_snapshot_version,
                "status": AttemptStatus.SUPERSEDED.value,
            }
        )
        attempt = AttemptStateSchema(
            attempt_id=(
                canonical_attempt_id
                or f"{payload.run_id}:{section_id}:attempt:{next_attempt}"
            ),
            attempt_number=next_attempt,
            execution_id=(
                f"{payload.run_id}:{section_id}:execution:{next_attempt}"
            ),
            plan_revision=max(1, previous.plan_revision) + 1,
            input_snapshot_version=snapshot,
            status=AttemptStatus.PENDING,
            metadata={
                "superseded_attempts": superseded,
                "replan_reason": str(
                    revised_plan.metadata.get("replan_reason") or "local_replan"
                ),
                "recovery_integration_round": "round8",
            },
        )
        section_state = SectionStateSchema(
            section_id=section_id,
            section_title=revised_plan.section_title,
            section_order=revised_plan.section_order,
            lifecycle_status=SectionLifecycleStatus.PLANNED,
            execution_group=revised_plan.execution_group,
            can_run_in_parallel=revised_plan.can_run_in_parallel,
            requires_global_context=revised_plan.requires_global_context,
            hard_dependencies=list(revised_plan.depends_on),
            attempt=attempt,
        )
        task_budget = dict(payload.budget_state.get("task_budget") or {})
        budget_state = (
            {
                "task_budget": task_budget,
                "task_draining": bool(task_budget.get("draining", False)),
            }
            if task_budget
            else {}
        )
        document_bundle = payload.document_evidence_bundle
        if document_bundle is None:
            raise ValueError("section replan requires canonical document_evidence_bundle")
        active_context, active_chunks, active_citations = (
            project_evidence_bundle(document_bundle)
        )
        active_query = document_bundle.query
        active_scope = document_bundle.retrieval_scope
        evidence_contract_sha256 = evidence_bundle_sha256(document_bundle)

        return payload.model_copy(
            update={
                "section_plan": revised_plan,
                "section_title": revised_plan.section_title,
                "section_order": revised_plan.section_order,
                "section_state": section_state,
                "citation_registry": list(payload.citation_registry),
                "active_evidence_bundle": document_bundle,
                "active_context": active_context,
                "active_chunks": active_chunks,
                "active_citations": active_citations,
                "active_query": active_query,
                "active_scope": active_scope,
                "tool_call_ids": list(payload.document_tool_call_ids),
                "retrieved_chunks": [],
                "rag_outputs": [],
                "retrieval_metadata": {
                    "replanned_from_attempt": previous.attempt_id,
                    "replan_attempt": next_attempt,
                    "recovery_integration_round": "round8",
                    "evidence_contract_sha256": evidence_contract_sha256,
                    "evidence_projection_source": "canonical_bundle",
                },
                "budget_state": budget_state,
                "research_agent_payloads": [],
                "writer_agent_payloads": [],
                "verifier_agent_payloads": [],
                "paragraph_candidates": [],
                "paragraph_artifacts": [],
                "paragraph_precheck_results": [],
                "paragraph_claims": [],
                "paragraph_cursor": 0,
                "paragraph_blocked_id": None,
                "paragraph_pipeline_revision": 0,
                "recovery_count": 0,
                "generation_attempt": next_attempt,
                "generation_check": {},
                "self_rag_retrieval_rounds": [],
                "pending_self_rag_round": None,
                "evidence_blocked": False,
                "budget_exhaustion_resource": None,
                "budget_exhaustion_limit": None,
                "section": None,
                "evidence": None,
                "result": None,
                "warnings": [],
                "error": None,
                "route": "prepare",
                "current_node": "not_started",
                "executed_nodes": [],
                "transition_count": 0,
                "completed": False,
            },
            deep=True,
        )


class MainlineRunningInvocationRecovery:
    """Build a recovery context directly from a persisted Section checkpoint."""

    def __init__(
        self,
        service: RunningInvocationRecoveryService | None = None,
        *,
        lifecycle_service: RunAttemptLifecycleService | None = None,
    ) -> None:
        self.service = service or RunningInvocationRecoveryService()
        self.lifecycle_service = lifecycle_service

    def _read_identity(self, payload) -> dict[str, Any]:
        if payload.section_state is None or payload.section_id is None:
            raise ValueError("running invocation recovery requires Section state")
        if self.lifecycle_service is None:
            return MainlineAttemptGuard.active_identity(run_id=payload.run_id,
                section_id=payload.section_id, section_state=payload.section_state,
                project_input=payload.project_input, section_plan=payload.section_plan)
        run = self.lifecycle_service.load_run_lifecycle(payload.run_id)
        current = self.lifecycle_service.load_latest_attempt(payload.run_id,
            MainlineAttemptGuard.attempt_scope_ref(payload.section_id))
        if run.identity.task_id != payload.task_id or current is None:
            raise ValueError("native Task/Run/Attempt recovery identity is unresolved")
        if (payload.active_execution_lineage is None
                or payload.active_execution_lineage.run_id != payload.run_id
                or payload.active_execution_lineage.attempt_scope_ref != MainlineAttemptGuard.attempt_scope_ref(payload.section_id)
                or payload.active_execution_lineage.attempt_id != current.identity.attempt_id
                or payload.active_execution_lineage.input_snapshot_version != current.identity.input_snapshot_version
                or payload.section_state.attempt.attempt_id != current.identity.attempt_id):
            raise ValueError("native recovery Attempt is no longer current")
        return {"attempt_number": current.identity.attempt_ordinal,
                "attempt_id": current.identity.attempt_id,
                "input_snapshot_version": current.identity.input_snapshot_version,
                "invocation_prefix": f"{payload.run_id}:{payload.section_id}:",
                "invocation_suffix": f":a{current.identity.attempt_ordinal}"}

    def writer_request(self, payload) -> RunningInvocationRecoveryInputSchema:
        active = self._read_identity(payload)
        key = payload.writer_logical_execution_key
        if key is None:
            raise ValueError("native Writer recovery key is unavailable")
        invocation_id = f"{payload.run_id}:{payload.section_id}:writer:a{active['attempt_number']}"
        return RunningInvocationRecoveryInputSchema(invocation_id=invocation_id,
            task_id=payload.task_id, run_id=payload.run_id,
            execution_id=f"{invocation_id}:execution", attempt_id=active["attempt_id"],
            input_snapshot_version=active["input_snapshot_version"],
            idempotency_key="writer-model-call:" + canonical_sha256({
                "run_id": payload.run_id, "section_id": payload.section_id,
                "attempt_id": active["attempt_id"],
                "route_decision_id": key.route_decision_ref.route_decision_id}))

    def reconcile_writer(self, payload, invocation=None) -> dict[str, Any]:
        try:
            expected = self.writer_request(payload)
            invocation = invocation or expected
            if any(getattr(invocation, field) != getattr(expected, field) for field in (
                    "task_id", "run_id", "attempt_id", "input_snapshot_version",
                    "invocation_id", "execution_id", "idempotency_key")):
                return self.service._indeterminate()
            generation = payload.pending_writer_generation_invocation
            generation_ref = payload.writer_generation_invocation_ref
            fingerprint = (canonical_sha256(generation.model_dump(mode="json")) if generation is not None
                           else generation_ref.invocation_sha256 if generation_ref is not None else None)
            if fingerprint is None:
                return self.service._indeterminate()
            return self.service.reconcile_writer(invocation, payload.writer_logical_execution_key,
                fingerprint, result_ref=payload.writer_execution_ref,
                generation_invocation_ref=generation_ref, generation_invocation=generation)
        except (ValueError, KeyError, TypeError, AttributeError):
            return self.service._indeterminate()

    def decide(
        self,
        *,
        invocation: RunningInvocationRecoveryInputSchema,
        payload: SectionExecutionGraphPayloadSchema,
    ) -> InvocationRecoveryDecisionSchema:
        if payload.section_state is None or payload.section_id is None:
            raise ValueError("running invocation recovery requires Section state")
        try:
            active = self._read_identity(payload)
        except (ValueError, KeyError):
            return self.service._decision(invocation, InvocationRecoveryAction.DO_NOT_RESUME, "INDETERMINATE")
        invocation_current = bool(
            invocation.task_id == payload.task_id
            and invocation.run_id == payload.run_id
            and invocation.attempt_id == active["attempt_id"]
            and invocation.input_snapshot_version
            == active["input_snapshot_version"]
            and invocation.invocation_id.startswith(active["invocation_prefix"])
            and invocation.invocation_id.endswith(active["invocation_suffix"])
            and invocation.execution_id == f"{invocation.invocation_id}:execution"
        )
        budget = dict(payload.budget_state or {})
        context = RunningInvocationRecoveryContextSchema(
            task_id=payload.task_id,
            run_id=payload.run_id,
            input_snapshot_version=active["input_snapshot_version"],
            active_execution_ids=(
                [invocation.execution_id] if invocation_current else []
            ),
            active_attempt_ids=[active["attempt_id"]],
            draining=bool(
                budget.get("task_draining")
                or budget.get("draining")
                or payload.budget_exhaustion_resource
            ),
            cancelled=False,
        )
        view = self.reconcile_writer(payload, invocation)
        if invocation_current and not context.draining and not context.cancelled:
            if view["outcome"] == "RECOVERED_DURABLE_RESULT":
                return self.service._decision(invocation, InvocationRecoveryAction.NO_ACTION,
                    view["outcome"], result_ref=view["result_ref"].result_sha256)
            if view["outcome"] == "DURABLE_FAILURE":
                return self.service._decision(invocation, InvocationRecoveryAction.DO_NOT_RESUME, view["outcome"])
        return self.service.decide(invocation, context)
