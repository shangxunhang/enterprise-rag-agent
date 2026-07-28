# TASK-004 Algorithm Placement Handoff

## 1. Handoff Metadata

- Task ID: `TASK-004`
- From: Algorithm Lead
- Primary recipient: Architect / TASK-005
- Verification recipient: QA / Reviewer / TASK-006
- Repository baseline: `main` @ `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`
- Exact tag: none
- Workspace: dirty before TASK-004
- Suggested task status: `review`
- Full evidence: `docs/team/ANALYSIS/TASK-004-algorithm-placement.md`

## 2. Confirmed Facts

### 2.1 Hybrid Retrieval

- Current implementation: configured plugin chain, not the monolithic legacy class.
- Main files:
  - `backend/rag/config/static_retrieval_v1.yaml`
  - `backend/rag/runtime/parent_child_runtime_factory.py`
  - `backend/rag/application/parent_child_retrieval.py`
  - `backend/rag/plugins/retrievers/plugin.py`
  - `backend/rag/plugins/fusions/plugin.py`
  - `backend/rag/plugins/candidate_enrichers/plugin.py`
- Active call edge:
  - `ParentChildRetrievalPipeline._retrieve_one_query`
  - → dense and BM25 retriever plugins
  - → child RRF
  - → parent enrichment/backfill.
- Output is consumed by query fusion/rerank, then CRAG, context packing and generation.
- Status: configured implementation **active** under real-RAG/resource conditions; `HybridParentChildRetriever.retrieve` **legacy**.

### 2.2 RAG-Fusion

- Current implementation:
  - `AdaptiveRetrievalPlanner.plan`
  - → `QueryTransformSelector.transform`
  - → `MultiQueryTransformer.transform`
  - → one complete retrieval stack per query
  - → `ParentRRFFusionPlugin.fuse`
  - → `MultiQueryFusion.fuse`.
- Trigger: planner selects `multi_query` or request override forces it.
- Formula: reciprocal-rank contribution `1 / (rrf_k + rank)`, current parent fusion `rrf_k=60`, top 5.
- Fused output is reranked and consumed downstream.
- Status: **active conditionally**.
- Experiment YAMLs force the same implementation and are **experimental configurations**, not separate production algorithms.

### 2.3 HyDE

- Current implementation:
  - `HyDEQueryTransformer.transform`
  - → `QueryExpander._build_hypothetical_document`
  - → retrieve original + hypothetical query
  - → parent RRF
  - → rerank with original query.
- Trigger: planner selects `hyde` or request override forces it.
- Failure mode: budget/cancellation propagates; ordinary model/parse failure uses deterministic fallback when enabled.
- Status: **active conditionally**; real model and real-index execution remains unconfirmed.

### 2.4 CRAG

- Retrieval assessor:
  - `CRAGEvidenceAssessorPlugin.assess`
  - → `CRAGJudge.evaluate`.
- It runs after rerank, observes a deep copy, and does not mutate/filter/reorder the candidate list.
- Current thresholds: confidence `0.55`, minimum relevant chunks `1`, max judged chunks `8`.
- Corrective branch:
  - `EvidenceSufficiencyCorrectionGate.decide`
  - → `SectionGapCorrectiveQueryPlanner.plan`
  - → complete reretrieval/fusion/rerank
  - → reassessment.
- Current default correction budget: `1`; max corrective queries per round: `2`.
- Status: assessor **active** on every retrieval run; corrective retrieval **active conditionally**; no-op assessor **inactive** under current spec.

### 2.5 Self-RAG

- Default enablement:
  - `ENABLE_AGENT_SELF_RAG` defaults to true when unset;
  - `run_pipeline.py` disables it only through `--disable-agent-self-rag`;
  - current shell variable is unset.
- Active checker:
  - `SectionGenerationService.generate_section`
  - → `SelfRAGLiteGenerationCheckerPlugin.check`
  - → `SelfRAGJudge.check_answer`.
- Local rewrite branch:
  - `need_rewrite=true` and `need_retrieve_more=false`
  - → consume rewrite budget
  - → local rewrite
  - → rebuild citations/bindings
  - → checker recheck
  - → accept only a supported candidate.
- Retrieve-more branch:
  - `SectionExecutionGraph._evaluate_generation`
  - reads `generation_check.need_retrieve_more`
  - → `_retrieve_more` / `_retrieve_recovery`
  - → scope `self_rag_recovery`
  - → regenerate and recheck.
- Current default section bounds: one retrieval round, one rewrite round.
- Runtime test observed one round with before=true, after=false, and retrieval budget usage 1.
- `SectionExecutionCoordinator.execute` contains similar coarse logic but is bypassed by the attached section graph in the current production mainline.
- Status: active checker/repair/retrieve-more branches **active conditionally**; coordinator coarse execution **legacy**; no-op components represent explicit disabled mode.

### 2.6 Adaptive-RAG

- Current implementation: `AdaptiveRetrievalPlanner.plan`.
- Runs on every nonempty retrieval request.
- Selects exactly one of `identity`, `multi_query`, or `hyde`, and carries a separate correction budget.
- It does not choose “no retrieval” and does not predict CRAG outcome from citation requirements.
- Status: **active**, with narrower actual behavior than the general Adaptive-RAG name.

### 2.7 Lost-in-the-Middle Mitigation

- Current implementation:
  - `ContextGate.pack`
  - → `LostInMiddleContextPacker`
  - → inherited `ContextPacker._lost_in_middle_reorder` and `pack`.
- Trigger: estimated evidence tokens are strictly greater than `evidence_token_budget * 0.65`.
- Candidate selection remains based on original rerank order; only selected context order is changed to move high-ranked evidence toward prompt edges.
- Output is projected into the canonical evidence contract and used as generation context.
- Status: **active conditionally**.

## 3. State / Contract Fields for TASK-005

TASK-005 should determine ownership, authorized writers and invariants for the following fields. TASK-004 only records observed readers/writers.

### 3.1 Retrieval plan and query expansion

Observed inputs:

- request query;
- `extra_metadata.task_type`;
- `extra_metadata.retrieval_plan_overrides`;
- static retrieval spec and policy IDs/versions.

Observed outputs/writes:

- `retrieval_plan.plan_id`;
- `retrieval_plan.query_transform_mode`;
- `retrieval_plan.correction_budget`;
- `retrieval_plan.reason`;
- `retrieval_plan.signals`;
- `query_expansion.original_query`;
- `query_expansion.rewritten_queries`;
- `query_expansion.hyde_query`;
- `query_expansion.retrieval_queries`;
- query-transformer execution metadata.

Observed readers:

- `QueryTransformSelector`;
- correction loop;
- `RAGResultMapper` and trace/evidence bundle projection.

### 3.2 Retrieval candidates and algorithm lineage

Observed writes:

- source candidate-set metadata;
- `retrieval_sources`;
- source ranks/scores and `rrf_contributions`;
- `matched_child_chunk_ids`;
- `matched_child_chunks`;
- `matched_child_count`;
- `parent_found`;
- query-fusion source ranks/contributions/query labels;
- `query_fusion_score` and query-fusion stage.

Observed readers:

- parent enricher;
- query fusion;
- reranker;
- evidence mapper/citation construction;
- context packer.

### 3.3 CRAG assessment and correction

Observed outputs/writes:

- `evidence_quality.initial_assessment`;
- `evidence_quality.final_assessment`;
- `evidence_quality.evidence_assessment`;
- `evidence_quality.verification_mode`;
- `evidence_quality.quality_mode_degraded`;
- `evidence_quality.corrective_retrieval.triggered`;
- correction rounds;
- gate decision;
- corrective query plan;
- round rerank count and reassessment;
- canonical `EvidenceBundleSchema.assessment`;
- `EvidenceBundleSchema.correction_trace`;
- evidence-contract assessment details and reason codes.

Observed readers:

- correction gate/loop;
- `RAGResultMapper`;
- `SchemeEvidenceService.extract_rag_output`;
- section evidence gate;
- final evidence metadata.

### 3.4 Context packing

Observed inputs:

- `context_requirements.model_context_window`;
- `context_requirements.prompt_reserved_tokens`;
- `context_requirements.section_token_budget`;
- `context_requirements.max_evidence_items`;
- `context_requirements.max_context_chars`.

Observed outputs/writes:

- `context_pack.packing_strategy`;
- `context_pack.context`;
- `context_pack.selected_results`;
- `context_pack.dropped_results`;
- `context_pack.citations`;
- token/character budgets and usage;
- truncation IDs;
- candidate `pre_context_rank`;
- candidate `context_rank`.

Observed readers:

- `RAGResultMapper`;
- evidence contract builder;
- `SchemeEvidenceService.extract_rag_output`;
- section prompt/generation services.

### 3.5 Self-RAG and section graph

Observed inputs:

- final generated section text;
- `RAGContextSchema.context_text`;
- citations;
- citation bindings;
- `generation_quality_metadata`;
- workflow budget snapshot.

Observed outputs/writes:

- section `extra.generation_check`;
- `generation_check.is_supported`;
- `generation_check.need_rewrite`;
- `generation_check.need_retrieve_more`;
- faithfulness/relevance labels, problems, unsupported claims, score and verification mode;
- `repair_result`;
- `repair_recheck`;
- `repair_accepted`;
- graph payload `generation_check`;
- `generation_attempt`;
- `recovery_count`;
- `pending_self_rag_round`;
- `self_rag_retrieval_rounds`;
- active context/chunks/citations/query/scope/assessment;
- `budget_state` and budget exhaustion fields;
- final evidence metadata for retrieve-more success, recheck, unresolved state and budget exhaustion;
- section `workflow_budget` snapshot.

Observed readers:

- `SectionGenerationService` local rewrite branch;
- `SectionExecutionGraph._evaluate_generation`;
- `_retrieve_recovery`;
- `_finalize_section`;
- section quality evaluator;
- final document/capture path.

## 4. Algorithm Routing and State Transitions

### 4.1 Retrieval request

```text
query
→ AdaptiveRetrievalPlanner.plan
→ exactly one query transform
→ one HYB-1 execution per retrieval query
→ optional parent query fusion
→ rerank
→ CRAG assessment
→ optional bounded correction + reassessment
→ ContextGate
→ default or Lost-in-the-Middle packer
→ evidence contract
```

Key transition conditions:

- `multi_query`: short/ambiguous/multi-aspect/formal or explicit override;
- `hyde`: abstract/mechanism/architecture after higher-priority checks or explicit override;
- CRAG correction: insufficient assessment and remaining correction budget;
- Lost-in-the-Middle: measured token threshold exceeded.

### 4.2 Section generation

```text
generate_section
→ Self-RAG check
├─ need_rewrite && !need_retrieve_more
│  → consume rewrite round
│  → local rewrite
│  → citation rebuild
│  → recheck
└─ return section + generation_check
   → SectionExecutionGraph.evaluate_generation
      ├─ need_retrieve_more && budget available
      │  → self_rag_recovery retrieval
      │  → regenerate
      │  → recheck
      ├─ missing required citation binding && corrective enabled
      │  → corrective_retrieval
      │  → regenerate
      └─ finalize
```

## 5. Budget Consumption Clues

- Retrieval-side CRAG correction budget originates from `retrieval_gate_policy_v1.yaml` and can be overridden per request; default `1`, clamped `0..3`.
- Section Self-RAG budget originates from `grounded_generation_v1.yaml` and section overrides:
  - `max_retrieval_rounds=1`;
  - `max_rewrite_rounds=1`;
  - `max_total_llm_calls=35`;
  - `max_total_tokens=24000`;
  - `human_review_on_exhaustion=true`;
  - `budget_scope=section`.
- Query rewrite, HyDE, CRAG judge, corrective query planning, Self-RAG check and local repair model calls route through model-budget hooks or an active workflow budget.
- Budget/cancellation exceptions are intentionally propagated at model-call boundaries; ordinary model/parse failures may use deterministic fallbacks where configured.

## 6. Trace and Observability Clues

- RAG service decorator writes `rag_started` and `rag_finished` events with `retrieval_trace_id`/call ID and selected evidence/citation counts.
- RAG trace extras include:
  - query expansion;
  - retrieval plan;
  - evidence quality;
  - packing strategy;
  - selected/dropped counts;
  - context token budget and usage;
  - truncation IDs.
- Section workflow emits custom events for section start/completion, retrieval start/completion, section generation and repair.
- Self-RAG recovery uses retrieval scope `self_rag_recovery` and distinct call suffixes.
- CRAG correction rounds are projected into evidence correction trace.

## 7. Runtime Evidence Supplied to TASK-006

Selected existing tests executed in one command:

- all tests in `backend/tests/test_adaptive_profile_router.py`;
- Multi-Query and HyDE mutually exclusive transformer tests;
- configured source RRF/parent enrichment test;
- parent query-fusion test;
- test proving main pipeline does not construct legacy Hybrid retriever;
- full configured multi-query stack test;
- all tests in `backend/tests/test_rag_corrective_retrieval_loop.py`;
- context gate strategy-selection and Lost-in-the-Middle reorder tests;
- mainline corrective retrieval acceptance test;
- mainline Self-RAG retrieve-more/recheck acceptance test.

Result:

```text
21 passed in 11.39s
```

Current local dependency inspection:

```text
pymilvus=False
sentence_transformers=False
```

TASK-006 should not treat the test result as evidence of real Milvus, embedding, reranker or quality-model execution.

## 8. Unconfirmed Questions

1. Does the external production environment use the default static retrieval, intent, gate and grounded-generation policy files, or override them through environment/CLI injection?
2. Can the active historical index satisfy the current trusted tenant/kb/file/doc scope filters end to end?
3. What are the real model/provider selections and fallback rates for query rewrite, HyDE, CRAG judging and Self-RAG judging?
4. What are the real execution frequencies for Multi-Query, HyDE, CRAG correction, Self-RAG rewrite, Self-RAG retrieve-more and Lost-in-the-Middle packing?
5. Does any external caller construct `SchemeGenerationStageService` without `EnterpriseDocumentWorkflowEngine` and therefore use the legacy coordinator fallback?
6. Does the public evidence budget projection intentionally omit HyDE/corrective queries, or is ownership elsewhere?
7. What exact reranker model/version should own the public trace lineage fields currently emitted as runtime placeholders?

## 9. Newly Discovered but Unhandled Findings

- `F-004-01`: the inspected `.venv` cannot execute the real online retrieval/model chain because both Milvus and sentence-transformer dependencies are absent.
- `F-004-02`: active index lineage/scope compatibility remains unresolved for real online execution.
- `F-004-03`: public `queries_executed` projection does not visibly count HyDE and corrective query executions.
- `F-004-04`: current code retains both the active section graph and the legacy coarse coordinator execution body.
- `F-004-05`: several acceptance scripts explicitly disable Agent Self-RAG even though normal runtime defaults enable it; each trace must be interpreted with scenario configuration.

No finding was fixed or changed.

## 10. Suggested Handoff Owners

- TASK-005 / Architect:
  - State/Contract ownership for all fields listed in Section 3;
  - authority for algorithm routing overrides;
  - budget ownership and query-count accounting;
  - trace/lineage field ownership;
  - active graph versus compatibility fallback contract.
- TASK-006 / QA Reviewer:
  - independently reproduce active/legacy classifications;
  - verify that output fields are actually consumed;
  - verify default/override priority;
  - separate Fake/Stub acceptance evidence from real-resource evidence;
  - challenge the narrowed naming claims for CRAG-lite, Self-RAG-lite and Adaptive-RAG-style planning.
- Data Engineer / index owner:
  - active index lineage and scope-field compatibility evidence.
- Runtime/Platform owner:
  - dependency closure and external environment override evidence.

## 11. Final Status Summary

| Algorithm | Final status |
|---|---|
| Hybrid | configured decomposed stack **active conditionally**; monolithic implementation **legacy** |
| RAG-Fusion | **active conditionally**; experiment configs **experimental** |
| HyDE | **active conditionally**; real model/index run **unknown** |
| CRAG | assessor **active**; correction **active conditionally**; no-op **inactive** under current config |
| Self-RAG | checker/local rewrite/retrieve-more **active conditionally** under default enabled policy; coordinator execution **legacy** |
| Adaptive-RAG | heuristic planner **active** on every retrieval request |
| Lost-in-the-Middle | context reorder **active conditionally** |

## 12. Suggested Task Status

`review`

The investigation is complete within TASK-004 scope. Remaining items require contract ownership review and independent verification, not additional Algorithm Lead implementation work in this task.
