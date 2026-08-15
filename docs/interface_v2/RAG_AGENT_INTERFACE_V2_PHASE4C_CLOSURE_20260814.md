# RAG-Agent Interface V2 Phase 4C Closure

日期：2026-08-14

阶段：Interface V2 Phase 4C — Compatibility Removal Implementation

最终状态：

```text
PHASE 4C CLOSED
```

---

# 1. Closure 结论

Phase 4C 已按冻结 Solution 实施完成。

本轮没有重新设计 Phase 4C，没有进入 Phase 5 / Multi-Agent V2 / RAG V2 / Evaluation / Deployment，也没有修改 Gate policy、Claim Audit、strict/shadow、retrieval strategy 或模型配置。

最终生产边界已经收口为：

```text
EvidenceBundleSchema
=
sole canonical Evidence authority
```

正常 production 数据方向：

```text
EvidenceBundle
-> typed consumer
-> Application projection / Gate / citation mapping / hash / summary / diagnostics
```

非法 reverse recovery：

```text
projection / dict / RAGContext / SharedState compatibility state
-> model_validate
-> canonical EvidenceBundle
```

最终 production 命中：

```text
0
```

唯一保留的兼容入口：

```text
LegacyEvidenceAdapter
legacy ToolResult / legacy evidence payload
-> canonical typed EvidenceBundle
```

LegacyEvidenceAdapter 完成升级后立即重新进入正常 typed path，不再向 RAGContext.extra、normalized payload、metadata、terminal output 或 capture 重新导出完整 Bundle。

Phase 4C focused acceptance 全绿；broader regression 剩余红项全部可归因于 Phase 4B closure 已记录的 strict Claim Audit / Document Hard Gate / Verifier expectation drift 或 stale RAG Gate fixture，没有发现新的 Phase 4C blocker。

---

# 2. 本轮实际修改文件

## 2.1 Production / runtime

本窗口直接实施或收口的主要 production 文件：

```text
backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py
backend/apps/enterprise_document/services/scheme_writer/evidence_service.py
backend/context_manager/policies.py
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
backend/apps/enterprise_document/services/scheme_writer/stage_service.py
backend/apps/enterprise_document/schemas/scheme_writer/evidence.py
backend/apps/enterprise_document/schemas/scheme_writer/document_assembly.py
backend/apps/enterprise_document/services/scheme_writer/document_assembler.py
backend/schemas/context.py
backend/agent/runtime/state_access.py
backend/agent/runtime/graph_state_ops.py
backend/agent/runtime/workflow_observer.py
backend/apps/enterprise_document/workflows/scheme_generation.py
backend/apps/enterprise_document/workflows/top_level_business_graph.py
backend/apps/enterprise_document/schemas/scheme_writer/state_projection.py
backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_adapter.py
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/apps/enterprise_document/runtime/agent_rag_v1/mainline_recovery.py
backend/apps/enterprise_document/schemas/scheme_generation_graph.py
backend/apps/enterprise_document/schemas/scheme_writer/output.py
backend/apps/enterprise_document/schemas/scheme_writer/section_execution.py
backend/observability/trace_summary.py
```

注意：工作树打开时已经包含 Phase 4B 累计未提交修改，因此 `git diff --stat` 是 Phase 4B + Phase 4C 累计工作树差异，不能把所有 dirty 文件都归因于 Phase 4C。

## 2.2 Tests

本轮新增：

```text
backend/tests/test_rag_interface_v2_phase4c_compatibility_removal.py
```

本轮为新边界迁移或更新的重点测试：

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
backend/tests/test_rag_interface_v2_phase3c_gate_ownership.py
backend/tests/test_document_assembler.py
backend/tests/test_section_execution_contracts.py
backend/tests/test_decoupled_architecture.py
backend/tests/test_step_12_evidence_contract.py
backend/tests/test_phase4_section_agents_mainline.py
```

---

# 3. Reconstruction path 删除结果

Phase 4C 开始时确认的三个非法 production reverse reconstruction：

```text
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
backend/context_manager/policies.py
```

均已删除。

最终扫描：

```text
RAGEvidenceContractSchema.model_validate = 1
EvidenceBundleSchema.model_validate       = 0
```

唯一 `RAGEvidenceContractSchema.model_validate`：

```text
backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py
```

分类：

```text
legal legacy/test ingress
```

因此：

```text
production projection -> canonical Bundle reconstruction = 0
```

---

# 4. LegacyEvidenceAdapter 最终边界

`LegacyEvidenceAdapter` 现在具有单一明确职责：

```text
legacy payload / ToolResult
-> validate/build canonical EvidenceBundle
```

新增/收口的核心行为：

```text
LegacyEvidenceAdapter.upgrade(...)
-> EvidenceBundleSchema
```

`SchemeEvidenceService.retrieve()` 对 legacy ToolResult 的处理：

```text
legacy result
-> LegacyEvidenceAdapter.upgrade()
-> typed EvidenceBundle
-> normal production typed path
```

升级之后禁止重新写入：

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
normalized["evidence_contract"]
metadata full Bundle
```

专项测试验证 legacy input 仍能成功升级，同时 projection 不重新导出 full Bundle。

---

# 5. RAGContext 清理结果

已删除 production：

```text
RAGContext.extra["evidence_contract"]
```

最终禁止 full-copy write 扫描：

```text
"evidence_contract": <full payload> = 0
normalized["evidence"]             = 0
```

RAGContext 当前只承担 context projection。

允许继续存在的是：

```text
context_text
used_doc_ids
context packing metadata
Application citation rendering metadata
retrieval scope / trace projection metadata
```

这些内容不能反向恢复完整 EvidenceBundle。

---

# 6. ContextBundle.evidence 退休结果

已完整删除：

```text
ContextBundle.evidence
EvidenceContextSchema
SharedStateWriter.set_evidence_context
```

并同步清理：

```text
Graph alias
graph_state_ops
workflow observer
scheme_generation graph write paths
top_level_business_graph contract
production Stage writer
```

最终 production 扫描：

```text
context_bundle.evidence = 0
"context_bundle.evidence" = 0
set_evidence_context = 0
```

因此不存在“Schema 字段删除但 Graph Contract 仍声明”的半迁移状态。

---

# 7. Application duplicate Evidence fields 退休结果

`SchemeGenerationGraphPayloadSchema` 已退出以下 persisted duplicate roles：

```text
document_rag_context
document_retrieved_chunks
document_citations
document_rag_output
document_evidence_assessment
document_evidence_available
document_assessment_status
all_retrieved_chunks
```

文档生命周期 canonical carrier 只保留：

```text
document_evidence_bundle
```

最终 document citation capability 没有删除，而是由 Application-owned：

```text
all_citations
```

承担 document citation catalog / projection。

`all_retrieved_chunks` 业务能力没有删除；DocumentAssembler 在需要时从：

```text
document canonical Bundle
+
final section Evidence Bundles
```

按需聚合并去重 chunks。

最终 production 扫描：

```text
document_evidence_assessment = 0
document_evidence_available  = 0
document_assessment_status   = 0
all_retrieved_chunks          = 0
```

---

# 8. Assessment consumer migration

已删除 persisted duplicate authority：

```text
active_assessment
ResearchState.assessment
document_evidence_assessment
```

最终：

```text
active_assessment = 0
```

Section graph / recovery / coordinator 读取：

```text
active_evidence_bundle.assessment
```

DocumentAssembler 读取：

```text
document_evidence_bundle.assessment
SectionEvidenceBundleSchema.evidence_bundle.assessment
```

`quality_mode_degraded` 语义已专项测试保持：

```text
canonical Bundle.assessment.details["quality_mode_degraded"]
-> DocumentAssembler
-> draft.extra["quality_mode_degraded"]
```

`ResearchState.evidence_gaps` 未误删。

---

# 9. SectionEvidenceBundleSchema 最终职责

最终 persisted business fields：

```text
section_id
section_title
evidence_bundle
tool_call_ids
recovery_count
evidence_contract_sha256
writer_gate_binding
metadata
```

另保留 schema infrastructure：

```text
schema_version = "section_evidence_bundle_v2"
```

以下内容不再作为 persisted fields：

```text
retrieval_scope
query
rag_context
retrieved_chunks
citations
generation_allowed
assessment duplicate copies
```

为了不破坏运行时明确消费，这些值需要时通过单向 deterministic property 从 canonical Bundle / WriterGateBinding 派生，但不会进入 `model_fields` 或 checkpoint serialization。

专项测试同时验证：

```text
model_dump()
```

不会重新出现上述退休字段。

---

# 10. Citation transactional commit 实现

冻结事务边界已经落实：

```text
candidate Bundle
-> candidate-local citation mapping / registry
-> Application acceptance
-> accepted ? commit document registry : discard candidate registry mutation
```

覆盖三条主路径：

```text
direct recovery
ResearchAgent Tool Gateway
parallel section fan-in
```

legacy coordinator 的 self-RAG / corrective recovery 也使用 candidate-local registry，避免形成旁路。

## 10.1 Direct recovery

recovery candidate 成功检索后，不立即覆盖 document registry。

只有 Application 接受 candidate 后才提交：

```text
citation_registry
retrieved chunks projection
active canonical Bundle
```

rejected candidate 只允许留在 lightweight retrieval history。

## 10.2 ResearchAgent Gateway

Gateway candidate 同样先使用 candidate-local registry。

Application 拒绝时：

```text
candidate citation_ref not in final document registry
```

## 10.3 Parallel section fan-in

parallel result 的 local registry 可能同时包含：

```text
accepted final Evidence citations
diagnostic/rejected candidate citations
```

fan-in 现在只按 final accepted canonical Bundle 中存在的 stable citation identity 提交 document registry。

不再注册 diagnostic-only citation。

## 10.4 Citation label gap

专项测试覆盖：

```text
C1 = existing accepted
rejected candidate = no commit
next accepted citation = C2
```

rejected candidate 不占用 label，不产生提前分配或编号空洞。

---

# 11. DocumentCitationRegistry 最终职责

DocumentCitationRegistry 当前只负责 Application：

```text
citation_ref
-> document rendering label
```

以及：

```text
dedup
ordering
mapping
Prompt marker projection
Writer rendering
CitationBinding
final document citation catalog
```

已删除：

```text
canonical Bundle citation rewrite
EvidenceItem.citation_ids rewrite
canonical Bundle.context rewrite
replacement Bundle model_validate
canonical hash recomputation from remapped projection
```

专项测试验证：

```text
hash(canonical Bundle) before remap
==
hash(canonical Bundle) after remap
```

canonical `citation_ref` 也保持不变。

---

# 12. Full Bundle persistence whitelist 最终结果

正式 production full typed Bundle 只允许：

```text
1. document_evidence_bundle
2. active_evidence_bundle
3. SectionEvidenceBundleSchema.evidence_bundle
4. transient Research invocation typed Bundle
```

最终 full-copy write 扫描：

```text
RAGContext.extra full evidence_contract          = 0
normalized["evidence"]                          = 0
ContextBundle.evidence                           = 0
SchemeWriterOutput section full-Bundle dump      = 0
contexts.rag_tool_output full-Bundle dump        = 0
section_evidence.*model_dump full fan-out         = 0
terminal generic result full Bundle fan-out       = 0
capture full Bundle carrier                       = 0
observer full Bundle carrier                      = 0
```

`document_rag_output` 当前剩余 4 个 production 命中，全部位于 transient SectionExecution request / coordinator Application projection 链，内容为 lightweight Application projection，不是 persisted canonical carrier，也不包含 full Bundle。

`all_rag_outputs` 当前剩余 10 个 production 命中，均通过：

```text
SchemeEvidenceService.retrieval_history_projection
```

保存：

```text
scope
query
retrieval_trace_id
canonical hash
status
chunk count
citation count
```

分类：

```text
lightweight diagnostic history
```

不是 full Bundle carrier。

---

# 13. SchemeWriterOutput / terminal output / capture / observability

## 13.1 SchemeWriterOutput

`SchemeWriterOutput.section_evidence` 已改为 lightweight Evidence summaries。

每个 section 最终只输出类似：

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

不再：

```text
SectionEvidenceBundleSchema.model_dump()
-> SchemeWriterOutput.extra
-> enriched_rag_output
-> contexts.rag_tool_output
-> terminal AgentResult
```

## 13.2 contexts.rag_tool_output

现在写入的是：

```text
document lightweight RAG summary
section lightweight Evidence summaries
lightweight retrieval histories
```

不含 canonical Bundle full dump。

## 13.3 Capture

`SchemeCaptureService` 不持有 typed canonical Bundle。

focused capture tests 在没有 full Bundle fan-out 的情况下通过。

## 13.4 Observability

`workflow_observer` 不再读取 `ContextBundle.evidence`。

`trace_summary.py` 对 `payload["evidence"]` 的剩余处理仅为明确标注的：

```text
legacy ToolResult / legacy RAGToolOutput compatibility boundary
```

正常 production canonical path 不依赖该字段。

---

# 14. Checkpoint v2 breaking boundary

版本已正式升级：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION = 2
SECTION_EXECUTION_GRAPH_STATE_VERSION = 2
```

恢复策略：

```text
v1 -> explicit reject
v2 -> supported
```

没有新增 migration framework。

已经删除所有已确认的：

```text
values.get("...state_version", 1)
```

兼容后门。

专项测试覆盖：

```text
top-level v1 explicit rejection
section v1 explicit rejection
missing section version explicit rejection
top-level v2 JSON round-trip
section v2 JSON round-trip
completed section recover_or_reuse direct reuse
incomplete section recover_or_reuse -> resume exact v2 cursor
```

另外 broader recovery tests 中 running invocation recovery 的核心四个 decision 断言：

```text
SUPPLEMENT_EVENT
RESUME_MONITORING
IDEMPOTENT_RETRY
DISCARD_STALE
```

均实际通过；其测试最终失败仅发生在后续 strict Document Hard Gate 对 deliverable 的旧期望。

---

# 15. Phase 4B recovery acceptance regression

冻结语义保持：

```text
current permitted + candidate blocked
-> retain current

current blocked + candidate permitted
-> accept candidate

current permitted + candidate permitted
-> accept candidate
```

没有重新设计 ranking policy。

Phase 4C 新增的是 citation transaction：

```text
candidate accepted
-> citation commit

candidate rejected
-> no citation commit
```

Phase 4B canonical carrier / acceptance focused regression 全部通过。

---

# 16. Static scan 最终分类

production 范围：

```text
backend/apps
backend/context_manager
backend/agent
backend/schemas
backend/observability
```

关键扫描计数：

```text
evidence_contract                         = 74
normalized["evidence"]                   = 0
context_bundle.evidence                   = 0
set_evidence_context                      = 0
"context_bundle.evidence"                = 0
active_assessment                         = 0
document_evidence_assessment              = 0
document_evidence_available               = 0
document_assessment_status                = 0
document_rag_output                       = 4
all_retrieved_chunks                      = 0
all_rag_outputs                           = 10
RAGEvidenceContractSchema.model_validate  = 1
EvidenceBundleSchema.model_validate       = 0
evidence_contract_sha256                  = 59
SectionEvidenceBundleSchema               = 17
section_evidence                          = 46
rag_tool_output                           = 16
```

分类：

### `evidence_contract = 74`

主要是：

```text
hash field name
evidence_contract_sha256
lineage / binding metadata
legacy adapter protocol name
section lifecycle hash
```

禁止的 full payload key write：

```text
0
```

### `RAGEvidenceContractSchema.model_validate = 1`

仅：

```text
LegacyEvidenceAdapter legacy ingress
```

合法。

### `document_rag_output = 4`

仅 transient Application projection，用于 legacy coordinator 的明确 Gate / generation projection 消费。

不 persisted，不 full Bundle。

### `all_rag_outputs = 10`

仅 lightweight retrieval diagnostic history。

### `SectionEvidenceBundleSchema / section_evidence`

命中包括合法 section lifecycle canonical slot、typed consumer、lightweight final summary。

不存在 `section_evidence.model_dump()` 向 generic output 的 full-copy fan-out。

### `rag_tool_output = 16`

为 Application output/capture projection 与 legacy trace consumer；当前生产写入内容已经是 lightweight summary，不包含 reconstructable canonical Bundle。

---

# 17. Phase 4C 专项测试

新增：

```text
backend/tests/test_rag_interface_v2_phase4c_compatibility_removal.py
```

最终：

```text
23 passed
0 failed
```

覆盖 Phase 4C solution 要求的核心 acceptance，包括：

```text
projection -> Bundle reconstruction = 0
ContextManager no reverse fallback
WriterGovernance no normalized fallback
CitationRegistry projection-only
canonical hash invariant
direct rejected citation transaction
Gateway rejected citation transaction
parallel diagnostic citation filtering
rejected candidate no label reservation
normalized no full Bundle
RAGContext no evidence_contract
ContextBundle.evidence complete retirement
Application duplicate state retirement
SectionEvidence exact persisted shape
Bundle-derived context/chunk/citation
all_retrieved_chunks retirement
quality_mode_degraded semantics
output/terminal no full Bundle
capture/observer compatibility
v1 rejection
v2 round-trip
completed recover_or_reuse
incomplete resume
LegacyEvidenceAdapter upgrade
SchemeEvidenceService immediate typed re-entry
Phase 4B recovery acceptance regression
```

---

# 18. Focused regression

最终 focused 集合：

```text
Phase 4C dedicated
Phase 4B canonical carrier
Phase 3C Gate ownership
DocumentAssembler
Section execution contracts
Decoupled architecture
Step 12 Evidence contract
Capture success/failure focused tests
Scheme writer decomposition
Citation binding regression
Removed legacy assessment hook regression
```

结果：

```text
83 passed
0 failed
```

---

# 19. Broader regression 与已知无关红项

最终 broader 集合：

```text
backend/tests/test_phase4_durable_resume_hitl.py
backend/tests/test_phase4_section_agents_mainline.py
backend/tests/test_phase3_section_execution_subgraph.py
backend/tests/test_citation_binding_regression.py
backend/tests/test_phase8_recovery_budget_retirement.py::test_running_invocation_recovery_uses_latest_section_checkpoint
backend/tests/test_phase8_recovery_budget_retirement.py::test_section_crash_resume_reuses_committed_paragraph_without_duplication
```

当前结果：

```text
14 passed
8 failed
```

8 个失败按真实执行链分类如下。

## 19.1 既有 strict Claim Audit / Document Hard Gate

```text
test_phase4_durable_resume_hitl.py::test_new_engine_resumes_without_replaying_completed_nodes
test_phase4_durable_resume_hitl.py::test_application_service_inspects_and_approves_across_new_instance
test_phase3_section_execution_subgraph.py::test_production_path_uses_section_subgraph_not_legacy_coordinator
test_phase8_recovery_budget_retirement.py::test_running_invocation_recovery_uses_latest_section_checkpoint
```

这些测试的 resume / section graph / recovery 核心行为实际执行成功，最终失败发生在已有 strict Claim Audit / Document Hard Gate，测试仍期待 deliverable success。

Phase 4C 没有放松 Claim Audit、Hard Gate 或 strict mode。

## 19.2 Phase 4B 已记录的 section-agent / Verifier expectation drift

```text
test_mainline_invokes_three_projected_section_agents
test_section_checkpoint_persists_typed_agent_payloads
test_verifier_research_more_drives_real_section_route
```

Phase 4B Final Closure 已把这三个测试列为既有 broader red。

本轮运行中：

```text
Research / Writer / Verifier projected calls 正常
section checkpoints 正常
research_more -> retrieve_more 路由正常
第二次 generation / verification 正常发生
```

当前旧断言继续期望：

```text
VerifierAction.ACCEPT
```

实际 existing strict pipeline 返回：

```text
VerifierAction.REJECT
```

这不是 Phase 4C Evidence authority / compatibility / checkpoint / citation transaction failure。

本轮没有修改 Verifier policy 或 fake model behavior。

## 19.3 stale RAG Gate fixture

```text
test_final_writer_gate_blocked_stops_before_writer_and_verifier
```

测试仍通过修改：

```text
evidence.extra["gate_decision"]
evidence.extra["generation_allowed"]
```

制造 RAG Gate。

这与 Phase 3C 已冻结：

```text
Application = Gate authority
RAG = Evidence facts only
```

冲突。

因此没有为了该旧 fixture 恢复 RAG Gate authority。

结论：

```text
broader red != Phase 4C blocker
```

---

# 20. py_compile / git diff --check

最终 `py_compile`：

```text
PY_COMPILE_OK
```

最终 `git diff --check`：

```text
DIFF_CHECK_OK
```

仅存在工作树既有 CRLF -> LF warning，没有 whitespace error。

---

# 21. Git 边界

当前 HEAD 保持：

```text
f43f959360df073b53f5e57e60b06ce535aff379
```

本轮未执行：

```text
git reset
git checkout .
git restore .
git clean
git stash
git add
git commit
git push
```

工作树继续保留 Phase 4B 累计未提交修改、Phase 4C 修改、审计文档和既有未跟踪 `nul`。

没有删除或覆盖不属于本轮的已有工作树修改。

---

# 22. Phase 4C blocker 判定

未发现新的 Phase 4C blocker。

冻结 Acceptance Criteria 已满足：

```text
illegal production reconstruction                  = 0
RAGContext full evidence_contract carrier          = 0
ContextBundle.evidence authority                   = retired
Application persisted duplicate Evidence authority = retired
active_assessment                                  = retired
ResearchState.assessment                           = retired
SectionEvidence persisted shape                    = closed
Citation candidate pre-accept mutation             = closed
rejected candidate label reservation               = closed
parallel diagnostic citation commit                = closed
final output full Bundle fan-out                   = closed
capture/observer full Bundle carrier               = closed
checkpoint v1 implicit compatibility               = closed
checkpoint v2 round-trip/resume                     = verified
canonical hash mutation by citation remap           = 0
LegacyEvidenceAdapter                              = sole legal ingress
Phase 4B recovery acceptance semantics             = preserved
```

最终结论：

```text
PHASE 4C CLOSED
```

停止于 Phase 4C，不自动进入 Phase 5、Multi-Agent V2 或其他阶段。
