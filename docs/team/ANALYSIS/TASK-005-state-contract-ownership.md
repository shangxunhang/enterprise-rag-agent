# TASK-005 State / Contract / Ownership Investigation

## 1. Investigation Metadata

- Task ID: TASK-005
- Title: State, Contract and Ownership Investigation
- Owner Role: Architect
- Investigation Status: completed_for_review
- Repository: `D:\MyCode\rag-agent`
- Branch: `main`
- Git HEAD: `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`
- Exact Git Tag: none
- Investigation Baseline: Git HEAD plus the current uncommitted working tree
- Baseline Warning: the working tree contains many modified and untracked files, including the code and upstream TASK-001 to TASK-004 outputs used by this investigation. Therefore this report does not claim that HEAD alone reproduces the investigated implementation.
- Business Code Modified: no
- `docs/team/ARCHITECTURE.md` Modified: no
- `docs/team/CONTRACTS.md` Modified: no
- Upstream Inputs Read:
  - `docs/team/PROJECT.md`
  - `docs/team/ORGANIZATION.md`
  - `docs/team/WORKFLOW.md`
  - `docs/team/POLICIES.md`
  - `docs/team/STATUS.md`
  - `docs/team/TASKS/TASK-005-state-contract-ownership.md`
  - TASK-001 to TASK-004 ANALYSIS and HANDOFF files

## 2. Scope and Method

This investigation is limited to the current lifecycle, read/write ownership, persistence, projection and duplication status of:

1. `ProjectInput`
2. `GraphState`
3. `RAGEvidenceContract`
4. `CitationBinding`
5. `ExecutionStatus`
6. `Trace`
7. `WorkflowBudget`

Method:

- Follow the current production entry and explicit enterprise-document LangGraph path confirmed by TASK-001.
- Follow the current RAG boundary confirmed by TASK-002.
- Use TASK-003 only where offline lineage/scope fields affect contract truth.
- Use TASK-004 only where algorithm placement writes or consumes state/contract objects.
- Treat definitions without an active constructor/caller/consumer path as insufficient evidence of activation.
- Classify each implementation as `active`, `inactive`, `legacy`, `experimental`, or `unknown`.

Out of scope:

- Architecture redesign.
- Contract redesign.
- Business-code changes.
- Fixing any discovered issue.
- Updating `ARCHITECTURE.md` or `CONTRACTS.md`.

## 3. Upstream Baseline Consistency

| Upstream task | Branch | Commit | Working tree | Main conclusion used here |
|---|---|---|---|---|
| TASK-001 | `main` | `07dda2e...` | dirty | Current enterprise-document mainline is `MainlineApplicationService -> SupervisorAgent -> GraphStateSchema -> EnterpriseDocumentWorkflowEngine` explicit business graph. |
| TASK-002 | `main` | `07dda2e...` | dirty | Current RAG mainline returns canonical `EvidenceBundleSchema`; projections are derived from the contract. |
| TASK-003 | `main` | `07dda2e...` | dirty | Current active index lineage and scope fields are runtime inputs to RAG evidence lineage; historical artifact provenance remains partly unknown. |
| TASK-004 | `main` | `07dda2e...` | dirty | Current algorithm loops execute in explicit document/section graphs; section budgets and evidence/citation checks are active. |

Consistency result:

- Branch and commit metadata are consistent across TASK-001 to TASK-004.
- All four upstream investigations and TASK-005 were performed against a dirty working tree.
- No upstream contradiction was found regarding the current enterprise-document mainline.
- The baseline is not a clean, immutable release baseline.

## 4. Ownership Vocabulary

This report uses the following ownership labels:

- **authoritative**: the object or field is the current source used to make the next business/control decision at that boundary.
- **derived projection**: deterministically reconstructed from an authoritative object; it must not overwrite the authority.
- **replica/cache**: a copied representation retained for checkpointing, compatibility, reporting or transport.
- **conflicting candidate**: multiple candidates exist and runtime selection can depend on priority rather than equality enforcement.
- **unknown**: code evidence is insufficient to assign authority.

## 5. Object Status Summary

| Object | Current authoritative boundary | Main replicas/projections | Status |
|---|---|---|---|
| ProjectInput | `ProjectInputSchema` produced by `ProjectInputFactory`, then normalized business payload `SchemeGenerationGraphPayloadSchema.project_input` after the normalize node | `TaskSchema.project_input`, `GraphState.task.project_input`, `GraphState.requirements.project_input`, `context_bundle.business.project_input`, `contexts.project_input`, section request/payload copies | active |
| GraphState | In-memory `GraphStateSchema` inside one validated node transaction; between nodes, JSON `graph_state_payload` in LangGraph checkpoint state | `shared_state` in final `AgentResult`, section `shared_state_payload`, inspection output | active |
| RAGEvidenceContract | Remapped `EvidenceBundleSchema` stored under normalized `rag_output["evidence"]` after `DocumentCitationRegistry.remap_bundle`; the business payload carries that normalized output | `RAGToolOutputSchema` compatibility fields, `RAGContext.extra.evidence_contract`, `context_bundle.evidence.contract`, chunk/citation/context projections | active |
| CitationBinding | Filtered `CitationBindingSchema` list attached to final `SchemeSectionSchema.citation_bindings`; document assembly aggregates these | marker text, `citation_ids`, assembled draft binding list, gate reports | active |
| ExecutionStatus | Context-dependent: persisted task truth is `JsonlTaskManager` event log; workflow result truth is terminal `AgentResult/WorkflowEngineResult`; node status belongs to node record | `GraphState.status`, runtime context status, stream event status, payload output status | active |
| Trace | Run trace authority is append-only `RunTraceEventSchema` JSONL per run; active span context controls hierarchy only | trace validation report, RAG embedded `RAGTraceSchema`, retrieval trace IDs, capture references | active, but the word “Trace” denotes multiple non-equivalent contracts |
| WorkflowBudget | During a node: active mutable `WorkflowBudget`; between section nodes: serialized `SectionExecutionGraphPayloadSchema.budget_state` | section `extra.workflow_budget`, document RAG output snapshot, model usage ledger is a separate accounting authority | active |

## 6. ProjectInput

### 6.1 Lifecycle Diagram

```text
External caller / run_demo adapter
        |
        v
ProjectInputFactory.build
  - production: strict ProjectInputSchema.model_validate
  - demo: versioned demo seed + explicit defaults
        |
        v
TaskFactory.build
  - TaskSchema.project_input = normalized.model_dump()
  - separate source_materials / generation_requirements / output_schema copies
        |
        v
SupervisorAgent.run / stream
  - GraphState.task = task.model_dump()
  - GraphState.requirements.project_input = task.project_input
  - ContextBundleFactory creates initial context bundle
        |
        v
normalize_project_input business node
  - ProjectInputReader checks candidates in priority order
  - ProjectInputNormalizationUseCase validates/analyzes
  - SharedStateWriter writes canonical business context + compatibility projection
  - business payload.project_input receives typed ProjectInputSchema
        |
        v
plan / RAG / section graph / generation / gate
  - consume typed payload.project_input and checkpoint-safe copies
```

### 6.2 Definition and Validation

**Evidence record**

- File path: `backend/apps/enterprise_document/schemas/project_input_schema.py`
- Class: `ProjectInputSchema`
- Function: `align_generation_and_output_sections`
- Upstream caller: `ProjectInputFactory.build`, `ProjectInputReader.read`, graph payload validation and checkpoint restore
- Downstream caller: `TaskFactory.build`, normalization, planning, RAG request construction, section generation and document gate
- Input: strict project/business fields, source materials, generation requirements and output schema
- Output: validated `ProjectInputSchema`
- State changes:
  - trims and aligns generation/output required sections;
  - writes missing `business_goal` from `user_query`;
  - writes missing document title from project name or a neutral default;
  - normalizes citation-required sections.
- Exception behavior:
  - duplicate required sections -> `ValueError`;
  - generation/output section mismatch -> `ValueError`;
  - no required sections -> `ValueError`;
  - citation-required section outside required sections -> `ValueError`;
  - extra fields are rejected by strict Pydantic configuration.
- Classification: active

### 6.3 Entry Ownership

**Evidence record**

- File path: `backend/application/project_input_factory.py`
- Class: `ProjectInputFactory`
- Function: `build`
- Upstream caller: `TaskFactory.build`
- Downstream caller: `ProjectInputSchema.model_validate`
- Input: runtime task ID, user input, raw project input, demo-default flag
- Output: canonical entry `ProjectInputSchema`
- State changes: none outside the returned model; the model validator may normalize fields as stated above
- Exception behavior:
  - invalid/extra/missing fields -> Pydantic validation error;
  - runtime task ID mismatch -> `ValueError`;
  - runtime user input mismatch -> `ValueError`.
- Ownership:
  - production entry result: authoritative at ingress;
  - demo seed/default adapter: active only when explicitly selected by `allow_demo_defaults=True`.
- Classification: active

### 6.4 Task and Graph Mapping

| Source field | Writer | Destination | Destination ownership | Equality enforcement |
|---|---|---|---|---|
| entire `ProjectInputSchema` | `TaskFactory.build` | `TaskSchema.project_input` dictionary | replica/transport copy | validated at factory time |
| `tenant_id` | `TaskFactory.build` | `TaskSchema.tenant_id` | derived top-level routing field | copied from canonical input |
| `task_type` | `TaskFactory.build` | `TaskSchema.task_type` | derived routing field | copied from canonical input |
| `user_query` | `TaskFactory.build` | `TaskSchema.user_input` | derived runtime field | copied and checked at ingress |
| source material IDs | `TaskFactory.build` | `TaskSchema.file_ids/doc_ids/kb_ids` | derived routing/filter projection | derived from source material lists |
| task dump | `SupervisorAgent.run/stream` | `GraphState.task` | replica | no subsequent whole-object equality check |
| generation requirements + ProjectInput | `SupervisorAgent.run/stream` | `GraphState.requirements` | replica/input candidate | no subsequent whole-object equality check |
| initial project input candidate | `ProjectInputReader.read` | normalization use case | selected candidate | first non-empty candidate wins |
| normalized input | `SharedStateWriter.set_project_input_normalization` | `context_bundle.business.project_input` | authoritative GraphState business view | written by normalize node |
| normalized input | same | `contexts.project_input` | compatibility replica | one-way projection comment; no reverse authority |
| normalized typed input | `_normalize_project_input` | business payload `project_input` | authoritative for downstream top-level business nodes | validated by payload schema/checkpoint codec |
| typed project input | section graph request/payload constructors | section request/payload `project_input` | checkpoint-safe section-local replica | Pydantic validation on restore |

### 6.5 Candidate Conflict

**Evidence record**

- File path: `backend/agent/runtime/state_access.py`
- Class: `SharedStateReader`
- Function: `project_input_candidates`
- Upstream caller: `ProjectInputReader.read`
- Downstream caller: `ProjectInputSchema.model_validate`
- Input: `SharedStateSchema`
- Output: ordered list of four candidates
- State changes: none
- Exception behavior: none in this function; reader raises if no candidate validates
- Candidate priority:
  1. `context_bundle.business.project_input`
  2. `requirements.project_input`
  3. `task.project_input`
  4. `contexts.project_input`
- Ownership finding: after normalization, candidate 1 is authoritative. Before normalization it is usually empty and candidate 2 is selected. No code in this reader asserts that all populated candidates are identical.
- Classification: active

### 6.6 ProjectInput Findings

1. **Multiple ProjectInput copies are active.** They are not all authoritative. The current intended authority shifts from ingress model to normalized business payload/context.
2. **Selection is priority-based, not consensus-based.** If populated candidates diverge, `ProjectInputReader` returns the first valid candidate and does not compare hashes or values.
3. **`contexts.project_input` is explicitly a one-way compatibility projection.** It must not be used to overwrite `context_bundle.business.project_input`.
4. **The registered `ProjectInputNormalizerAgent.run` is not the current enterprise mainline executor.** TASK-001 confirmed the business graph calls `ProjectInputNormalizationUseCase.execute` directly. Classification: inactive for the current enterprise mainline.

## 7. GraphState

### 7.1 Lifecycle Diagram

```text
SupervisorAgent.run / stream
        |
        v
GraphStateSchema constructed in memory
        |
        v
EnterpriseDocumentWorkflowEngine.execute
  - deep copy
  - status set RUNNING on initial copy
  - LangGraphRuntimeStateCodec.dump_graph_state
        |
        v
LangGraph JSON state + checkpoint
  graph_state_payload
  scheme_generation_payload
        |
        v
for each business node
  restore -> deep-copy working state -> stage mutates copy
  -> GraphStateDiffer.diff
  -> declared path validation
  -> GraphStateApplier.apply atomically
  -> serialize/checkpoint
        |
        v
terminal/pause restore
  -> project_onto original Supervisor-owned GraphState
  -> WorkflowEngineResult
  -> AgentResult shared_state snapshot
```

### 7.2 Definition

**Evidence record**

- File path: `backend/agent/runtime/graph_state.py`
- Class: `GraphStateSchema`
- Function: model construction/validation
- Upstream caller: `SupervisorAgent.run`, `SupervisorAgent.stream`, checkpoint codecs
- Downstream caller: enterprise workflow engine, normalization/RAG/generation services, final result serialization
- Input: task identity, task/workflow/requirements dictionaries, typed context bundle, timestamps and routing metadata
- Output: mutable runtime state model
- State changes: business state, runtime status, node history, artifacts and compatibility projections
- Exception behavior: Pydantic validation errors on invalid state/restore
- Classification: active

### 7.3 Canonical and Compatibility Fields

`SharedStateSchema` explicitly declares:

- `context_bundle` is the canonical source for new business/evidence/generation/runtime state.
- `contexts` and `structured_facts` are retained compatibility views during migration.
- `SharedStateWriter` owns one-way compatibility projection.

Not all fields are single-copy:

| Semantic fact | Canonical location | Replica/projection |
|---|---|---|
| normalized project input | top-level business payload and `context_bundle.business.project_input` | `requirements.project_input`, `task.project_input`, `contexts.project_input`, section payload copies |
| workflow current step | `context_bundle.runtime.current_step` for typed context | top-level `state.current_step` |
| workflow step states | `context_bundle.runtime.workflow_step_states` | `state.workflow_step_states` |
| runtime errors | `context_bundle.runtime.errors` | `state.errors` |
| evidence contract | remapped contract in business payload normalized RAG output | `context_bundle.evidence.contract`, context extra, chunk/citation/context projections |
| generation context | `context_bundle.generation` | section payload and final artifact snapshots |
| terminal artifact | explicit `generated_draft/rejected_artifact/deliverable_final_result` | `final_result` generic compatibility field and AgentResult snapshot |

### 7.4 Validated Write/Commit Boundary

**Evidence record**

- File path: `backend/agent/runtime/graph_state_ops.py`
- Classes: `GraphStateDiffer`, `GraphStateWriteContract`, `GraphStateApplier`
- Functions: `diff`, `validate`, `apply`
- Upstream caller: `EnterpriseDocumentWorkflowEngine._validate_stage` and `_append_execution_record`
- Downstream caller: serialized LangGraph runtime state/checkpoint
- Input: before/after deep copies, declared GraphState write paths
- Output: validated `GraphStateDeltaSchema`, then committed `GraphStateSchema`
- State changes:
  - only changed roots/paths in the validated delta are committed;
  - `graph_revision` increments;
  - `current_node_id` is set;
  - node history/completion fields are appended by the engine after commit.
- Exception behavior:
  - undeclared path -> `StateWriteContractViolation`;
  - base revision mismatch -> `ValueError`;
  - post-commit hash mismatch -> `ValueError`.
- Ownership: in-memory state after `GraphStateApplier.apply` is authoritative inside that node completion boundary.
- Classification: active

### 7.5 Durable Representation

**Evidence record**

- File paths:
  - `backend/agent/runtime/langgraph_state.py`
  - `backend/apps/enterprise_document/schemas/scheme_generation_graph.py`
- Classes:
  - `LangGraphRuntimeStateCodec`
  - `SchemeGenerationGraphStateCodec`
- Functions:
  - `dump_graph_state`, `restore_graph_state`, `build_initial_state`
  - `dump_payload`, `restore_payload`, `build_initial_update`
- Upstream caller: `EnterpriseDocumentWorkflowEngine.execute/resume/_restore/_append_execution_record`
- Downstream caller: LangGraph checkpointer, inspect/resume/result conversion
- Input: validated GraphState and typed business payload
- Output: detached JSON-compatible mappings
- State changes: serialized state is written into checkpoint channels
- Exception behavior:
  - state version mismatch -> `ValueError`;
  - missing payload -> `ValueError`;
  - task/run/workflow identity mismatch -> `ValueError`;
  - non-JSON value -> serialization exception.
- Ownership:
  - between nodes and across process continuation, checkpoint JSON is authoritative durable state;
  - restored Pydantic models are validated working copies.
- Classification: active

### 7.6 Revision Semantics

- `graph_revision` increments for validated GraphState business delta commits.
- `business_revision` increments for every committed top-level business payload node.
- `transition_count` counts runtime transitions.
- task-state `revision` belongs to a separate event-sourced aggregate.
- These counters are not interchangeable.
- `graph_revision` does not version all runtime/lifecycle mutations; the source comment states this explicitly.

### 7.7 GraphState Findings

1. GraphState has an active controlled-write contract and atomic validation boundary.
2. Durable truth between nodes is the checkpoint-safe JSON envelope, not a live model instance.
3. `GraphState.status` and `context_bundle.runtime.status` are intentionally duplicated and written together at explicit boundaries, but they are still separate fields.
4. Top-level business payload contains typed business objects that are not all stored directly in GraphState; therefore “GraphState” alone does not contain the complete durable business state.
5. The generic Native/legacy workflow engine exists in the repository but is not the active current enterprise-document mainline confirmed by TASK-001. Classification for current enterprise mainline: inactive/legacy path.

## 8. RAGEvidenceContract

### 8.1 Lifecycle Diagram

```text
RAG runtime raw result
       |
       v
RAGResultMapper.map
  -> RAGTraceSchema
  -> chunks/citations
  -> RAGEvidenceContractBuilder.build
  -> EvidenceBundleSchema
       |
       v
SchemeEvidenceService.extract_rag_output
  -> projections: context/chunks/citations
  -> full contract copied into context.extra and normalized["evidence"]
       |
       v
DocumentCitationRegistry.remap_bundle
  -> document-wide citation IDs
  -> contract items/context/citations remapped
  -> contract revalidated
       |
       v
StageService / section graph payload
  -> normalized RAG output + context/chunks/citations
  -> SharedStateWriter evidence projection
       |
       v
section generation / assembly / gate / trace lineage
```

### 8.2 Schema and Invariants

**Evidence record**

- File path: `backend/schemas/rag.py`
- Class: `EvidenceBundleSchema`; alias `RAGEvidenceContractSchema = EvidenceBundleSchema`
- Function: `validate_contract`
- Upstream caller: `RAGEvidenceContractBuilder.build`, contract remap/restore, tests
- Downstream caller: RAG output, scheme evidence extraction, generation context and trace lineage
- Input: query, evidence items, selected/dropped IDs, citations, context, lineage, assessment, budget and trace
- Output: validated canonical evidence bundle
- State changes: validator overwrites assessment counts/availability from actual selected/dropped/citation lists
- Exception behavior:
  - duplicate evidence/citation IDs -> `ValueError`;
  - selected/dropped overlap or unknown IDs -> `ValueError`;
  - disposition mismatch -> `ValueError`;
  - dropped evidence carrying prompt citation IDs -> `ValueError`;
  - context char/token/count mismatch -> `ValueError`.
- Classification: active

The alias does not create a second runtime schema. It is a compatibility name for the same class.

### 8.3 Initial Contract Creation

**Evidence record**

- File paths:
  - `backend/rag/mapping/result_mapper.py`
  - `backend/rag/evidence/contract.py`
- Classes:
  - `RAGResultMapper`
  - `RAGEvidenceContractBuilder`
- Functions:
  - mapper’s bundle construction path
  - `RAGEvidenceContractBuilder.build`
- Upstream caller: `RAGService.retrieve` through the observed RAG boundary
- Downstream caller: `SchemeEvidenceService.extract_rag_output`
- Input: raw RAG result, request identity, selected/dropped retrieval records, context packing metadata
- Output: `EvidenceBundleSchema`
- State changes: none outside returned object
- Exception behavior:
  - raw RAG success false -> `RuntimeError`;
  - invalid context/bundle -> schema validation error.
- Ownership: initial canonical RAG service-boundary contract before document-wide citation remap.
- Classification: active

### 8.4 Document-wide Remap

**Evidence record**

- File path: `backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py`
- Class: `DocumentCitationRegistry`
- Function: `remap_bundle`
- Upstream caller: document-level and section/recovery RAG stage code
- Downstream caller: StageService/section graph payload and SharedStateWriter
- Input: context, chunks, citations, normalized RAG output, retrieval scope/query
- Output: remapped context, unchanged chunk list, document-wide citations and normalized output
- State changes:
  - registry allocates/deduplicates stable document citation IDs;
  - rewrites visible context markers;
  - rewrites contract item citation IDs;
  - rewrites contract citation list and context;
  - validates a new contract;
  - updates contract hash and scope metadata.
- Exception behavior: invalid remapped contract -> Pydantic/contract validation error
- Ownership: the remapped contract in `normalized["evidence"]` is the authority consumed by the enterprise-document business workflow after this boundary. The pre-remap bundle is superseded for document generation.
- Classification: active

### 8.5 Projection Matrix

| Representation | Writer | Consumer | Ownership |
|---|---|---|---|
| `normalized["evidence"]` | `extract_rag_output`, then `remap_bundle` | StageService and section graph payload | authoritative business-boundary copy after remap |
| `RAGContext.extra.evidence_contract` | `extract_rag_output`, then `remap_bundle` | context manager, section metadata/hash readers | replica of full contract attached to context |
| `context_bundle.evidence.contract` | `SharedStateWriter.set_evidence_context` | GraphState readers/projector/final snapshots | GraphState projection/replica |
| `context_bundle.evidence.context_text` | same | generation | derived projection from contract context |
| retrieved chunks | contract reader/remap stage | generation and assembly | derived projection from selected evidence items |
| citations | contract reader/remap stage | citation linker | derived projection, with document-wide IDs after remap |
| `RAGToolOutputSchema` legacy fields | schema validator/projector | compatibility callers | compatibility projection |
| evidence contract hash | extraction/remap | trace, section evidence and integrity checks | derived integrity identifier |

### 8.6 Evidence Contract Findings

1. One runtime schema exists; `RAGEvidenceContractSchema` is an alias, not a competing implementation.
2. Structured contract items are declared source of truth; context/chunks/citations are deterministic projections.
3. Enterprise-document generation uses the remapped contract after document-wide citation ID assignment.
4. `context_bundle.evidence.contract` is stored as a dictionary copy and is not the sole durable copy.
5. `budget_usage.queries_executed` in `RAGResultMapper` is a derived RAG metric and is not the `WorkflowBudget` authority. TASK-002 identified that it omits at least some HyDE/corrective query executions. This remains an unprocessed finding.
6. `LegacyEvidenceAdapter` remains an active compatibility fallback when the result is not an `EvidenceBundleSchema`; it is not the canonical current RAG path. Classification: active compatibility/legacy adapter.

## 9. CitationBinding

### 9.1 Lifecycle Diagram

```text
Document-wide CitationSchema list
        |
        v
LLM section output
  -> remove all model-emitted marker-like tokens
  -> deterministic supported marker insertion
        |
        v
CitationService.bind_citations
  -> occurrence-specific CitationBindingSchema
        |
        v
CitationService.supported_bindings
  -> strict lexical/exact support filtering
        |
        +--> repair/regenerate may repeat binding + filtering
        |
        v
reconcile_citation_markers
  -> visible markers match accepted bindings
        |
        v
SchemeSectionSchema.citation_bindings
        |
        v
DocumentAssembler aggregates
        |
        v
Document gate / high-risk claim gate / occurrence integrity gate
```

### 9.2 Definition

**Evidence record**

- File path: `backend/schemas/citation.py`
- Class: `CitationBindingSchema`
- Function: model validation
- Upstream caller: `CitationService.bind_citations`
- Downstream caller: section schema, document assembler, document gate, high-risk fact gate and generation checker
- Input: citation identity, target document/section/paragraph/claim/marker coordinates, source identity and claim/quote text
- Output: typed citation-to-claim binding
- State changes: none outside the created model
- Exception behavior: missing/invalid required fields -> Pydantic validation error
- Classification: active

### 9.3 Creation and Filtering

**Evidence record**

- File path: `backend/apps/enterprise_document/services/scheme_writer/citation_service.py`
- Class: `CitationService`
- Functions: `bind_citations`, `supported_bindings`, `reconcile_citation_markers`
- Upstream caller: `SectionGenerationService.generate_section`, grounding repair and stage fan-in rebuild
- Downstream caller: `SchemeSectionSchema`, document assembler and gates
- Input: final candidate section content plus available document-wide citations
- Output: occurrence-specific bindings, then verified binding subset and reconciled visible text
- State changes: returns new binding objects and content; does not mutate global state
- Exception behavior:
  - unknown visible citation ID is skipped during binding;
  - unsupported claim/evidence pair is filtered out;
  - integrity reconciliation removes visible markers without accepted bindings.
- Ownership: filtered binding list attached to the accepted section is authoritative for citation support of that section.
- Classification: active

### 9.4 Read/Write Matrix

| Field | Initial writer | Later writers | Readers |
|---|---|---|---|
| `binding_id` | `CitationService.bind_citations` | stage fan-in may rebuild occurrence positions after citation remap | integrity gate, document gate |
| `citation_id` | same | fan-in remap/rebuild | assembler, gate, high-risk gate |
| target paragraph/claim/marker coordinates | occurrence extractor through `bind_citations` | rebuilt when merged/remapped content changes | occurrence integrity gate |
| source IDs | copied from `CitationSchema` | no semantic rewrite observed | grounding and audit consumers |
| `claim_text` | occurrence extractor | regenerated if section content is regenerated and rebound | support filter, high-risk gate |
| grounding metadata | `supported_bindings` | candidate repair/recheck may replace binding list | generation checker/gates |

### 9.5 CitationBinding Findings

1. Citation source identity (`CitationSchema`) and claim binding (`CitationBindingSchema`) are distinct active contracts.
2. A citation existing in the evidence contract does not imply a valid CitationBinding.
3. Model-emitted markers are explicitly distrusted; accepted bindings are rebuilt deterministically.
4. The visible marker list and `citation_ids` are projections of accepted bindings, not separate authorities.
5. Section fan-in can rebuild bindings after document-wide citation remap; the rebuilt section bindings supersede earlier section-local coordinates.

## 10. ExecutionStatus

### 10.1 Enum

**Evidence record**

- File path: `backend/schemas/status.py`
- Class: `ExecutionStatus`
- Functions: enum construction, `is_failure`
- Upstream caller: task manager, workflow engine, Agent/Tool/RAG/section schemas and stream events
- Downstream caller: lifecycle routing, retry/failure classification, persisted task event and UI/application result
- Input: status string/enum
- Output: canonical status enum or failure predicate
- State changes: none
- Exception behavior: invalid enum construction -> `ValueError`; `is_failure` treats unknown values as failure
- Classification: active

Enum values:

`created`, `queued`, `pending`, `running`, `paused`, `success`, `partial_success`, `failed`, `retryable_failed`, `cancelled`, `skipped`.

### 10.2 Persistent Task Transition Authority

**Evidence record**

- File path: `backend/task/task_manager.py`
- Class: `JsonlTaskManager`
- Functions: `create_task`, `_mark`, `_commit_record`, `_read_task_events`
- Upstream caller: `TaskLifecycleService`; application task creation/inspection
- Downstream caller: task status inspection, resume/cancel validation and stream reconciliation
- Input: `TaskSchema`, target status, optional result/error and expected revision
- Output: `TaskStateRecordSchema`
- State changes: append-only task event JSONL; in-memory record updated only after append succeeds
- Exception behavior:
  - invalid task ID -> `ValueError`;
  - duplicate task immutable request mismatch -> `ValueError`;
  - illegal transition -> `ValueError`;
  - revision conflict -> `ValueError`;
  - corrupt/non-contiguous event log -> `ValueError`.
- Ownership: persisted event log is authoritative external task lifecycle truth.
- Classification: active

Allowed task transitions:

| From | Allowed to |
|---|---|
| `created` | `pending` |
| `queued` | `pending`, `running` |
| `pending` | `running`, `failed`, `cancelled` |
| `running` | `paused`, `success`, `partial_success`, `failed`, `retryable_failed`, `cancelled` |
| `paused` | `running`, `failed`, `retryable_failed`, `cancelled` |
| terminal statuses | none |

Current task creation starts directly at revision 0 / `pending`; `created` and `queued` remain enum/state-machine possibilities but are not emitted by `JsonlTaskManager.create_task`.

### 10.3 Workflow/Graph Status Flow

```text
Task event log: pending
  -> TaskLifecycleService.mark_running
  -> task event log: running

Graph initial checkpoint copy: running
  -> node statuses recorded independently
  -> gate/payload output_status
  -> finalizer writes GraphState.status + context runtime status
  -> WorkflowEngineResult.status
  -> Supervisor AgentResult.status
  -> TaskLifecycleService.mark_terminal
  -> append terminal/paused task event
```

### 10.4 Layered Status Ownership

| Status carrier | Owner/scope | Authority |
|---|---|---|
| task JSONL current event/snapshot | `JsonlTaskManager` | authoritative persisted task lifecycle |
| `GraphState.status` | workflow engine | current graph-runtime status snapshot |
| `context_bundle.runtime.status` | SharedState/runtime projection | typed runtime replica, written with GraphState status at explicit boundaries |
| node `AgentResult.status` / `GraphNodeOutput.status` | business node executor | authoritative for that node execution record |
| business payload `output_status` | document gate/review path | authoritative business output decision before finalization |
| `WorkflowEngineResult.status` | workflow engine result converter | authoritative workflow return status |
| Supervisor `AgentResult.status` | Supervisor | application result boundary; passed to task lifecycle |
| stream event status | stream adapter | event-local projection, not durable task truth |
| RAG evidence `status` | RAG boundary | retrieval call status only |
| section status | section generation/quality evaluator | section-local status only |

### 10.5 Pause/Resume/Reconciliation

- A resume/cancel request first requires persisted task status `paused`, durable interrupt data and checkpoint identity.
- Checkpoint GraphState identity must match task/run/task type.
- During stream reconciliation, an already persisted non-running task status takes precedence; otherwise durable interrupts imply paused; otherwise terminal checkpoint status may be used; otherwise a failure category determines failed/retryable/cancelled.

### 10.6 ExecutionStatus Findings

1. One shared enum is active, but status facts are scoped to different aggregates.
2. No single field is authoritative for all scopes.
3. The persisted task event log is the authority for externally observed task lifecycle and resume eligibility.
4. GraphState status can be a checkpoint/runtime replica and may be reconciled against task truth.
5. `created`, `queued`, and `skipped` are active enum members but were not found as emitted statuses on the current enterprise-document task creation/mainline path. Their current mainline emission status is inactive/unknown depending on other task types.

## 11. Trace

### 11.1 Trace Is Not One Object

The repository has at least four active trace-related contracts with different ownership:

1. `RunTraceEventSchema`: append-only operational run trace event.
2. `TraceSpanContext/TraceSpanHandle`: in-process hierarchy propagation.
3. `RAGTraceSchema`: embedded retrieval metadata/lineage inside evidence.
4. retrieval/capture trace IDs: correlation identifiers in RAG outputs and capture records.

They share identifiers but are not interchangeable sources of truth.

### 11.2 Run Trace Lifecycle

```text
Supervisor WorkflowTraceService starts root run/workflow spans
       |
       v
TraceSpanHandle + ContextVar activation
       |
       +--> business node observer
       +--> tool executor observer
       +--> model observer
       +--> RAG service observer
       |
       v
JsonlRunTraceRecorder.record
  - resolves trace/span IDs
  - allocates event sequence
  - bounds summaries
  - appends one JSON line
       |
       v
<run_id>_trace.jsonl
       |
       v
load_trace_events / validate_trace_v2 / acceptance tooling
```

### 11.3 Run Trace Event Authority

**Evidence record**

- File paths:
  - `backend/schemas/trace.py`
  - `backend/data_capture/run_trace_recorder.py`
- Classes:
  - `RunTraceEventSchema`
  - `JsonlRunTraceRecorder`
- Function: `record`
- Upstream caller: `WorkflowTraceService`, workflow observer, tool/model/RAG observers and business graph trace hooks
- Downstream caller: JSONL readers, trace validator, acceptance scripts and post-training/evaluation capture consumers
- Input: task/run identity, event/component/call hierarchy, status, bounded payload/summary, lineage and metrics
- Output: typed trace event plus appended JSONL row
- State changes:
  - per-run in-process sequence counter increments;
  - one row appended to `data/runs/<run_id>_trace.jsonl` or configured run directory.
- Exception behavior:
  - schema validation/serialization/file I/O errors propagate;
  - existing file line count initializes the next sequence.
- Ownership: append-only JSONL event stream is authoritative run trace record.
- Classification: active

### 11.4 Span Context

**Evidence record**

- File path: `backend/observability/trace_context.py`
- Classes: `TraceSpanContext`, `TraceSpanHandle`
- Functions: `new_span`, `activate_span`, `current_span`
- Upstream caller: Supervisor/workflow/tool/model/RAG observers
- Downstream caller: nested recorder calls
- Input: run ID, span name/kind and optional parent
- Output: immutable span handle/context
- State changes: sets/resets a `ContextVar` during the context manager
- Exception behavior: context resets in `finally`; no persistence
- Ownership: active hierarchy propagation only; not the durable trace store
- Classification: active

### 11.5 Trace Validation

**Evidence record**

- File path: `backend/observability/trace_reader.py`
- Functions: `load_trace_events`, `validate_trace_v2`
- Upstream caller: tests and acceptance scripts
- Downstream caller: validation reports
- Input: trace JSONL rows
- Output: invariant report
- State changes: none
- Exception behavior: invalid JSON/non-object row -> `ValueError`
- Validated invariants include:
  - one schema version, trace ID, run ID and task ID;
  - contiguous event sequence;
  - each span has one start and one terminal event;
  - stable/existing parent span;
  - one root span;
  - required run/workflow/agent/tool/model event types;
  - RAG lineage hash when RAG evidence is summarized;
  - model prompt data is summarized rather than stored raw.
- Classification: active support/acceptance path

### 11.6 RAG Trace

- File path: `backend/schemas/rag.py`
- Class: `RAGTraceSchema`
- Writer: `RAGResultMapper`
- Readers: evidence lineage builder, normalized RAG output and reporting
- Ownership: retrieval metadata embedded in one evidence bundle; not the operational run event stream
- Classification: active

### 11.7 Trace Findings

1. “Trace” has no single class or storage authority across all semantics.
2. Operational run trace authority is the JSONL event stream.
3. Span context is transient propagation state.
4. `RAGTraceSchema` is an evidence-lineage component.
5. `retrieval_trace_id` is a correlation identifier and may appear without representing a full Trace v2 span/event record by itself.
6. The current recorder sequence is protected by an instance lock and derives its initial value from existing line count; cross-process concurrent writers to the same run file were not established by the inspected code. Status: unknown.

## 12. WorkflowBudget

### 12.1 Lifecycle Diagram

```text
Grounded generation policy metadata / request override
        |
        v
WorkflowBudget.from_policy_metadata
        |
        v
section payload.budget_state = budget.snapshot()
        |
        v
node restore: WorkflowBudget.from_snapshot
        |
        v
activate_workflow_budget(ContextVar)
  - model boundary reserve_llm_call
  - retrieval loop consume_retrieval_round
  - rewrite path consume_rewrite_round
        |
        v
budget.snapshot() -> checkpointed payload.budget_state
        |
        v
final section extra.workflow_budget + exhaustion metadata
```

Document-level RAG uses a separate one-stage `WorkflowBudget` with document defaults and writes only a resulting snapshot into normalized RAG output metadata.

### 12.2 Definition and Semantics

**Evidence record**

- File path: `backend/apps/enterprise_document/quality/budget.py`
- Class: `WorkflowBudget`
- Functions: `from_policy_metadata`, `from_snapshot`, `reserve_llm_call`, `consume_retrieval_round`, `consume_rewrite_round`, `snapshot`, `activate_workflow_budget`
- Upstream caller: StageService document RAG and section graph; model call boundary and generation/repair paths
- Downstream caller: routing/fallback logic, section result metadata and checkpoint state
- Input: policy limits and consumed-counter snapshot
- Output: mutable budget object and serialized snapshot
- State changes: increments logical model calls, reserved output token allowance, retrieval rounds and rewrite rounds
- Exception behavior: limit exceeded -> `WorkflowBudgetExceeded(resource, limit)`
- Ownership:
  - active mutable object is authoritative while its node/context is executing;
  - `budget_state` snapshot is authoritative between section graph nodes and across checkpoints.
- Classification: active

### 12.3 Default Limits Observed

| Scope | Source | Defaults |
|---|---|---|
| section | `grounded_generation_v1.yaml` / `GroundedGenerationConfig` | retrieval 1; rewrite 1; logical LLM calls 35; reserved output tokens 24000; human review on exhaustion true |
| document RAG | `StageService.retrieve_document_evidence` | retrieval 4; rewrite 2; logical LLM calls 16; reserved output tokens 32000; human review on exhaustion true |
| section parallel aggregate | `StageService.execute_sections` | derived from per-section slices × section count unless explicitly overridden |

### 12.4 Write/Read Matrix

| Field | Writer | Reader/decision |
|---|---|---|
| max limits | policy metadata and request overrides through factory | every consume/reserve function |
| `retrieval_rounds` | section recovery nodes | evaluate-generation routing and final exhaustion metadata |
| `rewrite_rounds` | local rewrite/repair path | future rewrite calls and final report |
| `llm_calls` | model call boundary through active ContextVar | model-call fuse and final report |
| `reserved_tokens` | model call boundary using requested max output tokens | token allowance fuse and final report |
| `budget_state` | section graph after each relevant node | next node restore and durable checkpoint |
| `workflow_budget` in section extra | section finalizer | reporting/gate/capture consumers |
| document RAG budget snapshot | document evidence stage | normalized RAG output metadata/reporting |

### 12.5 Relationship to ModelUsageLedger

**Evidence record**

- File path: `backend/model_gateway/usage_ledger.py`
- Class: `ModelUsageLedger`
- Functions: `record_logical_call`, `record_attempt`, `snapshot`
- Upstream caller: model gateway
- Downstream caller: Supervisor/application result `model_usage`
- Input: model selection and actual provider response
- Output: aggregate usage snapshot
- State changes: counts logical calls, provider attempts, actual prompt/completion/total tokens, latency, failures, fallbacks and cost if configured
- Exception behavior: no budget exception; it is accounting, not enforcement
- Ownership: authoritative accounting aggregate for actual provider usage in the process/session; separate from WorkflowBudget safety fuse
- Classification: active

### 12.6 Budget Findings

1. WorkflowBudget token count is reserved output allowance, not actual provider token usage.
2. WorkflowBudget LLM count is logical boundary calls, not provider fallback attempts.
3. Actual provider attempts/tokens/cost belong to ModelUsageLedger.
4. Section budget is checkpoint-resumable through `budget_state`.
5. Document RAG budget is activated for one stage and then reported as a snapshot; it is not carried as a top-level graph-wide budget object.
6. RAG evidence `budget_usage` is a separate derived metric and must not be treated as WorkflowBudget truth.

## 13. Cross-object Ownership Matrix

| Fact | Authoritative writer/boundary | Active readers | Replicas/projections | Conflict/unknown |
|---|---|---|---|---|
| external project request | `ProjectInputFactory` result | `TaskFactory` | Task/Graph/section copies | multiple candidates not cross-compared |
| normalized business project input | normalize node payload + business context | plan/RAG/section/gate | contexts/task/requirements | priority-based selection |
| graph business state within node | validated `GraphStateApplier` commit | workflow engine | final shared-state snapshot | graph revision is partial, not global |
| durable workflow state | LangGraph checkpoint JSON envelope | restore/inspect/resume | restored models | clean release provenance absent |
| RAG evidence | remapped `EvidenceBundleSchema` in normalized output | generation/citation/assembly | context/chunks/citations/state copy | pre-remap contract superseded |
| citation support | accepted section CitationBinding list | assembly/gates | markers/citation IDs | no binding for unsupported citations |
| task lifecycle | task JSONL event log | inspect/resume/reconcile | Graph/Agent/stream statuses | statuses scoped by aggregate |
| operational trace | run trace JSONL events | validator/acceptance | reports | cross-process writer safety unknown |
| retrieval lineage | `RAGTraceSchema` inside evidence | lineage/report | normalized trace dict | not same as run Trace v2 |
| section safety budget | active budget, then `budget_state` checkpoint | section graph/model boundary | final section report | not actual token accounting |
| actual model usage | ModelUsageLedger | Supervisor result/report | persisted/merged snapshots | process/session durability depends on caller merge path |

## 14. Duplicate, Legacy and Inactive Implementations

| Implementation | Classification | Reason |
|---|---|---|
| `RAGEvidenceContractSchema` name | active alias | same class as `EvidenceBundleSchema`; not duplicate runtime schema |
| `RAGToolOutputSchema` context/chunks/citations fields | active compatibility projection | canonical evidence contract projects into these fields |
| `contexts.project_input` and related `contexts.*` fields | active compatibility replica | explicitly one-way projection; not authority |
| `ProjectInputNormalizerAgent.run` | inactive for current enterprise mainline | explicit graph directly invokes normalization use case |
| `SchemeWriterAgent.run` coarse orchestration | legacy/inactive for current enterprise mainline | explicit StageService/business graph executes stages |
| `SchemeGenerationUseCase.run` | legacy/unreachable for current enterprise mainline | no current production caller confirmed by TASK-001 |
| generic Native workflow path | inactive/legacy for enterprise-document mainline | current engine is `EnterpriseDocumentWorkflowEngine` |
| `LegacyEvidenceAdapter` | active compatibility/legacy adapter | fallback still callable when result is not canonical bundle |
| `RAGTraceSchema` versus `RunTraceEventSchema` | both active, not duplicates | different semantics and persistence |
| WorkflowBudget versus ModelUsageLedger | both active, not duplicates | enforcement/reservation versus accounting/actual usage |
| demo ProjectInput seed/default path | active demo adapter | only used when explicit demo defaults are enabled |

No inspected object was classified as experimental on the current mainline. Repository code outside the current enterprise-document path may still be experimental, but it was not required to establish these seven object lifecycles.

## 15. Confirmed Conflicts, Unknowns and Gaps

### 15.1 Confirmed Conflicts / Divergence Risks

1. **ProjectInput candidate divergence is not rejected.** Multiple populated copies can exist; the reader selects the first valid candidate.
2. **Status is intentionally multi-layered.** Task, GraphState, business output, node, RAG, section and stream statuses are not one fact and can differ during execution.
3. **Evidence exists in several full/partial copies.** Authority after citation remap must be resolved by boundary, not by field name alone.
4. **Budget metrics have different semantics.** RAG `budget_usage`, WorkflowBudget and ModelUsageLedger cannot be compared as if they count the same thing.

### 15.2 Unknowns

1. Whether the dirty working tree corresponds to a named release/tag: unknown.
2. Whether all current working-tree changes were produced together and validated as one immutable baseline: unknown.
3. Cross-process concurrent safety of writing the same run trace JSONL: unknown from inspected code.
4. Whether `created`, `queued`, or `skipped` are active on another production task type: not established within TASK-005 scope.
5. Historical active index artifact commit provenance remains unknown from TASK-003.

### 15.3 Gaps Requiring QA Verification, Not Architecture Editing

1. Runtime equality/invariant check across all active ProjectInput replicas.
2. Runtime evidence that document-wide remapped contract hash remains identical across normalized output, context extra and GraphState evidence copy at each stage.
3. Runtime evidence that final visible citation markers, section bindings and document bindings remain occurrence-equivalent after parallel fan-in/remap.
4. Runtime evidence that task event status, checkpoint GraphState status, WorkflowEngineResult and Supervisor AgentResult converge for success, partial success, pause/resume, failure and cancellation.
5. Runtime evidence that trace event sequence and parent hierarchy remain valid under parallel section execution and process restart.
6. Runtime evidence comparing WorkflowBudget logical counts with ModelUsageLedger provider attempts/actual tokens, without treating differences as errors.

### 15.4 New Test-isolation Finding

A supplemental isolated test batch that included `backend/tests/test_p1_security_durability_identity_closure.py` failed during test collection before any test in that batch executed.

Observed import chain:

```text
test_p1_security_durability_identity_closure.py
  -> apps.enterprise_document.services.document_gate
  -> apps.enterprise_document.services.scheme_writer.citation_integrity_service
  -> scheme_writer package __init__
  -> SchemeGenerationUseCase
  -> SchemeGenerationStageService
  -> apps.enterprise_document.services.document_gate (partially initialized)
```

Observed exception:

```text
ImportError: cannot import name 'evaluate_scheme_draft' from partially initialized module
'apps.enterprise_document.services.document_gate'
```

Classification: active test-collection finding associated with import order/package initialization; no business code was changed and no fix is claimed. The remaining supplemental files passed when executed without this file.

## 16. Verification Executed

Command executed after correcting Bash path syntax:

```text
./.venv/Scripts/python.exe -m pytest -q \
  backend/tests/stage1/test_contracts.py \
  backend/tests/test_step_12_evidence_contract.py \
  backend/tests/test_step_13_trace_v2.py \
  backend/tests/test_step_15_graph_state_workflow_engine.py \
  backend/tests/test_citation_binding_regression.py \
  backend/tests/test_grounded_generation_budget.py \
  backend/tests/test_phase3_section_execution_subgraph.py \
  backend/tests/test_phase4_durable_resume_hitl.py
```

Result:

- 58 tests passed.
- No business code was changed.
- The first attempted command failed before running tests because Windows backslashes were consumed by Bash; the corrected forward-slash executable path completed successfully.

Supplemental test attempt:

```text
./.venv/Scripts/python.exe -m pytest -q \
  backend/tests/test_p1_security_durability_identity_closure.py \
  backend/tests/test_phase2_runtime_safety_closure.py \
  backend/tests/test_side_effect_state_machine_closure.py \
  backend/tests/test_workflow_budget_exhaustion_semantics.py
```

Result:

- Collection stopped with the circular-import `ImportError` recorded in section 15.4.
- No tests in that batch executed.

The remaining supplemental files were then executed without the failing collection file:

```text
./.venv/Scripts/python.exe -m pytest -q \
  backend/tests/test_phase2_runtime_safety_closure.py \
  backend/tests/test_side_effect_state_machine_closure.py \
  backend/tests/test_workflow_budget_exhaustion_semantics.py
```

Result:

- 173 tests passed.
- Successful test executions across the two completed batches: 231.
- This is not a claim that the full repository test suite passes.

The passing tests support the inspected contract invariants, but they do not convert the dirty working tree into a clean release baseline, resolve the failed isolated collection, or resolve the unknowns listed above.

## 17. Final Investigation Conclusions

1. The current enterprise-document system has active typed contracts and explicit write boundaries for all seven investigated objects.
2. Several objects have multiple copies, but those copies have different ownership categories; repository presence or matching names do not make them equally authoritative.
3. Current ProjectInput authority shifts across boundaries: strict ingress model -> normalized business payload/context -> section-local checkpoint copies.
4. Current GraphState authority alternates between validated in-memory node commits and checkpoint-safe JSON state between nodes.
5. Current RAG evidence authority is the validated, document-wide citation-remapped `EvidenceBundleSchema`; context/chunks/citations are projections.
6. Current CitationBinding authority is the verified occurrence-specific binding list attached to accepted section content.
7. Current external task lifecycle authority is the append-only task event log; GraphState and AgentResult statuses are workflow/result-layer facts.
8. Current operational trace authority is the per-run Trace v2 JSONL event stream; RAG trace and retrieval IDs are separate lineage/correlation objects.
9. Current section WorkflowBudget authority is the mutable active object inside a node and its checkpointed `budget_state` between nodes; actual provider usage belongs to ModelUsageLedger.
10. No issue recorded in this report is claimed to be fixed.
