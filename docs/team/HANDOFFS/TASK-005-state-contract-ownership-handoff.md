# TASK-005 State / Contract / Ownership Handoff

## 1. Handoff Metadata

- Task ID: TASK-005
- From Role: Architect
- Primary Handoff Role: QA / Reviewer
- Secondary Handoff Roles:
  - Agent / Workflow Engineer for GraphState, status and checkpoint questions
  - RAG Engineer for evidence contract and citation remap questions
  - Data Engineer for unresolved offline lineage/provenance questions
  - Algorithm Lead for budget-loop semantic verification
  - AI Project Manager for baseline/release-state coordination
- Repository: `D:\MyCode\rag-agent`
- Branch: `main`
- Git HEAD: `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`
- Baseline: HEAD plus current dirty working tree
- Analysis File: `docs/team/ANALYSIS/TASK-005-state-contract-ownership.md`
- Business Code Modified: no
- Architecture Baseline Modified: no
- Contract Baseline Modified: no

## 2. 已确认事实

### 2.1 Baseline

1. TASK-001 to TASK-004 and TASK-005 use the same branch and HEAD commit metadata.
2. All investigations were performed against a dirty working tree.
3. No exact Git tag identifies the investigated baseline.
4. The current enterprise-document mainline is the explicit LangGraph business graph confirmed by TASK-001, not the coarse legacy SchemeWriter/Native workflow paths.

### 2.2 ProjectInput

1. `ProjectInputFactory.build` is the active ingress authority.
2. Production ingress uses strict `ProjectInputSchema.model_validate` and rejects task ID/user query mismatch.
3. `TaskFactory.build` converts the typed input to dictionary copies and derived task fields.
4. `SupervisorAgent` creates additional task/requirements copies in `GraphStateSchema`.
5. The normalize business node invokes `ProjectInputNormalizationUseCase.execute` directly.
6. `SharedStateWriter.set_project_input_normalization` writes `context_bundle.business.project_input` and a one-way `contexts.project_input` compatibility projection.
7. Downstream top-level business nodes consume typed `SchemeGenerationGraphPayloadSchema.project_input`.
8. `ProjectInputReader` uses priority selection across four candidates and does not assert equality among all populated candidates.
9. `ProjectInputNormalizerAgent.run` is inactive for the current enterprise-document mainline.

Primary evidence:

- `backend/apps/enterprise_document/schemas/project_input_schema.py`
- `backend/application/project_input_factory.py`
- `backend/application/task_factory.py`
- `backend/agent/runtime/state_access.py`
- `backend/apps/enterprise_document/services/project_input_normalization/input_reader.py`
- `backend/apps/enterprise_document/workflows/top_level_business_graph.py`

### 2.3 GraphState

1. `GraphStateSchema` is active and created by `SupervisorAgent.run/stream`.
2. Business nodes operate on deep copies.
3. `GraphStateDiffer` computes deltas; `GraphStateWriteContract` validates declared paths; `GraphStateApplier` commits atomically.
4. Undeclared writes, revision conflicts and hash mismatches fail closed.
5. Between nodes, durable authority is JSON-compatible LangGraph state/checkpoint data.
6. Top-level business payload is separate from `graph_state_payload`; complete durable business truth is not contained in GraphState alone.
7. `graph_revision`, `business_revision`, runtime transition count and task-state revision have different semantics.
8. `context_bundle` is declared the canonical typed context; `contexts` is compatibility projection.

Primary evidence:

- `backend/agent/runtime/graph_state.py`
- `backend/agent/runtime/shared_state_schema.py`
- `backend/agent/runtime/graph_state_ops.py`
- `backend/agent/runtime/langgraph_state.py`
- `backend/apps/enterprise_document/schemas/scheme_generation_graph.py`
- `backend/apps/enterprise_document/workflows/top_level_business_graph.py`

### 2.4 RAGEvidenceContract

1. `EvidenceBundleSchema` is the canonical evidence schema.
2. `RAGEvidenceContractSchema` is an alias to the same class, not a second implementation.
3. `RAGResultMapper` plus `RAGEvidenceContractBuilder` creates the initial RAG boundary contract.
4. `SchemeEvidenceService.extract_rag_output` derives context/chunks/citations and copies the full contract into normalized output and context extra.
5. `DocumentCitationRegistry.remap_bundle` assigns document-wide citation IDs, rewrites contract citations/items/context, validates a new contract and updates its hash.
6. The remapped contract under normalized `rag_output["evidence"]` is the current enterprise-document evidence authority after that boundary.
7. `context_bundle.evidence.contract`, context/chunks/citations and `RAGToolOutputSchema` compatibility fields are copies/projections.
8. `LegacyEvidenceAdapter` remains an active compatibility fallback, not the canonical path.

Primary evidence:

- `backend/schemas/rag.py`
- `backend/rag/evidence/contract.py`
- `backend/rag/mapping/result_mapper.py`
- `backend/apps/enterprise_document/services/scheme_writer/evidence_service.py`
- `backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`

### 2.5 CitationBinding

1. `CitationSchema` and `CitationBindingSchema` are separate contracts.
2. RAG citations do not become bindings automatically.
3. Model-emitted citation markers are removed before final binding.
4. `CitationService.bind_citations` creates occurrence-specific bindings from accepted candidate content.
5. `CitationService.supported_bindings` filters unsupported claim/evidence pairs.
6. `reconcile_citation_markers` removes visible markers without accepted bindings.
7. The filtered binding list on `SchemeSectionSchema.citation_bindings` is authoritative for section citation support.
8. Document assembly aggregates bindings; document and high-risk gates consume them.
9. Fan-in/remap may rebuild binding positions and IDs; rebuilt bindings supersede earlier coordinates.

Primary evidence:

- `backend/schemas/citation.py`
- `backend/apps/enterprise_document/services/scheme_writer/citation_service.py`
- `backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py`
- `backend/apps/enterprise_document/services/scheme_writer/document_assembler.py`
- `backend/apps/enterprise_document/services/document_gate.py`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`

### 2.6 ExecutionStatus

1. One shared `ExecutionStatus` enum is active across task/workflow/agent/tool/RAG/section schemas.
2. Status ownership is scoped; there is no one field authoritative for every layer.
3. `JsonlTaskManager` event log is authoritative for persisted task lifecycle and resume eligibility.
4. Task event append is the commit point; memory updates afterward.
5. GraphState/runtime status is workflow runtime state.
6. Business payload `output_status` is the gate/review decision before finalization.
7. Node output status is authoritative only for that node record.
8. `WorkflowEngineResult.status` and Supervisor `AgentResult.status` are workflow/application result boundaries.
9. Resume/cancel requires persisted paused task state, durable interrupt and checkpoint identity.
10. Stream reconciliation prefers already-persisted non-running task status, then durable interrupt/checkpoint status, then failure classification.

Primary evidence:

- `backend/schemas/status.py`
- `backend/task/task_manager.py`
- `backend/agent/services/task_lifecycle.py`
- `backend/agent/supervisor_agent.py`
- `backend/apps/enterprise_document/workflows/top_level_business_graph.py`

### 2.7 Trace

1. “Trace” denotes multiple non-equivalent active contracts.
2. Operational run trace authority is append-only per-run JSONL of `RunTraceEventSchema`.
3. `TraceSpanContext/TraceSpanHandle` is transient hierarchy propagation through a `ContextVar`.
4. `RAGTraceSchema` is retrieval metadata/lineage embedded in evidence.
5. `retrieval_trace_id` is a correlation ID, not a complete Trace v2 event stream.
6. Trace validator checks one trace/run/task identity, contiguous sequence, span start/terminal pairs, parent hierarchy, required event types, RAG lineage and prompt summarization.

Primary evidence:

- `backend/schemas/trace.py`
- `backend/data_capture/run_trace_recorder.py`
- `backend/observability/trace_context.py`
- `backend/observability/trace_reader.py`
- `backend/schemas/rag.py`
- business/tool/model/RAG observer callers

### 2.8 WorkflowBudget

1. `WorkflowBudget` is an active safety fuse.
2. In a running node, the active mutable object is authoritative.
3. Between section nodes/checkpoints, `SectionExecutionGraphPayloadSchema.budget_state` is authoritative.
4. Section defaults are retrieval 1, rewrite 1, logical LLM calls 35, reserved output tokens 24000.
5. Document RAG defaults are retrieval 4, rewrite 2, logical LLM calls 16, reserved output tokens 32000.
6. `llm_calls` counts logical model boundary calls, not provider fallback attempts.
7. `reserved_tokens` counts requested output allowance, not actual tokens.
8. Actual provider attempts/tokens/cost belong to `ModelUsageLedger`.
9. RAG evidence `budget_usage` is a separate derived metric.

Primary evidence:

- `backend/apps/enterprise_document/quality/budget.py`
- `backend/apps/enterprise_document/config/grounded_generation.py`
- `backend/apps/enterprise_document/config/grounded_generation_v1.yaml`
- `backend/apps/enterprise_document/schemas/section_execution_graph.py`
- `backend/apps/enterprise_document/workflows/section_execution_graph.py`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`
- `backend/model_gateway/usage_ledger.py`

## 3. Verification Completed

Existing tests executed:

```text
backend/tests/stage1/test_contracts.py
backend/tests/test_step_12_evidence_contract.py
backend/tests/test_step_13_trace_v2.py
backend/tests/test_step_15_graph_state_workflow_engine.py
backend/tests/test_citation_binding_regression.py
backend/tests/test_grounded_generation_budget.py
backend/tests/test_phase3_section_execution_subgraph.py
backend/tests/test_phase4_durable_resume_hitl.py
```

Result:

- 58 passed.
- The first command attempt did not run tests because Bash consumed Windows backslashes in the executable path.
- The corrected `./.venv/Scripts/python.exe` command passed.

Supplemental test attempt:

```text
backend/tests/test_p1_security_durability_identity_closure.py
backend/tests/test_phase2_runtime_safety_closure.py
backend/tests/test_side_effect_state_machine_closure.py
backend/tests/test_workflow_budget_exhaustion_semantics.py
```

Result:

- Collection stopped before test execution because `test_p1_security_durability_identity_closure.py` exposed a circular import involving `document_gate` and `scheme_writer.stage_service`.
- The remaining three supplemental files passed independently: 173 passed.
- Successful test executions across completed batches: 231.
- This is not a claim that the full repository test suite passes.
- No business code was changed and the collection issue was not fixed.

## 4. 未确认问题

1. Which immutable release/tag, if any, corresponds to the dirty working-tree baseline.
2. Whether every populated ProjectInput replica remains byte/semantic-equivalent through all real runtime paths.
3. Whether all evidence contract copies retain identical remapped contract hash through document and section recovery paths.
4. Whether final visible markers and CitationBinding occurrence coordinates remain equivalent after every parallel section fan-in/remap case.
5. Cross-process concurrent append safety when more than one process writes the same `<run_id>_trace.jsonl`.
6. Whether `created`, `queued`, or `skipped` are emitted by another current production task type outside TASK-005 scope.
7. Historical active index artifact commit provenance, already unresolved by TASK-003.
8. Full process-restart durability semantics of ModelUsageLedger aggregates beyond the inspected merge interfaces.

These questions must not be copied into `ARCHITECTURE.md` or `CONTRACTS.md` as established facts.

## 5. 新发现但未处理的问题

### Finding-005-01: ProjectInput replicas are priority-selected, not cross-validated

- Evidence: `SharedStateReader.project_input_candidates` and `ProjectInputReader.read`.
- Current behavior: first non-empty valid candidate wins.
- Impact statement: divergence can remain latent; this report does not establish that divergence occurs in a tested production run.
- Suggested owner: Agent / Workflow Engineer, then QA.
- Status: finding only; not fixed.

### Finding-005-02: RAG query budget metric has different and incomplete semantics

- Evidence: `RAGResultMapper` writes `budget_usage.queries_executed = 1 + len(rewritten_queries)`.
- Upstream corroboration: TASK-002 found HyDE/corrective executions can be omitted.
- Impact statement: this metric must not be used as WorkflowBudget or actual query-call truth.
- Suggested owner: RAG Engineer / Algorithm Lead, then QA.
- Status: finding only; not fixed.

### Finding-005-03: Generic “Trace” terminology hides multiple contracts

- Evidence: `RunTraceEventSchema`, `TraceSpanContext`, `RAGTraceSchema`, retrieval trace IDs.
- Impact statement: ownership must be stated with the concrete contract/storage boundary.
- Suggested owner: Architect / QA documentation review.
- Status: finding only; not fixed.

### Finding-005-04: Trace recorder cross-process same-run writer safety is unknown

- Evidence: recorder uses an instance `RLock` and line-count sequence initialization.
- Impact statement: no conclusion was found for multi-process writes to one run file.
- Suggested owner: Agent / Workflow Engineer or Observability owner, then QA.
- Status: unknown/finding only; not fixed.

### Finding-005-05: GraphState alone is not the complete durable business state

- Evidence: LangGraph runtime stores `graph_state_payload` and separate `scheme_generation_payload`; section graph stores another payload with `shared_state_payload` and budget/evidence/section state.
- Impact statement: readers must not infer full workflow truth from GraphState snapshot alone.
- Suggested owner: QA / Architect documentation review.
- Status: confirmed fact requiring acceptance wording; not a repair claim.

### Finding-005-06: WorkflowBudget, RAG budget metrics and ModelUsageLedger are non-equivalent

- Evidence: separate schemas, writers, counters and semantics.
- Impact statement: differences are expected unless a test explicitly maps them.
- Suggested owner: Algorithm Lead / QA.
- Status: confirmed fact; no fix performed.

### Finding-005-07: Isolated durability/identity test collection exposes a circular import

- Evidence chain:
  - `backend/tests/test_p1_security_durability_identity_closure.py`
  - `apps.enterprise_document.services.document_gate`
  - `apps.enterprise_document.services.scheme_writer.citation_integrity_service`
  - `scheme_writer.__init__`
  - `SchemeGenerationUseCase`
  - `SchemeGenerationStageService`
  - partially initialized `document_gate`
- Observed exception: `ImportError` for `evaluate_scheme_draft` from the partially initialized `document_gate` module.
- Impact statement: this test file could not be collected independently in the observed import order; no conclusion is made about whether another aggregate test order masks the condition.
- Suggested owner: Agent / Workflow Engineer or test/package owner, then QA.
- Status: finding only; not fixed.

## 6. QA Verification Requests

QA should verify the following against the same immutable baseline before TASK-005 facts are promoted to accepted architecture/contract documentation.

### QA-005-01 ProjectInput replica invariant

For production and demo entry modes, capture/hash:

- `TaskSchema.project_input`
- `GraphState.task.project_input`
- `GraphState.requirements.project_input`
- `context_bundle.business.project_input`
- `contexts.project_input`
- top-level business payload `project_input`
- section payload `project_input`

Expected review question: which differences are deterministic normalization/projection, and which would be a conflict?

### QA-005-02 GraphState write boundary and checkpoint restore

Exercise:

- successful declared write;
- undeclared write;
- revision conflict;
- checkpoint restore identity mismatch;
- pause/resume without replaying completed nodes.

Expected evidence: delta paths, revisions, hashes, checkpoint identity and terminal status.

### QA-005-03 Evidence authority after remap

For document, section and recovery retrieval:

- compare remapped contract hash in normalized output;
- compare context extra contract/hash;
- compare GraphState evidence copy;
- compare context/chunks/citations projections;
- verify no pre-remap citation ID survives as an active visible marker.

### QA-005-04 CitationBinding occurrence integrity

Exercise:

- duplicate source citation across retrieval scopes;
- parallel section fan-in;
- citation repair/regeneration;
- unsupported marker removal;
- document assembly and hard gate.

Expected evidence: marker occurrence ↔ binding ID ↔ citation ID ↔ claim coordinates.

### QA-005-05 Status convergence matrix

Exercise:

- success;
- partial success;
- technical failure;
- retryable failure;
- pause;
- approved resume;
- rejected resume;
- cancellation;
- stream consumer disconnect.

Compare:

- task event log status/revision;
- checkpoint GraphState/runtime status;
- business payload output status;
- WorkflowEngineResult;
- Supervisor AgentResult;
- terminal stream event.

### QA-005-06 Trace hierarchy and restart

Verify:

- one trace ID per run;
- contiguous event sequence;
- one root span;
- valid parent spans;
- exactly one start and terminal per span;
- RAG contract hash lineage;
- parallel section execution;
- recorder restart on an existing trace file.

Cross-process same-run writing should be reported separately because current code evidence does not establish support.

### QA-005-07 Budget/accounting semantics

For one section with retrieval recovery, rewrite and provider fallback, compare:

- checkpointed WorkflowBudget snapshot;
- final section `workflow_budget` report;
- RAG evidence `budget_usage`;
- ModelUsageLedger snapshot.

Expected result: counters differ according to documented semantics; QA should verify each counter against its own contract rather than require equality.

### QA-005-08 Isolated test collection

Run `backend/tests/test_p1_security_durability_identity_closure.py` in a fresh process and record:

- whether collection succeeds independently;
- the exact module import order;
- whether aggregate-suite order changes the result;
- whether any future correction preserves the state/identity assertions in that file.

Current TASK-005 evidence records the collection failure only; it does not claim a correction.

## 7. 建议交接对象

### Primary

- **QA / Reviewer**
  - Reason: TASK-005 has completed As-Is investigation and now needs runtime invariant and cross-object consistency verification.

### Secondary

- **Agent / Workflow Engineer**
  - GraphState candidate consistency, status convergence, checkpoint/trace process semantics, and the isolated package-import/test-collection finding.
- **RAG Engineer**
  - remapped evidence authority and RAG budget metric semantics.
- **Algorithm Lead**
  - WorkflowBudget versus actual model/retrieval loop counts.
- **Data Engineer**
  - unresolved historical index lineage/provenance.
- **AI Project Manager**
  - select/freeze an immutable baseline before acceptance.

## 8. 建议任务状态

- Recommended TASK-005 status: `review`
- Reason:
  - required investigation and evidence mapping are complete;
  - required ANALYSIS and HANDOFF files exist;
  - 231 tests passed across the completed targeted batches;
  - one supplemental test file failed isolated collection and is recorded as an unfixed Finding;
  - unresolved questions require QA validation, not further architectural inference;
  - no business code or architecture baseline was modified.

Do not mark `accepted` or `done` solely from this handoff. Acceptance should occur only after QA records evidence for the requested invariants and the project freezes the investigated baseline.

## 9. Explicit Non-claims

- No discovered issue is claimed to be fixed.
- No architecture or contract redesign is proposed in this handoff.
- No repository definition is treated as active solely because it exists.
- No dirty-working-tree conclusion is claimed to be reproducible from HEAD alone.
