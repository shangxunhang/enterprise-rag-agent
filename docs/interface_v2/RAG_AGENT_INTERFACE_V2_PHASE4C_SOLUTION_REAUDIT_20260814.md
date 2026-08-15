# RAG-Agent Interface V2 Phase 4C Solution Re-Audit

> 日期：2026-08-14  
> 仓库：`D:\MyCode\rag-agent`  
> 阶段：Interface V2 Phase 4C — Compatibility Removal Solution Re-Audit  
> 审计性质：**Solution Re-Audit only；未实施 Phase 4C；未修改 production/test code**

---

# 1. Final Decision

```text
PHASE 4C SOLUTION READY TO FREEZE
```

修订后的：

```text
docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4C_COMPATIBILITY_REMOVAL_SOLUTION_20260814.md
```

已经完整吸收上一轮 Solution Audit 的 7 个 blocker，并与：

- Phase 4 frozen canonical Evidence architecture；
- Phase 4A authority/recovery audit；
- Phase 4B typed canonical carrier；
- Phase 4B Final Closure 的 recovery candidate acceptance semantics；
- 当前 production code 事实；

保持一致。

本轮没有发现新的、需要重新设计 Phase 4C 的 blocker，也没有发现需要新的用户业务决策的问题。

---

# 2. 本轮实际读取的架构/审计依据

完整复核：

1. `RAG_AGENT_INTERFACE_V2_PHASE4C_COMPATIBILITY_REMOVAL_SOLUTION_20260814.md`
2. `RAG_AGENT_INTERFACE_V2_PHASE4C_SOLUTION_AUDIT_20260814.md`
3. `RAG_AGENT_INTERFACE_V2_PHASE4_CANONICAL_EVIDENCE_HANDOFF_20260814.md`
4. `RAG_AGENT_INTERFACE_V2_PHASE4_EVIDENCE_AUTHORITY_FIELD_MATRIX_AUDIT_20260814.md`
5. `RAG_AGENT_INTERFACE_V2_PHASE4A_CANONICAL_EVIDENCE_RECOVERY_AUDIT_20260814.md`
6. `RAG_AGENT_INTERFACE_V2_PHASE4B_FIX_CLOSURE_20260814.md`
7. `RAG_AGENT_INTERFACE_V2_PHASE4B_FINAL_FIX_HANDOFF_20260814.md`
8. `RAG_AGENT_INTERFACE_V2_PHASE4B_FINAL_CLOSURE_20260814.md`

本轮没有重新设计 Phase 4，也没有把 Phase 5 / Multi-Agent V2 / Gate / Claim Audit / retrieval / storage 等问题扩入 Phase 4C。

---

# 3. 冻结架构一致性复核

修订方案继续满足：

```text
EvidenceBundleSchema
= sole canonical Evidence authority
```

合法方向：

```text
Canonical EvidenceBundle
→ typed consumer
→ Application projection / Gate / citation mapping / hash / diagnostics
```

禁止方向：

```text
RAGContext / normalized dict / SharedState projection / output snapshot
→ model_validate
→ reconstruct canonical Bundle
```

唯一允许例外仍是：

```text
legacy input
→ LegacyEvidenceAdapter
→ EvidenceBundleSchema
→ common typed production path
```

与 Phase 4 frozen architecture 无冲突。

---

# 4. 七个旧 blocker 复审状态

| Issue | Re-Audit | 结论 |
|---|---|---|
| P4C-SA-001 Field-retirement matrix incomplete | **CLOSED** | 修订方案已把 document、section、assessment、history/output 等字段逐项冻结为 KEEP / REMOVE / DERIVE / APPLICATION PROJECTION / DIAGNOSTIC ONLY；`SectionEvidenceBundleSchema` 最终 persisted shape 已明确 |
| P4C-SA-002 Assessment current consumers misclassified | **CLOSED** | `document_evidence_assessment`、`active_assessment`、`ResearchState.assessment` 已按真实 current consumer 分别定义迁移顺序，不再统一误判为 historical compatibility |
| P4C-SA-003 LegacyEvidenceAdapter typed ingress not closed | **CLOSED** | 已冻结 legacy → typed Bundle → common typed projection path；禁止 adapter 再通过 `RAGContext.extra` / `normalized["evidence"]` 把 full Bundle 回灌 production |
| P4C-SA-004 Recovery citation commit boundary missing | **CLOSED** | 已冻结 candidate-local citation projection/registry → Application acceptance → accepted-only commit；direct / ResearchAgent gateway / parallel fan-in 全覆盖 |
| P4C-SA-005 Checkpoint breaking schema version missing | **CLOSED** | 已冻结 top-level/section state version `1 → 2`，v1 explicit unsupported-version rejection，v2 lifecycle 验收完整 |
| P4C-SA-006 Full Bundle persistence whitelist incomplete | **CLOSED** | 已建立 full Bundle carrier whitelist，并显式覆盖 `SchemeWriterOutput`、extra、SharedState compatibility output、AgentResult、Capture、Observer、history、normalized 等 fan-out |
| P4C-SA-007 Verification Matrix insufficient | **CLOSED** | 修订方案已经扩展到 reconstruction=0、ContextBundle retirement、SectionEvidence exact shape、checkpoint v2、recover_or_reuse、rejected citation isolation、parallel fan-in、output/capture negative scan、Phase 4B recovery acceptance regression |

结论：

```text
7 / 7 CLOSED
```

---

# 5. P4C-SA-001 — Field Retirement Matrix

修订方案已明确 document persisted duplicate state：

```text
document_rag_context
document_retrieved_chunks
document_citations      # RAG canonical-copy role
document_rag_output
document_evidence_assessment
document_evidence_available
document_assessment_status
all_retrieved_chunks    # long-lived role
```

目标为 REMOVE / DERIVE / Application projection。

`all_citations` 的 document-level citation business capability 保留，但语义属于：

```text
citation_ref → document label
```

而不是第二套 RAG canonical Citation。

`all_rag_outputs / retrieval history` 只允许 lightweight diagnostic，不得保存 reconstructable full Bundle。

## SectionEvidence final persisted shape

修订方案已经冻结到字段级：

```text
section_id
evidence_bundle
section_title
tool_call_ids
recovery_count
evidence_contract_sha256
writer_gate_binding
metadata                  # workflow diagnostics only
```

退出长期 persisted duplicate state：

```text
retrieval_scope
query
rag_context
retrieved_chunks
citations
generation_allowed
assessment-related copies
```

需要时：

```text
Evidence facts
→ 从 evidence_bundle derive

Gate facts
→ 从 WriterGateBindingSchema derive
```

这与 Phase 4 §10.6 / §10.7 冻结结果一致。

**P4C-SA-001：CLOSED。**

---

# 6. P4C-SA-002 — Assessment current consumer migration

当前代码事实复核确认：

## 6.1 `document_evidence_assessment`

仍有真实 consumer：

```text
DocumentAssembler
→ document_evidence_assessment.details.quality_mode_degraded
```

修订方案已明确：

```text
先迁 consumer
→ Bundle.assessment / minimal explicit quality projection
→ 再删 persisted document_evidence_assessment
```

顺序正确。

## 6.2 `active_assessment`

当前 Section graph / coordinator / replan 仍保留 compatibility writes/fallback。

修订方案已明确：

```text
先迁所有 current consumer
→ active_evidence_bundle.assessment
→ 再删除 active_assessment
```

顺序正确。

## 6.3 `ResearchState.assessment`

Phase 4B 当前 production writers 已显式写：

```text
assessment = {}
```

本轮未发现真实 production read consumer。

修订方案直接删除 schema field 是确定性清理。

**P4C-SA-002：CLOSED。**

---

# 7. P4C-SA-003 — LegacyEvidenceAdapter typed ingress

当前代码仍是：

```text
LegacyEvidenceAdapter.extract(...)
→ tuple(context, chunks, citations, normalized)
```

并且 adapter 当前仍会：

```text
contract.model_dump()
→ RAGContext.extra["evidence_contract"]
→ normalized["evidence"]
```

因此上一轮问题仍是当前真实代码事实。

修订方案已经把目标冻结为：

```text
legacy input
→ LegacyEvidenceAdapter.upgrade(...)
→ EvidenceBundleSchema
→ same typed production path
→ RAGEvidenceContractReader.projections(Bundle)
```

禁止：

```text
legacy → Bundle → hidden full-copy → downstream recovery
```

这已经形成完整 typed return/handoff contract。

**P4C-SA-003：CLOSED。**

---

# 8. Production reconstruction site 定点复核

本轮重新扫描当前 production reconstruction sites：

```text
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py
    RAGEvidenceContractSchema.model_validate(...)

backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
    EvidenceBundleSchema.model_validate(...)

backend/context_manager/policies.py
    RAGEvidenceContractSchema.model_validate(...)

backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py
    RAGEvidenceContractSchema.model_validate(...)
```

分类：

```text
DocumentCitationRegistry  = illegal Application reconstruction → Phase 4C remove
WriterGovernance           = illegal normalized copy recovery   → Phase 4C remove
ContextManager             = illegal RAGContext recovery        → Phase 4C remove
LegacyEvidenceAdapter      = allowed legacy ingress             → KEEP
```

本轮未发现新的第五个 production projection → canonical reconstruction site。

修订方案对这四类均已覆盖。

---

# 9. P4C-SA-004 — Recovery citation transactional commit

本轮定点代码复核确认该问题真实存在，而且修订方案覆盖正确。

## 9.1 ResearchAgent Gateway recovery

当前 `SectionExecutionGraph` 在 retrieval technical success 后，会在 candidate acceptance 之前把：

```text
retrieval.citation_registry
```

写入 pending updates。

虽然 Phase 4B 已保证：

```text
active_evidence_bundle promotion
→ only after candidate_accepted
```

但 citation registry 本身仍可能提前取得 rejected candidate citation。

## 9.2 Direct recovery

当前 direct recovery：

```text
result
→ extract projections
→ registry.remap_bundle(...)
→ registry 已 mutation
→ 再 _recovery_candidate_decision(...)
```

所以 citation commit 同样早于 acceptance。

## 9.3 Parallel fan-in

`StageService._merge_parallel_section_result()` 当前会：

```text
local_citations
→ citation_registry.register(...)
```

并且代码注释明确说明 diagnostic-only citations 也会进入 remap/register 范围。

因此 parallel rejected/diagnostic citation pollution 是真实风险。

## 修订方案目标

已冻结：

```text
candidate retrieval
→ candidate-local citation projection / registry
→ Application acceptance
→ accepted
→ commit/promote to document citation registry
```

Rejected：

```text
可以保留 diagnostic trace/ref/hash/count
不得进入 document registry
不得进入 final citations/catalog
不得占用 document citation numbering
```

并显式覆盖：

```text
direct recovery
ResearchAgent Gateway recovery
parallel fan-in
```

与 Phase 4B candidate acceptance 语义完全一致，没有冲突。

**P4C-SA-004：CLOSED。**

---

# 10. P4C-SA-005 — Checkpoint version boundary

当前代码：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION = 1
SECTION_EXECUTION_GRAPH_STATE_VERSION = 1
```

两个 codec 当前都已经先检查 version，再解析 payload：

```text
version != current
→ ValueError("unsupported ... graph state version ...")
```

因此修订方案要求：

```text
1 → 2
```

即可形成明确 breaking schema boundary。

不需要额外引入 migration framework / ArtifactStore / 新 persistence service。

修订方案同时要求：

```text
v1 → explicit reject
v2 → JSON/checkpointer round-trip
     reload
     new-instance resume
     replan
     recover_or_reuse incomplete resume
     recover_or_reuse completed-section reuse
```

当前 `recover_or_reuse()` 确实存在两条独立业务路径：

```text
persisted.completed == true
→ restore state + registry + reuse result

persisted.completed == false
→ resume exact durable cursor
```

所以把两条都纳入 v2 acceptance 是必要且充分的。

**P4C-SA-005：CLOSED。**

---

# 11. P4C-SA-006 — Full Bundle persistence whitelist

当前代码仍存在真实 fan-out：

```text
payload.section_evidence
→ SchemeWriterOutput.section_evidence
→ SchemeWriterOutput.extra["section_evidence"] model_dump
→ enriched_rag_output["section_evidence"] model_dump
→ SharedState contexts.rag_tool_output
→ terminal AgentResult.result["scheme_writer_output"]
```

修订方案已经先识别这条完整链，再冻结 whitelist。

## 允许 full typed Bundle 的位置

```text
document_evidence_bundle
active_evidence_bundle
SectionEvidenceBundleSchema.evidence_bundle
transient research invocation typed Bundle
```

## 禁止形成新的 full Bundle carrier

```text
RAGContext.extra
normalized payload
SchemeWriterOutput / SchemeWriterOutput.extra
contexts.rag_tool_output
terminal AgentResult generic dict
Capture
Observer
metadata / history
all_rag_outputs
```

最终输出如需 Evidence，只能使用：

```text
hash/ref
status
counts
lineage summary
trace ids
Application citation mapping
```

### 对 `SchemeWriterOutput.section_evidence` 的解释

当前 `SchemeWriterOutput.section_evidence` 没有发现必须读取 full Bundle 的 downstream production consumer。

修订方案的 AC-10 和 focused test #22 已明确要求：

```text
final SchemeWriterOutput
→ 不复制 full canonical Bundle
```

因此 implementation 不能把“`SectionEvidenceBundleSchema.evidence_bundle` 是允许的 canonical final slot”误解为“可以把该 slot 再复制一份到 terminal output”。

白名单约束的是 **canonical lifecycle carrier instance**，不是允许任何嵌套同类型对象无限复制。

这是已有 Acceptance Criteria 的直接含义，不需要新的用户决策。

**P4C-SA-006：CLOSED。**

---

# 12. Capture / Observability feasibility

本轮定点复核：

## `SchemeCaptureService`

当前主要记录：

```text
rag_context projection
retrieved_chunks projection
citations projection
rag_trace
input/draft/gate/section diagnostics
lineage refs
```

并不要求保存完整 `EvidenceBundle` 才能完成 capture lifecycle。

## `workflow_observer`

当前对 `ContextBundle.evidence.contract` 只用来生成：

```text
has_evidence_contract: bool
```

退休 `ContextBundle.evidence` 后可直接由 typed lifecycle carrier / summary projection 得到等价信息。

## `trace_summary`

legacy ToolResult summary 目前仍从：

```text
payload["evidence"]
```

提取 lineage/hash；修订方案已经把 observability consumer migration 纳入范围，并要求改为 typed Bundle / explicit summary projection。

因此：

```text
Capture / Observability
≠ full Bundle persistence requirement
```

修订方案可在无 full copy 条件下保留现有观测职责。

---

# 13. P4C-SA-007 — Verification Matrix

修订方案的 Verification Matrix 已覆盖上一轮缺口，包括：

```text
projection → canonical reconstruction = 0

ContextBundle.evidence:
schema/write/read/alias/observer/workflow write-contract = 0

SectionEvidence exact persisted shape
assessment current consumer migration

checkpoint v1 explicit reject
checkpoint v2 round-trip
new instance reload/resume
replan
recover_or_reuse incomplete resume
recover_or_reuse completed reuse

parallel fan-in

direct rejected citation isolation
ResearchAgent gateway rejected citation isolation
parallel rejected citation isolation
rejected candidate 不提前占 document citation numbering

full Bundle serialization whitelist negative scan
SchemeWriterOutput negative proof
SharedState compatibility output negative proof
terminal AgentResult negative proof
Capture/Observer negative proof

DocumentAssembler aggregation/quality regression
Phase 4B recovery acceptance four-quadrant regression
```

这已经能够证明：

```text
canonical authority 唯一
projection 单向
reconstruction 清零
duplicate full Bundle 清零
checkpoint lifecycle 正常
recovery acceptance 不回归
```

**P4C-SA-007：CLOSED。**

---

# 14. `ContextBundle.evidence` retirement 覆盖复核

当前真实引用仍包括：

```text
StageService.set_evidence_context(...)
SchemeGeneration write_paths / failure_write_paths
TopLevelBusinessGraph graph_write_paths
GraphStateOps write alias: evidence_context
GraphStateOps read alias: evidence_contract / rag_context
SharedStateWriter.set_evidence_context(...)
WorkflowObserver
EvidenceContextSchema / ContextBundle schema
```

修订方案已经明确要求同时清理：

```text
schema
writer
read alias
write alias
observer
workflow write/failure-write contract
top-level business graph declaration
StageService write
```

所以不是“只删 Schema 字段”。

**Retirement closure：PASS。**

---

# 15. 两个额外疑点复核

本轮在 7 个 blocker 之外额外追了两个容易误判的问题。

## 15.1 `ResearchState.evidence_gaps`

Phase 4 frozen handoff 曾规定：

```text
如果只是 EvidenceBundle.assessment 的机械复制 → 退出
如果是 Research Agent 新业务判断 → 可保留
```

代码复核确认它不是纯机械副本。

Research reasoner 会在 canonical assessment 基础上，根据：

```text
tool error
retrieval unavailable
citation required
research status
```

补充/归一化 gap，并据此产生：

```text
research_status
recommended_action
```

因此 `ResearchState.evidence_gaps` 属于 Research/Application workflow projection，而不是第二 canonical Evidence authority。

**不构成 Phase 4C blocker。**

## 15.2 `DocumentAssembler` 对 `SectionEvidence.metadata["evidence_assessment"]` 的残留 read

当前 `DocumentAssembler` 仍有：

```text
bundle.metadata.get("evidence_assessment")
```

用于查 `quality_mode_degraded`。

但本轮全仓定点搜索未发现 production `SectionEvidenceBundleSchema.metadata["evidence_assessment"]` writer；当前两个 SectionEvidence producer 都只写 workflow/retrieval diagnostics。

所以这是一个 residual compatibility/dead reader，不是仍在形成 persisted assessment duplicate 的 producer path。

修订方案已经要求：

```text
SectionEvidence metadata = workflow diagnostics only
assessment-related copies退出
DocumentAssembler focused regression
```

因此实现时删除/改为 typed Bundle derive 是现有冻结原则的确定性 cleanup，不需要新架构决策。

**不构成新的 Solution blocker。**

---

# 16. 本轮 15 个必答问题

## 1. 修订后的 Phase 4C Solution 是否完整吸收 7 个 blocker？

```text
YES — 7/7 CLOSED
```

## 2. 是否还存在遗漏的 persisted Evidence duplicate state？

基于本轮定点扫描：

```text
没有发现修订方案范围外、仍需要新增架构设计的 persisted canonical duplicate state。
```

当前旧 duplicate fields/path 都已经被 Solution matrix / removal / derive / diagnostic rules 覆盖。

## 3. 是否还存在未被方案覆盖的 projection → canonical reconstruction？

```text
NO
```

当前 3 个非法 production reconstruction + 1 个合法 legacy ingress 均已被方案分类和覆盖。

## 4. LegacyEvidenceAdapter typed ingress contract 是否闭环？

```text
YES
```

目标已冻结为 typed Bundle return → common typed production path。

## 5. ContextBundle.evidence retirement 是否覆盖完整？

```text
YES
```

覆盖 schema、writer、alias、observer、workflow write/failure-write、business graph declaration、StageService write。

## 6. SectionEvidenceBundleSchema 最终形状是否字段级明确？

```text
YES
```

最终保留字段和删除/derive 字段均已明确。

## 7. assessment current consumers 迁移顺序是否正确？

```text
YES
```

先迁 consumer，后删 persisted duplicate。

## 8. rejected recovery citation commit boundary 是否完整覆盖 direct/gateway/parallel？

```text
YES
```

commit 明确发生在 Application acceptance 后。

## 9. checkpoint `v1 → v2` 是否足以表达 breaking schema？

```text
YES
```

现有 codec 已有 explicit unsupported-version guard；常量 bump 即形成明确边界。

## 10. `recover_or_reuse()` 是否纳入新 checkpoint 验收？

```text
YES
```

同时覆盖 incomplete resume 与 completed reuse。

## 11. full Bundle whitelist 是否足以阻止 output / SharedState / AgentResult / Capture / Observer 再复制？

```text
YES
```

Solution + AC + negative scan/test 已形成闭环。

## 12. Capture / Observability 是否仍能无 full copy 完成职责？

```text
YES
```

当前职责可由 typed Bundle summary/hash/counts/lineage/projections 实现。

## 13. Acceptance Criteria 是否足够证明最终 architecture invariants？

```text
YES
```

覆盖 authority、projection direction、reconstruction=0、duplicate full copy=0、checkpoint lifecycle、recovery acceptance。

## 14. 是否出现新的、真正需要用户业务决策的问题？

```text
NO
```

额外发现均可由已冻结原则唯一确定。

## 15. 最终判定

```text
PHASE 4C SOLUTION READY TO FREEZE
```

---

# 17. Freeze Readiness

满足 READY 条件：

```text
7 个旧 blocker 全部闭合                     PASS
没有新的 Phase 4C blocker                   PASS
Solution 内部架构无明显矛盾                  PASS
与 Phase 4 frozen architecture 一致          PASS
与 Phase 4B accepted/rejected semantics 一致 PASS
Acceptance / Verification 足以证明实施结果   PASS
新的用户业务决策                             NONE
```

最终：

```text
PHASE 4C SOLUTION READY TO FREEZE
```

下一步可以把：

```text
RAG_AGENT_INTERFACE_V2_PHASE4C_COMPATIBILITY_REMOVAL_SOLUTION_20260814.md
```

作为 Phase 4C implementation Source of Truth 冻结后进入实施。

本报告本身不实施 Phase 4C。

---

# 18. Git / 修改范围说明

本轮开始时工作树已经包含此前 Phase 4B 累计未提交修改、测试、审计文档、Multi-Agent 文档和 `nul`。

本轮严格未执行：

```text
git clean
git reset
git checkout .
git add
git commit
stage
production code edit
test code edit
```

本轮唯一新增写入：

```text
docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4C_SOLUTION_REAUDIT_20260814.md
```

没有进入 Phase 4C implementation。
