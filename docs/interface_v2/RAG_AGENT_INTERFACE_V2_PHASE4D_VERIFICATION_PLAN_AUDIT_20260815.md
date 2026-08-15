# RAG-Agent Interface V2 Phase 4D Verification Plan Audit

日期：2026-08-15

审计对象：

`docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4D_VERIFICATION_PLAN_20260815.md`

本轮性质：**Verification Plan independent audit only**

本轮未修改 production code / tests。

---

# 1. Final Audit Decision

当前结论：

```text
PHASE 4D VERIFICATION PLAN NOT YET READY TO FREEZE
```

原因不是发现新的 Phase 4 production architecture blocker。

当前仍然成立：

```text
C. REAL IMPLEMENTATION GAP = NONE PROVEN
USER ARCHITECTURE DECISION REQUIRED = NONE
```

但 Verification Plan 本身存在 3 个需要在进入 implementation 前收口的问题：

```text
P4D-PA-001  deterministic stale-test inventory incomplete
P4D-PA-002  runtime compatibility consumer contract insufficiently grounded
P4D-PA-003  cross-instance resume proof must require a genuinely fresh runtime boundary
```

另有 2 个非阻塞但建议补强的 test-design 条目：

```text
P4D-PA-N01 CitationRegistry test should bind to actual checkpoint payload shape
P4D-PA-N02 broader-red classification must distinguish first failure from latent stale assertions
```

因此当前正确动作是：

```text
补正 Phase 4D Verification Plan
→ 再冻结
→ 然后进入 verification implementation
```

不需要重新设计 Phase 4 architecture。

---

# 2. Audit Scope

本轮重新核对：

- Phase 4D Verification Plan 全文；
- `test_phase4_durable_resume_hitl.py`；
- `test_step_14_context_manager.py`；
- `test_phase3_section_execution_subgraph.py`；
- `test_section_aware_evidence_retrieval.py`；
- `test_mainline_closure_acceptance.py`；
- `test_phase5_real_agent_adapters.py`；
- `DocumentCitationRegistry` 当前实现；
- `SectionGenerationContextPolicy` 当前 typed path；
- `StageService` final output projection；
- `SectionExecutionGraph` completed/recovery path；
- `scripts/run_demo.py` 当前 final-output consumer；
- Phase 4C frozen output/persistence requirements。

同时实际运行了针对 output compatibility stale assumptions 的 focused tests。

---

# 3. What the Plan Gets Right

以下判断可以保留，不需要推翻。

## 3.1 Phase 4D is verification-first

正确：

```text
Phase 4D
=
verification + deterministic cleanup
```

当前没有证据要求重新设计 Phase 4B / 4C architecture。

## 3.2 The five main verification gaps are directionally correct

Plan 识别的：

```text
top-level durable cross-instance resume
completed document retrieval no-replay
CitationRegistry restore continuity
ContextManager typed prompt parity
lightweight output runtime compatibility
```

都是合理的 Phase 4D verification concerns。

## 3.3 Old top-level resume E2E is not sufficient no-replay evidence

审计确认：

`test_new_engine_resumes_without_replaying_completed_nodes`

当前断言顺序为：

```text
resume()
→ assert final status success/partial_success
→ assert no duplicate business nodes
→ assert model call count unchanged
→ assert RAG call count unchanged
```

当前 strict Document Hard Gate 会在第一条 terminal-success assertion 处失败，因此后面的 no-replay assertions 没有执行。

Plan 对此判断正确：

```text
不能把旧 broad E2E 当前 red 当成 resume implementation gap
```

也不能修改 Gate policy 来迎合该 test。

## 3.4 ContextManager migration direction is correct

当前 `SectionGenerationContextPolicy.build_request()` 已明确：

```text
contract = evidence_bundle
```

并且不再从 `RAGContext.extra` reverse-recover Bundle。

旧 Step14 test 应迁为：

```text
RAGContext = projection only
EvidenceBundle = explicit typed input
```

而不是恢复旧 fallback。

## 3.5 Citation continuity is verification gap, not yet proven bug

当前 `DocumentCitationRegistry.restore()` / `register()` 已具备 stable citation identity + append numbering 的实现基础。

目前没有证据证明生产 checkpoint catalog 会错误恢复。

因此当前保持：

```text
B. IMPLEMENTED BUT VERIFICATION GAP
```

是合理分类。

---

# 4. Blocker P4D-PA-001 — Deterministic Cleanup Inventory Is Incomplete

Plan 当前只明确列出两条 stale test：

```text
test_step_14_context_manager.py

test_scheme_writer_secondary_service_boundaries.py
```

该 inventory 不完整。

## 4.1 Independently reproduced additional stale test

实际运行：

```text
backend/tests/test_section_aware_evidence_retrieval.py::
test_fake_mainline_can_explicitly_enable_section_aware_retrieval
```

当前明确失败：

```text
KeyError: 'retrieval_scope'
```

原因：

旧测试读取：

```text
summary["scheme_writer_output"]["section_evidence"][...]["retrieval_scope"]
```

而 Phase 4C frozen output 已经把 `SchemeWriterOutput.section_evidence` 收口成 lightweight summary，不再把完整 `SectionEvidenceBundleSchema` fan-out 到 terminal/output compatibility path。

当前 summary 保留：

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

`retrieval_scope/query` 的真实 Application/runtime projection 仍存在于 generated section：

```text
section.extra["evidence_scope"]
section.extra["evidence_query"]
```

因此该 failure 当前应分类：

```text
D. DETERMINISTIC CLEANUP
```

不是恢复 full SectionEvidence output 的理由。

## 4.2 Additional latent stale assertions behind unrelated Gate failures

以下 tests 当前首先因 strict Hard Gate / Claim Audit terminal status 失败：

```text
backend/tests/test_mainline_closure_acceptance.py::
test_corrective_retrieval_branch_runs_and_recovers

backend/tests/test_mainline_closure_acceptance.py::
test_document_citation_registry_resolves_local_id_collisions
```

但继续检查其后续 assertions，可确认它们仍读取 Phase 4C 已退出的 old output shape：

```text
section_evidence[*]["retrieval_scope"]
section_evidence[*]["query"]
section_evidence[*]["citations"]
```

同文件：

```text
test_self_rag_need_retrieve_more_runs_bounded_retrieval_and_recheck
```

还读取：

```text
section_evidence[*]["metadata"]["self_rag_retrieval_rounds"]
section_evidence[*]["retrieval_scope"]
```

这些 stale assertions 不能因为前面已有 unrelated Gate red 就被忽略。

否则后续 Gate concern 一旦被隔离，test 会继续因 Phase 4C output shape 变更失败。

## 4.3 Another stale output-shape dependency

`backend/tests/test_phase5_real_agent_adapters.py` 当前存在：

```text
summary["scheme_writer_output"]["section_evidence"]
→ item["retrieval_scope"]
```

该 test 名中的 “phase5” 是仓库既有 agent integration round 名称，不等同于 Interface V2 Phase 5，不能因为文件名而自动标为当前 Interface Phase 5 out-of-scope。

其旧 output assumption 仍应纳入 Phase 4D compatibility cleanup inventory 或明确归因。

## 4.4 Required plan correction

Phase 4D Plan 不应继续写：

```text
confirmed stale compatibility tests = 2
```

至少应扩展为一份 exact stale-output-assumption inventory，逐条扫描：

```text
SchemeWriterOutput.section_evidence consumers
section_evidence[*].retrieval_scope
section_evidence[*].query
section_evidence[*].citations
section_evidence[*].metadata
normalized["evidence"]
RAGContext.extra["evidence_contract"]
```

并分类：

```text
real production/runtime consumer
stale test consumer
out-of-scope test
still-valid lightweight projection consumer
```

这一步完成前，不应冻结 4D implementation scope。

---

# 5. Blocker P4D-PA-002 — Runtime Compatibility Consumer Contract Is Too Abstract

Plan 的 T4D-05 当前写法是：

```text
当前 consumer 所需关键字段至少保持：
hash/ref
status
counts
trace/retrieval ids
section_id / section_title
```

问题是：

```text
“当前 consumer”没有被具体枚举
```

这会产生两种相反风险：

1. 为了迁就 stale tests，把已经退休的 output full-copy/projection 字段重新加回来；
2. 只测 summary 自己能构造，却没有证明真正 runtime consumer 能继续工作。

## 5.1 Real runtime consumer already exists

当前仓库真实 CLI/report consumer：

```text
scripts/run_demo.py::_section_report_rows()
```

它读取：

```text
SchemeWriterOutput.section_evidence:
- section_title
- recovery_count
- tool_call_ids

Generated SchemeSection.extra:
- evidence_scope
- evidence_query
```

其中 scope/query 有明确 fallback：

```text
section.extra["evidence_scope"]
or section_evidence["retrieval_scope"]

section.extra["evidence_query"]
or section_evidence["query"]
```

Phase 4C 后正常路径应优先由 `section.extra` 满足，而不是为了旧 test 重新把 complete SectionEvidence projection 放回 output。

## 5.2 T4D-05 must be consumer-driven

T4D-05 应改成至少验证：

```text
real StageService final output
→ MainlineApplicationService summary
→ scripts/run_demo.py report consumer
```

证明：

```text
section report rows 仍能获得：
section_id/title
status
citation_count
scope/query
recovery_count
tool_call_ids

且不需要 full EvidenceBundle / full SectionEvidence dump
```

同时验证：

```text
contexts.rag_tool_output
```

当前 legitimate consumer / read path 实际需要的字段，而不是先假定一个字段集合。

如果某字段只有 stale tests 读取，而 production/runtime consumer 不需要，应迁 test，不应扩 production output。

## 5.3 Compatibility output requirement must distinguish capability from old shape

Phase 4D 原始要求是：

```text
compatibility outputs 仍能正常生成
```

它不等于：

```text
pre-4C full SectionEvidence serialization shape 必须原样保留
```

Phase 4C 已冻结：

```text
output/capture/trace
→ summary/ref/hash/count/status/lineage/citation projection only
```

因此 4D 的 compatibility acceptance 应验证“当前真实能力不退化”，而不是“旧 dict shape 不变”。

---

# 6. Blocker P4D-PA-003 — Cross-Instance Test Must Require a Truly Fresh Runtime Boundary

Plan 的 T4D-01 方向正确，但必须进一步锁死测试实现方式。

现有旧 test：

```text
test_new_engine_resumes_without_replaying_completed_nodes
```

虽然创建了新的 `EnterpriseDocumentWorkflowEngine`，但它复用了：

```text
old_engine.stage_service
old_engine.side_effect_commit_store
```

因此它是：

```text
new graph engine + reopened checkpoint
```

但不是最强意义上的：

```text
fresh Application/Supervisor/StageService runtime instance
```

Phase 4D Plan 本身已经要求：

```text
new engine/service instance
```

implementation 时不能退化成简单复制旧 test。

## 6.1 Required proof shape

建议冻结为：

```text
first MainlineApplicationService / Supervisor
→ real SQLite checkpoint
→ human-review pause
→ close first runtime resource

second MainlineApplicationService / Supervisor / StageService
→ reopen same checkpoint DB
→ resume same run
```

no-replay guard 应挂在新的 runtime 边界，例如：

```text
patch SchemeGenerationStageService.retrieve_document_evidence fail-on-call
```

或者等价的 fresh RAG service fail-on-call guard。

如果 resume 错误 replay document retrieval：

```text
test must fail immediately
```

这样才能同时证明：

```text
checkpoint persistence
cross-instance restore
node cursor correctness
document retrieval no replay
```

而不依赖旧 in-memory StageService object。

## 6.2 Final Gate must remain orthogonal

仍保留 Plan 当前正确要求：

```text
final Document Hard Gate success
不是该 test 的 pass prerequisite
```

应先断言 resume/no-replay invariants，再单独记录最终 business status。

---

# 7. Non-blocking Strengthening P4D-PA-N01 — Bind Citation Test to Actual Checkpoint Shape

T4D-02 当前写：

```text
C1...C3
→ payload JSON/checkpoint-safe serialization
→ restore registry
```

方向正确，但 `payload` 应写清楚。

建议使用真实 top-level persisted projection：

```text
SchemeGenerationGraphPayloadSchema.all_citations
→ SchemeGenerationGraphStateCodec JSON round-trip
→ restored_payload.all_citations
→ DocumentCitationRegistry.from_citations(...)
```

然后验证：

```text
same citation_ref + different local id
→ original document label
→ catalog size unchanged

new citation_ref
→ C4

same new citation_ref again
→ C4
→ catalog size unchanged
```

这样证据能同时覆盖：

```text
checkpoint serialization
registry restore
identity dedup
numbering continuity
```

而不是只测 registry 内存 API。

注意：当前生产 v2 registry 正常生成的 catalog 本身应为 `C1...Cn` 连续序列；不要用 synthetic sparse label 行为扩大为新的 Phase 4 architecture requirement。

---

# 8. Non-blocking Strengthening P4D-PA-N02 — Broader Red Must Record Latent Assertions

Plan 已正确要求 broader red 分类。

但本次审计证明：

```text
first failing assertion
!=
该 test 唯一潜在问题
```

例如 `test_mainline_closure_acceptance.py` 当前先被 Hard Gate terminal status 截断，但后面还有已经 stale 的 SectionEvidence output assertions。

因此 final Phase 4D closure 对 known-red test 应记录：

```text
first observed failure
Phase 4 related? YES/NO
remaining assertions audited? YES/NO
latent Phase 4 stale assumption? YES/NO
```

避免把一整个 test 只因为第一处 failure 属于 Hard Gate，就错误整体标成 `E. OUT OF PHASE 4`。

---

# 9. Revised Verification Gap Classification

审计后建议保持 5 个 B 类 verification concerns：

```text
B-01 top-level durable cross-instance resume
B-02 completed document retrieval no-replay
B-03 CitationRegistry checkpoint restore continuity
B-04 ContextManager typed prompt semantic parity
B-05 lightweight compatibility output real-consumer proof
```

当前仍没有证据把任一项升级为：

```text
C. REAL IMPLEMENTATION GAP
```

但 D 类 cleanup 应扩展。

至少包括：

```text
D-01 test_step_14_context_manager.py
      old RAGContext.extra full Bundle fallback assumption

D-02 test_scheme_writer_secondary_service_boundaries.py
      old normalized["evidence"] assumption

D-03 test_section_aware_evidence_retrieval.py
      old section_evidence[*].retrieval_scope output assumption

D-04 test_mainline_closure_acceptance.py
      old section_evidence retrieval_scope/query/citations/metadata assumptions
      （与 unrelated Hard Gate red 必须分开处理）

D-05 test_phase5_real_agent_adapters.py
      old section_evidence[*].retrieval_scope assumption
      （先确认该 test 当前业务 concern 后迁到合法 projection/trace）
```

是否还有其他同类 stale tests，应由 implementation 前的一次 exact consumer scan 再确认，不能把上述 5 条视为穷举结果。

---

# 10. Required Plan Amendments Before Freeze

在允许：

```text
PHASE 4D VERIFICATION PLAN READY TO FREEZE
```

之前，Plan 至少应补充：

1. 扩展 deterministic cleanup inventory，不再只写两条 stale tests；
2. 对 `SchemeWriterOutput.section_evidence` 所有 test/runtime consumers 做 exact scan；
3. T4D-05 明确真实 runtime consumer，至少覆盖 `scripts/run_demo.py::_section_report_rows()`；
4. 明确 old output shape 不等于 compatibility requirement；
5. T4D-01 要求 fresh Application/Supervisor/StageService runtime boundary，不复用 old StageService；
6. T4D-01 fail-on-call guard 必须直接保护 document retrieval replay；
7. T4D-02 使用真实 `SchemeGenerationGraphPayloadSchema.all_citations` checkpoint shape；
8. broader-red 分类加入 latent stale assertion audit，不能只看第一处失败。

这些都属于：

```text
Verification Plan correction
```

不是新的 architecture decision。

---

# 11. User Decision Requirement

当前：

```text
USER DECISION REQUIRED = NONE
```

上述问题都有唯一确定性工程处理方向：

```text
补全 verification inventory
迁 stale tests 到已冻结合法 projection
增强 test isolation
不恢复 retired compatibility authority
```

不需要用户重新选择 Evidence authority、checkpoint compatibility、citation ownership 或 Gate ownership。

---

# 12. Final Answer

当前 Phase 4D Plan：

```text
Architecture direction      = CORRECT
Verification direction      = CORRECT
Real implementation gap     = NONE PROVEN
User decision               = NONE
Plan completeness           = NOT YET SUFFICIENT
Implementation readiness    = NOT YET
```

最终审计结论：

```text
PHASE 4D VERIFICATION PLAN NOT YET READY TO FREEZE
```

先补正上述 3 个 blocker，再进入 Phase 4D verification implementation。
