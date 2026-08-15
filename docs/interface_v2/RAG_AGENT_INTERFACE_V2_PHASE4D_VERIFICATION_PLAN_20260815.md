# RAG-Agent Interface V2 Phase 4D Verification Plan / Gap Audit

日期：2026-08-15  
仓库：`D:\MyCode\rag-agent`  
阶段：Interface V2 Phase 4D — Checkpoint / Compatibility / Architecture Verification  
修订性质：**Verification Plan revision after independent audit**  
实施状态：**未进入 Phase 4D implementation；未修改 production code / tests**

---

# 1. Phase 4D Goal

Phase 4D 不是新的架构建设阶段。

前置阶段已经关闭：

```text
Phase 4A — Recovery Path Audit / Freeze
Phase 4B — Typed Canonical Evidence Carrier
Phase 4C — Compatibility Removal / Authority Cleanup
```

Phase 4D 的职责是：

```text
verification
+
deterministic stale-test / stale-consumer cleanup
```

而不是：

```text
production architecture redesign
```

当前总判断继续冻结：

```text
C. REAL IMPLEMENTATION GAP = NONE PROVEN
USER DECISION REQUIRED = NONE
```

本 Plan 的目标是用足够强、足够独立、与已知 Gate 红项解耦的证据关闭以下 5 个 Verification Gap：

```text
B-01 top-level durable cross-instance resume
B-02 completed document retrieval no replay
B-03 CitationRegistry checkpoint restore continuity
B-04 ContextManager typed prompt semantic parity
B-05 lightweight compatibility output real-consumer proof
```

只有 Verification 真正证明 production behavior 与冻结 invariant 冲突时，才允许把对应项升级为：

```text
C. REAL IMPLEMENTATION GAP candidate
```

不得因为旧 test 失败、旧 output shape 消失或 broad E2E 被 Hard Gate 阻断，就预设 production bug。

---

# 2. Frozen Architecture

以下事实已由 Phase 4A / 4B / 4C 冻结，Phase 4D 不重新讨论。

## 2.1 Canonical Evidence authority

```text
EvidenceBundleSchema
=
sole canonical Evidence authority
```

合法方向：

```text
EvidenceBundle
→ typed Application carrier
→ deterministic projection / Gate / citation mapping / hash / summary
```

禁止方向：

```text
projection / dict / RAGContext / SharedState compatibility payload
→ canonical Bundle reconstruction
```

唯一合法 compatibility exception：

```text
legacy ToolResult / legacy payload
→ LegacyEvidenceAdapter
→ canonical EvidenceBundleSchema
→ same typed production path
```

## 2.2 Full typed Bundle whitelist

正常 production full typed Bundle 只允许：

```text
1. document_evidence_bundle
2. active_evidence_bundle
3. SectionEvidenceBundleSchema.evidence_bundle
4. transient Research invocation typed evidence_bundle
```

禁止重新引入：

```text
RAGContext.extra["evidence_contract"] full Bundle
normalized["evidence"] full Bundle
ContextBundle.evidence
Application duplicate Evidence authority
SchemeWriterOutput full SectionEvidence/Bundle fan-out
contexts.rag_tool_output full Bundle fan-out
terminal AgentResult full Bundle fan-out
capture / observer / history full Bundle fan-out
citation pre-accept document registry mutation
```

## 2.3 Citation boundary

冻结三层语义：

```text
citation_ref
= stable canonical source identity

citation_id
= Bundle-local rendering label

document citation label
= Application-owned document rendering projection
```

`DocumentCitationRegistry` 只负责：

```text
citation_ref → document label
```

不得修改或重建 canonical Bundle。

## 2.4 Checkpoint boundary

当前：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION = 2
SECTION_EXECUTION_GRAPH_STATE_VERSION = 2
```

冻结：

```text
v1 checkpoint → explicit unsupported-version rejection
v2 checkpoint → supported
```

不承担 pre-4C 开发期 checkpoint backward compatibility。

## 2.5 Compatibility capability != pre-4C dict shape

Phase 4D 的原始要求：

```text
compatibility outputs 仍能正常生成
```

必须解释为：

> **当前真实 production/runtime consumer 需要的能力继续可用。**

不得解释为：

> **Phase 4C 之前完整 SectionEvidence / EvidenceBundle dict shape 必须原样保留。**

因此不能为了旧 test / script 重新把以下内容塞回 `SchemeWriterOutput.section_evidence[*]`：

```text
retrieval_scope
query
citations
full metadata
full EvidenceBundle
```

---

# 3. Independent Audit Corrections Incorporated

独立审计曾判定：

```text
PHASE 4D VERIFICATION PLAN NOT YET READY TO FREEZE
```

原因不是新的 architecture blocker，而是 Plan 证据设计不足。

本次修订已逐项吸收：

| Audit Item | 修订结果 |
|---|---|
| `P4D-PA-001 deterministic stale-test inventory incomplete` | **CLOSED IN PLAN** — 已全仓扫描 `section_evidence`、`scheme_writer_output`、`RAGContext.extra["evidence_contract"]`、`normalized["evidence"]`；确认 7 个 stale test cases / 5 个 test files，并额外识别 2 个 stale acceptance scripts |
| `P4D-PA-002 runtime compatibility consumer contract insufficiently grounded` | **CLOSED IN PLAN** — B-05 改为真实 `StageService → MainlineApplicationService → run_demo/E2E report → manifest/capture` consumer-driven proof |
| `P4D-PA-003 cross-instance proof not fresh enough` | **CLOSED IN PLAN** — T4D-01 要求 fresh `Application/Supervisor/WorkflowEngine/StageService/RAG facade`，不复用 old `StageService` |
| `P4D-PA-N01 CitationRegistry should bind actual checkpoint shape` | **INCORPORATED** — 使用 `SchemeGenerationGraphPayloadSchema.all_citations` + codec/JSON round-trip |
| `P4D-PA-N02 broader red first failure vs latent stale` | **INCORPORATED** — broad red 强制双层分类 |

本轮仍保持：

```text
Architecture direction = unchanged
5 verification gaps     = unchanged
Production refactor     = not planned
```

---

# 4. Original Verification Requirements

Phase 4D 最终验收要求：

```text
1.  top-level checkpoint typed Bundle JSON round-trip
2.  Section checkpoint typed Bundle round-trip
3.  Section checkpoint / resume
4.  durable cross-instance resume
5.  completed work 不错误 replay retrieval
6.  CitationRegistry projection restore / remap
7.  ContextManager prompt context 行为不退化
8.  LegacyEvidenceAdapter 能把旧结果升级为 canonical Bundle
9.  document citation remap 前后 canonical Evidence hash 不变
10. Application canonical reconstruction path = 0
11. projection → canonical Bundle reverse recovery = 0
12. compatibility outputs 仍能正常生成并满足当前真实 consumer
13. Phase 1–3 Gate semantics 不变
14. Phase 4 architecture invariants 有负向测试保护
15. 必要 deterministic cleanup
```

Checkpoint / recovery proof 继续遵守 Phase 4C frozen requirement：

```text
generic codec round-trip
!=
real durable resume proof
```

---

# 5. Evidence Inventory

## 5.1 本次修订完整阅读

1. `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4D_VERIFICATION_PLAN_20260815.md`
2. `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4D_VERIFICATION_PLAN_AUDIT_20260815.md`
3. `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4C_CLOSURE_20260814.md`
4. `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4C_COMPATIBILITY_REMOVAL_SOLUTION_20260814.md`

Audit 文档保留，不删除、不覆盖。

## 5.2 Existing Phase 4 evidence

已存在并继续有效的主要证据：

```text
Phase 4B canonical carrier tests
Phase 4C compatibility-removal tests
Phase 3C Gate ownership tests
Section real SQLite crash/resume test
completed Section recover_or_reuse test
LegacyEvidenceAdapter typed ingress tests
citation hash invariant tests
negative architecture scans/tests
```

## 5.3 Section-level no-replay 已有充分证据

已有两层：

```text
focused:
completed Section recover_or_reuse
→ persisted result direct reuse
→ resume.assert_not_called()

real durable:
SQLite checkpoint
→ crash after committed paragraph
→ new runtime
→ Section resume
→ committed paragraph not duplicated
```

因此 Phase 4D **不重复制造大量 Section no-replay tests**。

新增重点只针对：

```text
document/top-level retrieve_document_evidence
```

## 5.4 本次 stale scan 范围

全仓扫描：

```text
backend/tests/**/*.py
scripts/**/*.py
```

关键模式：

```text
scheme_writer_output
section_evidence
retrieval_scope
query
citations
metadata
RAGContext.extra["evidence_contract"]
normalized["evidence"]
```

`backend/tests` 中包含 `section_evidence` 的文件共 9 个：

```text
test_document_assembler.py
test_mainline_closure_acceptance.py
test_phase2_multi_agent_state_projection.py
test_phase3_section_execution_subgraph.py
test_phase5_real_agent_adapters.py
test_rag_interface_v2_phase4b_canonical_carrier.py
test_rag_interface_v2_phase4c_compatibility_removal.py
test_root_cause_closure.py
test_section_aware_evidence_retrieval.py
```

其中真正依赖 **pre-4C terminal/output shape** 的 test files 为：

```text
5 files
7 test cases
```

其余命中属于：

```text
canonical SectionEvidenceBundleSchema construction
checkpoint state
DocumentAssembler typed input
Phase 4B/4C architecture verification
node-name strings
```

不是 stale output consumer。

## 5.5 本次 focused stale run

实际运行 7 个 identified stale / latent-stale cases：

```text
7 failed
```

其中：

```text
DIRECT STALE FAILURE
- Step14 ContextManager old fallback setup
- LegacyEvidenceAdapter normalized["evidence"] old expectation
- Section-aware retrieval old section_evidence.retrieval_scope expectation

FIRST FAILURE = unrelated strict Gate
LATENT STALE ASSERTION = confirmed by source/contract scan
- 3 x test_mainline_closure_acceptance.py
- 1 x test_phase5_real_agent_adapters.py
```

因此 broad red 不能再只按第一 traceback 整体分类。

---

# 6. Requirement-by-Requirement Evidence Matrix

| # | Requirement | Current Code Fact | Existing Evidence | 4D Action | Production change expected? | Classification |
|---:|---|---|---|---|---|---|
| 1 | top-level typed Bundle JSON round-trip | v2 `SchemeGenerationGraphStateCodec` 持有 `document_evidence_bundle` | 4B + 4C dedicated tests | rerun only | NO | **A** |
| 2 | Section typed Bundle round-trip | v2 Section codec 持有 document/active Bundle | 4B + 4C dedicated tests | rerun only | NO | **A** |
| 3 | Section checkpoint/resume | `resume()` + `recover_or_reuse()` | focused completed/incomplete + real SQLite crash/resume | rerun representative real test | NO | **A** |
| 4 | durable cross-instance resume | `MainlineApplicationService._build_existing_runtime()` 会重新 `SupervisorFactory.build()` | 旧 test 只 fresh Engine，复用 old StageService；证据不够强 | **T4D-01 fresh runtime** | normally NO | **B-01** |
| 5 | completed retrieval no replay | Section 已证明；top-level resume cursor 已实现 | old top-level no-replay assertions 被 Gate status assertion 截断 | **T4D-02 document retrieval fail-on-call** | normally NO | **B-02** |
| 6 | CitationRegistry restore/remap | `restore()` + stable `citation_ref` dedup + append numbering | stable-id test + rejected-candidate no-label-gap test | **T4D-03 persisted catalog chain** | normally NO | **B-03** |
| 7 | ContextManager prompt parity | explicit `evidence_bundle`; no reverse fallback | typed-path test + negative fallback test；旧综合 Step14 stale | **T4D-04 positive + negative** | test cleanup only | **B-04 + D1** |
| 8 | Legacy adapter upgrade | sole legal legacy ingress | 4C dedicated upgrade tests | rerun + stale test cleanup | test cleanup only | **A + D2** |
| 9 | canonical hash remap invariant | registry projection-only | 4B/4C dedicated tests | rerun | NO | **A** |
| 10 | Application canonical reconstruction = 0 | illegal production model_validate removed | 4C negative architecture test | rerun static lock | NO | **A** |
| 11 | projection → canonical reverse recovery = 0 | only LegacyEvidenceAdapter legal ingress | 4C negative architecture test | rerun | NO | **A** |
| 12 | compatibility output current consumer works | lightweight summaries + section.extra projection exist | no full-Bundle proof exists；real consumer end-to-end proof insufficient | **T4D-05 real-consumer chain** | normally NO | **B-05** |
| 13 | Phase 1–3 Gate semantics | production writer governance evaluates Bundle assessment | Phase 3C production-wiring tests | rerun | NO | **A** |
| 14 | Phase 4 negative architecture protection | dedicated 4C tests lock retired paths | 4C 23-test suite | rerun | NO | **A** |
| 15 | deterministic cleanup | 7 stale test cases + 2 stale acceptance scripts identified | scan + focused run | cleanup only in implementation | NO production change expected | **D** |

---

# 7. Already Verified Items

以下不需要新 architecture / implementation，只需 final regression rerun：

```text
A-01 top-level typed Bundle JSON round-trip
A-02 Section typed Bundle round-trip
A-03 Section durable resume / recover_or_reuse
A-04 LegacyEvidenceAdapter canonical upgrade
A-05 citation remap canonical hash invariant
A-06 Application canonical reconstruction = 0
A-07 projection → canonical reverse recovery = 0 except legacy ingress
A-08 RAGContext full Bundle retirement
A-09 normalized["evidence"] retirement
A-10 ContextBundle.evidence retirement
A-11 Application duplicate Evidence state retirement
A-12 Phase 1–3 Application Gate production authority
A-13 Phase 4 architecture negative protections
A-14 citation candidate accepted-only commit semantics
```

特别说明：

```text
Section-level no-replay = already sufficiently evidenced
Document/top-level retrieval no-replay = still B-02
```

不得把二者混为一个未完成项。

---

# 8. Verification Gaps — Revised Exact Specifications

# B-01 Top-level durable cross-instance resume

## 8.1 Goal

证明：

```text
first runtime dies/closes
+
durable SQLite checkpoint remains
+
fresh second runtime reopens same run
→ exact durable resume works
```

而不是只证明：

```text
new EnterpriseDocumentWorkflowEngine
+
old StageService
```

## 8.2 Required instance boundary

### First runtime instance

必须是完整 runtime：

```text
MainlineApplicationService #1
Supervisor / SupervisorFactory build #1
EnterpriseDocumentWorkflowEngine #1
SchemeGenerationStageService #1
RAG/model facades #1
SQLite checkpoint resource #1
```

执行：

```text
run
→ document retrieval completed
→ later human-review pause
→ durable checkpoint persisted
→ first supervisor/checkpoint/runtime lifecycle closed
```

暂停点必须在：

```text
retrieve_document_evidence completed
```

之后。

暂停前记录：

```text
document_evidence_bundle hash
retrieval_trace_id
completed business-node sequence
checkpoint DB path / run identity
RAG invocation count
```

### Second runtime instance

优先且当前代码结构上可实现：

```text
MainlineApplicationService #2
new SupervisorFactory / build #2
new Supervisor
new EnterpriseDocumentWorkflowEngine
new SchemeGenerationStageService
new EvidenceService / RAG facade
new model facade
reopened same SQLite DB
```

当前 `MainlineApplicationService._build_existing_runtime()` 会重新执行：

```text
SupervisorFactory.build(...)
```

因此 Plan 不接受“为了省事复用 old StageService”的实现作为最强 4D closure proof。

## 8.3 Freshness assertions

测试必须显式证明对象 boundary，而不是只靠变量名：

```text
first supervisor is not second supervisor
first engine is not second engine
first stage_service is not second stage_service
first evidence/RAG facade is not second evidence/RAG facade
checkpoint DB identity/path is same durable store
```

## 8.4 If full freshness unexpectedly cannot be achieved

若 implementation 时遇到真实 factory constraint，测试报告必须逐项列出：

```text
fresh objects
reused objects
why reuse is structurally unavoidable
how replay risk is still isolated
strength limitation vs full fresh runtime
```

不能模糊称为 `cross-instance`。

但基于当前 `_build_existing_runtime()` 事实：

```text
full fresh Application/Supervisor/StageService path appears feasible
```

因此默认不接受降级证据。

## 8.5 Gate orthogonality

本测试禁止要求：

```text
final Document Hard Gate must PASS
```

允许：

```text
resume invariants PASS
+
final status later becomes FAILED because existing strict Gate
```

只要失败发生在 resume/no-replay assertions 已完成之后，并有清晰归因。

分类：

```text
B. IMPLEMENTED BUT VERIFICATION GAP
```

---

# B-02 Completed document retrieval no replay

B-02 可以与 B-01 使用同一个真实 scenario，但必须有**独立明确 assertions**。

## 8.6 Fail-on-call guard

第二 runtime resume 前必须对 document retrieval 建立强 guard，例如：

```text
SchemeGenerationStageService.retrieve_document_evidence
→ fail-on-call during resumed execution
```

并可叠加：

```text
fresh SchemeEvidenceService/RAG facade document-scope invocation counter
```

如果 resume 错误 replay：

```text
test must fail immediately
```

不能只在测试最后比较一个不可靠的 aggregate count。

## 8.7 Required assertions

至少：

```text
1. fresh second runtime resumed same durable run
2. retrieve_document_evidence replay = 0
3. RAG document retrieval invocation count does not grow during resume
4. completed business nodes do not duplicate
5. document_evidence_bundle hash unchanged
6. retrieval_trace_id unchanged
7. resume metadata/cursor is correct
8. resumed=true / resume_count correct
9. final Hard Gate success is not a prerequisite
```

其中 2/3 是 B-02 核心，5/6 防止“没调用同名 node、却重建了 canonical document Evidence”的假阳性。

---

# B-03 CitationRegistry checkpoint restore continuity

## 8.8 Must use actual persisted top-level shape

不得只做：

```text
DocumentCitationRegistry.from_citations()
→ register()
```

必须绑定真实 checkpoint-safe carrier：

```text
SchemeGenerationGraphPayloadSchema.all_citations
→ SchemeGenerationGraphStateCodec / model_dump(mode="json", round_trip=True)
→ JSON serialization
→ restore payload
→ restored_payload.all_citations
→ DocumentCitationRegistry.from_citations(...)
```

## 8.9 Initial persisted catalog

构造连续合法 catalog：

```text
C1 = source A / citation_ref A
C2 = source B / citation_ref B
C3 = source C / citation_ref C
```

不使用 synthetic sparse labels 扩大 architecture requirement。

## 8.10 Restore continuity assertions

恢复后：

```text
same citation_ref A
+ different local citation_id
→ document label still C1
→ catalog size unchanged
→ citation_ref unchanged

same citation_ref B
→ still C2
→ catalog size unchanged

new citation_ref D
→ C4
→ continuous next label

register D again
→ still C4
→ catalog size unchanged
```

## 8.11 Remap assertion

如测试走 `remap_bundle()`：

```text
bundle-local marker
→ restored document label
```

必须正确。

并再次证明：

```text
canonical EvidenceBundle unchanged
canonical hash unchanged
```

本测试只验证 Application citation projection persistence，不赋予 registry canonical authority。

---

# B-04 ContextManager typed prompt semantic parity

## 8.12 Legal baseline

旧合法语义 baseline **不是**：

```text
RAGContext.extra["evidence_contract"]
```

新的合法输入必须是：

```text
RAGContext = projection only
+
evidence_bundle = explicit typed canonical Bundle
```

## 8.13 Positive semantic parity

迁移旧 Step14 综合语义 case，但保留业务 assertions：

```text
selected Evidence E1 exists
E1.citation_ids == [C1]
rendered prompt/context contains [C1]
previous_sections exists
lineage.index_version preserved
lineage.embedding_model preserved
section/task/project context preserved
context budget still deterministic/bounded
required citation catalog behavior preserved
```

还应明确：

```text
selected Evidence comes from typed Bundle
not from rag_context.extra
```

## 8.14 Negative illegal-fallback protection

同时必须保留/重跑：

```text
only inject fake full evidence_contract into RAGContext.extra
+
do not pass typed evidence_bundle
→ ContextManager must NOT reverse recover canonical Bundle
```

正向 + 负向同时存在，才能证明：

```text
semantic parity preserved
without restoring illegal recovery
```

---

# B-05 SchemeWriterOutput / rag_tool_output real runtime compatibility

## 8.15 Consumer inventory — grounded in current code

### Consumer A — StageService final projection producer

当前 `StageService` 生成 `SchemeWriterOutput.section_evidence` lightweight summaries：

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

并生成：

```text
contexts.rag_tool_output
→ document lightweight summary
→ section lightweight summaries
→ lightweight rag_tool_outputs history
```

不得把这一步重新改成 full Bundle / full SectionEvidence dump。

### Consumer B — MainlineApplicationService

当前 `MainlineApplicationService` 从最终 SchemeWriter sub-result 提取：

```text
generated_draft
scheme_writer_output
rejected_artifact
deliverable_final_result
```

它不要求 pre-4C SectionEvidence full shape。

### Consumer C — `scripts/run_demo.py::_section_report_rows()`

这是明确真实 CLI/E2E report consumer。

它读取：

`SchemeWriterOutput.section_evidence`：

```text
section_title
recovery_count
tool_call_ids
```

`generated section`：

```text
section_id
section_title
status
citation_bindings
citation_ids
section.extra["evidence_scope"]
section.extra["evidence_query"]
```

其中 scope/query 当前合法主来源已经是：

```text
section.extra["evidence_scope"]
section.extra["evidence_query"]
```

`bundle.get("retrieval_scope/query")` 只是兼容 fallback，不是要求 output 继续生成这些字段的理由。

### Consumer D — `persist_end_to_end_artifacts()` / E2E report

`persist_end_to_end_artifacts()` 调用 E2E report builder；report 的 section rows 来自：

```text
_section_report_rows(summary)
```

并消费：

```text
section statuses
citation counts
scope/query
recovery counts
trace event counts
Hard Gate/report status
```

这些能力可以由当前 lightweight output + generated section projection 完成。

### Consumer E — Experiment manifest

Experiment manifest 主要消费：

```text
E2E report metrics
runtime_preflight
artifact paths/hashes
rag_call_count
index/dataset metadata
```

没有证据要求 full SectionEvidence / full EvidenceBundle output。

### Consumer F — Capture

`SchemeCaptureService` 当前读取：

```text
SchemeWriterOutput.rag_context
retrieved_chunks
citations
generated draft sections
section.extra["evidence_tool_call_ids"]
rag_output lightweight trace field if present
```

不需要 canonical Bundle fan-out。

### Consumer G — SharedState `contexts.rag_tool_output`

当前是 compatibility state projection：

```text
document summary
section summaries
retrieval histories
```

Phase 4D 要验证：

```text
state write/read path remains valid
payload is lightweight
no reconstructable full Bundle
```

而不是恢复旧 `evidence` contract。

## 8.16 Stale runtime/acceptance tooling discovered

除 tests 外，扫描发现两个历史 acceptance scripts 仍把旧 terminal shape 当 source：

```text
scripts/run_mainline_closure_acceptance.py
scripts/run_section_aware_retrieval_acceptance.py
```

旧依赖包括：

```text
section_evidence[*].retrieval_scope
section_evidence[*].query
```

合法 replacement 已存在：

```text
generated section.extra["evidence_scope"]
generated section.extra["evidence_query"]
trace / context package lineage
```

因此当前分类：

```text
D3. stale consumer path
not C. implementation gap
```

## 8.17 B-05 runtime proof

T4D-05 必须验证真实链：

```text
StageService final projection
→ SchemeWriterOutput
→ SharedState / MainlineApplicationService summary
→ run_demo._section_report_rows()
→ E2E report
→ experiment manifest build
```

至少证明 report row 可以继续得到：

```text
section_id
section_title
status
citation_count
scope
query
recovery_count
tool_call_ids
```

并证明：

```text
scope/query are satisfied from current legal projection
full EvidenceBundle not required
full SectionEvidence output not required
```

## 8.18 Potential implementation gap escalation rule

如果 T4D-05 实际证明：

```text
real production/runtime consumer needs field X
+
no current legal projection contains X
```

则：

```text
classify = C. REAL IMPLEMENTATION GAP candidate
stop output-schema expansion
report exact consumer + missing capability + evidence
```

不得自行把 retired fields 全部塞回 output。

当前扫描没有发现这种 C 证据。

---

# 9. Deterministic Cleanup Inventory — Complete Test Scan

本轮不再写“只有两条 stale tests”。

结论：

```text
5 test files
7 confirmed stale test cases
```

其中“confirmed”包括：

```text
directly reproduced stale failure
or
first failure blocked by unrelated Gate, but later assertion is statically incompatible with frozen Phase 4C output contract
```

## 9.1 Classification rules

```text
D1. Test setup stale
D2. Test expected output shape stale
D3. Test consumer path stale
D4. Potential real runtime consumer regression
```

只有 D4 才可能升级为 C candidate。

## 9.2 Exact stale-test table

| # | Test file | Test case | Old assumption | Why stale | Current legal source | Required cleanup | Production change required? | Class |
|---:|---|---|---|---|---|---|---|---|
| D-01 | `test_step_14_context_manager.py` | `test_section_policy_preserves_evidence_citations_history_and_lineage` | full Bundle 写入 `RAGContext.extra["evidence_contract"]`，不传 typed Bundle | 4C 已禁止 ContextManager reverse recovery | explicit `evidence_bundle=contract` + projection-only `rag_context` | 改 setup；保留 E1/citation/history/lineage/budget assertions | **NO** | **D1** |
| D-02 | `test_scheme_writer_secondary_service_boundaries.py` | `test_legacy_evidence_adapter_upgrades_chunks_into_canonical_contract` | `normalized["evidence"]` 仍含 full Bundle | 4C 已删除 full normalized Evidence copy | `LegacyEvidenceAdapter.upgrade()` typed Bundle；`extract()` lightweight normalized projection | 断言 canonical metadata 在 typed Bundle；负向断言 normalized 无 full evidence | **NO** | **D2** |
| D-03 | `test_section_aware_evidence_retrieval.py` | `test_fake_mainline_can_explicitly_enable_section_aware_retrieval` | `scheme_writer_output.section_evidence[*]["retrieval_scope"]` | terminal SectionEvidence full projection 已退休；已实际复现 `KeyError: retrieval_scope` | generated section `extra["evidence_scope"]`; `tool_call_ids` 仍可由 lightweight summary/trace 验证 | 迁 consumer assertion 到 section.extra / trace | **NO** | **D3** |
| D-04 | `test_mainline_closure_acceptance.py` | `test_corrective_retrieval_branch_runs_and_recovers` | `section_evidence[*].retrieval_scope/query` | 旧 output shape retired；当前 first failure 先被 strict Hard Gate 截断 | section.extra `evidence_scope/evidence_query`; summary `recovery_count/tool_call_ids`; trace events | 保留 corrective semantic，迁合法 projection；同时记录 Gate first failure | **NO** | **D3** |
| D-05 | `test_mainline_closure_acceptance.py` | `test_document_citation_registry_resolves_local_id_collisions` | 每个 `section_evidence[*]["citations"]` 仍存在 | section summary 不再复制 canonical citation list | `scheme_writer_output.citations` document catalog + generated section `citation_ids/citation_bindings` | 验证 document label collision 与 section binding，不恢复 section full citations | **NO** | **D3** |
| D-06 | `test_mainline_closure_acceptance.py` | `test_self_rag_need_retrieve_more_runs_bounded_retrieval_and_recheck` | `section_evidence[*].metadata.self_rag_retrieval_rounds` + `retrieval_scope` | workflow diagnostics 不再 fan-out 到 terminal summary；current first failure 为 strict Gate | section checkpoint/SectionEvidence workflow metadata、trace/generation-check events、section.extra `evidence_scope/workflow_budget` | 从 workflow/trace legal source 验证 self-RAG round，不扩 terminal output | **NO** | **D3** |
| D-07 | `test_phase5_real_agent_adapters.py` | `test_research_agent_tool_gateway_owns_section_retrieval` | 通过 `section_evidence[*].retrieval_scope == section` 推导 required sections | terminal summary 已无 retrieval_scope；当前 first failure 为 strict Gate | test 已记录 `evidence_calls/gateway_calls`; 可结合 generated section.extra / plan required sections | 直接验证 gateway/evidence call scope，不依赖 retired output | **NO** | **D3** |

## 9.3 No D4 found

当前 7 个 test cases 均存在合法 replacement。

因此：

```text
D4. Potential real runtime consumer regression = NONE FOUND
C. REAL IMPLEMENTATION GAP candidate from stale scan = NONE
```

## 9.4 Additional stale acceptance scripts

这两项不计入“7 stale test cases”，但 4D implementation cleanup inventory 必须记录：

| File | Old assumption | Current legal source | Classification | Production architecture change? |
|---|---|---|---|---|
| `scripts/run_mainline_closure_acceptance.py` | `section_evidence.retrieval_scope` | generated section.extra + trace | **D3 stale acceptance consumer** | NO |
| `scripts/run_section_aware_retrieval_acceptance.py` | `section_evidence.retrieval_scope/query` | section.extra + context package lineage + trace | **D3 stale acceptance consumer** | NO |

## 9.5 `run_demo.py` is not currently classified stale

`run_demo._section_report_rows()` already uses：

```text
section.extra["evidence_scope"]
or legacy bundle fallback

section.extra["evidence_query"]
or legacy bundle fallback
```

正常 current path 有合法 primary source。

Phase 4D 不要求为了“清理得漂亮”修改该 fallback；B-05 只要求证明 current legal path 真正工作。

---

# 10. Broader Red Classification — Two-Layer Rule

以后 broad test 失败不能只记录一个 root cause。

每个 relevant red 必须记录：

```text
1. FIRST FAILURE
2. LATENT STALE ASSERTIONS
```

最终表至少包含：

```text
Test
First observed failure
First failure Phase classification
Remaining assertions audited? YES/NO
Latent Phase 4 stale assumption? YES/NO
Latent stale classification
Blocks PHASE 4 CLOSED? YES/NO + reason
```

## 10.1 Known examples

### `test_mainline_closure_acceptance.py` three cases

当前：

```text
FIRST FAILURE
= strict Claim Audit / Document Hard Gate terminal status
= E. OUT OF PHASE 4
```

但：

```text
LATENT STALE ASSERTION
= section_evidence retrieval_scope/query/citations/metadata
= D. DETERMINISTIC CLEANUP
```

因此不得写成：

```text
whole test = E, ignore remainder
```

### `test_phase5_real_agent_adapters.py::test_research_agent_tool_gateway_owns_section_retrieval`

同理：

```text
FIRST FAILURE = strict Gate status expectation (E)
LATENT STALE = section_evidence.retrieval_scope (D3)
```

### old top-level durable resume E2E

```text
FIRST FAILURE = final strict Hard Gate status assertion
LATENT PHASE 4 VERIFICATION = no-replay assertions never executed
```

这里不是 stale output，而是：

```text
B-01/B-02 evidence blocked by assertion order
```

因此必须由 dedicated 4D test 解耦。

---

# 11. Real Implementation Gaps

当前基于：

```text
Phase 4D Plan + independent audit
Phase 4C Solution/Closure
production code consumer scan
all section_evidence test scan
runtime script consumer scan
focused 7-case pytest run
```

仍然没有证据证明：

```text
C. REAL IMPLEMENTATION GAP
```

最终当前结论：

```text
C. REAL IMPLEMENTATION GAP = NONE PROVEN
```

特别注意：

```text
old consumer/test asks for retired field
!=
production implementation gap
```

只有“真实 consumer 无合法 replacement”才能升级 C candidate。

---

# 12. Out-of-Scope Findings

以下继续标记：

```text
E. OUT OF PHASE 4
```

不得在 4D 修：

```text
accepted_evidence_ids Citation ID / Chunk ID namespace mix
citation_id / citation_ref namespace redesign
Phase 5 ToolGateway / ID Namespace Closure
Phase 6 Application → RAG internal import cleanup
Phase 7 facade
Multi-Agent V2
Evaluation / RAGAS
RAG V2
Gate policy redesign
Claim Audit redesign
Document Hard Gate strictness
Verifier policy / fake-model behavior
regex cleanup
模型配置
部署
数据库 / new storage
```

Stale RAG Gate fixture 同样不允许恢复：

```text
RAG extra.gate_decision
generation_allowed
```

---

# 13. Exact Phase 4D Dedicated Test Plan

建议新增：

```text
backend/tests/test_rag_interface_v2_phase4d_verification.py
```

不要机械追求测试数量；同一真实 scenario 可以关闭多个 requirement，但每个 requirement 必须有独立 assertion。

## T4D-01 — Fresh-runtime top-level durable resume

关闭：

```text
B-01
```

Scenario：

```text
Runtime #1
→ run to human-review pause after document retrieval
→ persist SQLite checkpoint
→ record Bundle hash/trace/node sequence
→ close Runtime #1

Runtime #2
→ new MainlineApplicationService
→ new SupervisorFactory build
→ new Supervisor/Engine/StageService/RAG facade
→ reopen same run DB
→ resume
```

Assertions：

```text
fresh object identities
same durable DB identity
resumed metadata/cursor correct
completed node sequence unique
Bundle hash preserved
retrieval_trace_id preserved
```

Final Hard Gate may fail after these assertions without invalidating B-01.

## T4D-02 — Completed document retrieval no replay

可与 T4D-01 同 scenario，但独立关闭：

```text
B-02
```

Required guards：

```text
retrieve_document_evidence = fail-on-call during resume
fresh RAG facade document retrieval counter = no growth / zero resumed call
```

Assertions：

```text
document retrieval replay = 0
RAG document invocation replay = 0
```

不要再用 final terminal success 作为这些 assertions 的前置条件。

## T4D-03 — CitationRegistry persisted catalog restore / dedup / next-label continuity

关闭：

```text
B-03
```

链路：

```text
SchemeGenerationGraphPayloadSchema.all_citations
→ top-level state codec JSON-safe dump
→ JSON serialization
→ restore_payload
→ restored all_citations
→ DocumentCitationRegistry.from_citations
```

Initial：

```text
A→C1
B→C2
C→C3
```

Assert：

```text
A with different local ID → C1
B again → C2
D new → C4
D again → C4
catalog sizes stable on duplicates
citation_ref unchanged
local marker → restored document label correct
canonical Bundle/hash unchanged
```

## T4D-04 — ContextManager typed prompt semantic parity

关闭：

```text
B-04
```

Positive：

```text
rag_context = projection only
evidence_bundle = explicit typed Bundle
```

保留：

```text
E1 selected
E1 citation_ids=[C1]
rendered [C1]
previous_sections
lineage index_version
lineage embedding_model
section/task/project context
budget behavior
```

Negative：

```text
fake full evidence_contract only in RAGContext.extra
without typed Bundle
→ no canonical reverse recovery
```

## T4D-05 — Real lightweight output consumer compatibility

关闭：

```text
B-05
```

真实链：

```text
StageService final projection
→ SchemeWriterOutput
→ SharedState/MainlineApplicationService summary
→ run_demo._section_report_rows
→ persist_end_to_end_artifacts E2E report
→ ExperimentRunManifest build
```

验证 report consumer 能取得：

```text
section_id/title/status
citation_count
scope/query
recovery_count
tool_call_ids
```

来源必须符合 current legal contract：

```text
scope/query ← generated section.extra
recovery/tool ids ← lightweight section_evidence summary
citation data ← generated section binding / document citation projection
```

另验证：

```text
contexts.rag_tool_output can be written/read as lightweight projection
full EvidenceBundle absent
full SectionEvidence dump absent
```

如果这里出现真实 consumer 缺字段且无替代来源：

```text
STOP
→ report C candidate
→ do not expand schema automatically
```

## T4D-06 — Legacy adapter regression / deterministic cleanup verification

覆盖：

```text
LegacyEvidenceAdapter.upgrade legacy → typed Bundle
compatibility_upgrade metadata preserved in typed Bundle
extract projections valid
normalized["evidence"] absent
RAGContext.extra full evidence_contract absent
```

并重跑 cleanup 后的：

```text
Step14 ContextManager case
secondary LegacyAdapter case
section-aware/mainline/agent-adapter stale-consumer cases
```

验证 test cleanup 没有通过恢复 retired paths 实现。

## T4D-07 — Phase 3 Gate authority rerun

直接重跑：

```text
backend/tests/test_rag_interface_v2_phase3c_gate_ownership.py
```

特别保留：

```text
production writer gate
← EvidenceGatePolicy.evaluate(bundle.assessment)
```

不得修改 Gate policy 迎合 broad red。

## T4D-08 — Phase 4B / 4C architecture negative regression

重跑：

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
backend/tests/test_rag_interface_v2_phase4c_compatibility_removal.py
```

必须继续锁死：

```text
RAGContext.extra full Bundle = 0
normalized["evidence"] = 0
ContextBundle.evidence = 0
projection → canonical reconstruction = 0
full output Bundle fan-out = 0
citation pre-accept commit = 0
```

---

# 14. Exact Static Scan Plan

Phase 4D closure 必须重新扫描当前 production，而不是只引用 4C Closure。

范围：

```text
backend/apps/**
backend/context_manager/**
backend/agent/**
backend/schemas/**
backend/observability/**
```

## S4D-01 Canonical reconstruction

搜索：

```text
EvidenceBundleSchema.model_validate
RAGEvidenceContractSchema.model_validate
```

目标：

```text
illegal projection/dict reconstruction = 0
LegacyEvidenceAdapter ingress = only legal compatibility hit
checkpoint codec typed restore = separately classified legal
```

## S4D-02 RAGContext full Bundle

搜索：

```text
RAGContext.extra
"evidence_contract"
model_dump
```

目标：

```text
full Bundle writer = 0
```

`evidence_contract_sha256` 作为 hash/ref 合法，不得按字符串误杀。

## S4D-03 normalized full Evidence

搜索：

```text
normalized["evidence"]
normalized.get("evidence")
"evidence": bundle.model_dump(...)
```

正常 production：

```text
0
```

Legacy adapter 可以读 legacy input 的 `payload["evidence"]`，但不得 re-export。

## S4D-04 ContextBundle retirement

搜索：

```text
ContextBundle.evidence
context_bundle.evidence
set_evidence_context
"evidence_context"
```

目标：

```text
schema/write/read alias/observer/graph contract = 0
```

## S4D-05 Full Bundle fan-out whitelist

扫描：

```text
section_evidence
model_dump
SchemeWriterOutput
rag_tool_output
AgentResult
capture
observer
all_rag_outputs
metadata
history
```

full carrier 只允许：

```text
document_evidence_bundle
active_evidence_bundle
SectionEvidenceBundleSchema.evidence_bundle
transient Research typed Bundle
```

## S4D-06 Citation acceptance boundary

验证：

```text
candidate citation commit before Application acceptance = 0
rejected diagnostic citation final commit = 0
```

## S4D-07 Checkpoint version

确认：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION = 2
SECTION_EXECUTION_GRAPH_STATE_VERSION = 2
```

并搜索 implicit v1 fallback：

```text
values.get(..., 1)
state_version", 1
```

目标：0。

## S4D-08 Phase 3 Gate authority

RAG package 继续禁止：

```text
apps.enterprise_document reverse import
GateDecisionSchema
DeliveryMode
def _gate(
generation_allowed
gate_decision
human_review_required
```

## S4D-09 Stale test/output-shape rescan

implementation cleanup 后重新全仓扫描：

```text
scheme_writer_output["section_evidence"]
section_evidence[*].retrieval_scope
section_evidence[*].query
section_evidence[*].citations
section_evidence[*].metadata
RAGContext.extra["evidence_contract"]
normalized["evidence"]
```

剩余命中必须逐条分类：

```text
legal canonical/derived test
legal lightweight consumer
legacy input boundary
stale consumer
forbidden regression
```

不能只给 grep 数字。

---

# 15. Planned Acceptance Test Set

## Layer 1 — Phase 4D dedicated

```text
backend/tests/test_rag_interface_v2_phase4d_verification.py
```

要求：

```text
T4D-01 ... T4D-08 relevant dedicated assertions PASS
0 Phase 4D failures
```

## Layer 2 — deterministic stale cleanup

至少覆盖 7 confirmed cases：

```text
test_step_14_context_manager.py

test_scheme_writer_secondary_service_boundaries.py

test_section_aware_evidence_retrieval.py

test_mainline_closure_acceptance.py

test_phase5_real_agent_adapters.py
```

注意：

```text
文件内 unrelated Gate/Verifier failures
!= stale cleanup failure
```

必须按 test case + first/latent 双层报告。

两个 stale acceptance scripts 也应纳入 focused compatibility verification：

```text
scripts/run_mainline_closure_acceptance.py
scripts/run_section_aware_retrieval_acceptance.py
```

但不得为了让它们恢复历史 output shape 而改 production schema。

## Layer 3 — Phase 4 frozen regression

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
backend/tests/test_rag_interface_v2_phase4c_compatibility_removal.py
```

要求：0 failed。

## Layer 4 — Phase 3 Gate authority

```text
backend/tests/test_rag_interface_v2_phase3c_gate_ownership.py
```

要求：0 failed。

## Layer 5 — real persistence representative tests

至少：

```text
T4D-01/T4D-02 fresh top-level resume scenario
existing Section SQLite crash/resume no-duplication test
existing completed Section recover_or_reuse focused test
T4D-03 CitationRegistry persisted catalog restore
```

不重复铺大量 Section tests。

## Layer 6 — runtime output / capture / report regression

至少：

```text
T4D-05 real consumer chain
run_demo report rows / persist_end_to_end_artifacts
experiment manifest build
capture focused regression
observability/trace compatibility focused regression
```

## Layer 7 — broader regression

至少覆盖：

```text
checkpoint/resume/replan/recover_or_reuse
DocumentAssembler
section execution contracts
citation binding
section-agent mainline
parallel section
```

每个 red 必须报告：

```text
FIRST FAILURE
LATENT STALE ASSERTIONS
Phase 4 impact
closure blocker? yes/no
```

## Layer 8 — hygiene

```text
py_compile / equivalent syntax validation
git diff --check
```

要求 PASS；既有 CRLF/LF warning 单独记录。

---

# 16. Phase 4D Acceptance Criteria

Phase 4D 完成必须满足：

```text
[ ] top-level v2 typed Bundle JSON round-trip remains PASS
[ ] Section v2 typed Bundle round-trip remains PASS
[ ] v1 top-level / Section explicit rejection remains PASS
[ ] existing Section real durable crash/resume remains PASS
[ ] completed Section recover_or_reuse direct reuse remains PASS

[ ] T4D-01 uses genuinely fresh second Application/Supervisor/Engine/StageService runtime
[ ] same SQLite durable run is reopened
[ ] top-level resume metadata/cursor correct

[ ] retrieve_document_evidence fail-on-call guard is not triggered
[ ] document RAG retrieval invocation replay = 0
[ ] completed business nodes not duplicated
[ ] document_evidence_bundle hash unchanged across resume
[ ] document retrieval_trace_id unchanged across resume
[ ] final strict Hard Gate status is orthogonal to resume assertions

[ ] CitationRegistry is restored from actual persisted all_citations shape
[ ] citation_ref → document label stable after restore
[ ] same-source dedup stable
[ ] next new source gets continuous label
[ ] duplicate new source does not grow catalog
[ ] marker remap correct after restore
[ ] canonical Bundle/hash unchanged by document citation projection

[ ] ContextManager positive typed semantic parity PASS
[ ] selected E1 / [C1] / previous_sections / lineage preserved
[ ] context budget behavior preserved
[ ] illegal RAGContext.extra reverse recovery remains forbidden

[ ] LegacyEvidenceAdapter old result → typed Bundle PASS
[ ] normalized["evidence"] not reintroduced
[ ] RAGContext.extra full evidence_contract not reintroduced

[ ] SchemeWriterOutput lightweight summaries generated
[ ] Mainline summary can carry current output
[ ] run_demo report rows obtain scope/query from legal projection
[ ] E2E report builds without full SectionEvidence output
[ ] experiment manifest builds without full Bundle fan-out
[ ] capture/observability remain functional without full Bundle
[ ] contexts.rag_tool_output remains lightweight

[ ] all 7 confirmed stale test cases are migrated to legal sources
[ ] 2 stale acceptance scripts are migrated or explicitly verified against legal sources
[ ] no D4 real-consumer regression found; if found, reported as C candidate before code expansion

[ ] Phase 3C production Gate wiring remains PASS
[ ] Phase 4B canonical carrier regression remains PASS
[ ] Phase 4C compatibility removal regression remains PASS
[ ] architecture negative tests remain PASS

[ ] broader red uses FIRST FAILURE + LATENT STALE ASSERTION classification
[ ] no Phase 5/6/7 scope creep
[ ] no Gate/Claim Audit/Verifier policy change
[ ] py_compile PASS
[ ] git diff --check PASS
```

---

# 17. Phase 4 Closure Criteria

只有以下全部成立，才允许最终写：

```text
PHASE 4 CLOSED
```

## Architecture closure

```text
EvidenceBundleSchema = sole canonical Evidence authority
production projection → canonical reconstruction = 0
full Bundle outside whitelist = 0
Application duplicate Evidence authority = 0
citation pre-accept document registry mutation = 0
```

## Persistence closure

```text
checkpoint version = v2
v1 explicit reject = PASS
top-level typed Bundle round-trip = PASS
Section typed Bundle round-trip = PASS
Section durable resume = PASS
fresh-runtime top-level durable resume = PASS
completed document retrieval replay = 0
completed Section reuse/no duplication = PASS
```

## Projection / compatibility closure

```text
CitationRegistry persisted restore/dedup/numbering continuity = PASS
ContextManager typed prompt semantics = PASS
Legacy adapter typed upgrade = PASS
SchemeWriterOutput real-consumer compatibility = PASS
rag_tool_output lightweight compatibility = PASS
canonical hash remap invariant = PASS
```

## Cleanup closure

```text
7 confirmed stale test cases migrated
2 stale acceptance scripts no longer depend on retired output shape
no retired compatibility path restored
```

## Regression closure

```text
Phase 4B = PASS
Phase 4C = PASS
Phase 3C Gate ownership = PASS
Phase 4 negative architecture tests = PASS
```

## Broader-red closure

Broader red 可以不阻塞 Phase 4 only if：

```text
FIRST FAILURE is out-of-phase
+
remaining assertions audited
+
no uncleaned latent Phase 4 stale assertion remains
+
Phase 4 dedicated acceptance remains green
```

---

# 18. Plan Freeze Criteria

本修订 Plan 只有同时满足以下条件才允许写：

```text
PHASE 4D VERIFICATION PLAN READY TO FREEZE
```

检查结果：

1. **5 个 verification gap 目标保持不变，测试设计已加强。** — PASS
2. **Top-level cross-instance instance boundary 明确。** — PASS
3. **Document retrieval no-replay 有 fail-on-call 强断言。** — PASS
4. **CitationRegistry 绑定 persisted/checkpoint-safe `all_citations` representation。** — PASS
5. **ContextManager typed positive + illegal fallback negative 双向验证。** — PASS
6. **Runtime compatibility 明确真实 consumer，不验证虚构 contract。** — PASS
7. **Stale-test inventory 已全仓扫描，不再只有两条。** — PASS
8. **Broader red 支持 FIRST FAILURE + LATENT STALE ASSERTION 双层分类。** — PASS
9. **未重新引入 retired compatibility paths。** — PASS
10. **未进入 Phase 5。** — PASS
11. **C. REAL IMPLEMENTATION GAP 仍为 NONE PROVEN。** — PASS
12. **USER DECISION REQUIRED 仍为 NONE。** — PASS

因此 Plan 本身已经满足 freeze 条件。

---

# 19. 是否需要用户决策

当前：

```text
USER DECISION REQUIRED = NONE
```

原因：

```text
fresh-runtime verification boundary
fail-on-call no-replay guard
checkpoint catalog restore proof
ContextManager typed parity
stale-test migration
real-consumer compatibility proof
```

都有由当前冻结架构唯一确定的工程方向。

没有出现新的 Evidence authority、citation ownership、checkpoint migration、Gate ownership 或 output architecture 选择。

若后续 T4D-05 真正发现：

```text
real runtime consumer has no legal replacement for required capability
```

则只把它报告为：

```text
C. REAL IMPLEMENTATION GAP candidate
```

先停止并复审，不自行扩 schema。

---

# 20. Final Phase 4D Planning Decision

修订后分类：

```text
A. ALREADY VERIFIED
- top-level typed Bundle JSON round-trip
- Section typed Bundle round-trip
- Section checkpoint/resume/recover_or_reuse
- Section durable crash/resume no duplication
- LegacyEvidenceAdapter typed canonical upgrade
- citation remap canonical hash invariant
- Application canonical reconstruction = 0
- projection → canonical reverse recovery = 0 except legal legacy ingress
- Phase 1–3 production Gate semantics
- Phase 4 architecture negative protections

B. IMPLEMENTED BUT VERIFICATION GAP
- B-01 fresh-runtime top-level durable cross-instance resume
- B-02 completed document retrieval no replay
- B-03 CitationRegistry persisted catalog restore continuity
- B-04 ContextManager typed prompt semantic parity
- B-05 lightweight output real-consumer compatibility

C. REAL IMPLEMENTATION GAP
- NONE PROVEN

D. DETERMINISTIC CLEANUP
- 7 stale test cases / 5 test files
- 2 stale acceptance scripts
- broad Gate reds must be separated from latent stale assertions

E. OUT OF PHASE 4
- Phase 5 ID namespace issues
- Claim Audit / Document Hard Gate policy
- Verifier behavior
- stale RAG Gate fixture
- Phase 6/7
- Multi-Agent V2 / Evaluation / RAG V2 / deployment / storage / model config
```

Plan 修订结论：

```text
PHASE 4D VERIFICATION PLAN READY TO FREEZE
```

当前仍然：

```text
PHASE 4D IMPLEMENTATION NOT STARTED
PRODUCTION CODE NOT MODIFIED
TESTS NOT MODIFIED
PHASE 5 NOT STARTED
```

到此停止。
