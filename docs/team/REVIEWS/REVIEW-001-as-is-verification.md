# REVIEW-001 As-Is Verification

## 1. Review Metadata

- Review ID: `REVIEW-001`
- Reviewer Role: Independent QA / Adversarial Reviewer
- Review Date: `2026-07-26`
- Repository: `D:\MyCode\rag-agent`
- Branch: `main`
- HEAD: `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`
- Workspace Description: `langgraph-phase1-serializable-state-v1-1-g07dda2e-dirty`
- Review Output: `docs/team/REVIEWS/REVIEW-001-as-is-verification.md`
- Business Code Modified: no
- Source Analysis/Handoff Files Modified: no

## 2. Reviewed Inputs

The review read and cross-checked:

- `docs/team/PROJECT.md`
- `docs/team/POLICIES.md`
- `docs/team/TASKS/TASK-001-online-agent-workflow-chain.md`
- `docs/team/TASKS/TASK-002-online-rag-chain.md`
- `docs/team/TASKS/TASK-003-offline-index-chain.md`
- `docs/team/TASKS/TASK-004-algorithm-placement.md`
- `docs/team/TASKS/TASK-005-state-contract-ownership.md`
- the five corresponding files under `docs/team/ANALYSIS/`
- the five corresponding files under `docs/team/HANDOFFS/`
- the referenced production, compatibility, test, configuration and data-artifact code paths

## 3. Independent Verification Method

The review did not accept report prose, file names, class names, comments or configuration presence as execution evidence by themselves.

Evidence used:

1. direct construction, registration and call sites;
2. reverse caller searches across `backend/` and `scripts/`;
3. explicit LangGraph node registration and edge definitions;
4. state mutation and persistence code;
5. existing runtime tests executed independently;
6. active-index pointer, manifest hash and JSONL field statistics;
7. a read-only adversarial concurrency probe against the current trace recorder;
8. reproduction of the reported isolated test-collection failure.

Independent test results:

```text
63 passed in 28.14s
53 passed in 21.25s
```

The two completed batches covered the coarse-path retirement guards, explicit top-level graph, LangChain takeover, query transforms, configured retrieval stack, CRAG correction, context packing, Self-RAG repair/retrieve-more, section subgraph, Evidence contract, GraphState write contract, citation bindings, workflow budget and durable pause/resume behavior.

Total independently completed passing tests in these non-overlapping batches: `116`.

The following isolated test still fails during collection:

```text
backend/tests/test_p1_security_durability_identity_closure.py
```

Observed exception:

```text
ImportError: cannot import name 'evaluate_scheme_draft' from partially initialized module
'apps.enterprise_document.services.document_gate'
```

Import chain:

```text
test_p1_security_durability_identity_closure.py
  -> document_gate
  -> scheme_writer.citation_integrity_service
  -> scheme_writer.__init__
  -> SchemeGenerationUseCase
  -> SchemeGenerationStageService
  -> document_gate (partially initialized)
```

## 4. Verification Summary Against Requested Questions

| # | Verification target | Independent result |
|---|---|---|
| 1 | Claimed entry is a real entry | Repository-level CLI/application entries are confirmed. Actual deployed production ingress remains unknown; the reports sometimes use “production” more strongly than the evidence permits. |
| 2 | Claimed call relations have code evidence | The principal TASK-001/TASK-002 call edges are confirmed by direct construction, registration and invocation statements. |
| 3 | Algorithm really enters the current mainline | Configured algorithm implementations are attached to the repository mainline and execute in controlled tests. Real Milvus/model execution against the active trusted-scope index is not confirmed. |
| 4 | Repository presence is not confused with current enablement | Most legacy/inactive distinctions are correct. `LegacyEvidenceAdapter` is classified inconsistently, and TASK-004 final summaries drop important runtime qualifications. |
| 5 | State creators and writers are complete | ProjectInput, GraphState, task state, Evidence, CitationBinding, status and budget ownership are mostly traced correctly. Trace writer concurrency semantics are incomplete. |
| 6 | Multiple fact sources exist | Correctly identified for ProjectInput, Evidence projections, status layers, Trace contracts and budget/accounting objects. |
| 7 | Conditions, retries and feedback loops omitted | Top-level retry configuration, CRAG correction, Self-RAG rewrite/retrieve-more, HITL pause/resume/reject/cancel and technical-failure routing are represented. No material omission was found in these listed loops. |
| 8 | Design intent is written as current fact | Repeated “真实生产主链” wording is stronger than repository evidence because deployed entry/configuration and real-provider execution are unverified. |
| 9 | Five reports contradict each other | One direct status contradiction exists for `LegacyEvidenceAdapter`; a second inconsistency exists between TASK-004 detailed limitations and its unqualified final `active` labels. |
| 10 | Unknown content written as certain | Historical offline entry attribution and production/active terminology need narrower wording. The reports correctly mark many other real-runtime gaps as unknown. |

## 5. Claims Independently Confirmed

### 5.1 Current repository mainline bypasses the coarse SchemeWriter path

Confirmed evidence:

- `backend/bootstrap/supervisor_factory.py::SupervisorFactory.build`
  - registers `ProjectInputNormalizerAgent` only;
  - constructs `SchemeWriterAgent` but does not register it;
  - injects `scheme_writer.stage_service` into `EnterpriseDocumentWorkflowEngine`;
  - injects that engine into `SupervisorAgent`.
- `backend/apps/enterprise_document/agents/scheme_writer_agent.py::SchemeWriterAgent.run`
  - only delegates to `self.use_case.run(shared_state)`.
- `backend/apps/enterprise_document/services/scheme_writer/use_case.py::SchemeGenerationUseCase.run`
  - only delegates to `stage_service.run_compat`.
- Repository caller search found no confirmed current business caller of either legacy `run` method.
- Production-guard tests pass while both methods are patched to raise if called.

Therefore these report conclusions are supported:

- `SchemeWriterAgent.__init__`: active construction;
- `SchemeGenerationUseCase.__init__`: active construction;
- `SchemeWriterAgent.run`: legacy/inactive for the confirmed repository mainline;
- `SchemeGenerationUseCase.run`: legacy/inactive for the confirmed repository mainline.

### 5.2 Explicit graph RAG trigger is real

Confirmed call chain:

```text
EnterpriseDocumentWorkflowEngine.build_graph
  -> add_node("retrieve_document_evidence", ...)
  -> EnterpriseDocumentWorkflowEngine._run_stage
  -> SchemeGenerationStageService.retrieve_document_evidence
  -> SchemeEvidenceService.retrieve
  -> LangChainRAGServiceFacade.retrieve/invoke
  -> RunnableLambda
  -> ObservedRAGService.retrieve
  -> RAGService.retrieve
  -> RAGRequestMapper.map
  -> RetrievalRuntime.retrieve
  -> ParentChildRAGEngine.run
  -> ParentChildRetrievalPipeline.run
```

Direct code also confirms that the retrieval node is between `plan_document` and `initialize_generation` and routes stage failure to technical failure handling.

### 5.3 Configured online retrieval topology is attached and consumed

The current static spec and runtime factory attach:

- mutually exclusive identity / multi-query / HyDE transforms;
- Milvus dense child retrieval;
- BM25 child retrieval;
- child RRF;
- parent enrichment/dedup/backfill;
- conditional parent-level query RRF;
- parent reranking;
- CRAG-style assessment;
- bounded correction;
- context-gate selection including lost-in-the-middle packing.

`ParentChildRetrievalPipeline.run` directly consumes the planner and transformer output, calls all configured retrievers, fuses/enriches/reranks, always assesses, and conditionally enters correction. `ParentChildRAGEngine.run` consumes the final candidates through `ContextGate.pack`.

Controlled execution is independently confirmed. Real-resource execution is not.

### 5.4 Self-RAG section feedback path is attached

Confirmed evidence:

- `EnterpriseDocumentWorkflowEngine.__init__` constructs `SectionExecutionGraph` when a coordinator exists and attaches it to `SchemeGenerationStageService`.
- `SchemeGenerationStageService.execute_sections` uses the attached graph when present.
- `SectionGenerationService.generate_section` calls the configured generation checker.
- `SectionExecutionGraph` consumes `need_retrieve_more`, performs bounded recovery retrieval, regenerates and rechecks.
- Controlled tests independently passed.

The report distinction between the active section graph and legacy coordinator coarse execution is supported.

### 5.5 Offline artifact facts are reproducible

The active pointer resolves to:

```text
idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f
```

Independent checks confirmed:

- active manifest exists;
- pointer `manifest_sha256` matches the actual manifest bytes;
- parent chunks: `69`;
- child chunks: `258`;
- vector records: `258`;
- vector dimension: `768`;
- manifest/acceptance record Milvus rows: `258`;
- acceptance report status: `success`;
- recorded self-retrieval: `3/3`.

Independent JSONL field statistics:

```text
child_chunks:          258 records
  tenant_id present:     0
  kb_id present:         0
  file_id present:       0
  doc_id present:      258

vector_index_records: 258 records
  tenant_id present:     0
  kb_id present:         0
  file_id present:       0
  doc_id present:      258
```

The TASK-002/TASK-003 warning about trusted-scope metadata mismatch is therefore supported.

### 5.6 State ownership findings mostly match code

Confirmed:

- `ProjectInputFactory.build` is the strict production ingress constructor and explicit demo adapter constructor.
- `ProjectInputReader.read` selects the first non-empty candidate in priority order and does not cross-validate all replicas.
- `SupervisorAgent.run/stream` constructs `GraphStateSchema`.
- the enterprise workflow uses deep-copy, diff, declared write validation and `GraphStateApplier.apply` before checkpoint serialization;
- task lifecycle is committed as append-only events before in-memory projection update;
- document-level Evidence is remapped before business-state projection;
- citation bindings are consumed by document assembly and hard-gate validation;
- `WorkflowBudget` snapshots are restored between section graph nodes;
- `ModelUsageLedger`, RAG `budget_usage` and `WorkflowBudget` are separate accounting objects.

## 6. Blockers

### Blocker B-001 — The investigated baseline is not immutable or reproducible

Affected reports: TASK-001 through TASK-005.

Evidence:

- all reports identify commit `07dda2e80645cac67f8b5f5e30002d21c2ab01eb` plus a dirty worktree;
- current `git status --short` contains `82` changed/untracked entries;
- the changed set includes core workflow/RAG/state code, tests, task files and all Analysis/Handoff directories;
- no report supplies a worktree patch hash, archive hash, source-file hash set or immutable tree identifier for the dirty content.

Impact:

A reviewer can verify the current checkout, but cannot prove that TASK-001, TASK-002, TASK-003, TASK-004, TASK-005 and this QA review examined identical bytes. A commit hash does not identify uncommitted content. This prevents approval of the five reports as a reproducible As-Is baseline.

Required owner action:

- AI Project Manager must freeze an immutable baseline: clean commit, dedicated review worktree commit, or content-addressed archive/patch;
- each original task owner must rebind its Analysis and Handoff to that immutable identifier and rerun its load-bearing verification;
- reports must not be promoted to accepted architecture/contract fact before this is done.

### Blocker B-002 — Required high-risk identity/durability verification cannot be collected independently

Affected report: TASK-005 and its Handoff QA request `QA-005-08`.

Evidence:

```text
./.venv/Scripts/python.exe -m pytest -q \
  backend/tests/test_p1_security_durability_identity_closure.py
```

Result:

```text
collection error: circular import involving document_gate and stage_service
```

Impact:

The test file specifically intended to close security, durability and identity invariants cannot execute in isolation. TASK-005 itself states that acceptance requires QA verification and explicitly calls out this test. The remaining passing tests provide substantial support but do not satisfy this declared verification requirement.

Required owner action:

- the responsible workflow/package/test owner must correct the import/collection problem in a separate implementation task;
- the TASK-005 owner must update its Analysis/Handoff only after the isolated test collects and the intended assertions execute;
- QA must rerun the isolated test on the frozen baseline.

## 7. Major Issues

### Major M-001 — `LegacyEvidenceAdapter` is classified inconsistently and its `active` status is unsupported

Affected files:

- `docs/team/ANALYSIS/TASK-002-online-rag-chain.md`
- `docs/team/HANDOFFS/TASK-002-online-rag-chain-handoff.md`
- `docs/team/ANALYSIS/TASK-005-state-contract-ownership.md`
- `docs/team/HANDOFFS/TASK-005-state-contract-ownership-handoff.md`

Conflicting claims:

- TASK-002: `legacy compatibility`, not in canonical mainline;
- TASK-005: `active compatibility/legacy adapter`.

Code evidence:

- `SchemeEvidenceService.retrieve` returns `Optional[EvidenceBundleSchema]` through the current RAG service port;
- real and fake current service implementations both return `EvidenceBundleSchema`;
- all confirmed business callers pass the result of `SchemeEvidenceService.retrieve` to `extract_rag_output`;
- repository search found no confirmed current business caller passing a `ToolResultSchema` into `extract_rag_output`;
- `LegacyEvidenceAdapter.extract` is reached only by the non-`EvidenceBundleSchema` fallback branch.

Conclusion:

The adapter is callable and tested as a compatibility seam, but current-mainline execution is not established. Under the project status definitions, “callable” is insufficient for `active`.

Required owner action:

- TASK-005 owner must change the classification to `legacy/inactive for the confirmed current mainline`, or supply a direct current caller and runtime evidence;
- TASK-002 and TASK-005 owners must use one consistent classification and explicitly separate compatibility callability from mainline activation.

### Major M-002 — TASK-005 misses a same-process Trace ordering race

Affected files:

- `docs/team/ANALYSIS/TASK-005-state-contract-ownership.md`
- `docs/team/HANDOFFS/TASK-005-state-contract-ownership-handoff.md`

Report claim:

- sequence is protected by an instance lock;
- only cross-process same-run writer safety is marked unknown;
- validator expects contiguous event sequence.

Code evidence:

- `JsonlRunTraceRecorder.record` obtains `event_sequence` through `_next_sequence` under a lock;
- that lock is released before event construction;
- append is performed later under a separate lock acquisition;
- `validate_trace_v2` requires file-order sequences to equal `[1, 2, ..., n]`.

A deterministic read-only adversarial probe forced two threads to interleave and produced:

```text
file_order [('second', 2), ('first', 1)]
```

No exception occurred during writing, but the resulting file fails the validator's contiguous file-order requirement.

Reachability:

- current shell default `SECTION_MAX_CONCURRENCY=1` avoids this in the default serial path;
- the repository explicitly supports section concurrency up to `32`, and parallel execution shares the recorder instance;
- therefore this is a reachable conditional state, not merely a cross-process hypothetical.

Required owner action:

- TASK-005 owner must record same-process ordering safety as unresolved/failed under concurrent writers;
- the Trace authority claim must be scoped to serial or otherwise ordered writer conditions until the runtime owner fixes and verifies atomic sequence-allocation-plus-append behavior;
- QA must add/rerun a concurrency verification after the implementation owner addresses it.

### Major M-003 — Final `active` and “production” wording exceeds the verified runtime boundary

Affected reports: primarily TASK-001, TASK-002 and TASK-004; terminology also propagates into TASK-005.

Evidence boundary actually established:

- repository entry and composition-root reachability: confirmed;
- controlled execution with test doubles/controlled resources: confirmed;
- current `.venv` contains neither `pymilvus` nor `sentence_transformers`;
- active JSONL artifacts lack trusted tenant/KB/file scope fields;
- real active-DB behavior under trusted filters is unknown;
- actual deployed environment variables, service entry and external adapters are unknown;
- no real provider + active index + trusted scope end-to-end run was completed.

Issue:

The detailed report bodies often preserve these limitations, but summaries still use unqualified wording such as “真实生产主链”, Hybrid `active`, RAG-Fusion `active`, CRAG assessor `active`, and similar final labels. That wording can be read as evidence of actual deployed execution, which was not established.

Required owner action:

- use `current repository mainline` or `production-intended composition path` where deployment was not verified;
- preserve `mainline-wired`, `controlled-runtime-confirmed`, `resource-conditional`, and `real-production-execution-unknown` qualifications in final summary tables, not only in body text;
- do not convert repository reachability into deployed-production fact.

## 8. Minor Issues

### Minor m-001 — TASK-003 historical entry attribution is stronger than the provenance record

Affected files:

- `docs/team/ANALYSIS/TASK-003-offline-index-chain.md`
- `docs/team/HANDOFFS/TASK-003-offline-index-chain-handoff.md`

Supporting evidence is strong:

- `scripts/run_step_11_2_acceptance.py` is the only repository producer found for the exact report schema/stage strings;
- the active index directory contains the matching acceptance report;
- the report's build and verification fields match the script's output structure.

Remaining gap:

- no original command line, process log, source commit or signed producer identity is stored;
- the report itself acknowledges that CLI override provenance and build commit are unknown.

Required owner action:

Replace “已证实构建/验收入口” with narrower wording such as “唯一仓库内可归因的高置信报告生产入口”; keep the exact invocation and historical code version as `unknown`.

### Minor m-002 — “Evidence 唯一 canonical 创建点” needs path scoping

Affected file:

- `docs/team/ANALYSIS/TASK-002-online-rag-chain.md`

For the real-RAG success path, the stated `RAGResultMapper -> RAGEvidenceContractBuilder` creation point is correct.

It is not globally unique across all repository runtime modes:

- `FakeRAGService._bundle_from_tool_output` can construct an `EvidenceBundleSchema` when the fake tool output does not already contain one;
- `LegacyEvidenceAdapter.extract` can construct a canonical contract while upgrading legacy data.

Required owner action:

Change the heading/conclusion to “real-RAG canonical success path creation point” and list fake/legacy construction as non-production or compatibility alternatives with their correct statuses.

## 9. Questions Requiring Explicit Resolution

### Question Q-001 — What process is the actual deployed ingress?

Repository CLI and application boundaries are confirmed. No HTTP/API registration or deployment service command is confirmed inside this repository. External deployment adapters remain unknown.

The owner should state whether the review scope is strictly “repository mainline” or includes an external deployed entry. Do not call the latter verified without external evidence.

### Question Q-002 — What is the actual Milvus trusted-filter behavior of the active database?

BM25 exact filtering against the current JSONL metadata will return no scoped records. The active Milvus entity schema/value behavior was not inspected because `pymilvus` is absent in the current environment.

This must remain `unknown` until a read-only trusted-scope probe is run against the frozen active database with the required dependency.

### Question Q-003 — Which real deployment values override repository defaults?

The inspected shell has relevant environment overrides unset, but service-manager, container, VPN/API and deployment environment values were not supplied. Algorithm and real/fake RAG status must not be inferred from the local shell alone.

## 10. Cross-Report Consistency Matrix

| Topic | TASK-001 | TASK-002 | TASK-003 | TASK-004 | TASK-005 | QA result |
|---|---|---|---|---|---|---|
| Coarse SchemeWriter/UseCase execution | legacy/inactive | accepts TASK-001 boundary | n/a | coordinator coarse path legacy | accepts explicit graph | consistent and confirmed |
| Explicit graph RAG trigger | active | active starting point | online consumer | algorithm attachment base | Evidence/state consumer | consistent and confirmed |
| Active index identity | n/a | exact pointer/version | exact pointer/version | inherited | lineage reference | consistent and confirmed |
| Missing scope metadata | n/a | confirmed | confirmed historical difference | prerequisite gap | ownership finding | consistent and confirmed |
| `LegacyEvidenceAdapter` | n/a | legacy/inactive canonical mainline | n/a | n/a | active compatibility | contradictory; TASK-005 unsupported |
| Algorithm activation | boundary only | active/conditional with real-runtime unknown | active artifact limitations | final labels active despite real-runtime unknown | consumes statuses | body mostly consistent; final status wording requires correction |
| Trace concurrency | trace exists | RAG trace lineage | n/a | parallel/feedback trace clues | cross-process only marked unknown | same-process ordering risk omitted |
| Immutable baseline | dirty noted | dirty noted | dirty noted | dirty noted | dirty noted and QA freeze requested | blocks approval |

## 11. Required Changes by Original Owner

| Owner | Required change |
|---|---|
| AI Project Manager | Freeze an immutable review baseline and require every report to reference it. |
| TASK-001 Owner | Replace deployed-production implications with repository-mainline wording unless deployment evidence is supplied. Rebind tests and evidence to the frozen baseline. |
| TASK-002 Owner | Scope the Evidence creation-point claim; align `LegacyEvidenceAdapter` classification; preserve real-runtime unknowns in summary. |
| TASK-003 Owner | Downgrade historical entry certainty to evidence-supported attribution; preserve exact command/commit as unknown. |
| TASK-004 Owner | Carry resource and real-runtime qualifications into every final `active` label; do not equate controlled execution with deployed execution. |
| TASK-005 Owner | Correct `LegacyEvidenceAdapter` status; add same-process Trace ordering risk; update Trace authority conditions; re-run declared QA invariants after the isolated test collects. |
| Workflow/Package/Test Owner | Resolve the circular import that prevents the high-risk identity/durability test from collecting. |
| Trace Runtime Owner | Resolve and verify atomic event-sequence allocation and append under concurrent writers in a separate implementation task. |

## 12. Review Disposition by Report

| Report | Disposition |
|---|---|
| TASK-001 As-Is | Core chain supported; changes required for baseline and production terminology. |
| TASK-002 As-Is | Core online RAG chain supported; changes required for Evidence creation scope and compatibility classification consistency. |
| TASK-003 As-Is | Artifact chain/counts supported; historical entry provenance wording requires correction. |
| TASK-004 As-Is | Algorithm placement and controlled execution supported; final activation wording requires correction. |
| TASK-005 As-Is | Most ownership mapping supported; blocked by uncollectable declared verification and material Trace/compatibility classification issues. |

## 13. Final Conclusion

blocked
