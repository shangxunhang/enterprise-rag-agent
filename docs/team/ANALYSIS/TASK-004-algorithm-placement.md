# TASK-004 Algorithm Placement Analysis

## 1. Investigation Metadata

- Task ID: `TASK-004`
- Owner Role: Algorithm Lead
- Investigation date: `2026-07-26`
- Repository: `D:\MyCode\rag-agent`
- Git branch: `main`
- Git commit: `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`
- Exact tag: none
- Workspace state: dirty before this task started; the pre-existing changes are not attributed to TASK-004.
- Files written by TASK-004: only this Analysis file and the matching Handoff file.
- Business code, configuration, Prompt, tests, index, data, `ARCHITECTURE.md`, `CONTRACTS.md`, `STATUS.md`, and TASK files were not modified.

## 2. Upstream Baseline Consistency

The following upstream investigation artifacts were read before this analysis:

- `docs/team/ANALYSIS/TASK-001-online-agent-workflow-chain.md`
- `docs/team/HANDOFFS/TASK-001-online-agent-workflow-chain-handoff.md`
- `docs/team/ANALYSIS/TASK-002-online-rag-chain.md`
- `docs/team/HANDOFFS/TASK-002-online-rag-chain-handoff.md`
- `docs/team/ANALYSIS/TASK-003-offline-index-chain.md`
- `docs/team/HANDOFFS/TASK-003-offline-index-chain-handoff.md`

All three upstream tasks record the same branch and commit as this task. Their shared baseline is also a dirty checkout. No branch/commit divergence was found.

The online algorithm investigation is attached to the TASK-002 verified chain:

`SchemeGenerationStageService / SchemeEvidenceService`
→ `RAGService`
→ `RetrievalRuntime`
→ `ParentChildRAGEngine`
→ `ParentChildRetrievalPipeline`
→ evidence contract/context
→ section generation
→ generation quality check
→ section workflow routing.

The offline TASK-003 chain supplies the active index lineage and parent/child artifacts consumed by the online runtime. None of the seven named algorithms is implemented as an offline indexing algorithm in the current baseline; parent/child chunking and embedding are separate offline processing steps.

## 3. Evidence and Status Rules Used

A name, import, class, registry entry, configuration block, or object construction was treated only as candidate evidence. `active` was assigned only where the concrete implementation can be attached to the current mainline, its condition can be satisfied, a direct call is present, and controlled runtime evidence demonstrates execution and output consumption.

Runtime evidence in this task is bounded to existing tests and read-only environment inspection. The following command completed successfully:

```text
21 passed in 11.39s
```

The selected test set covered adaptive planning, mutually exclusive Multi-Query/HyDE transforms, source and query RRF, configured Hybrid execution without the legacy Hybrid object, CRAG corrective retrieval, context strategy selection/reordering, the section corrective branch, and the Self-RAG retrieve-more/recheck branch.

Current local interpreter capability check:

```text
pymilvus=False
sentence_transformers=False
```

Therefore, controlled execution is confirmed, but a real Milvus Lite + real embedding/reranker/model run in this checkout remains unconfirmed. This distinction is retained in every relevant conclusion.

## 4. Current Algorithm Call-Point Index

| Chain stage | Current call edge | Algorithm candidate |
|---|---|---|
| Request planning | `ParentChildRetrievalPipeline.run` → `AdaptiveRetrievalPlanner.plan` | Adaptive-RAG-style request planning |
| Query transform | `ParentChildRetrievalPipeline.run` → `QueryTransformSelector.transform` → one transformer | Identity / RAG-Fusion Multi-Query / HyDE |
| Per-query recall | `_retrieve_one_query` → every configured `CandidateRetrieverPort.retrieve` | Hybrid retrieval sources |
| Source fusion | `_retrieve_one_query` → `ChildRRFFusionPlugin.fuse` | Hybrid RRF |
| Parent enrichment | `_retrieve_one_query` → `ParentChildCandidateEnricher.enrich` | Parent-child backfill/dedup |
| Cross-query fusion | `_fuse_query_sets` → `ParentRRFFusionPlugin.fuse` → `MultiQueryFusion.fuse` | RAG-Fusion result fusion; also shared by HyDE’s two-query result sets |
| Rerank | `ParentChildRetrievalPipeline.run` → configured reranker | BGE parent cross-encoder rerank |
| Retrieval assessment | `_assess` → `CRAGEvidenceAssessorPlugin.assess` → `CRAGJudge.evaluate` | CRAG-lite evidence assessment |
| Bounded correction | `_run_correction` → gate → `SectionGapCorrectiveQueryPlanner.plan` → full retrieval again | CRAG-style corrective retrieval |
| Context assembly | `ParentChildRAGEngine.run` → `ContextGate.pack` → selected packer | Lost-in-the-Middle mitigation |
| Generation check | `SectionGenerationService.generate_section` → `SelfRAGLiteGenerationCheckerPlugin.check` | Self-RAG-lite |
| Local generation repair | generation checker result → `LocalRewriteRepairStrategyPlugin.repair` → checker recheck | Self-RAG local rewrite branch |
| More retrieval after generation | `SectionExecutionGraph._evaluate_generation` → `_retrieve_more` → `_retrieve_recovery` → generate/check again | Self-RAG retrieve-more branch |

## 5. Algorithm Position Matrix

| ID | Algorithm / concrete implementation | Stage | Direct caller | Direct callee | Status on this baseline |
|---|---|---|---|---|---|
| HYB-1 | Configured Dense + BM25 + child RRF + parent enrichment | Recall/fusion | `ParentChildRetrievalPipeline._retrieve_one_query` | `MilvusDenseChildRetrieverPlugin.retrieve`, `BM25ChildRetrieverPlugin.retrieve`, `ChildRRFFusionPlugin.fuse`, `ParentChildCandidateEnricher.enrich` | **active**, conditional on real-RAG runtime resources; controlled execution confirmed |
| HYB-2 | `HybridParentChildRetriever` monolithic class | Legacy combined retrieval | no current production caller found | its own `retrieve` method | **legacy** |
| RF-1 | `MultiQueryTransformer` + per-query retrieval + parent-level RRF | Query expansion/cross-query fusion | pipeline planner/selector and `_fuse_query_sets` | `QueryExpander.rewrite_queries`, `ParentRRFFusionPlugin.fuse` | **active** when plan mode is `multi_query` |
| RF-2 | `online_strategy_*` experiment matrix overrides | Experiment runner/config | experiment path only | same RF-1 implementation through overrides | **experimental** as an experiment configuration, not a second production implementation |
| HYDE-1 | `HyDEQueryTransformer` + original/hypothetical query retrieval + parent RRF | Query expansion/cross-query fusion | pipeline selector and `_fuse_query_sets` | `QueryExpander.build_hypothetical_document`, parent RRF | **active** when plan mode is `hyde` |
| CRAG-1 | `CRAGEvidenceAssessorPlugin` + `CRAGJudge` | Post-rerank evidence assessment | `ParentChildRetrievalPipeline._assess` | assessor `assess`, judge `evaluate` | **active**, assessor executes on every retrieval run |
| CRAG-2 | Evidence sufficiency gate + section-gap corrective query planner + full reretrieval | Bounded correction | `ParentChildRetrievalPipeline._run_correction` | gate `decide`, planner `plan`, `_retrieve_one_query` | **active** when evidence is insufficient and budget remains |
| CRAG-3 | `NoOpEvidenceAssessorPlugin` | Controlled baseline/test alternative | registry construction only when explicitly configured | no-op `assess` | **inactive** under current static config |
| SR-1 | Self-RAG-lite post-generation checker | Generation post-check | `SectionGenerationService.generate_section` | `SelfRAGLiteGenerationCheckerPlugin.check` → `SelfRAGJudge.check_answer` | **active** by current default unless explicitly disabled |
| SR-2 | Local rewrite + citation rebuild + checker recheck | Generation repair | `SectionGenerationService.generate_section` | `LocalRewriteRepairStrategyPlugin.repair`, checker `check` | **active** when `need_rewrite=true` and `need_retrieve_more=false` |
| SR-3 | Section graph retrieve-more + regenerate + recheck | Generation-to-retrieval feedback | `SectionExecutionGraph._evaluate_generation` | `_retrieve_more` / `_retrieve_recovery` / `_generate_section` | **active** when `need_retrieve_more=true` and budget remains |
| SR-4 | Self-RAG logic inside `SectionExecutionCoordinator.execute` | Old coarse section orchestration | fallback only when no section graph is attached | coordinator-local loop | **legacy** for the current production graph |
| SR-5 | `NoOpGenerationCheckerPlugin` + `NoOpRepairStrategyPlugin` | Explicit disabled baseline | `AgentQualityFactory.build` when Self-RAG disabled | no-op checker/repair | **inactive** under default configuration; active only as explicit disabled-mode baseline |
| ADP-1 | `AdaptiveRetrievalPlanner` | Request planning | `ParentChildRetrievalPipeline.run` | `AdaptiveRetrievalPlanner.plan` | **active** on every retrieval request |
| LIM-1 | `ContextGate` + `LostInMiddleContextPacker` | Context ordering/packing | `ParentChildRAGEngine.run` | `ContextGate.pack` → packer `pack` | **active** when measured evidence size exceeds threshold |
| LIM-2 | `ContextPacker(packing_strategy=...)` compatibility facade | Compatibility implementation surface | concrete plugin subclasses call inherited behavior | `_lost_in_middle_reorder` | **legacy compatibility surface**, while its inherited algorithm body is used by LIM-1 |

## 6. Enablement and Trigger Matrix

| Implementation | Configuration source and default | Override priority / current value | Trigger condition | Execution count bound |
|---|---|---|---|---|
| HYB-1 | `static_retrieval_v1.yaml`; dense and BM25 entries omit `enabled`, whose schema default is `true` | `RAG_STATIC_RETRIEVAL_SPEC_FILE` can replace the spec; current shell variable unset, so default file applies | `RuntimeOptions.use_real_rag=true`; runtime factory builds successfully; one call per retrieval query | 2 retriever calls + 1 source fusion + 1 enricher per query |
| RF-1 | same static spec; `multi_query.num_rewrites=3` | request `retrieval_plan_overrides.query_transform_mode` has highest per-request effect; otherwise planner rules | query is short/ambiguous, multi-aspect, or formal task; or explicit override | one transform; up to original + 3 rewrites; query RRF only if result-set count > 1 |
| HYDE-1 | same static spec; `hyde.use_llm=true`, deterministic fallback true | same plan override mechanism | abstract/mechanism/architecture intent not already matching higher-priority Multi-Query conditions; or explicit override | one transform; original + one hypothetical query when nonempty; one cross-query RRF |
| CRAG-1 | same static spec; confidence threshold `0.55`, minimum relevant chunks `1`, max judged chunks `8` | spec replacement only; current shell unset | always after rerank, including after each correction round | initial assessment once; one reassessment per correction round |
| CRAG-2 | `retrieval_gate_policy_v1.yaml`; correction budget `1`; planner max queries `2` | request override may set correction budget `0..3` | `assessment.sufficient=false` and remaining budget > 0 | default at most 1 correction round, max 2 corrective queries in that round |
| SR-1 | `grounded_generation_v1.yaml`; checker enabled; `ENABLE_AGENT_SELF_RAG` default true | explicit function/CLI argument overrides environment; `run_pipeline.py` passes `not --disable-agent-self-rag`; current shell unset | generated nonempty section reaches post-citation/post-grounding check | at least one pre-check per generated section; additional post-rewrite and post-retrieval checks conditional |
| SR-2 | same policy; local rewrite enabled; max rewrite rounds `1` | section budget override can reduce available budget | `need_rewrite=true`, `need_retrieve_more=false`, rewrite budget available | default at most 1 local rewrite; accepted only after citation rebuild and recheck |
| SR-3 | same policy; max retrieval rounds `1` | section/parallel budget override can reduce available budget | `need_retrieve_more=true` and retrieval budget remains | default at most 1 Self-RAG recovery retrieval; then regenerate and recheck |
| ADP-1 | `intent_policy_v1.yaml`; short query threshold `16`; correction budget from gate policy | request override may force one of `identity`, `multi_query`, `hyde` and correction budget | every nonempty retrieval request | exactly one planning call and one mutually exclusive query transform |
| LIM-1 | static spec; ratio `0.65`; default window `8192`, reserved `1536`, section budget `4096`, max evidence items `5` | request `context_requirements` from `SchemeEvidenceService.retrieve` overrides defaults | estimated tokens of the first candidate set exceed `evidence_token_budget * 0.65` | exactly one selected context packer per RAG engine run |

Current shell values for all relevant environment variables were unset. This establishes which code defaults apply in the inspected shell, not how an external deployment is configured.

## 7. Detailed Findings by Algorithm

### 7.1 Hybrid Retrieval

#### Minimum verifiable behavior

Hybrid is considered running only when at least two retrieval sources execute for the same retrieval query, their ranked outputs are combined by a defined fusion operation, the fused output is transformed into candidates used downstream, and the downstream reranker/context path consumes those candidates.

#### HYB-1 — current configured implementation

- Files:
  - `backend/rag/config/static_retrieval_v1.yaml`
  - `backend/rag/runtime/parent_child_runtime_factory.py`
  - `backend/rag/application/parent_child_retrieval.py`
  - `backend/rag/plugins/retrievers/plugin.py`
  - `backend/rag/plugins/fusions/plugin.py`
  - `backend/rag/plugins/candidate_enrichers/plugin.py`
  - `backend/rag/runtime/resource_pool.py`
- Construction:
  - `ParentChildRuntimeFactory.build` reads the static spec and builds both retriever plugins, child RRF, parent enricher, query fusion, reranker and quality components through the registry.
- Complete reachable path:
  - `SchemeEvidenceService.retrieve`
  - → application RAG facade/service
  - → `RAGService.retrieve`
  - → `RetrievalRuntime.retrieve`
  - → `ParentChildRAGEngine.run`
  - → `ParentChildRetrievalPipeline.run`
  - → `_retrieve_one_query`
  - → each configured retriever’s `retrieve`
  - → `ChildRRFFusionPlugin.fuse`
  - → `ParentChildCandidateEnricher.enrich`
  - → query-set fusion if needed
  - → reranker
  - → CRAG/context pack/evidence contract.
- Direct inputs:
  - `RetrievalRequest.query`, dense `filter_expr`, and trusted keyword scope fields `tenant_id`, `kb_ids`, `file_ids`, `doc_ids`.
- Internal processing:
  - dense child search and BM25 child search each return `CandidateSet`;
  - child RRF contribution uses reciprocal rank through `rrf_fuse` with `rrf_k=60`;
  - parent enricher groups by `parent_chunk_id`, preserves matched child evidence, backfills parent text, and selects top 5.
- Output:
  - parent-level `retrieval_result_v2` candidates with retrieval source, rank, score, matched child IDs/chunks and lineage metadata.
- Downstream consumer:
  - `_fuse_query_sets` or the reranker; then CRAG assessment; then context pack/evidence mapping; finally generation consumes the packed evidence.
- State/metadata changes:
  - source hit counts, configured stack metadata, `retrieval_sources`, `rrf_contributions`, matched-child fields and parent-found metadata are attached.
- Exception behavior:
  - missing configured retrievers or duplicate/empty source names fail construction;
  - missing runtime resources fail retrieval and are converted by `RAGService.retrieve` into a failed evidence bundle, except budget/cancellation exceptions which are re-raised.
- Runtime evidence:
  - tests verified source RRF and parent enrichment semantics and a full multi-query configured retrieval stack without constructing the legacy Hybrid object.
- Status: **active**, under the real-RAG/resource condition. Current local real-dependency execution is **unknown** because the inspected `.venv` lacks both Milvus and sentence-transformer dependencies.

#### HYB-2 — monolithic old implementation

- File: `backend/rag/retriever/hybrid_parent_child_retriever.py`
- Class/method: `HybridParentChildRetriever.retrieve`
- Behavior: directly calls dense and BM25 retrievers, applies RRF, parent grouping and backfill in one object.
- Current callers: no production caller or runtime factory construction was found outside tests/compatibility references.
- Replacement evidence: HYB-1 decomposes the same duties into registered retriever, fusion and enricher plugins; the current pipeline test explicitly verifies that the main pipeline does not construct the legacy Hybrid object.
- Status: **legacy**.

### 7.2 RAG-Fusion

#### Minimum verifiable behavior

RAG-Fusion requires multiple rewritten retrieval queries for one original question, retrieval for each query, reciprocal-rank fusion across the result sets, and consumption of the fused ranking by reranking/context assembly.

#### RF-1 — current production-capable implementation

- Files/classes:
  - `backend/rag/planning/retrieval_planner.py` — `AdaptiveRetrievalPlanner.plan`
  - `backend/rag/query/query_transform_selector.py` — `QueryTransformSelector.transform`
  - `backend/rag/plugins/query_transformers/plugin.py` — `MultiQueryTransformer.transform`
  - `backend/rag/query/query_expander.py` — rewrite generation and deterministic fallback
  - `backend/rag/plugins/fusions/plugin.py` — `ParentRRFFusionPlugin.fuse`
  - `backend/rag/retrieval/query_fusion.py` — `MultiQueryFusion.fuse`
  - `backend/rag/application/parent_child_retrieval.py` — per-query execution and `_fuse_query_sets`.
- Trigger:
  - short/ambiguous, multi-aspect or formal query/task, or explicit request override.
- Input:
  - original query, runtime context, configured rewrite count `3`, optional model gateway.
- Processing:
  - the original query remains in `retrieval_queries`;
  - up to three distinct rewrites are added;
  - every query executes the full HYB-1 per-query stack;
  - parent-level result sets are fused with `1 / (rrf_k + rank)`, `rrf_k=60`, top 5;
  - fused output is reranked against the original query.
- Output:
  - `QueryExpansionResult` with rewritten queries and `rag_fusion` metadata;
  - parent candidates with `query_fusion_source_ranks`, contributions, source query labels and `query_fusion_score`.
- Consumer:
  - configured reranker, CRAG assessor, context gate and evidence contract.
- State/metadata:
  - `retrieval_plan.query_transform_mode=multi_query`, `query_expansion.rewritten_queries`, transformer execution metadata and query-fusion execution metadata.
- Exception behavior:
  - budget/cancellation from query-generation model calls is re-raised;
  - other generation/parse failures use deterministic rewrite fallback when enabled;
  - invalid transform overrides fail with `ValueError`.
- Runtime evidence:
  - selected test verifies only the Multi-Query transformer runs in this mode;
  - full configured stack test verifies multi-query retrieval and parent RRF without the legacy Hybrid object.
- Status: **active** when the plan selects `multi_query`.

#### RF-2 — experiment matrix

- Files: `backend/rag/experiments/online_strategy_matrix_v1.yaml`, smoke variants.
- Behavior: forces `query_transform_mode=multi_query` and disables correction for controlled comparison, but invokes RF-1 rather than defining another algorithm body.
- Status: **experimental** configuration only.

### 7.3 HyDE

#### Minimum verifiable behavior

HyDE requires generation of a hypothetical document from the original query, use of that generated text as a retrieval query, combination of its retrieval results with the original query’s results, and downstream consumption of the combined candidates.

#### HYDE-1 — current implementation

- Files/classes:
  - planner and selector files listed above;
  - `HyDEQueryTransformer.transform` in `backend/rag/plugins/query_transformers/plugin.py`;
  - `QueryExpander._build_hypothetical_document` in `backend/rag/query/query_expander.py`;
  - current per-query retrieval and parent RRF files.
- Trigger:
  - the query matches the planner’s abstract/mechanism/architecture terms after higher-priority Multi-Query conditions are false, or a request override forces `hyde`.
- Input:
  - original query, model gateway/runtime context, HyDE generation parameters.
- Processing:
  - generate one 200–350 Chinese-character hypothetical knowledge-base-style passage when the model succeeds;
  - otherwise create a deterministic hypothetical passage when fallback is enabled;
  - append it to the original query;
  - execute full HYB-1 retrieval for both query texts;
  - fuse parent result sets with parent RRF and rerank with the original query.
- Output:
  - `QueryExpansionResult.hyde_query`, `metadata.hyde`, and combined/reranked parent candidates.
- Consumer:
  - CRAG assessor, context gate, evidence contract, and generation.
- State/metadata:
  - `retrieval_plan.query_transform_mode=hyde`, `query_expansion.hyde_query`, generation method, latency, raw output/fallback markers and query-fusion metadata.
- Exception behavior:
  - model budget/cancellation is re-raised;
  - other generation/empty-output failures use deterministic fallback when enabled;
  - an empty final hypothetical query would leave only the original query and prevent query-set fusion.
- Runtime evidence:
  - adaptive planner test selects `hyde` for an abstract query;
  - transformer test confirms a nonempty hypothetical query is appended and no other transformer runs;
  - code path shows both result sets feed `_fuse_query_sets` and its output feeds rerank.
- Status: **active** when the plan selects `hyde`. A real model-generated HyDE passage and real-index effect are **unknown** in the inspected environment.

### 7.4 CRAG

#### Minimum verifiable behavior

For this codebase, CRAG is considered running only when reranked evidence is explicitly judged, a sufficiency result is emitted, an insufficient result can open a bounded correction gate, a new corrective query is executed through the retrieval stack, and the corrected result is reassessed and used downstream.

#### CRAG-1 — evidence assessor

- Files/classes:
  - `backend/rag/plugins/evidence_assessors/plugin.py` — `CRAGEvidenceAssessorPlugin.assess`
  - `backend/rag/judge/rag_quality_judge.py` — `CRAGJudge.evaluate`, `judge_chunk`
  - `backend/rag/application/parent_child_retrieval.py` — `_assess`.
- Position: after rerank, before context packing and generation.
- Input: original query and a deep copy of reranked results.
- Processing:
  - judge at most 8 chunks;
  - each chunk receives `relevant`, `partial`, or `irrelevant` plus score/reason;
  - aggregate confidence is computed from label weights and per-item scores;
  - evidence is sufficient only when confidence is at least `0.55` and relevant count is at least `1`.
- Output: immutable-style `EvidenceAssessment` plus detailed report and verification mode.
- Consumer: correction gate, final `evidence_quality`, result mapper, canonical evidence assessment, section evidence gate and trace metadata.
- State behavior: the judge is assessment-only and receives a deep copy; it does not filter, mutate, reorder or annotate the live candidate list.
- Exception behavior:
  - model budget/cancellation is re-raised;
  - other judge failures fall back to deterministic judging when enabled and mark degraded mode;
  - without results, confidence is zero and evidence is insufficient.
- Runtime evidence: correction-loop tests verify assessment is always called initially and again after full correction.
- Status: **active**, always after rerank.

#### CRAG-2 — bounded corrective retrieval

- Files/classes:
  - `backend/rag/plugins/correction_gates/plugin.py` — `EvidenceSufficiencyCorrectionGate.decide`
  - `backend/rag/plugins/corrective_query_planners/plugin.py` — `SectionGapCorrectiveQueryPlanner.plan`
  - `backend/rag/application/parent_child_retrieval.py` — `_run_correction`.
- Trigger: assessment is insufficient and correction budget remains.
- Input: original query, assessment reason/item judgments, request/document context and previous reranked candidates.
- Processing:
  - plan at most 2 complementary queries;
  - execute each through dense + BM25 + child RRF + parent enrichment;
  - optionally merge previous candidates;
  - parent-RRF fuse, rerank, reassess;
  - terminate when sufficient, no queries are returned, or budget is exhausted.
- Output: final reranked evidence, final assessment and `corrective_retrieval` trace with rounds, queries, fusion, rerank count and assessment.
- Consumer: context gate, evidence contract, section evidence gate, budget/trace projection.
- Exception behavior:
  - gate closed on sufficient evidence or exhausted budget;
  - planner budget/cancellation is re-raised;
  - other planner generation failures use deterministic query fallback when enabled;
  - empty planned queries stop correction with an explicit final reason.
- Runtime evidence: four correction-loop tests passed; mainline acceptance test demonstrated a corrective section retrieval branch and evidence recovery.
- Status: **active** conditionally.

#### Naming boundary

This implementation is explicitly “CRAG-style / C-RAG-lite.” It performs knowledge-base evidence assessment and bounded corrective retrieval. It does not implement external web search, corpus decompose-recompose, or evidence deletion as part of the judge itself.

### 7.5 Self-RAG

#### Minimum verifiable behavior

Self-RAG is considered running only when a generated section is checked against its query and evidence, the check emits an actionable result, the workflow consumes that result to rewrite or retrieve more, and the repaired/regenerated answer is checked again before finalization.

#### SR-1 — active checker

- Files/classes:
  - `backend/apps/enterprise_document/config/grounded_generation_v1.yaml`
  - `backend/bootstrap/agent_quality_factory.py`
  - `backend/apps/enterprise_document/quality/plugins.py` — `SelfRAGLiteGenerationCheckerPlugin.check`
  - `backend/rag/judge/rag_quality_judge.py` — `SelfRAGJudge.check_answer`
  - `backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py` — `generate_section`.
- Reachable production path:
  - `SupervisorFactory.build` constructs and injects checker/repair into `SchemeWriterAgent`;
  - `EnterpriseDocumentWorkflowEngine` attaches `SectionExecutionGraph`;
  - `SchemeGenerationStageService.execute_sections` calls the section graph;
  - graph `_generate_section` calls `SectionGenerationService.generate_section`;
  - the service invokes the checker after citation linking and grounding repair.
- Input:
  - section task/query, final section text, packed evidence context, citations, citation bindings and runtime IDs.
- Processing:
  - LLM or deterministic check of faithfulness, answer relevance and support;
  - emits `is_supported`, labels, `need_rewrite`, `need_retrieve_more`, problems, unsupported claims and score.
- Output consumer:
  - local rewrite branch inside `SectionGenerationService`;
  - `SectionExecutionGraph._evaluate_generation` reads `generation_check.need_retrieve_more`;
  - quality evaluator and final section metadata also consume the result.
- Exception behavior:
  - empty answer returns a non-supported “no_answer” report without rewrite/retrieval request;
  - budget/cancellation is re-raised;
  - other model failures use deterministic fallback when configured.
- Status: **active** by default. Current CLI requires an explicit `--disable-agent-self-rag` to replace it with no-op components.

#### SR-2 — local rewrite branch

- Direct condition: `need_rewrite=true` and `need_retrieve_more=false`.
- Processing:
  - consume one rewrite round from the section workflow budget;
  - run evidence-constrained local rewrite;
  - strip candidate citation markers, rebuild deterministic citations and bindings;
  - reject empty/truncated/unbound required-section candidate;
  - run the Self-RAG checker again;
  - accept only if the candidate is supported and no longer needs rewrite.
- Output:
  - possibly replaced final section content and bindings;
  - `repair_result`, `repair_recheck`, `repair_accepted` and final `generation_check` in section `extra`.
- Downstream consumer:
  - section quality evaluator, graph generation evaluation, final section artifact.
- Exception behavior:
  - unavailable repair LLM or configured fallback returns original text with report;
  - budget/cancellation is re-raised;
  - an unaccepted candidate does not replace the original section.
- Status: **active** conditionally.

#### SR-3 — retrieve-more branch

- Files/classes:
  - `backend/apps/enterprise_document/workflows/section_execution_graph.py` — `_evaluate_generation`, `_retrieve_more`, `_retrieve_recovery`, `_generate_section`, `_finalize_section`.
- Direct condition: `generation_check.need_retrieve_more=true` and retrieval-round budget remains.
- Processing:
  - consume one retrieval round;
  - build a recovery query for the section;
  - retrieve with scope `self_rag_recovery`;
  - remap citations through the document registry;
  - if context/citations are usable and evidence is not blocked, increment generation attempt;
  - regenerate section with recovered evidence;
  - check it again;
  - append before/after check data to `self_rag_retrieval_rounds`.
- Output consumer:
  - graph routing and final `SectionEvidenceBundleSchema`/section metadata.
- State transitions:
  - `evaluate_generation` → `retrieve_more` → `generate_section` → `evaluate_generation` → `finalize_section`, bounded by workflow budget.
- Exception/failure behavior:
  - exhausted budget routes to finalization and records exhaustion;
  - failed/empty recovered context routes to finalization with error metadata;
  - recovered evidence assessed insufficient for a citation-required section produces an insufficient-evidence section and finalizes;
  - unresolved `need_retrieve_more` is explicitly recorded at finalization.
- Runtime evidence:
  - mainline acceptance test produced exactly one round; `check_before.need_retrieve_more=true`, `check_after=false`, scope `self_rag_recovery`, and retrieval budget usage `1`.
- Status: **active** conditionally.

#### SR-4 — legacy coordinator implementation

- File/class: `backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py`, `SectionExecutionCoordinator.execute`.
- It contains similar section retrieval, Self-RAG and recovery logic.
- Current production relation:
  - `EnterpriseDocumentWorkflowEngine.__init__` constructs and attaches `SectionExecutionGraph` whenever the stage service exposes the coordinator;
  - `SchemeGenerationStageService.execute_sections` calls `section_execution_graph.execute` when attached;
  - `SectionExecutionCoordinator.execute` is only a fallback when no graph is attached.
- Direct runtime evidence:
  - existing phase tests patch `SectionExecutionCoordinator.execute` to raise and still complete the production mainline through the section graph.
- Status: **legacy** for the current production graph, although it remains a compatibility fallback for manually constructed stage services without an attached graph.

#### Naming boundary

The active implementation is “Self-RAG-lite,” not a token-level reflection/retrieval training method. It is a post-generation judge plus bounded local rewrite or workflow-level retrieve-more/regenerate/recheck loop.

### 7.6 Adaptive-RAG

#### Minimum verifiable behavior

Adaptive behavior requires a request-dependent routing decision that changes actual downstream retrieval execution, rather than a static “adaptive” label in trace metadata.

#### ADP-1 — current request planner

- Files/classes:
  - `backend/rag/planning/retrieval_planner.py` — `AdaptiveRetrievalPlanner.plan`
  - `backend/rag/query/query_transform_selector.py`
  - `backend/rag/application/parent_child_retrieval.py`.
- Position: first algorithmic decision inside every retrieval pipeline run.
- Input:
  - nonempty query and request context (`task_type`, optional `retrieval_plan_overrides`).
- Processing priority:
  1. short (`<=16` compact characters), ambiguous, multi-aspect or formal → `multi_query`;
  2. otherwise abstract/mechanism/architecture → `hyde`;
  3. otherwise → `identity`;
  4. request override can replace the selected mode;
  5. correction budget is carried separately and does not predict CRAG outcome.
- Output:
  - `RetrievalPlan` containing plan ID, one mutually exclusive query-transform mode, correction budget, reason and matched signals.
- Consumer:
  - `QueryTransformSelector.transform` chooses exactly one transformer;
  - `_run_correction` consumes the plan’s correction budget after evidence assessment;
  - result/trace mapper records the plan.
- State/metadata:
  - `retrieval_plan`, query-transform mode, signals and reason are stored in engine output and evidence trace.
- Exception behavior:
  - empty query or unsupported override raises `ValueError`;
  - correction budget is clamped to `0..3`.
- Runtime evidence:
  - tests cover short→Multi-Query, abstract→HyDE, formal routing, citation requirement not prematurely predicting CRAG, valid/invalid overrides and policy separation.
- Status: **active** on every retrieval request.

#### Naming boundary

This is an Adaptive-RAG-style heuristic request planner, but it does not select among “no retrieval / single-hop / multi-hop” modes. Retrieval always proceeds through the configured stack. It adapts the query transform and correction budget; CRAG correction remains a post-retrieval evidence decision.

No second production `AdaptiveRAGRouter` or similar implementation was found.

### 7.7 Lost-in-the-Middle Mitigation

#### Minimum verifiable behavior

A valid conclusion requires measured long-context detection, explicit reordering or placement behavior, and proof that the reordered context becomes the prompt/evidence context consumed downstream.

#### LIM-1 — current conditional context reordering

- Files/classes:
  - `backend/rag/context/context_gate.py` — `ContextGate.pack`
  - `backend/rag/plugins/context_packers/plugin.py` — `LostInMiddleContextPacker`
  - `backend/rag/context/context_packer.py` — `_collect_candidates`, `_lost_in_middle_reorder`, `pack`
  - static retrieval config context section.
- Position: after final retrieval/CRAG correction and before evidence contract/generation.
- Input:
  - final reranked evidence and resolved `ContextRequirements`.
- Trigger:
  - estimate tokens from up to `max_evidence_items` candidates;
  - choose the Lost-in-the-Middle packer only when estimated tokens are strictly greater than `evidence_token_budget * 0.65`.
- Processing:
  - candidate selection/dedup/max-item filtering occurs in original rerank order;
  - only the selected context order is rearranged;
  - example ordering `[1,2,3,4,5,6] → [1,3,5,6,4,2]`, keeping high-ranked evidence near prompt edges;
  - token/character budgets may drop later items or truncate the first item.
- Output:
  - `ContextPack.context`, selected/dropped results, citations, packing strategy, token usage and truncation IDs;
  - selected records preserve `pre_context_rank` and receive new `context_rank`/rank.
- Consumer:
  - `RAGResultMapper` builds the canonical evidence contract from selected/dropped records and exposes the packed context;
  - `SchemeEvidenceService.extract_rag_output` projects this context;
  - section generation consumes `rag_context.context_text`.
- Exception behavior:
  - invalid strategy/budgets fail construction;
  - packer drops empty/duplicate/overflow items with explicit reasons;
  - it raises if the final packed context exceeds the token budget.
- Runtime evidence:
  - tests verify measured-size gate selection and exact prompt-edge reordering.
- Status: **active** when the measured threshold is crossed; otherwise the default packer is active.

#### Naming boundary

This is explicit context reordering and edge placement. It does not perform segmented generation, retrieval diversification, or a model-side attention modification.

## 8. Input / Processing / Output / Consumer Summary

| Algorithm | Main input | Key processing | Main output | Actual consumer |
|---|---|---|---|---|
| Hybrid | one retrieval query + trusted filters/scope | dense and BM25 recall; child RRF; parent grouping/backfill | parent candidates | cross-query fusion/reranker |
| RAG-Fusion | original query | generate rewrites; retrieve each; parent RRF | fused parent ranking | reranker |
| HyDE | original abstract query | hypothetical document; retrieve original + hypothetical; parent RRF | fused parent ranking | reranker |
| CRAG | reranked evidence | per-item relevance; aggregate sufficiency; optional corrective reretrieval | final evidence + assessment + correction trace | context gate and evidence contract |
| Self-RAG | generated section + evidence + citations | support/relevance check; optional rewrite or retrieve-more; recheck | accepted/rejected repair, before/after checks | section graph, quality evaluator, final artifact |
| Adaptive planner | query + request context | mutually exclusive transform selection; correction budget | retrieval plan | query selector and correction loop |
| Lost-in-the-Middle | final ranked candidates + context budget | measured long-context gate; edge-oriented reorder; budget packing | packed context/citations/rank projection | evidence contract and generation |

## 9. Multi-Implementation Relationships

| Algorithm | Current implementation | Other implementation/artifact | Relationship |
|---|---|---|---|
| Hybrid | decomposed configured plugins | `HybridParentChildRetriever` | old monolithic implementation replaced on the current mainline |
| RAG-Fusion | production-capable Multi-Query transformer + parent RRF | experiment matrix overrides | experiment config reuses production implementation |
| HyDE | production-capable HyDE transformer + shared parent RRF | experiment matrix override | experiment config reuses production implementation |
| CRAG | configured assessor/gate/planner | no-op assessor | no-op is an explicit baseline, not current config |
| Self-RAG | generation service + section graph | coordinator coarse loop | coordinator method is legacy production fallback; graph is current production path |
| Self-RAG | configured checker/repair | no-op checker/repair | no-op components represent explicit disabled mode |
| Adaptive-RAG | `AdaptiveRetrievalPlanner` | none found | single current implementation |
| Lost-in-the-Middle | concrete plugin subclass using inherited reorder body | generic strategy facade | facade retained for compatibility; current factory selects concrete plugins |

## 10. Static and Runtime Evidence Table

| Evidence | Result | What it proves | What it does not prove |
|---|---|---|---|
| Static spec and policy files | all seven current components/conditions located | configuration and defaults | external deployment override values |
| Runtime factory and supervisor factory | components constructed and injected | composition reachability | successful real resource loading |
| Direct call-chain inspection | outputs feed rerank/context/graph decisions | downstream consumption | production data quality or effect size |
| 21 selected existing tests | all passed | controlled algorithm execution and route behavior | real Milvus/model execution |
| Self-RAG mainline acceptance | one bounded retrieve-more/recheck round observed | SR-3 execution and output consumption | real judge provider behavior |
| Corrective retrieval acceptance | one recovery path observed | bounded section corrective branch | CRAG real-index/model quality |
| Local dependency inspection | Milvus and sentence-transformer packages absent | current `.venv` cannot run full real chain | other deployment environments |
| Current shell environment | algorithm override variables unset | default files/default Self-RAG apply in this shell | service-manager/container environment |

## 11. State and Contract Fields Written or Read

### Retrieval planning and query transformation

- Reads:
  - query;
  - `extra_metadata.task_type`;
  - `extra_metadata.retrieval_plan_overrides`;
  - static spec and policy files.
- Writes/returns:
  - `retrieval_plan.plan_id`;
  - `retrieval_plan.query_transform_mode`;
  - `retrieval_plan.correction_budget`;
  - `retrieval_plan.reason` and `signals`;
  - `query_expansion.original_query`, `rewritten_queries`, `hyde_query`, `retrieval_queries`, transformer metadata.

### Retrieval and CRAG

- Reads:
  - `RAGToolInputSchema.query`, filters, trusted `access_scope`, `extra.extra_metadata`;
  - candidate IDs, ranks, texts and scores.
- Writes/returns:
  - source/query fusion metadata;
  - matched-child and parent lineage fields;
  - `evidence_quality.initial_assessment`;
  - `evidence_quality.final_assessment`;
  - `evidence_quality.corrective_retrieval.triggered`, rounds, gate decisions, query plans and assessments;
  - canonical `EvidenceBundleSchema.assessment`, `correction_trace`, `budget_usage`, trace extras.

### Context packing

- Reads:
  - `context_requirements.model_context_window`, `prompt_reserved_tokens`, `section_token_budget`, `max_evidence_items`, `max_context_chars`.
- Writes/returns:
  - `context_pack.packing_strategy`, `context`, `selected_results`, `dropped_results`, citations, token/character usage and truncation IDs;
  - selected candidate `pre_context_rank` and `context_rank`.

### Self-RAG and section workflow

- Reads:
  - generated section text;
  - `RAGContextSchema.context_text`;
  - citations and citation bindings;
  - `generation_quality_metadata` and `WorkflowBudget`;
  - `generation_check.need_rewrite` and `need_retrieve_more`.
- Writes/returns:
  - section `extra.generation_check`;
  - `repair_result`, `repair_recheck`, `repair_accepted`;
  - section graph payload `generation_check`, `budget_state`, `generation_attempt`, `recovery_count`, `pending_self_rag_round`, `self_rag_retrieval_rounds`, active evidence fields and exhaustion fields;
  - final section evidence metadata `self_rag_retrieve_more_success`, `self_rag_recheck`, unresolved/budget-exhausted flags;
  - `workflow_budget.retrieval_rounds`, `rewrite_rounds`, LLM-call/token counters;
  - trace/custom events for retrieval, generation, repair and section completion.

These fields are handed to TASK-005 for ownership analysis; no ownership conclusion is made here.

## 12. Confirmed Facts

1. The current online retrieval topology is configuration-driven and decomposed; the old monolithic Hybrid retriever is not the current mainline object.
2. Multi-Query and HyDE are mutually exclusive request transforms, selected by the active planner; they are not executed as a sequential strategy chain.
3. RAG-Fusion’s current result fusion occurs at parent-result-set level after each query has already completed dense/BM25 child fusion and parent enrichment.
4. CRAG assessment always occurs after rerank; corrective retrieval is a separate, bounded conditional loop.
5. The CRAG judge is assessment-only and does not mutate/filter/reorder the live evidence list.
6. Self-RAG is active by default and executes after citation linking/grounding repair; its result is consumed by both local repair logic and the current section graph.
7. Self-RAG retrieve-more is bounded by the section workflow budget and performs regenerate + recheck before finalization.
8. The production section path uses `SectionExecutionGraph`; the coordinator’s coarse `execute` method is a legacy fallback.
9. Adaptive planning always runs, but adapts query transformation and correction budget rather than deciding whether retrieval should happen at all.
10. Lost-in-the-Middle mitigation is an actual measured-threshold context reorder whose packed output reaches generation.

## 13. Unknowns and Evidence Gaps

1. Real Milvus Lite, real m3e embedding, real BGE reranker, real query-generation model, real CRAG judge model and real Self-RAG judge model were not executed in this task. The inspected `.venv` lacks required retrieval/model packages.
2. External deployment environment variables and command-line overrides are unknown; only current shell defaults were resolved.
3. The active historical index’s exact build commit remains unknown from TASK-003.
4. The active historical index’s scope fields do not fully match the current online trusted-scope requirements; real production retrieval against that index remains unconfirmed by TASK-002/003.
5. Real runtime frequency distribution among `identity`, `multi_query`, and `hyde` is unknown because no production trace corpus for this baseline was established.
6. Real runtime frequency of CRAG correction, Self-RAG rewrite, Self-RAG retrieve-more and Lost-in-the-Middle selection is unknown.
7. `RAGResultMapper` sets reranker model/version trace fields to runtime placeholders; exact real reranker lineage in a public evidence trace is not established by this task.
8. Evidence-bundle `budget_usage.queries_executed` is derived from original + rewritten queries and does not visibly include HyDE or corrective query counts in that formula; this is recorded as a handoff finding, not changed.

## 14. Findings Outside the Execution Scope

- Finding F-004-01: current real-runtime dependency closure is incomplete in the inspected `.venv`; production execution cannot be inferred from controlled tests.
- Finding F-004-02: current active index lineage/scope compatibility remains a prerequisite for validating HYB-1, RF-1, HYDE-1 and CRAG-1/2 on real data.
- Finding F-004-03: public retrieval budget/trace projection may not fully count HyDE and corrective queries.
- Finding F-004-04: the codebase retains a legacy coarse section coordinator implementation alongside the active section graph; production reachability is differentiated, but the duplicate behavior remains present.
- Finding F-004-05: several acceptance scripts explicitly disable Agent Self-RAG, while the normal CLI and runtime defaults enable it; test scenario settings must be read before using a run as evidence of Self-RAG execution.

No finding was fixed or modified in this task.

## 15. Final Status by Required Algorithm

| Required algorithm | Concrete current conclusion |
|---|---|
| Hybrid Retrieval | **active** as the configured Dense + BM25 + child RRF + parent enrichment stack; old monolithic class **legacy** |
| RAG-Fusion | **active** conditionally under `multi_query`; experiment matrices **experimental** only |
| HyDE | **active** conditionally under `hyde`; real model/index execution **unknown** |
| CRAG | assessor **active always after rerank**; correction **active conditionally**; no-op alternative **inactive** under current config |
| Self-RAG | checker, local rewrite and section-graph retrieve-more paths **active conditionally** under default-enabled policy; old coordinator execution **legacy** |
| Adaptive-RAG | active Adaptive-RAG-style planner on every request, with narrower behavior than the general algorithm name |
| Lost-in-the-Middle | **active conditionally** as measured-threshold context edge reordering; generic facade is compatibility/legacy surface |

## 16. Suggested Task Status

`review`

Reason: all seven required algorithms have concrete implementation-level status, call paths, conditions, I/O, consumption evidence, runtime tests and explicit unknowns. TASK-005 should now verify state/contract ownership, and TASK-006 should independently reproduce the high-risk active/legacy distinctions and real-runtime limitations.
