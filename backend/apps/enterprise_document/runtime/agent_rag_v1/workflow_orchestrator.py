"""Archived fixture orchestration for the retired Standalone runtime."""

from __future__ import annotations

LEGACY_FIXTURE_ONLY = True

from collections import defaultdict
from typing import Any, Iterable

from apps.enterprise_document.schemas.agent_rag_v1 import (
    AgentExecutionContextSchema,
    AgentExecutionResultSchema,
    AgentInvocationStatus,
    AgentRole,
    BudgetVectorSchema,
    ChangeSetSchema,
    ClaimLifecycleStatus,
    ClaimSchema,
    MultiAgentSharedStateSchema,
    PatchOperation,
    PlannerExecutionMode,
    PlannerInvocationInputSchema,
    ResearchInvocationInputSchema,
    ResearchRecommendedAction,
    ResumeRequestSchema,
    ResumeTriggerKind,
    RouterAction,
    RouterSignalKind,
    RouterSignalSchema,
    SectionStatus,
    StateActorRole,
    StatePatchActorSchema,
    StatePatchOperationSchema,
    StatePatchRequestSchema,
    TaskStatus,
    VerifierAction,
    VerifierExecutionMode,
    VerifierInvocationInputSchema,
    WriterInvocationInputSchema,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    StateGatewayError,
    TaskBudgetExhausted,
)

from .contracts import (
    SectionAttemptRecordSchema,
    StandaloneRunResultSchema,
)
from .runtime_factory import RuntimeComponents


_TERMINAL_SECTIONS = {
    SectionStatus.ACCEPTED,
    SectionStatus.PARTIALLY_ACCEPTED,
    SectionStatus.WAITING_HUMAN,
    SectionStatus.BLOCKED,
    SectionStatus.FAILED,
    SectionStatus.SKIPPED,
}


class WorkflowOrchestrator:
    """Owns scheduling and routing; business agents never call one another."""

    def __init__(self, components: RuntimeComponents) -> None:
        self.c = components
        self.state: MultiAgentSharedStateSchema | None = None
        self.latest_checkpoint = None
        self._patch_sequence = 0
        self._invocation_counts: dict[tuple[str, str], int] = defaultdict(int)
        self._attempt_counts: dict[str, int] = defaultdict(int)
        self._claims: dict[str, list[ClaimSchema]] = defaultdict(list)
        self._paragraph_hashes: dict[str, dict[str, str]] = defaultdict(dict)
        self._task_execution_id = f"task-execution:{self.c.project_input.run_id}"
        self._task_attempt_id = f"task-attempt:{self.c.project_input.run_id}"

    def run(
        self,
        initial_state: MultiAgentSharedStateSchema,
    ) -> StandaloneRunResultSchema:
        self.state = initial_state
        self.latest_checkpoint = self.c.checkpoint_store.save(self.state)
        self._patch(
            actor=StateActorRole.SUPERVISOR,
            target_domain="task_context",
            values={"task_status": TaskStatus.RUNNING},
            execution_id=self._task_execution_id,
            attempt_id=self._task_attempt_id,
            reason_code="standalone_runtime_started",
        )
        try:
            self._invoke_planner(
                mode=PlannerExecutionMode.INITIAL_PLAN,
                target_section_ids=list(self.state.section_states),
            )
            self.c.dag_scheduler.validate(self.state)
            self._run_section_dag()
        except TaskBudgetExhausted as exc:
            self._enter_draining(exc)
        except Exception as exc:
            self._persist_runtime_error("task", exc)
            self._fail_all_open_sections("runtime_unhandled_failure")
        return self._close_document()

    def _run_section_dag(self) -> None:
        assert self.state is not None
        while True:
            batch = self.c.dag_scheduler.next_batch(
                self.state,
                max_parallel_sections=self.c.project_input.max_parallel_sections,
            )
            if batch.section_ids:
                self.c.trace.section_batches.append(list(batch.section_ids))
            for section_id in batch.blocked_section_ids:
                self._fail_section(section_id, "hard_dependency_block")
            if not batch.section_ids:
                break
            for section_id in batch.section_ids:
                if self.c.budget_manager.snapshot().draining:
                    self._fail_section(section_id, "task_budget_draining")
                    continue
                try:
                    self._execute_section(
                        section_id,
                        plan_revision=max(1, self.state.execution_plan.plan_version),
                    )
                except TaskBudgetExhausted as exc:
                    self._enter_draining(exc)
                    return
                except Exception as exc:
                    error_ref = self._persist_runtime_error(section_id, exc)
                    self._fail_section(section_id, error_ref)
            if self.c.budget_manager.snapshot().draining:
                return

    def _execute_section(
        self,
        section_id: str,
        *,
        plan_revision: int,
    ) -> None:
        assert self.state is not None
        _, attempt_id = self._start_attempt(
            section_id,
            plan_revision=plan_revision,
        )
        while True:
            research = self._invoke_research(section_id)
            action = research.payload.evidence_summary.recommended_action
            if action == ResearchRecommendedAction.RESEARCH_MORE:
                decision = self._route(
                    RouterSignalKind.RESEARCH_MORE,
                    section_id,
                )
                if decision == RouterAction.RESEARCH_MORE:
                    continue
            elif action == ResearchRecommendedAction.HUMAN_REVIEW:
                self._route(
                    RouterSignalKind.SECTION_HUMAN_REVIEW,
                    section_id,
                )
                self._transition_section(
                    section_id,
                    SectionStatus.WAITING_HUMAN,
                    last_error_ref=research.payload.evidence_package_ref,
                )
                self._finish_attempt(attempt_id, "completed")
                return
            elif action == ResearchRecommendedAction.PARTIAL_DELIVERY:
                self._route(RouterSignalKind.REJECT, section_id)
                self._fail_section(
                    section_id,
                    research.payload.evidence_package_ref,
                )
                self._finish_attempt(attempt_id, "failed")
                return

            self._transition_section(
                section_id,
                SectionStatus.EVIDENCE_READY,
            )
            break

        while True:
            self._transition_section(section_id, SectionStatus.GENERATING)
            writer = self._invoke_writer(section_id)
            if (
                self.c.project_input.scenario == "crash-recovery"
                and section_id == "compute"
                and not writer.payload.generation_complete
                and not self.c.trace.recovery_performed
            ):
                self._recover_interrupted_writer(section_id)
                writer = self._invoke_writer(section_id)
            self._commit_agent_result(writer)
            self._update_claims(section_id)

            if not writer.payload.generation_complete:
                self._transition_section(
                    section_id,
                    SectionStatus.PAUSED_FOR_PRECHECK,
                    generation_cursor=writer.payload.generation_cursor,
                )
                decision = self._route(RouterSignalKind.REWRITE, section_id)
                if decision != RouterAction.REWRITE:
                    self._fail_section(section_id, "writer_partial_not_repaired")
                    self._finish_attempt(attempt_id, "failed")
                    return
                continue

            self._transition_section(
                section_id,
                SectionStatus.GENERATED,
                generation_cursor=writer.payload.generation_cursor,
            )
            self._transition_section(section_id, SectionStatus.VERIFYING)
            verifier = self._invoke_verifier(section_id)
            self._commit_agent_result(verifier)
            verifier_action = verifier.payload.action

            if verifier_action == VerifierAction.ACCEPT:
                self._route(RouterSignalKind.ACCEPT, section_id)
                self._transition_section(section_id, SectionStatus.ACCEPTED)
                self._finish_attempt(attempt_id, "completed")
                return
            if verifier_action == VerifierAction.PARTIAL_ACCEPT:
                self._route(RouterSignalKind.PARTIAL_ACCEPT, section_id)
                self._transition_section(
                    section_id,
                    SectionStatus.PARTIALLY_ACCEPTED,
                )
                self._finish_attempt(attempt_id, "completed")
                return
            if verifier_action == VerifierAction.REWRITE:
                self._route(RouterSignalKind.REWRITE, section_id)
                self._transition_section(section_id, SectionStatus.GENERATING)
                continue
            if verifier_action == VerifierAction.RESEARCH_MORE:
                self._route(RouterSignalKind.RESEARCH_MORE, section_id)
                self._transition_section(section_id, SectionStatus.RESEARCHING)
                return self._resume_research_loop(
                    section_id,
                    attempt_id=attempt_id,
                )
            if verifier_action == VerifierAction.REPLAN:
                self._route(RouterSignalKind.REPLAN, section_id)
                self._replan_section(
                    section_id,
                    stale_result=verifier,
                    old_attempt_id=attempt_id,
                )
                return
            if verifier_action == VerifierAction.HUMAN_REVIEW:
                self._route(
                    RouterSignalKind.SECTION_HUMAN_REVIEW,
                    section_id,
                )
                self._transition_section(
                    section_id,
                    SectionStatus.WAITING_HUMAN,
                    last_error_ref=verifier.payload.verification_ref,
                )
                self._finish_attempt(attempt_id, "completed")
                return
            self._route(RouterSignalKind.REJECT, section_id)
            self._fail_section(section_id, verifier.payload.verification_ref)
            self._finish_attempt(attempt_id, "failed")
            return

    def _resume_research_loop(
        self,
        section_id: str,
        *,
        attempt_id: str,
    ) -> None:
        assert self.state is not None
        while True:
            research = self._invoke_research(section_id)
            action = research.payload.evidence_summary.recommended_action
            if action == ResearchRecommendedAction.RESEARCH_MORE:
                self._route(RouterSignalKind.RESEARCH_MORE, section_id)
                continue
            if action != ResearchRecommendedAction.WRITE:
                self._route(RouterSignalKind.REJECT, section_id)
                self._fail_section(
                    section_id,
                    research.payload.evidence_package_ref,
                )
                self._finish_attempt(attempt_id, "failed")
                return
            self._transition_section(
                section_id,
                SectionStatus.EVIDENCE_READY,
            )
            self._transition_section(section_id, SectionStatus.GENERATING)
            writer = self._invoke_writer(section_id)
            self._commit_agent_result(writer)
            self._update_claims(section_id)
            if not writer.payload.generation_complete:
                self._fail_section(section_id, "writer_partial_after_research")
                self._finish_attempt(attempt_id, "failed")
                return
            self._transition_section(section_id, SectionStatus.GENERATED)
            self._transition_section(section_id, SectionStatus.VERIFYING)
            verifier = self._invoke_verifier(section_id)
            self._commit_agent_result(verifier)
            if verifier.payload.action == VerifierAction.ACCEPT:
                self._route(RouterSignalKind.ACCEPT, section_id)
                self._transition_section(section_id, SectionStatus.ACCEPTED)
                self._finish_attempt(attempt_id, "completed")
            else:
                self._route(RouterSignalKind.REJECT, section_id)
                self._fail_section(
                    section_id,
                    verifier.payload.verification_ref,
                )
                self._finish_attempt(attempt_id, "failed")
            return

    def _replan_section(
        self,
        section_id: str,
        *,
        stale_result: AgentExecutionResultSchema,
        old_attempt_id: str,
    ) -> None:
        assert self.state is not None
        resume = self.c.resume_router.decide(
            ResumeRequestSchema(
                task_id=self.state.state_meta.task_id,
                section_ids=[section_id],
                trigger_kind=ResumeTriggerKind.VERIFIER_REPLAN,
                trigger_ref=stale_result.payload.verification_ref,
            )
        )
        if resume.reason_code != ResumeTriggerKind.VERIFIER_REPLAN.value:
            raise RuntimeError("ResumeRouter rejected verifier replan")
        self._invoke_planner(
            mode=PlannerExecutionMode.LOCAL_REPLAN,
            target_section_ids=[section_id],
            replan=True,
        )
        next_revision = self.state.execution_plan.plan_version
        self._transition_section(
            section_id,
            SectionStatus.PLANNED,
            execution_id=None,
            active_attempt_id=None,
            active_plan_version=next_revision,
            generation_cursor=None,
        )
        self._finish_attempt(old_attempt_id, "superseded")
        _, new_attempt_id = self._start_attempt(
            section_id,
            plan_revision=next_revision,
        )
        try:
            self._commit_agent_result(stale_result)
        except StateGatewayError:
            self.c.trace.stale_write_rejected = True
        else:
            raise RuntimeError("stale section attempt unexpectedly committed")
        self._resume_research_loop(
            section_id,
            attempt_id=new_attempt_id,
        )

    def _recover_interrupted_writer(self, section_id: str) -> None:
        assert self.state is not None
        recovered = self.c.checkpoint_store.load_latest(
            self.state.state_meta.task_id,
            self.state.state_meta.run_id,
        )
        if recovered is None:
            raise RuntimeError("writer recovery checkpoint is missing")
        events = self.c.writer_event_log.list_events(
            self.state.state_meta.task_id,
            self.state.state_meta.run_id,
            section_id,
        )
        reused = [
            event.paragraph.paragraph_id
            for event in events
            if hasattr(event, "paragraph")
        ]
        self.state = recovered
        self.c.trace.recovery_performed = True
        self.c.trace.checkpoint_event_recovery = True
        self.c.trace.reused_paragraph_ids = reused

    def _invoke_planner(
        self,
        *,
        mode: PlannerExecutionMode,
        target_section_ids: list[str],
        replan: bool = False,
    ) -> AgentExecutionResultSchema:
        assert self.state is not None
        context = self._context(
            role=AgentRole.PLANNER,
            execution_mode=mode.value,
            section_id=None,
            expected_domains=["execution_plan"],
            writable_paths=[
                "/plan_ref",
                "/plan_version",
                "/active_plan_revision_id",
                "/section_order",
            ],
            authorized_refs=[
                self.state.source_snapshot.template_schema_ref,
                self.state.source_snapshot.source_inventory_ref,
                self.state.source_snapshot.fact_snapshot_ref,
                *(
                    [self.state.execution_plan.plan_ref]
                    if self.state.execution_plan.plan_ref
                    else []
                ),
            ],
        )
        invocation = PlannerInvocationInputSchema(
            template_schema_ref=self.state.source_snapshot.template_schema_ref,
            source_inventory_ref=self.state.source_snapshot.source_inventory_ref,
            fact_snapshot_ref=self.state.source_snapshot.fact_snapshot_ref,
            current_plan_ref=self.state.execution_plan.plan_ref,
            target_section_ids=target_section_ids,
            constraints={"scenario": self.c.project_input.scenario},
        )
        result = self._call_agent(
            self.c.planner_agent,
            context,
            invocation,
            BudgetVectorSchema(
                model_calls=1,
                total_replans=1 if replan else 0,
            ),
        )
        self._commit_agent_result(result)
        self.c.trace.plan_revisions.append(result.payload.plan_ref)
        return result

    def _invoke_research(
        self,
        section_id: str,
    ) -> AgentExecutionResultSchema:
        assert self.state is not None
        section = self.state.section_states[section_id]
        context = self._context(
            role=AgentRole.RESEARCH,
            execution_mode="section_research",
            section_id=section_id,
            expected_domains=["section.research"],
            writable_paths=[
                "/evidence_package_ref",
                "/evidence_ids",
                "/evidence_gap_count",
            ],
            authorized_refs=[
                self.state.source_snapshot.fact_snapshot_ref,
                self.state.source_snapshot.source_inventory_ref,
                *(
                    [section.research.evidence_package_ref]
                    if section.research.evidence_package_ref
                    else []
                ),
            ],
            authorized_tools=["knowledge.retrieve"],
        )
        invocation = ResearchInvocationInputSchema(
            section_id=section_id,
            section_task_ref=context.section_state_ref,
            fact_snapshot_ref=self.state.source_snapshot.fact_snapshot_ref,
            source_inventory_ref=self.state.source_snapshot.source_inventory_ref,
            evidence_topics=["overview", "implementation"],
            prior_evidence_package_ref=section.research.evidence_package_ref,
        )
        result = self._call_agent(
            self.c.research_agent,
            context,
            invocation,
            BudgetVectorSchema(model_calls=1, tool_calls=1),
        )
        self._commit_agent_result(result)
        return result

    def _invoke_writer(
        self,
        section_id: str,
    ) -> AgentExecutionResultSchema:
        assert self.state is not None
        section = self.state.section_states[section_id]
        context = self._context(
            role=AgentRole.WRITER,
            execution_mode="section_generate",
            section_id=section_id,
            expected_domains=["section.writing"],
            writable_paths=[
                "/draft_ref",
                "/draft_version",
                "/paragraph_refs",
                "/paragraph_versions",
                "/expected_paragraph_ids",
                "/generation_complete",
                "/change_manifest_ref",
            ],
            authorized_refs=[
                self.state.source_snapshot.fact_snapshot_ref,
                section.research.evidence_package_ref,
                *([section.writing.draft_ref] if section.writing.draft_ref else []),
                *(
                    [section.verification.verification_ref]
                    if section.verification.verification_ref
                    else []
                ),
            ],
        )
        invocation = WriterInvocationInputSchema(
            section_id=section_id,
            strategy=section.strategy,
            fact_snapshot_ref=self.state.source_snapshot.fact_snapshot_ref,
            evidence_package_ref=section.research.evidence_package_ref,
            base_draft_ref=section.writing.draft_ref,
            verifier_issue_refs=(
                [section.verification.verification_ref]
                if section.verification.verification_ref
                else []
            ),
            generation_cursor=section.execution.generation_cursor,
        )
        revision = self.c.trace.writer_revisions.get(section_id, 0)
        return self._call_agent(
            self.c.writer_agent,
            context,
            invocation,
            BudgetVectorSchema(
                model_calls=1,
                total_rewrites=1 if revision else 0,
            ),
        )

    def _invoke_verifier(
        self,
        section_id: str,
    ) -> AgentExecutionResultSchema:
        assert self.state is not None
        section = self.state.section_states[section_id]
        context = self._context(
            role=AgentRole.VERIFIER,
            execution_mode=VerifierExecutionMode.SECTION_VERIFY.value,
            section_id=section_id,
            expected_domains=["section.verification"],
            writable_paths=[
                "/execution_mode",
                "/recommended_action",
                "/verification_ref",
                "/issue_ids",
                "/accepted_paragraph_ids",
                "/rejected_paragraph_ids",
            ],
            authorized_refs=[
                self.state.source_snapshot.fact_snapshot_ref,
                section.writing.draft_ref,
                *(
                    [section.claims.extraction_ref]
                    if section.claims.extraction_ref
                    else []
                ),
            ],
        )
        invocation = VerifierInvocationInputSchema(
            execution_mode=VerifierExecutionMode.SECTION_VERIFY,
            target_artifact_ref=section.writing.draft_ref,
            fact_snapshot_ref=self.state.source_snapshot.fact_snapshot_ref,
            section_id=section_id,
            claim_ids=section.claims.active_claim_ids,
            binding_refs=(
                [section.claims.extraction_ref]
                if section.claims.extraction_ref
                else []
            ),
        )
        return self._call_agent(
            self.c.verifier_agent,
            context,
            invocation,
            BudgetVectorSchema(model_calls=1),
        )

    def _call_agent(
        self,
        agent: Any,
        context: AgentExecutionContextSchema,
        invocation: Any,
        budget: BudgetVectorSchema,
    ) -> AgentExecutionResultSchema:
        reservation_id = f"budget:{context.invocation_id}"
        self.c.budget_manager.reserve(reservation_id, budget)
        try:
            result = agent.run(context, invocation)
        except Exception:
            self.c.budget_manager.release(reservation_id)
            raise
        self.c.budget_manager.settle(reservation_id, budget)
        self.c.trace.agent_invocations.append(
            f"{context.agent_role.value}:{context.section_id or 'task'}:"
            f"{context.execution_mode}"
        )
        if context.agent_role == AgentRole.RESEARCH:
            assert context.section_id is not None
            self.c.trace.research_rounds[context.section_id] = (
                self.c.trace.research_rounds.get(context.section_id, 0) + 1
            )
            self.c.trace.retrieval_tool_calls += len(result.tool_call_refs)
        elif context.agent_role == AgentRole.WRITER:
            assert context.section_id is not None
            self.c.trace.writer_revisions[context.section_id] = (
                self.c.trace.writer_revisions.get(context.section_id, 0) + 1
            )
        elif context.agent_role == AgentRole.VERIFIER:
            assert context.section_id is not None
            self.c.trace.verifier_rounds[context.section_id] = (
                self.c.trace.verifier_rounds.get(context.section_id, 0) + 1
            )
        return result

    def _commit_agent_result(
        self,
        result: AgentExecutionResultSchema,
    ) -> None:
        if result.status == AgentInvocationStatus.FAILED:
            message = result.error.message if result.error else "agent failed"
            raise RuntimeError(message)
        if not result.proposed_state_patches:
            raise RuntimeError("agent result did not propose a state patch")
        for request in result.proposed_state_patches:
            self._commit_request(request)

    def _context(
        self,
        *,
        role: AgentRole,
        execution_mode: str,
        section_id: str | None,
        expected_domains: list[str],
        writable_paths: list[str],
        authorized_refs: Iterable[str | None],
        authorized_tools: list[str] | None = None,
    ) -> AgentExecutionContextSchema:
        assert self.state is not None
        section_key = section_id or "task"
        key = (role.value, section_key)
        self._invocation_counts[key] += 1
        invocation_id = (
            f"{role.value}:{section_key}:{self._invocation_counts[key]:03d}"
        )
        if section_id is None:
            execution_id = self._task_execution_id
            attempt_id = self._task_attempt_id
            section_state_ref = None
        else:
            execution = self.state.section_states[section_id].execution
            if not execution.execution_id or not execution.active_attempt_id:
                raise RuntimeError("section invocation has no active attempt")
            execution_id = execution.execution_id
            attempt_id = execution.active_attempt_id
            section_state_ref = (
                f"artifact://{self.state.state_meta.task_id}/runtime-context/"
                f"{invocation_id}"
            )
            self.c.artifact_store.put_json(
                self.state.section_states[section_id].model_dump(mode="json"),
                artifact_type="section_runtime_context",
                schema_version="section_runtime_context_v1",
                created_by="workflow_orchestrator",
                task_id=self.state.state_meta.task_id,
                section_id=section_id,
                attempt_id=attempt_id,
                input_snapshot_version=(
                    self.state.state_meta.input_snapshot_version
                ),
                artifact_ref=section_state_ref,
            )
        refs = sorted({str(ref) for ref in authorized_refs if ref})
        return AgentExecutionContextSchema(
            invocation_id=invocation_id,
            logical_operation_id=f"{role.value}:{section_key}",
            task_id=self.state.state_meta.task_id,
            run_id=self.state.state_meta.run_id,
            section_id=section_id,
            agent_role=role,
            execution_mode=execution_mode,
            execution_id=execution_id,
            attempt_id=attempt_id,
            input_snapshot_version=self.state.state_meta.input_snapshot_version,
            expected_domain_versions={
                domain: self._domain_version(domain, section_id)
                for domain in expected_domains
            },
            task_context_ref=self.state.task_context.task_context_ref,
            source_snapshot_ref=self.state.source_snapshot.source_snapshot_ref,
            execution_plan_ref=self.state.execution_plan.plan_ref,
            section_state_ref=section_state_ref,
            authorized_artifact_refs=refs,
            authorized_tool_names=list(authorized_tools or []),
            writable_state_paths=writable_paths,
            budget=self.c.budget_manager.snapshot().model_dump(mode="json"),
            trigger={"scenario": self.c.project_input.scenario},
            trace_context={"trace_ref": f"trace://{invocation_id}"},
        )

    def _update_claims(self, section_id: str) -> None:
        assert self.state is not None
        section = self.state.section_states[section_id]
        contents = {
            paragraph_id: self.c.artifact_store.get_bytes(
                artifact_ref,
                task_id=self.state.state_meta.task_id,
                section_id=section_id,
                input_snapshot_version=(
                    self.state.state_meta.input_snapshot_version
                ),
            ).decode("utf-8")
            for paragraph_id, artifact_ref in section.writing.paragraph_refs.items()
        }
        current_hashes = {
            paragraph_id: section.writing.paragraph_versions[paragraph_id]
            for paragraph_id in contents
        }
        previous_hashes = self._paragraph_hashes[section_id]
        added = set(current_hashes) - set(previous_hashes)
        deleted = set(previous_hashes) - set(current_hashes)
        modified = {
            paragraph_id
            for paragraph_id in set(current_hashes) & set(previous_hashes)
            if current_hashes[paragraph_id] != previous_hashes[paragraph_id]
        }
        update = self.c.incremental_claim_pipeline.update(
            section_id=section_id,
            current_paragraphs=contents,
            previous_claims=self._claims[section_id],
            added_paragraph_ids=added,
            modified_paragraph_ids=modified,
            deleted_paragraph_ids=deleted,
            fact_snapshot=self.c.fact_snapshot,
        )
        self._claims[section_id] = update.claims
        self._paragraph_hashes[section_id] = current_hashes
        claim_version = section.claims.claim_set_version + 1
        extraction_ref = (
            f"artifact://{self.state.state_meta.task_id}/claims/{section_id}/"
            f"v{claim_version}"
        )
        self.c.artifact_store.put_json(
            update.model_dump(mode="json"),
            artifact_type="claim_extraction",
            schema_version=update.schema_version,
            created_by="claim_pipeline",
            task_id=self.state.state_meta.task_id,
            section_id=section_id,
            attempt_id=section.execution.active_attempt_id or self._task_attempt_id,
            input_snapshot_version=self.state.state_meta.input_snapshot_version,
            artifact_ref=extraction_ref,
        )
        active_claim_ids = [
            claim.claim_id
            for claim in update.claims
            if claim.lifecycle_status == ClaimLifecycleStatus.ACTIVE
        ]
        self._patch(
            actor=StateActorRole.CLAIM_PIPELINE,
            target_domain="section.claims",
            section_id=section_id,
            values={
                "claim_ids": [claim.claim_id for claim in update.claims],
                "active_claim_ids": active_claim_ids,
                "claim_set_version": claim_version,
                "extraction_ref": extraction_ref,
            },
            execution_id=section.execution.execution_id or "",
            attempt_id=section.execution.active_attempt_id or "",
            reason_code="incremental_claims_updated",
        )

    def _start_attempt(
        self,
        section_id: str,
        *,
        plan_revision: int,
    ) -> tuple[str, str]:
        self._attempt_counts[section_id] += 1
        number = self._attempt_counts[section_id]
        execution_id = f"execution:{section_id}:{number:03d}"
        attempt_id = f"attempt:{section_id}:{number:03d}"
        self._transition_section(
            section_id,
            SectionStatus.RESEARCHING,
            execution_id=execution_id,
            active_attempt_id=attempt_id,
            active_plan_version=plan_revision,
            generation_cursor=None,
            last_error_ref=None,
        )
        self.c.trace.attempt_records.append(
            SectionAttemptRecordSchema(
                section_id=section_id,
                attempt_id=attempt_id,
                execution_id=execution_id,
                plan_revision=plan_revision,
                status="active",
            )
        )
        return execution_id, attempt_id

    def _finish_attempt(self, attempt_id: str, status: str) -> None:
        for record in reversed(self.c.trace.attempt_records):
            if record.attempt_id == attempt_id:
                record.status = status
                return

    def _transition_section(
        self,
        section_id: str,
        status: SectionStatus,
        **updates: Any,
    ) -> None:
        values = {"status": status, **updates}
        self._patch(
            actor=StateActorRole.ROUTER,
            target_domain="section.execution",
            section_id=section_id,
            values=values,
            execution_id=str(updates.get("execution_id") or "router"),
            attempt_id=str(updates.get("active_attempt_id") or "router"),
            reason_code=f"section_transition_{status.value}",
        )

    def _route(
        self,
        kind: RouterSignalKind,
        section_id: str | None,
    ) -> RouterAction:
        assert self.state is not None
        decision = self.c.workflow_router.decide(
            self.state,
            [
                RouterSignalSchema(
                    signal_id=(
                        f"signal:{kind.value}:{section_id or 'task'}:"
                        f"{len(self.c.trace.router_actions) + 1}"
                    ),
                    kind=kind,
                    section_id=section_id,
                )
            ],
        )
        if decision is None:
            raise RuntimeError("WorkflowRouter returned no decision")
        self.c.trace.router_actions.append(decision.action.value)
        return decision.action

    def _enter_draining(self, exc: TaskBudgetExhausted) -> None:
        assert self.state is not None
        self.c.budget_manager.start_draining()
        self._route(RouterSignalKind.TASK_BUDGET_EXHAUSTED, None)
        self._patch(
            actor=StateActorRole.ROUTER,
            target_domain="runtime_control",
            values={"draining": True},
            execution_id="router",
            attempt_id="router",
            reason_code=f"budget_exhausted_{exc.resource}",
        )
        if self.state.task_context.task_status == TaskStatus.RUNNING:
            self._patch(
                actor=StateActorRole.SUPERVISOR,
                target_domain="task_context",
                values={"task_status": TaskStatus.DRAINING},
                execution_id=self._task_execution_id,
                attempt_id=self._task_attempt_id,
                reason_code="task_entered_draining",
            )
        self._fail_all_open_sections(f"budget_exhausted:{exc.resource}")

    def _fail_all_open_sections(self, reason: str) -> None:
        assert self.state is not None
        for section_id, section in list(self.state.section_states.items()):
            if section.execution.status not in _TERMINAL_SECTIONS:
                self._fail_section(section_id, reason)

    def _fail_section(self, section_id: str, reason: str) -> None:
        assert self.state is not None
        section = self.state.section_states[section_id]
        if section.execution.status in _TERMINAL_SECTIONS:
            return
        self._transition_section(
            section_id,
            SectionStatus.FAILED,
            last_error_ref=reason,
        )
        if section.execution.active_attempt_id:
            self._finish_attempt(section.execution.active_attempt_id, "failed")

    def _persist_runtime_error(self, scope: str, exc: Exception) -> str:
        assert self.state is not None
        error_ref = (
            f"artifact://{self.state.state_meta.task_id}/runtime-error/"
            f"{scope}-{self._patch_sequence + 1}"
        )
        self.c.artifact_store.put_json(
            {
                "scope": scope,
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
            artifact_type="runtime_error",
            schema_version="runtime_error_v1",
            created_by="workflow_orchestrator",
            task_id=self.state.state_meta.task_id,
            section_id=scope if scope in self.state.section_states else None,
            attempt_id=self._task_attempt_id,
            input_snapshot_version=self.state.state_meta.input_snapshot_version,
            artifact_ref=error_ref,
        )
        return error_ref

    def _close_document(self) -> StandaloneRunResultSchema:
        assert self.state is not None
        self._fail_all_open_sections("section_did_not_converge")
        section_ids = list(self.state.section_states)
        group = self.c.group_aggregator.aggregate(
            self.state,
            group_id="standalone-document",
            section_ids=section_ids,
            waiting_human_downgraded=True,
        )
        self.c.trace.group_status = group.status.value
        verification = self.c.document_verifier.verify(
            state=self.state,
            claims=[
                claim
                for section_claims in self._claims.values()
                for claim in section_claims
            ],
            fact_snapshot=self.c.fact_snapshot,
        )
        document_verification_ref = (
            f"artifact://{self.state.state_meta.task_id}/"
            f"document-verification/{self.state.state_meta.run_id}"
        )
        self.c.artifact_store.put_json(
            verification.model_dump(mode="json"),
            artifact_type="document_verification",
            schema_version=verification.schema_version,
            created_by="document_verifier",
            task_id=self.state.state_meta.task_id,
            attempt_id=self._task_attempt_id,
            input_snapshot_version=self.state.state_meta.input_snapshot_version,
            artifact_ref=document_verification_ref,
        )
        self._patch(
            actor=StateActorRole.VERIFIER,
            target_domain="document_validation",
            values={
                "verification_ref": document_verification_ref,
                "status": "passed" if verification.passed else "failed",
                "issue_ids": [
                    conflict.conflict_id for conflict in verification.conflicts
                ],
            },
            execution_id=self._task_execution_id,
            attempt_id=self._task_attempt_id,
            reason_code="document_verification_completed",
        )
        version = self.c.version_policy.decide(
            previous_version=self.c.project_input.previous_document_version,
            changes=ChangeSetSchema(
                broad_replan=self.c.project_input.scenario == "replan"
            ),
        )
        budget = self.c.budget_manager.snapshot()
        report = self.c.generation_report.build(
            self.state,
            document_version=version.next_version,
            budget_usage=budget.model_dump(mode="json"),
        )
        report_ref = self.c.generation_report.persist(
            report,
            artifact_store=self.c.artifact_store,
            attempt_id=self._task_attempt_id,
            input_snapshot_version=self.state.state_meta.input_snapshot_version,
        )
        assembled = self.c.document_assembler.assemble(
            state=self.state,
            report=report,
            generation_report_ref=report_ref,
            document_title=self.c.template.document_title,
            attempt_id=self._task_attempt_id,
        )
        assembly_validation = self.c.assembly_validator.validate(
            state=self.state,
            assembled=assembled,
            report=report,
        )
        assembly_validation_ref = (
            f"artifact://{self.state.state_meta.task_id}/"
            f"assembly-validation/{version.next_version}"
        )
        self.c.artifact_store.put_json(
            assembly_validation.model_dump(mode="json"),
            artifact_type="assembly_validation",
            schema_version=assembly_validation.schema_version,
            created_by="assembly_validator",
            task_id=self.state.state_meta.task_id,
            attempt_id=self._task_attempt_id,
            input_snapshot_version=self.state.state_meta.input_snapshot_version,
            artifact_ref=assembly_validation_ref,
        )
        final_review = self.c.final_artifact_review.review(
            assembled=assembled,
            assembly_validation=assembly_validation,
            report=report,
        )
        published = self.c.version_publisher.publish(
            assembled=assembled,
            report=report,
            attempt_id=self._task_attempt_id,
            input_snapshot_version=self.state.state_meta.input_snapshot_version,
        )
        self._patch(
            actor=StateActorRole.ASSEMBLER,
            target_domain="artifact_state",
            values={
                "assembly_ref": assembled.document_artifact_ref,
                "final_artifact_ref": published.document_artifact_ref,
                "generation_report_ref": published.generation_report_ref,
                "document_version": published.document_version,
            },
            execution_id=self._task_execution_id,
            attempt_id=self._task_attempt_id,
            reason_code="document_artifacts_published",
        )
        self._patch(
            actor=StateActorRole.ASSEMBLY_VALIDATOR,
            target_domain="artifact_state",
            values={"assembly_validation_ref": assembly_validation_ref},
            execution_id=self._task_execution_id,
            attempt_id=self._task_attempt_id,
            reason_code="assembly_validation_recorded",
        )
        self._patch(
            actor=StateActorRole.SUPERVISOR,
            target_domain="task_context",
            values={"task_status": report.task_status},
            execution_id=self._task_execution_id,
            attempt_id=self._task_attempt_id,
            reason_code="standalone_task_completed",
        )
        self._patch(
            actor=StateActorRole.SUPERVISOR,
            target_domain="final_result",
            values={
                "task_status": report.task_status,
                "document_version": published.document_version,
                "document_artifact_ref": published.document_artifact_ref,
                "generation_report_ref": published.generation_report_ref,
                "published": True,
            },
            execution_id=self._task_execution_id,
            attempt_id=self._task_attempt_id,
            reason_code="final_result_published",
        )
        document_content = self.c.artifact_store.get_bytes(
            published.document_artifact_ref,
            task_id=self.state.state_meta.task_id,
        )
        exported = self.c.document_exporter.export(
            task_id=self.state.state_meta.task_id,
            run_id=self.state.state_meta.run_id,
            document_version=published.document_version,
            document_content=document_content,
            generation_report=report.model_dump(mode="json"),
        )
        assert self.latest_checkpoint is not None
        return StandaloneRunResultSchema(
            task_id=self.state.state_meta.task_id,
            run_id=self.state.state_meta.run_id,
            task_status=report.task_status,
            document_version=published.document_version,
            section_results=report.section_results,
            document_artifact_ref=published.document_artifact_ref,
            generation_report_ref=published.generation_report_ref,
            document_artifact_path=exported.document_path,
            generation_report_path=exported.generation_report_path,
            checkpoint_path=str(self.latest_checkpoint),
            event_log_path=str(self.c.paths.event_log_path),
            writer_event_log_path=str(self.c.paths.writer_event_log_path),
            assembly_validation_passed=assembly_validation.passed,
            final_artifact_review_passed=final_review.passed,
            generation_report=report,
            final_state=self.state,
            trace=self.c.trace,
        )

    def _patch(
        self,
        *,
        actor: StateActorRole,
        target_domain: str,
        values: dict[str, Any],
        execution_id: str,
        attempt_id: str,
        reason_code: str,
        section_id: str | None = None,
    ) -> None:
        assert self.state is not None
        self._patch_sequence += 1
        request = StatePatchRequestSchema(
            request_id=f"runtime-patch:{self._patch_sequence:05d}",
            task_id=self.state.state_meta.task_id,
            section_id=section_id,
            actor=StatePatchActorSchema(
                agent_role=actor,
                execution_id=execution_id,
                attempt_id=attempt_id,
            ),
            input_snapshot_version=self.state.state_meta.input_snapshot_version,
            expected_domain_version=self._domain_version(
                target_domain,
                section_id,
            ),
            target_domain=target_domain,
            operations=[
                StatePatchOperationSchema(
                    operation=PatchOperation.REPLACE,
                    path=f"/{name}",
                    value=value,
                )
                for name, value in values.items()
            ],
            reason_code=reason_code,
        )
        self._commit_request(request)

    def _commit_request(self, request: StatePatchRequestSchema) -> None:
        assert self.state is not None
        committed = self.c.state_gateway.commit(self.state, request)
        self.state = committed.state
        if committed.committed:
            self.c.trace.state_commit_count += 1
        self.latest_checkpoint = self.c.checkpoint_store.save(self.state)

    def _domain_version(
        self,
        target_domain: str,
        section_id: str | None,
    ) -> int:
        assert self.state is not None
        if target_domain.startswith("section."):
            assert section_id is not None
            field = target_domain.split(".", 1)[1]
            versions = self.state.domain_versions.sections.get(section_id)
            return int(getattr(versions, field)) if versions else 0
        return int(getattr(self.state.domain_versions, target_domain))
