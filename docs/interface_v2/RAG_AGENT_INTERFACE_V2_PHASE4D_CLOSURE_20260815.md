# RAG-Agent Interface V2 Phase 4D Closure

日期：2026-08-15
阶段：Interface V2 Phase 4D — Verification Implementation
仓库：`D:\MyCode\rag-agent`

---

# 1. Phase 4D Goal

本轮只执行已经冻结的 Phase 4D Verification Plan / Plan Audit：

1. 补齐 Phase 4 最后的独立 verification evidence；
2. 迁移 7 个 confirmed stale test cases；
3. 迁移 2 个 stale acceptance scripts；
4. 重跑 Phase 4B / 4C / Phase 3C architecture regressions；
5. 重新执行 production static architecture scan；
6. 对 broader red 做 `FIRST FAILURE + LATENT STALE ASSERTIONS` 双层分类；
7. 在不修改 production architecture 的前提下给出 Phase 4 最终关闭判定。

本轮没有进入 Phase 5，也没有开始 Multi-Agent V2。

---

# 2. Frozen Architecture

本轮继续以以下冻结结论为唯一架构口径：

```text
EvidenceBundleSchema
=
sole canonical Evidence authority
```

正常方向：

```text
EvidenceBundle
-> typed Application carrier
-> deterministic projection / Gate / citation mapping / hash / summary
```

禁止：

```text
projection / dict / RAGContext / SharedState compatibility state
-> canonical EvidenceBundle reconstruction
```

唯一合法 legacy compatibility ingress：

```text
legacy ToolResult / legacy payload
-> LegacyEvidenceAdapter
-> EvidenceBundleSchema
-> same typed production path
```

Application persistence full typed Bundle whitelist：

```text
1. document_evidence_bundle
2. active_evidence_bundle
3. SectionEvidenceBundleSchema.evidence_bundle
4. transient Research invocation typed evidence_bundle
```

说明：RAG producer 自身的 typed canonical output contract 不是 Application duplicate persistence authority；本轮扫描重点仍是禁止 canonical Bundle 在 Application projection / terminal / capture / observer / history 中二次 fan-out。

---

# 3. Actual Files Changed

Phase 4D 本轮实际新增/修改：

```text
backend/tests/test_rag_interface_v2_phase4d_verification.py        NEW
backend/tests/test_step_14_context_manager.py
backend/tests/test_scheme_writer_secondary_service_boundaries.py
backend/tests/test_section_aware_evidence_retrieval.py
backend/tests/test_mainline_closure_acceptance.py
backend/tests/test_phase5_real_agent_adapters.py
scripts/run_mainline_closure_acceptance.py
scripts/run_section_aware_retrieval_acceptance.py
docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4D_CLOSURE_20260815.md NEW
```

Production code modified by Phase 4D：

```text
NO
```

工作树中 Phase 4A / 4B / 4C 的既有累计 production 修改全部保留；本轮没有 reset / restore / clean / stash，也没有自动 add / commit / push。

---

# 4. Dedicated Verification Results

新增：

```text
backend/tests/test_rag_interface_v2_phase4d_verification.py
```

最终结果：

```text
5 passed
0 failed
```

覆盖关系：

| Requirement | Dedicated scenario | Result |
|---|---|---|
| T4D-01 | fresh-runtime top-level durable resume | PASS |
| T4D-02 | completed document retrieval fail-on-call / replay=0 | PASS |
| T4D-03 | real `all_citations` persisted restore continuity | PASS |
| T4D-04 | ContextManager typed positive parity + illegal extra negative | PASS |
| T4D-05 | real lightweight runtime consumer chain | PASS |
| T4D-06 | Legacy adapter regression / typed re-entry | PASS |
| T4D-07 | Phase 3 Gate authority regression | PASS（独立 suite） |
| T4D-08 | Phase 4B / 4C negative architecture regression | PASS（独立 suites） |

最终核心合并回归：

```text
Phase 4D dedicated
+ Phase 4B
+ Phase 4C
+ Phase 3C
=
48 passed
0 failed
```

---

# 5. Fresh-runtime Durable Resume Evidence

Dedicated test：

```text
test_fresh_runtime_durable_resume_preserves_bundle_and_never_replays_document_retrieval
```

第一实例真实创建：

```text
MainlineApplicationService
-> SupervisorFactory
-> Supervisor
-> EnterpriseDocumentWorkflowEngine
-> SchemeGenerationStageService
-> RAG facade / model facade
-> SQLite durable checkpoint
```

第一实例运行到：

```text
retrieve_document_evidence completed
+
final human-review pause
```

pause 前 business node sequence：

```text
normalize_project_input
plan_document
retrieve_document_evidence
initialize_generation
execute_sections
document_verify
repair_document_conflicts
incremental_document_reverify
assemble_document
validate_document_assembly
final_artifact_review
decide_document_version
evaluate_document_gate
build_generation_report
```

随后重新构造第二实例：

```text
new MainlineApplicationService
new SupervisorFactory
new Supervisor
new WorkflowEngine
new StageService
new RAG facade
new model facade
```

显式 identity assertions：

```text
second service      is not first service
second factory      is not first factory
second supervisor   is not first supervisor
second engine       is not first engine
second stage service is not first stage service
second RAG facade   is not first RAG facade
second model facade is not first model facade
```

同时确认：

```text
same SQLite checkpoint path
same checkpoint thread id / run_id
same checkpoint namespace
resumed = true
resume_count = 1
cursor continues from durable checkpoint
completed node sequence not duplicated
```

因此：

```text
fresh-runtime resume = TRUE FRESH
```

不是旧 Engine / StageService 复用测试。

---

# 6. Completed Document Retrieval No-Replay Evidence

resume 阶段直接对 production method：

```text
SchemeGenerationStageService.retrieve_document_evidence
```

建立 fail-on-call：

```text
if called -> AssertionError -> test immediately fails
```

实际结果：

```text
resume document retrieval calls = 0
second runtime RAG facade runnable_invocation_count = 0
business_node_sequence.count("retrieve_document_evidence") = 1
completed top-level node duplicate = 0
```

同时读取真实 SQLite persisted payload，验证：

```text
hash(document_evidence_bundle before resume)
==
hash(document_evidence_bundle after resume)

retrieval_trace_id before resume
==
retrieval_trace_id after resume
```

结论：

```text
document retrieval replay = 0
```

---

# 7. CitationRegistry Persisted Restore Continuity Evidence

Dedicated test 使用真实 persistence shape：

```text
SchemeGenerationGraphPayloadSchema.all_citations
-> SchemeGenerationGraphStateCodec.build_initial_update
-> JSON serialization
-> SchemeGenerationGraphStateCodec.restore_payload
-> restored all_citations
-> DocumentCitationRegistry.from_citations
```

验证序列：

```text
A -> C1
B -> C2
C -> C3

restore

A(new local id) -> C1, catalog size unchanged
B(new local id) -> C2, catalog size unchanged
D -> C4
D(new local id) -> C4, catalog size unchanged
```

同时验证：

```text
citation_ref stable
local marker remap stable
catalog dedup stable
document label allocation continuous
canonical Bundle citation ids unchanged
canonical Bundle hash unchanged
```

结论：

```text
CitationRegistry persisted restore / dedup / continuous numbering = PASS
```

---

# 8. ContextManager Typed Prompt Semantic Parity Evidence

正向输入：

```text
rag_context = projection only
evidence_bundle = explicit typed canonical Bundle
```

验证：

```text
selected Evidence E1 exists
E1.citation_ids == [C1]
rendered context contains [C1]
previous_sections exists
lineage.index_version == idx_v1
lineage.embedding_model == m3e-base
section/task/project context retained
context char/token budgets respected
```

负向输入：

```text
RAGContext.extra["evidence_contract"] = fake/full Bundle
BUT
evidence_bundle = None
```

验证：

```text
E1 is NOT recovered
only rag_context_projection is available
index_version is NOT reverse-recovered
embedding_model is NOT reverse-recovered
```

结论：

```text
ContextManager typed semantic parity = PASS
projection -> canonical reverse recovery = BLOCKED
```

---

# 9. Real Lightweight Compatibility Consumer Evidence

Dedicated T4D-05 沿真实 runtime chain 执行：

```text
StageService final projection
-> SchemeWriterOutput
-> SharedState.contexts.rag_tool_output
-> MainlineApplicationService summary
-> scripts/run_demo.py::_section_report_rows
-> persist_end_to_end_artifacts
-> E2E report
-> ExperimentRunManifest file
```

真实 lightweight section summary 验证：

```text
section_id
section_title
evidence_contract_sha256
status
citation_count
selected_evidence_count
tool_call_ids
recovery_count
```

明确禁止且实际 absent：

```text
section_evidence[*]["retrieval_scope"]
section_evidence[*]["query"]
section_evidence[*]["citations"]
section_evidence[*]["metadata"]
section_evidence[*]["evidence_bundle"]
```

合法替代来源：

```text
scope/query
<- generated section.extra[evidence_scope/evidence_query]

recovery_count/tool_call_ids/hash/count
<- lightweight section_evidence summary

citation semantics
<- document citation catalog + generated section citation_ids / bindings
```

`contexts.rag_tool_output` 完成 JSON round-trip，并递归验证不存在：

```text
schema_version == rag_evidence_contract_v1 full Bundle
nested evidence_bundle full carrier
```

真实 report 与 manifest 文件均成功构造。

结论：

```text
real compatibility consumers = PASS
C. REAL IMPLEMENTATION GAP candidate = NOT TRIGGERED
```

---

# 10. Legacy Adapter Regression Evidence

验证：

```text
legacy ToolResult
-> LegacyEvidenceAdapter.upgrade
-> EvidenceBundleSchema
```

保持：

```text
compatibility_upgrade = true
upgraded_by = LegacyEvidenceAdapter
```

projection 正常：

```text
context
chunks
citations
normalized lightweight fields
```

禁止字段实际 absent：

```text
normalized["evidence"]
normalized["evidence_contract"]
RAGContext.extra["evidence_contract"] full Bundle
```

`SchemeEvidenceService.retrieve()` 对 legacy ToolResult 执行 upgrade 后返回 typed canonical Bundle，重新进入正常 typed path。

结论：

```text
LegacyAdapter upgrade regression = PASS
```

---

# 11. Seven Stale Test Cases Cleanup Results

| ID | Test | Migration | Result / classification |
|---|---|---|---|
| D-01 | `test_section_policy_preserves_evidence_citations_history_and_lineage` | explicit `evidence_bundle=contract`; no full Bundle in RAGContext.extra | PASS |
| D-02 | `test_legacy_evidence_adapter_upgrades_chunks_into_canonical_contract` | `upgrade()` typed Bundle + `extract()` projections; normalized full evidence absent | PASS |
| D-03 | `test_fake_mainline_can_explicitly_enable_section_aware_retrieval` | scope from generated `section.extra`; tool ids from lightweight summary | PASS |
| D-04 | `test_corrective_retrieval_branch_runs_and_recovers` | scope/query from `section.extra`; recovery/tool ids from summary/trace | migration complete; FIRST FAILURE remains E strict Gate |
| D-05 | `test_document_citation_registry_resolves_local_id_collisions` | document catalog + section citation binding | migration complete; FIRST FAILURE remains E strict Gate |
| D-06 | `test_self_rag_need_retrieve_more_runs_bounded_retrieval_and_recheck` | workflow budget + generation check + section.extra + target trace | migration complete; FIRST FAILURE remains E strict Gate |
| D-07 | `test_research_agent_tool_gateway_owns_section_retrieval` | direct gateway/evidence call scope assertions | migration complete; FIRST FAILURE remains E strict Gate |

Focused final execution：

```text
3 passed
4 failed
```

4 failures 全部发生在旧：

```text
summary.status == success / partial_success
```

而当前 strict Claim Audit / Document Hard Gate 返回 `failed`。

这 4 个 case 的后置 pre-4C output-shape assertion 已全部迁走；没有通过恢复 retired terminal fields 制造绿测。

因此：

```text
7 confirmed stale test cases migrated = YES
```

其中 4 个测试文件仍因 E.OUT OF PHASE 4 的第一失败保持红色，这是计划内分类，不是 Phase 4D cleanup 未完成。

---

# 12. Two Stale Acceptance Scripts Cleanup Results

## 12.1 `scripts/run_mainline_closure_acceptance.py`

已迁：

```text
corrective scope
<- generated section.extra[evidence_scope]
```

不再从 retired section summary 取 `retrieval_scope/query`。

脚本可完整运行并生成 acceptance report；aggregate exit code 仍为 1，原因是 corrective/citation 场景最终业务状态被 strict Gate 拒绝。

分类：

```text
migration = PASS
script business acceptance = E.OUT OF PHASE 4 red
```

## 12.2 `scripts/run_section_aware_retrieval_acceptance.py`

已迁：

```text
scope <- section.extra[evidence_scope]
query <- section.extra[evidence_query]
```

实际检查：

```text
citation_required_sections_use_section_rag = PASS
context_package_uses_section_evidence_lineage = PASS
```

脚本 aggregate exit code 仍为 1，剩余失败：

```text
mainline_success                     -> strict Gate E
trace tool_started/tool_finished gap -> existing trace acceptance drift E
```

没有 retired `section_evidence.retrieval_scope/query` failure。

因此：

```text
2 stale acceptance scripts migrated = YES
```

---

# 13. Phase 4B / 4C Regression

## Phase 4B

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
16 passed
0 failed
```

包括：

```text
citation_ref stable identity
canonical hash invariant
v2 top-level Bundle checkpoint round-trip
v2 Section active Bundle checkpoint round-trip
one canonical Section carrier JSON round-trip
```

## Phase 4C

```text
backend/tests/test_rag_interface_v2_phase4c_compatibility_removal.py
23 passed
0 failed
```

包括：

```text
production reverse reconstruction negative scan
canonical hash immutable under citation projection
rejected candidate citation non-commit
Research candidate non-commit
parallel fan-in accepted-only citation commit
top-level v1 reject + v2 round-trip
Section v1 reject + v2 round-trip
no implicit missing-version -> v1 fallback
completed Section recover_or_reuse no replay
incomplete Section exact-v2-cursor resume
```

结论：

```text
Phase 4B regression = PASS
Phase 4C regression = PASS
```

---

# 14. Phase 3C Gate Regression

执行：

```text
backend/tests/test_rag_interface_v2_phase3c_gate_ownership.py
```

结果：

```text
4 passed
0 failed
```

生产 RAG negative scan：

```text
gate_decision = 0 source hits
generation_allowed = 0 source hits
human_review_required Application authority = 0 source hits
EvidenceGatePolicy under backend/rag = 0
DeliveryMode under backend/rag = 0
```

仍满足：

```text
Application = Gate authority
RAG = Evidence facts producer
```

---

# 15. Real Section Durable Resume / Reuse Evidence

额外执行：

```text
test_section_crash_resume_reuses_committed_paragraph_without_duplication
test_completed_section_recover_or_reuse_returns_checkpoint_result_without_resume
test_incomplete_section_recover_or_reuse_resumes_exact_v2_cursor
```

结果：

```text
3 passed
0 failed
```

证明：

```text
real Section crash/resume = PASS
committed paragraph not duplicated = PASS
completed Section reuse/no replay = PASS
incomplete Section v2 exact cursor resume = PASS
```

---

# 16. Static Architecture Scan Results

扫描范围：

```text
backend/apps/**
backend/context_manager/**
backend/agent/**
backend/schemas/**
backend/observability/**
```

## 16.1 Canonical reconstruction scan

source hit：

```text
RAGEvidenceContractSchema.model_validate
-> backend/apps/.../legacy_evidence_adapter.py only
```

普通 production projection -> Bundle reconstruction：

```text
0
```

`prompt_evidence_contract` 的剩余命中位于 writer governance 中，只读取 canonical Bundle `extra` 内 prompt-specific projection 并计算 hash；它不从 projection 构造 canonical Bundle。

## 16.2 RAGContext full Bundle writer

```text
RAGContext.extra["evidence_contract"] full Bundle writer = 0
```

## 16.3 normalized full Evidence

```text
normalized["evidence"] = 0
normalized.get("evidence") = 0
normalized full Evidence model_dump writer = 0
```

## 16.4 ContextBundle evidence retirement

```text
ContextBundle.evidence = 0
context_bundle.evidence = 0
set_evidence_context = 0
"evidence_context" = 0
```

## 16.5 Full Bundle fan-out classification

扫描剩余 `EvidenceBundleSchema` / `evidence_bundle` 命中后逐项分类：

```text
document_evidence_bundle                 -> legal canonical typed carrier
active_evidence_bundle                   -> legal canonical typed carrier
SectionEvidenceBundleSchema.evidence_bundle -> legal Section lifecycle carrier
Research invocation evidence_bundle      -> legal transient typed carrier
function parameters / readers            -> typed pass-through / projection read
all_rag_outputs                           -> retrieval_history_projection only
SchemeWriterOutput                        -> lightweight section summaries
contexts.rag_tool_output                  -> lightweight projections only
terminal AgentResult                      -> SchemeWriterOutput + generated/rejected/deliverable projections
capture                                   -> rag_context/chunks/citations/section projections only
observer                                  -> no canonical Bundle carrier
```

非法 Application full Bundle persistence/fan-out：

```text
0
```

## 16.6 Citation acceptance scan

关键 production path：

```text
parallel fan-in:
final accepted canonical Bundle citation identities
-> filter local diagnostic registry
-> DocumentCitationRegistry.register
```

direct recovery / Research recovery 由 Phase 4C negative tests 再次验证：

```text
rejected candidate document registry commit = 0
rejected diagnostic citation commit = 0
accepted-only commit = PASS
```

## 16.7 Checkpoint version scan

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION = 2
SECTION_EXECUTION_GRAPH_STATE_VERSION = 2
implicit values.get(..., 1) fallback = 0
implicit state_version default 1 = 0
```

Phase 4C tests 同时验证：

```text
top-level v1 explicit reject = PASS
Section v1 explicit reject = PASS
v2 round trips = PASS
```

---

# 17. Broader Red Classification — FIRST FAILURE + LATENT STALE

以下 broader reds 均不通过修改 Phase 4 production architecture 处理。

| Test | First failure | First failure classification | Remaining assertions audited? | Latent stale? | Latent stale classification | Phase 4 related? | Blocks PHASE 4 CLOSED? | Reason |
|---|---|---|---|---|---|---|---|---|
| `test_phase4_durable_resume_hitl::test_new_engine_resumes_without_replaying_completed_nodes` | resume 后期望 success/partial，实际 strict Gate FAILED | E.OUT OF PHASE 4 | Yes | No | N/A | No | No | dedicated fresh resume 已独立证明 no replay/hash/trace invariants |
| `test_phase4_durable_resume_hitl::test_application_service_inspects_and_approves_across_new_instance` | approve 后期望 deliverable success，strict Gate FAILED | E | Yes | No | N/A | No | No | HITL transport/resume 与 final quality gate 是不同验收面 |
| `test_phase3_section_execution_subgraph::test_production_path_uses_section_subgraph_not_legacy_coordinator` | expected success/partial, actual failed | E | Yes | No | N/A | No | No | strict Document Hard Gate |
| `test_phase4_section_agents_mainline::test_mainline_invokes_three_projected_section_agents` | verifier expected accept, actual reject | E | Yes | No Phase4 stale | verifier expectation drift | No | No | known verifier drift |
| `test_phase4_section_agents_mainline::test_section_checkpoint_persists_typed_agent_payloads` | checkpoint verifier action expected accept, actual reject | E | Yes | No Phase4 stale | verifier expectation drift | No | No | typed payload persists; verifier semantics changed |
| `test_phase4_section_agents_mainline::test_verifier_research_more_drives_real_section_route` | expected `[research_more, accept]`, actual `[research_more, reject]` | E | Yes | No Phase4 stale | verifier expectation drift | No | No | not Evidence carrier issue |
| `test_phase4_section_agents_mainline::test_final_writer_gate_blocked_stops_before_writer_and_verifier` | old fixture mutates RAG `gate_decision/generation_allowed`; retrieval fails before expected rejected artifact | E | Yes | Yes | stale RAG Gate fixture — E | No | No | restoring RAG Gate authority is explicitly forbidden |
| `test_phase8_recovery_budget_retirement::test_running_invocation_recovery_uses_latest_section_checkpoint` | final deliverable expected non-null | E | Yes | No | N/A | No | No | recovery-action assertions pass; strict Gate rejects final document |
| `test_phase8_recovery_budget_retirement::test_mainline_partial_success_publishes_deliverable` | expected partial_success, actual failed | E | Yes | No | N/A | No | No | strict Gate outcome |
| `test_phase8_recovery_budget_retirement::test_mainline_rewrite_scenario_creates_new_generation_attempt` | expected success, actual failed | E | Yes | Yes | verifier accept expectation drift — E | No | No | not Phase4 Evidence contract |
| `test_phase8_recovery_budget_retirement::test_mainline_paragraph_repair_replaces_conflicting_version` | expected success, actual failed | E | Yes | No | N/A | No | No | final strict Gate masks later assertions |
| `test_phase8_recovery_budget_retirement::test_mainline_replan_supersedes_attempt_and_rejects_old_result` | expected success, actual failed | E | Yes | No Phase4 stale | N/A | No | No | final strict Gate; replan contract belongs broader lifecycle suite |
| `test_p1_application_recovery::test_recover_run_resumes_after_committed_paragraph_without_duplication` | recovered expected success/partial, actual failed | E | Yes | No | N/A | No | No | Section recovery itself completed; final strict Gate fails |
| `test_p1_application_recovery::test_application_retry_run_recovers_retryable_failed_checkpoint` | retried expected success/partial, actual failed | E | Yes | No | N/A | No | No | retry path resumes; final strict Gate fails |
| `test_p1_application_recovery::test_running_invocation_recovery_actions_cover_resume_replay_retry_and_stale` | final deliverable expected non-null | E | Yes | No | N/A | No | No | all four recovery-action assertions pass before final deliverable assertion |
| `test_architecture_hardening::test_parallel_sections_are_not_replayed_or_recounted_after_durable_resume` | expected terminal `workflow_completed`, actual `workflow_failed` | E | Yes | No Phase4 stale | final gate expectation | No | No | no retired Evidence shape; dedicated + Section tests independently prove replay invariants |

Stale-cleanup cases D-04~D-07 的双层分类：

| Test | First failure | First failure classification | Remaining assertions audited? | Latent stale before migration? | Latent stale classification | Phase 4 related? | Blocks? |
|---|---|---|---|---|---|---|---|
| corrective retrieval | strict Gate status | E | Yes | Yes | D, migrated | only stale tail was Phase4 | No |
| citation collision | strict Gate status | E | Yes | Yes | D, migrated | only stale tail was Phase4 | No |
| self-RAG bounded retrieval | strict Gate status | E | Yes | Yes | D, migrated | only stale tail was Phase4 | No |
| Research gateway section retrieval | strict Gate status | E | Yes | Yes | D, migrated | only stale tail was Phase4 | No |

结论：

```text
broader red contains no unhandled Phase 4 blocker
```

---

# 18. Out-of-Phase Failures

当前已确认 E 类主要包括：

```text
Claim Audit / Document Hard Gate strict failure
Verifier accept/reject expectation drift
stale RAG Gate fixture
trace acceptance tool_started/tool_finished expectation drift
```

本轮没有做以下越界修改：

```text
NO Gate relaxation
NO Claim Audit modification
NO Verifier policy modification
NO strict/shadow mode change
NO RAG gate_decision restore
NO RAG generation_allowed restore
NO RAG human_review_required business authority restore
NO normalized["evidence"] restore
NO RAGContext full Bundle restore
NO ContextBundle.evidence restore
NO full SectionEvidence terminal output restore
```

---

# 19. Acceptance Script Runtime Classification

两份迁移脚本均已真实执行。

```text
run_mainline_closure_acceptance.py
-> report generated
-> migrated output-shape access works
-> aggregate red = strict business Gate / existing business checks

run_section_aware_retrieval_acceptance.py
-> report generated
-> scope/query legal-source checks PASS
-> aggregate red = strict Gate + existing trace tool-event expectation
```

因此脚本 cleanup 与脚本 aggregate business acceptance 必须分开判断；前者已完成，后者属于 E。

---

# 20. py_compile / compileall

执行：

```text
python -m py_compile <Phase4D changed tests/scripts>
python -m compileall -q backend scripts
```

结果：

```text
PASS
```

---

# 21. git diff --check

执行：

```text
git diff --check
```

结果：

```text
PASS
whitespace errors = 0
```

仅输出既有工作树 CRLF -> LF warning；没有 error，不影响 Phase 4 closure。

---

# 22. C. REAL IMPLEMENTATION GAP

Dedicated verification 没有证明以下任何 frozen invariant 被 production 违反：

```text
resume replay document retrieval                NO
CitationRegistry persisted restore label drift  NO
ContextManager typed semantics loss             NO
real runtime consumer missing legal source      NO
canonical Bundle hash changed by citation remap NO
projection -> canonical reverse reconstruction  NO
candidate pre-accept citation commit             NO
implicit v1 checkpoint fallback                 NO
RAG Gate authority regression                   NO
```

最终：

```text
C. REAL IMPLEMENTATION GAP = NONE
```

---

# 23. USER DECISION

本轮没有出现新的架构选择题，也没有 verification 结果要求重新打开 Phase 4A / 4B / 4C 设计。

```text
USER DECISION REQUIRED = NONE
```

---

# 24. Phase 4 Acceptance Matrix

| Acceptance | Result |
|---|---|
| EvidenceBundleSchema sole authority | PASS |
| production reverse reconstruction = 0 | PASS |
| illegal Application full Bundle outside persistence whitelist = 0 | PASS |
| ContextBundle.evidence retired | PASS |
| RAGContext full Bundle = 0 | PASS |
| normalized full Evidence = 0 | PASS |
| Application duplicate Evidence authority = 0 | PASS |
| citation pre-accept commit = 0 | PASS |
| v1 explicit reject | PASS |
| v2 top-level Bundle round-trip | PASS |
| v2 Section Bundle round-trip | PASS |
| real Section durable resume | PASS |
| fresh-runtime top-level durable resume | PASS |
| completed document retrieval replay = 0 | PASS |
| completed Section reuse/no replay | PASS |
| CitationRegistry persisted restore/dedup/C4 continuity | PASS |
| ContextManager typed semantics | PASS |
| LegacyAdapter upgrade | PASS |
| SchemeWriterOutput lightweight consumer | PASS |
| rag_tool_output lightweight consumer | PASS |
| canonical hash remap invariant | PASS |
| Phase 3C Gate ownership | PASS |
| Phase 4B regression | PASS |
| Phase 4C regression | PASS |
| 7 stale test cases migrated | PASS |
| 2 stale acceptance scripts migrated | PASS |
| architecture negative scans | PASS |
| py_compile / compileall | PASS |
| git diff --check | PASS（0 whitespace errors；仅既有 CRLF/LF warning） |
| unhandled Phase 4 implementation blocker | NONE |

---

# 25. Final Closure Decision

所有 Phase 4D dedicated verification gap 均已关闭。

所有已确认 Phase 4 stale tests / scripts 均已迁移到 frozen legal contract。

broader red 均已执行 FIRST FAILURE + LATENT STALE audit；剩余红项为 Phase 4 之外的 Gate / Verifier / legacy RAG-Gate fixture / trace expectation 问题，没有未处理 Phase 4 implementation blocker。

Phase 4D 本轮未修改 production code。

最终判定：

```text
PHASE 4D CLOSED
PHASE 4 CLOSED
```

Phase 4 关闭后停止；本轮不进入 Phase 5，不开始 Multi-Agent V2，不自动提交 Git。
