# RAG-Agent Interface V2 Phase 4C Compatibility Removal 解决方案

日期：2026-08-14  
阶段：Interface V2 Phase 4C  
状态：**方案已按独立 Solution Audit 修订；待短复审冻结；未进入代码实施**  
前置阶段：`PHASE 4B CLOSED`  
代码基线 HEAD：`f43f959`

---

# 1. Phase 4C 的目标

Phase 4B 已经建立并验证：

```text
EvidenceBundleSchema = sole canonical Evidence authority

Document lifecycle
→ document_evidence_bundle

Section lifecycle
→ active_evidence_bundle

Research retrieval
→ typed evidence_bundle
```

Phase 4C 不重新设计 Evidence 架构。

Phase 4C 的唯一目标是：

> **删除 Phase 4B 之后仍然残留的 full-copy、projection recovery、Application rebuild 和 duplicate Evidence state，使 canonical Evidence 只能单向投影，不能从 Application projection/dict 反向恢复为新的 canonical Bundle。**

目标链路：

```text
RAG
→ EvidenceBundleSchema
→ typed Application carrier
→ 按需 projection
```

禁止继续存在：

```text
EvidenceBundle
→ model_dump()
→ normalized["evidence"]
→ RAGContext.extra["evidence_contract"]
→ ContextBundle.evidence.contract
→ model_validate()
→ EvidenceBundle
```

Phase 4C 完成后的核心判据：

```text
Production projection/dict → EvidenceBundle reconstruction = 0
except LegacyEvidenceAdapter legacy → canonical boundary
```

---

# 2. 已冻结的架构原则

以下原则不在 Phase 4C 中重新讨论。

## 2.1 Canonical authority

```text
EvidenceBundleSchema
= sole canonical Evidence authority
```

任何 Application projection、Context projection、Citation projection、Observer payload、Capture payload 都不是 Evidence authority。

## 2.2 单向投影

只允许：

```text
EvidenceBundle
→ context projection
→ citation projection
→ observability summary
→ capture fields
```

正常 production 禁止：

```text
projection/dict
→ EvidenceBundleSchema.model_validate(...)
→ 恢复 canonical authority
```

唯一例外：

```text
LegacyEvidenceAdapter
legacy input
→ canonical EvidenceBundleSchema
```

该例外只表达 legacy → canonical 的入口升级，不允许升级后再次制造 full canonical copies 供 production 反向消费。

## 2.3 Application 不得重建 canonical Evidence

Application 可以：

```text
读取 Bundle
计算 Gate
生成 citation label projection
生成 prompt context
生成 hash/lineage summary
```

Application 不得：

```text
修改 Bundle citations/items/context
重新 model_validate() 构造 replacement Bundle
把 document citation label 写回 canonical Bundle
```

## 2.4 不为未知未来保留 duplicate state

Multi-Agent V2 后续是否需要共享 Evidence projection 当前尚未冻结。

因此 Phase 4C 不保留 `ContextBundle.evidence` 作为“以后可能用”的预留结构。

未来 Multi-Agent V2 若存在真实跨 Agent Evidence Contract 需求，应根据当时业务重新设计明确的 typed projection，例如：

```text
AgentEvidenceView
SharedEvidenceProjection
```

不得复活第二 Evidence authority。

---

# 3. 本轮审计确认的全部问题

## 3.1 `RAGContext.extra["evidence_contract"]` 保存完整 Bundle

当前 typed RAG 已经返回 `EvidenceBundleSchema`，但 Application `SchemeEvidenceService.extract_rag_output()` 仍会把完整 Bundle dump 到：

```text
RAGContext.extra["evidence_contract"]
```

同时还保存：

```text
evidence_contract_sha256
lineage
```

这使 `RAGContext` 同时承担：

```text
context projection
+
full Evidence carrier
```

违反单一 authority 原则。

### 4C 结论

正常 typed production 中：

```text
RAGContext.extra["evidence_contract"] = REMOVE
```

`RAGContext` 只保留真正属于 context projection 的字段和必要 projection metadata。

---

## 3.2 `DocumentCitationRegistry` 反向恢复并重建 Bundle

当前 `DocumentCitationRegistry.remap_bundle()` 仍然可能：

```text
RAGContext.extra["evidence_contract"]
→ 读取 full dict
→ 修改 citations
→ 修改 EvidenceItem.citation_ids
→ 修改 context_text marker
→ RAGEvidenceContractSchema.model_validate(...)
→ 得到新的 contract
→ 再写 normalized["evidence"]
```

这是 Phase 4C 最明确的 canonical rebuild violation。

### 4C 结论

`DocumentCitationRegistry` 必须收敛为纯 Application projection：

```text
citation_ref / source identity
→ document citation label
```

允许修改：

```text
Application RAGContext projection
Application citation list projection
Prompt/document marker
Application normalized projection metadata
```

禁止修改：

```text
canonical EvidenceBundle
canonical EvidenceItem
canonical citations
canonical Bundle context
canonical Bundle hash
```

禁止通过任何 projection 构造 replacement canonical Bundle。

---

## 3.3 ContextManager 仍可从 `RAGContext` 恢复 Bundle

当前 ContextManager 仍存在：

```python
contract = evidence_bundle or self._evidence_contract(rag_context)
```

`_evidence_contract()` 会读取：

```text
rag_context.extra["evidence_contract"]
```

并执行 `model_validate()`。

### 4C 结论

正常 production：

```text
需要 Evidence facts
→ 显式传 typed EvidenceBundle
```

禁止：

```text
拿 RAGContext
→ 检查 extra
→ 恢复 canonical Bundle
```

`ContextPolicy._evidence_contract()` compatibility recovery 退出 production。

---

## 3.4 `ContextBundle.evidence` 是第二套 Evidence state

当前 SharedState `ContextBundle.evidence` 保存：

```text
query
contract
context_text
retrieved_chunks
citations
used_doc_ids
evidence_available
assessment_status
evidence_sufficient
metadata
```

其中大部分事实已经由 typed `EvidenceBundleSchema` 持有。

当前生产链中的真正业务 authority 已经迁移至：

```text
document_evidence_bundle
active_evidence_bundle
```

`ContextBundle.evidence` 当前主要剩余价值是旧 alias、observer 和兼容访问。

### 已冻结决策

```text
ContextBundle.evidence
→ Phase 4C 整体退休
```

不保留薄 projection。

同步退出：

```text
SharedStateWriter.set_evidence_context()
GraphState evidence_contract / rag_context legacy aliases（若无其他真实消费）
Observer 对 ContextBundle.evidence 的依赖
相关 duplicate state writes
```

未来 Multi-Agent V2 若有真实需求，重新设计新的明确 projection Contract。

---

## 3.5 `normalized["evidence"]` 仍保存完整 Bundle copy

当前 `SchemeEvidenceService.extract_rag_output()` typed path 仍然构造：

```python
normalized["evidence"] = contract.model_dump()
```

其历史消费者包括：

```text
StageService legacy assessment helper
SectionExecutionGraph legacy assessment helper
SectionExecutionCoordinator legacy assessment helper
WriterGovernance compatibility fallback
LegacyEvidenceAdapter
Observability
旧测试/Fake ToolResult path
```

正常 production 主接口已经是：

```text
RAGService.retrieve()
→ EvidenceBundleSchema
```

没有发现真实前端、外部 API 或第三方业务调用方必须消费 `normalized["evidence"]` full copy。

Capture 当前也不依赖完整 `normalized["evidence"]`。

Observability 只需要：

```text
schema/hash/count/status/lineage summary
```

不需要完整 Bundle。

### 已冻结决策

正常 production：

```text
normalized["evidence"] full Bundle copy
→ REMOVE
```

Capture / Observability：

```text
直接从 typed Bundle 或明确 summary projection 取所需字段
```

Legacy/Fake boundary 如果暂时需要旧 ToolResult 形状，只能局限于：

```text
adapter/test boundary
```

不得重新进入 production state 或 production authority path。

---

## 3.6 `LegacyEvidenceAdapter` 升级后再次制造 full copies，且 typed handoff 未闭合

`LegacyEvidenceAdapter` 的合理职责是：

```text
legacy ToolResult / legacy payload
→ canonical EvidenceBundleSchema
```

当前代码虽然能在 Adapter 内部构造 canonical Bundle，但现有接口最终只返回：

```text
(context, chunks, citations, normalized)
```

而不是把 typed `EvidenceBundleSchema` 交回 production carrier。当前 legacy path 之所以还能被后续 production 继续消费，实际依赖：

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
```

这两个 full-copy egress。

### 4C 结论

保留唯一兼容入口，但冻结为真正 one-way ingress：

```text
LegacyEvidenceAdapter.upgrade(...)
→ EvidenceBundleSchema
→ 进入与 typed RAG 完全相同的 common projection path
→ RAGEvidenceContractReader.projections(Bundle)
→ Application projections
```

删除：

```text
Adapter canonical → RAGContext.extra full copy
Adapter canonical → normalized["evidence"] full copy
Adapter 自己维护第二套 normalized canonical serialization
```

因此实施顺序必须是：

```text
先完成 legacy → typed Bundle handoff
→ 再删除 legacy full-copy egress
```

Adapter 的 canonicalization 必须成为单向收口边界；Adapter 之后 production 只认识 typed Bundle，不再靠 dict 找回 canonical Evidence。

---

## 3.7 legacy assessment duplicate state 仍存在，但三类字段的当前角色不同

Phase 4B 已经冻结：

```text
Bundle exists
→ Bundle.assessment wins
```

因此以下字段都不再具有 production authority：

```text
document_evidence_assessment
active_assessment
ResearchState.assessment
```

但当前代码事实不能把三者都简单归类为“只服务旧 checkpoint”。

### `document_evidence_assessment`

当前仍是：

```text
current production lightweight projection
+
legacy checkpoint fallback
```

`DocumentAssembler` 仍读取其中 `details.quality_mode_degraded`。因此必须：

```text
先迁移 DocumentAssembler consumer
→ 直接读取 canonical Bundle.assessment
   或明确的最小 Application quality projection
→ 再删除 persisted document_evidence_assessment
```

### `active_assessment`

当前 Section graph / coordinator / replan 仍会写入并作为 `bundle_first_assessment_projection(...)` fallback 使用。

必须：

```text
先迁移 current typed reads
→ active_evidence_bundle.assessment
→ 再删除 persisted active_assessment
```

### `ResearchState.assessment`

当前新 production projection 已经显式写 `{}`，未发现真实 production read consumer。

因此：

```text
ResearchState.assessment
→ 可直接进入 removal（同步 schema / tests）
```

### 已冻结决策

Phase 4C **不承担 pre-4C 开发期历史 checkpoint backward compatibility**。

最终目标：

```text
新 4C checkpoint
→ typed canonical Bundle save/reload/resume/replan
```

执行原则仍是：

```text
有 current consumer
→ 先迁 consumer
→ 再删 persisted field

仅 compatibility field
→ 直接删 field / tests
```

不得机械删 Schema，也不得因为存在 current projection consumer 就继续保留第二 Evidence state。

---

## 3.8 canonical hash / lineage 仍存在 projection fallback

当前仍可找到：

```text
RAGContext.extra["evidence_contract_sha256"]
RAGContext.extra["evidence_contract"]["lineage"]
```

被部分路径用作：

```text
hash fallback
lineage fallback
replan fallback
generation report fallback
legacy coordinator fallback
```

### 4C 结论

正常 production：

```text
hash
← evidence_bundle_sha256(canonical Bundle)

lineage
← canonical Bundle.lineage

assessment
← canonical Bundle.assessment
```

不得再从 Context/normalized projection 恢复。

---

## 3.9 Phase 4 已冻结的 persisted field-retirement matrix 尚未全部落到 4C

Phase 4 §10.6 / §10.7 已经明确冻结：

```text
EvidenceBundle 已拥有的 Evidence fact
→ Application 不再长期独立持久化一份
```

因此 Phase 4C 不仅要删除三类显眼 full copy，还必须完成以下 persisted state 收口：

```text
document_rag_context
document_retrieved_chunks
document_citations          # 作为 RAG canonical copy role 退出
document_rag_output
document_evidence_assessment
document_evidence_available
document_assessment_status
all_retrieved_chunks         # 不再作为长期独立 Evidence state

SectionEvidence:
retrieval_scope
query
rag_context
retrieved_chunks
citations
generation_allowed
assessment-related copies
```

其中业务能力并不删除，而是改为：

```text
需要 document context/chunks/citations
→ 从 document_evidence_bundle 按需投影

需要 section final Evidence
→ 从 SectionEvidenceBundleSchema.evidence_bundle 按需投影

需要 aggregate chunks
→ document bundle + each final section bundle
→ on-demand aggregate / deduplicate

需要 document citation list
→ Application-owned citation mapping / final projection
```

### 4C 结论

Phase 4C 必须冻结最终 persisted schema，而不是只做到“这些字段不再是 authority”。

规则：

```text
Evidence-owned fact
→ REMOVE from long-lived duplicate persistence / derive on demand

Application-owned workflow/gate/citation state
→ KEEP

diagnostic history
→ KEEP lightweight only
```

`SectionEvidenceBundleSchema` 最终必须收敛为：

```text
section_id
section_title
evidence_bundle              # final accepted canonical Bundle
tool_call_ids
recovery_count
evidence_contract_sha256     # integrity ref
writer_gate_binding           # Application Gate state
metadata                      # workflow diagnostics only
```

`generation_allowed` 不再独立保存，其 Application owner 是 `WriterGateBindingSchema`。

---

## 3.10 rejected recovery candidate citation 当前在 acceptance 前进入 document registry

Phase 4B 已经保证：

```text
rejected candidate Bundle
→ cannot replace active Bundle
```

但当前 citation path 仍可能：

```text
candidate retrieval
→ registry register/remap
→ citation 已进入 document registry
→ Application candidate acceptance
→ candidate rejected
```

ResearchAgent gateway 和 parallel fan-in 也存在同类风险；parallel path 甚至会显式注册 diagnostic-only citation。

这会导致：

```text
rejected candidate citation
→ 污染 final document citation list
→ 或提前占用 document citation label
```

### 4C 结论

冻结 transactional citation promotion：

```text
candidate citation identity
→ candidate-local / temporary mapping
→ Application acceptance
→ accepted only then commit to document registry
```

rejected candidate：

```text
可以保留 trace/hash/count/source refs
不得进入 document registry
不得进入 final all_citations / final citation catalog
不得占用后续 accepted citation label
```

必须覆盖：

```text
direct recovery
ResearchAgent gateway recovery
parallel section fan-in
```

---

## 3.11 4C 是 checkpoint breaking change，必须显式升级 state version

当前：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION = 1
SECTION_EXECUTION_GRAPH_STATE_VERSION = 1
```

Phase 4C 会删除多个 persisted payload 字段，并且已冻结：

```text
pre-4C checkpoint backward compatibility
→ 不承担
```

因此不能继续让旧、新两套 schema 共用 version `1`。

### 4C 结论

同步升级：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION
1 → 2

SECTION_EXECUTION_GRAPH_STATE_VERSION
1 → 2
```

并冻结行为：

```text
v1 checkpoint
→ explicit unsupported-version rejection

v2 checkpoint
→ save/reload/resume/replan/recover_or_reuse
→ PASS
```

“不兼容旧 checkpoint”必须由明确版本边界表达，而不是依赖 Pydantic 偶然报错。

---

## 3.12 final output / SharedState compatibility output 仍可能复制 full canonical Bundle

即使删除：

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
ContextBundle.evidence
```

当前 `SectionEvidenceBundleSchema.evidence_bundle` 仍可能沿：

```text
payload.section_evidence
→ SchemeWriterOutput.section_evidence
→ SchemeWriterOutput.extra["section_evidence"] model_dump
→ enriched_rag_output["section_evidence"] model_dump
→ contexts.rag_tool_output
→ terminal AgentResult generic dict
```

产生多份 full Bundle serialization。

### 4C 结论

冻结 full Bundle persistence whitelist。

允许：

```text
document_evidence_bundle
    # document lifecycle canonical carrier

active_evidence_bundle
    # section lifecycle current canonical carrier

SectionEvidenceBundleSchema.evidence_bundle
    # section lifecycle completed final canonical slot

research invocation typed Bundle
    # transient typed carrier；不得落成 full-copy history
```

禁止：

```text
SchemeWriterOutput.extra full Bundle dump
contexts.rag_tool_output full Bundle dump
terminal AgentResult generic dict full Bundle dump
capture / observer full Bundle dump
normalized / metadata / diagnostic history full Bundle dump
```

最终输出如果需要 Evidence 信息，只允许明确 projection：

```text
bundle hash/ref
status
counts
lineage summary
tool/retrieval trace ids
Application citation mapping
```

---

## 3.13 `ContextBundle.evidence` 退休必须同步清理声明性 Graph Contract

`ContextBundle.evidence` 当前不仅存在于 Schema 和 writer，还挂在：

```text
GraphState write alias
GraphState read alias
workflow observer
scheme_generation workflow write_paths / failure_write_paths
top_level_business_graph graph_write_paths
production StageService write
```

### 4C 结论

整体退休必须同时满足：

```text
ContextBundle.evidence schema field = 0
set_evidence_context writer = 0
Graph read/write alias = 0
observer dependency = 0
workflow declared write path = 0
business node graph_write_path = 0
production writes = 0
```

否则即使 Schema 删除，Graph write contract 仍可能在运行时产生 undeclared path / stale contract failure。

---

# 4. 已冻结的用户决策

## Decision 1 — pre-4C checkpoint 不兼容

冻结：

```text
不要求 Phase 4C 新代码恢复 pre-4C 开发期历史 checkpoint。
```

原因：

这些 checkpoint 为开发/测试过程数据，没有真实生产恢复价值，不应长期绑架架构。

必须保证：

```text
4C breaking checkpoint schema
→ top-level state version 1 → 2
→ section state version 1 → 2

v1 checkpoint
→ explicit unsupported-version rejection

v2 canonical checkpoint
→ save
→ reload
→ resume
→ replan
→ recover_or_reuse
```

全部正确。

---

## Decision 2 — `ContextBundle.evidence` 整体退休

冻结：

```text
ContextBundle.evidence
→ REMOVE
```

不为了未知的 Multi-Agent V2 需求保留 duplicate Evidence shared state。

未来如果 Agent Contract 明确需要共享 Evidence projection，再重新设计。

---

## Decision 3 — `normalized["evidence"]` 退出正常 production

冻结：

```text
正常 production
→ 不生成 full normalized["evidence"] Bundle copy
→ 不消费 normalized["evidence"] 作为 Evidence source
```

Capture / Observability 直接从 typed canonical Bundle 或明确 summary projection 获取所需信息。

Fake/legacy test boundary 可以暂时保留旧 ToolResult 适配，但不得穿回 production。

---

# 5. Phase 4C 最终目标架构

## 5.1 RAG → Application

正常 typed production 唯一入口：

```text
RAGService.retrieve()
→ EvidenceBundleSchema
```

legacy 入口：

```text
legacy ToolResult / payload
→ LegacyEvidenceAdapter.upgrade(...)
→ EvidenceBundleSchema
→ same typed production path
```

不再允许：

```text
EvidenceBundle.model_dump()
→ RAGContext.extra full copy
→ normalized["evidence"] full copy
→ ContextBundle.evidence.contract
→ downstream model_validate()
```

---

## 5.2 Document lifecycle

```text
document_evidence_bundle
= document lifecycle canonical Evidence carrier
```

Document context/chunks/citations/assessment/hash/lineage 如果下游需要，必须从 canonical Bundle 按需获得：

```text
context/chunks/citations
← RAGEvidenceContractReader.projections(document_evidence_bundle)

assessment
← document_evidence_bundle.assessment

hash
← evidence_bundle_sha256(document_evidence_bundle)

lineage
← document_evidence_bundle.lineage
```

不再长期持久化第二套 document Evidence facts。

---

## 5.3 Section lifecycle

当前运行时 canonical authority：

```text
active_evidence_bundle
= currently accepted canonical Bundle
```

Section 完成后唯一允许保存的 final canonical slot：

```text
SectionEvidenceBundleSchema.evidence_bundle
= final accepted canonical Bundle
```

`SectionEvidenceBundleSchema` 最终持久化形状冻结为：

```text
section_id
section_title
evidence_bundle
tool_call_ids
recovery_count
evidence_contract_sha256
writer_gate_binding
metadata                  # workflow diagnostics only
```

以下字段退出 SectionEvidence 长期 persisted duplicate state：

```text
retrieval_scope
query
rag_context
retrieved_chunks
citations
generation_allowed
assessment-related copies
```

需要上述内容时从 `evidence_bundle` 或 Application-owned Gate/diagnostic state 按需 derive。

---

## 5.4 Citation lifecycle

Canonical Citation 只拥有稳定 source identity 和 Bundle-local rendering label；Application 只建立 document projection：

```text
canonical citation_ref
↓
candidate-local document label mapping
↓
Application acceptance
↓ accepted only
DocumentCitationRegistry commit
↓
document citation label
↓
prompt/document rendering
```

明确禁止：

```text
DocumentCitationRegistry
→ 修改 canonical Citation / EvidenceItem / Bundle.context
→ rebuild replacement Bundle
```

rejected candidate citation：

```text
trace/ref/count 可以保留
DocumentCitationRegistry commit = 0
final document citation catalog = 0
```

---

## 5.5 SharedState lifecycle

`ContextBundle.evidence` 整体退休后，SharedState 不再作为第二 Evidence owner。

同步退出：

```text
ContextBundleSchema.evidence
SharedStateWriter.set_evidence_context()
GraphState evidence_context write alias
GraphState evidence_contract / rag_context read aliases
workflow observer dependency
scheme_generation write_paths / failure_write_paths
TopLevelBusinessGraph graph_write_paths
StageService duplicate Evidence write
```

保留不受影响：

```text
UserContext
TaskContext
BusinessContext
GenerationContext
RuntimeContext
```

---

## 5.6 Full Bundle persistence whitelist

正常 production 只允许以下位置持有 full typed Bundle：

```text
1. document_evidence_bundle
   document lifecycle canonical carrier

2. active_evidence_bundle
   section lifecycle current canonical carrier

3. SectionEvidenceBundleSchema.evidence_bundle
   section lifecycle completed final canonical slot

4. research invocation typed evidence_bundle
   transient typed carrier；不得落入 full-copy history
```

禁止在以下位置再次序列化 full canonical Bundle：

```text
RAGContext.extra
normalized payload
SchemeWriterOutput.extra
contexts.rag_tool_output compatibility snapshot
terminal AgentResult generic dict
capture payload
observer payload
metadata / diagnostic history
all_rag_outputs / retrieval history
```

这些位置如果需要 Evidence 信息，只能保存明确 projection：

```text
hash/ref
status
counts
lineage summary
trace ids
tool_call_ids
Application citation mapping
```

---

## 5.7 Observability / Capture

```text
canonical Bundle
→ summary/hash/count/status/lineage projection
→ trace/capture
```

Capture 如业务确实需要 context/chunk/citation 快照，可以保存其 projection；但不得保存可被 production 重新解释为 canonical Bundle 的 full contract duplicate。

---

## 5.8 Checkpoint lifecycle

Phase 4C 的 persisted schema 是明确 breaking change。

版本冻结：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION = 2
SECTION_EXECUTION_GRAPH_STATE_VERSION = 2
```

行为：

```text
v1 checkpoint
→ explicit unsupported-version rejection

v2 top-level checkpoint
→ save/reload/resume

v2 section checkpoint
→ save/reload/resume/replan/recover_or_reuse
```

不通过“旧字段缺失/extra forbid”偶然表达版本不兼容。

---

# 6. 删除 / 保留矩阵

| 对象/路径 | 4C 结论 | 说明 |
|---|---|---|
| `EvidenceBundleSchema` | KEEP | 唯一 canonical Evidence authority |
| `document_evidence_bundle` | KEEP | document lifecycle canonical carrier |
| `active_evidence_bundle` | KEEP | section lifecycle current canonical carrier |
| `SectionEvidenceBundleSchema.evidence_bundle` | KEEP | section 完成后的 final accepted canonical slot |
| Research invocation typed `evidence_bundle` | KEEP transient | invocation typed carrier，不落 full-copy history |
| `RAGContext.extra["evidence_contract"]` | REMOVE | hidden full Bundle copy |
| `normalized["evidence"]` normal production | REMOVE | full serialized duplicate |
| `ContextBundle.evidence` | REMOVE | duplicate SharedState Evidence owner |
| `SharedStateWriter.set_evidence_context()` | REMOVE | 随 ContextBundle.evidence 退休 |
| Graph `evidence_context` write alias | REMOVE | stale duplicate-state alias |
| Graph `evidence_contract` / `rag_context` read aliases | REMOVE if no independent non-Evidence meaning | 不再从 SharedState 恢复 Evidence |
| workflow/BusinessNode `context_bundle.evidence` write paths | REMOVE | 声明性 Graph contract 同步收口 |
| ContextManager `RAGContext → Bundle` recovery | REMOVE | projection reconstruction |
| WriterGovernance `normalized["evidence"] → Bundle` fallback | REMOVE | production 必须显式传 typed Bundle |
| CitationRegistry `projection → Bundle rebuild` | REMOVE | Application 不得修改 canonical Bundle |
| CitationRegistry candidate citation pre-accept commit | REMOVE | accepted 后才 commit document registry |
| `document_rag_context` persisted Evidence role | REMOVE / derive | context 由 document Bundle 按需投影 |
| `document_retrieved_chunks` persisted Evidence role | REMOVE / derive | chunks 由 document Bundle 按需投影 |
| `document_citations` RAG canonical-copy role | REMOVE / replace by Application citation projection | final document citation capability 保留 |
| `document_rag_output` persisted Evidence role | REMOVE | 不再作为 production Evidence carrier |
| `document_evidence_assessment` | REMOVE after consumer migration | 先迁 DocumentAssembler quality consumer |
| `document_evidence_available` | REMOVE / derive | 从 Bundle.assessment / final section state derive |
| `document_assessment_status` | REMOVE / derive | 从 Bundle.assessment.status derive |
| `active_assessment` | REMOVE after consumer migration | graph/coordinator/replan 改读 active Bundle |
| `ResearchState.assessment` | REMOVE | 新 production writes 已为空，无真实 read consumer |
| context-owned Bundle hash fallback | REMOVE | hash 直接来自 typed Bundle |
| context-owned lineage fallback | REMOVE | lineage 直接来自 typed Bundle |
| `all_retrieved_chunks` long-lived Evidence state | REMOVE / derive on demand | assembler 从 document + final section Bundles 聚合去重 |
| `all_rag_outputs` | KEEP lightweight diagnostic only | 不保存 full Bundle / reconstructable canonical state |
| `retrieved_chunks` retrieval history | KEEP only if lightweight diagnostic requirement exists | 不得成为 canonical carrier |
| `SectionEvidence.retrieval_scope` | REMOVE persisted duplicate | 从 evidence_bundle.retrieval_scope derive |
| `SectionEvidence.query` | REMOVE persisted duplicate | 从 evidence_bundle.query derive |
| `SectionEvidence.rag_context` | REMOVE persisted duplicate | 从 evidence_bundle projection derive |
| `SectionEvidence.retrieved_chunks` | REMOVE persisted duplicate | 从 evidence_bundle projection derive |
| `SectionEvidence.citations` | REMOVE persisted duplicate | 从 evidence_bundle / Application citation projection derive |
| `SectionEvidence.generation_allowed` | REMOVE | owner 是 `WriterGateBindingSchema` |
| `SectionEvidence.writer_gate_binding` | KEEP | Application-owned Gate state |
| `SectionEvidence.evidence_contract_sha256` | KEEP | integrity ref，不是第二 authority |
| `SectionEvidence.tool_call_ids/recovery_count` | KEEP | Application workflow state |
| `LegacyEvidenceAdapter` | KEEP | 唯一 legacy → canonical ingress |
| Adapter canonical → hidden full copies | REMOVE | adapter 只返回 typed Bundle / common projections |
| rejected candidate retrieval history | KEEP lightweight diagnostic | 与 active authority 分离 |
| rejected diagnostic citation in final document citation list | REMOVE | 可保留 trace，不进入 document registry/catalog |
| `SchemeWriterOutput.extra` full Bundle serialization | REMOVE | 输出只保留 summary/ref/projection |
| `contexts.rag_tool_output` full Bundle serialization | REMOVE | compatibility output 不得复制 canonical Bundle |
| terminal `AgentResult` generic full Bundle serialization | REMOVE | terminal output 不得形成另一 full Evidence carrier |
| Capture / Observer full Bundle serialization | REMOVE | summary/projection only |
| top-level checkpoint state version | `1 → 2` | breaking schema boundary |
| section checkpoint state version | `1 → 2` | breaking schema boundary |

---

# 7. 实施顺序

Phase 4C 必须按依赖方向执行，不能从 Schema 机械删字段开始。

## Phase 4C-A — 先闭合 typed ingress，并断 production reverse consumers

### A1 Legacy ingress typed handoff

先把：

```text
LegacyEvidenceAdapter
legacy payload
→ EvidenceBundleSchema
```

做成真实接口契约，并让 Adapter 输出进入与 typed RAG 相同的 projection helper。

这一动作必须先于删除 legacy full-copy egress。

### A2 正常 production reverse consumer 清零

删除：

```text
ContextManager: RAGContext.extra → Bundle
WriterGovernance: normalized["evidence"] → Bundle
CitationRegistry: projection dict → replacement Bundle
```

同时：

```text
ContextManager 必须显式接 typed Bundle
WriterGovernance production 必须显式接 typed Bundle
```

验收：

```text
production projection/dict → EvidenceBundleSchema/RAGEvidenceContractSchema model_validate
= 0
```

唯一例外：`LegacyEvidenceAdapter` legacy ingress。

---

## Phase 4C-B — CitationRegistry 纯 projection + transactional promotion

重构 `DocumentCitationRegistry`：

```text
input:
Application citation projections + canonical citation_ref

output:
Application document label mapping / citation projection
```

禁止接收、修改、rebuild canonical Bundle。

candidate retrieval：

```text
candidate-local citation mapping
→ candidate acceptance
→ accepted only then commit registry
```

需要验证：

```text
canonical Bundle hash before citation remap
==
canonical Bundle hash after citation remap
```

并覆盖：

```text
direct recovery
ResearchAgent gateway recovery
parallel fan-in
```

rejected candidate citation 只能留在 diagnostic trace/ref/count，不能进入 registry 或 final citations。

---

## Phase 4C-C — 迁移 current duplicate-projection consumers

在删字段之前，先迁真实 production consumer。

### C1 assessment

```text
DocumentAssembler
  document_evidence_assessment.details
→ Bundle.assessment / minimal explicit quality projection

Section graph/coordinator/replan
  active_assessment fallback
→ active_evidence_bundle.assessment
```

### C2 hash / lineage

```text
context-owned hash fallback
→ evidence_bundle_sha256(Bundle)

context-owned lineage fallback
→ Bundle.lineage

GenerationReport legacy lineage fallback
→ typed Bundle lineage only in normal production
```

### C3 document/section projection consumer

凡需要 context/chunks/citations/query/scope：

```text
→ 从对应 typed Bundle 按需 projection/derive
```

不能把“当前 consumer 需要这个值”解释成“必须保留 persisted duplicate field”。

---

## Phase 4C-D — 停止 full-copy producer 与 full-copy fan-out

删除 typed production 中：

```text
RAGContext.extra["evidence_contract"] writer
normalized["evidence"] writer
ContextBundle.evidence.contract writer
```

同时清理：

```text
LegacyEvidenceAdapter full-copy egress
SchemeWriterOutput.extra full Bundle dump
contexts.rag_tool_output full Bundle dump
terminal AgentResult generic full Bundle dump
capture/observer full Bundle dump
metadata/history full Bundle dump
```

保留的 output/capture/trace 信息必须是 summary/ref/hash/count/status/lineage/citation projection。

---

## Phase 4C-E — SharedState duplicate retirement + Graph contract cleanup

整体删除：

```text
ContextBundle.evidence
SharedStateWriter.set_evidence_context()
```

同步删除：

```text
GraphState read/write aliases
workflow observer reads
scheme_generation write_paths
scheme_generation failure_write_paths
TopLevelBusinessGraph graph_write_paths
StageService writes
```

不得影响：

```text
UserContext
TaskContext
BusinessContext
GenerationContext
RuntimeContext
```

---

## Phase 4C-F — Persisted field retirement + checkpoint v2

在 C 阶段 consumer 已迁移后，删除/收口：

```text
document_rag_context persisted Evidence role
document_retrieved_chunks persisted Evidence role
document_citations RAG canonical-copy role
document_rag_output persisted Evidence role
document_evidence_assessment
document_evidence_available
document_assessment_status
active_assessment
ResearchState.assessment
all_retrieved_chunks long-lived Evidence state
SectionEvidence duplicate fields
```

`SectionEvidenceBundleSchema` 收敛到 §5.3 冻结形状。

与 breaking schema change 同步：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION: 1 → 2
SECTION_EXECUTION_GRAPH_STATE_VERSION: 1 → 2
```

并明确：

```text
v1 → unsupported version
v2 → supported
```

---

## Phase 4C-G — Fake / Legacy / diagnostic boundary confinement

FakeRAGService 和旧 ToolResult 测试如果还需要 `RAGToolOutputSchema`：

```text
允许：test/adapter boundary
```

但必须证明：

```text
旧 ToolResult
→ LegacyEvidenceAdapter
→ EvidenceBundleSchema
→ same typed production path
```

之后：

```text
normalized full Evidence copy
RAGContext.extra full Evidence copy
ContextBundle duplicate Evidence state
```

都不得重新进入 production。

`all_rag_outputs` / retrieval history 只允许 lightweight diagnostic projection。

---

# 8. Acceptance Criteria

Phase 4C 完成必须同时满足以下条件。

## AC-1 Canonical reconstruction

生产范围：

```text
backend/apps/**
backend/context_manager/**
backend/agent/runtime/**
```

除 `LegacyEvidenceAdapter` 的 legacy ingress 外：

```text
projection/dict
→ EvidenceBundleSchema.model_validate(...)
→ RAGEvidenceContractSchema.model_validate(...)
```

production violation = 0。

允许的 `model_validate()` 必须逐项证明输入是：

```text
legacy ingress
或 typed checkpoint codec 自身 schema restore
```

而不是 Application Evidence projection reconstruction。

---

## AC-2 Full Bundle persistence whitelist

正常 production 中 full typed Bundle 只允许存在于 §5.6 白名单：

```text
document_evidence_bundle
active_evidence_bundle
SectionEvidenceBundleSchema.evidence_bundle
research invocation transient evidence_bundle
```

以下 full Bundle serialization 必须为 0：

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
ContextBundle.evidence.contract
SchemeWriterOutput.extra full Bundle
contexts.rag_tool_output full Bundle
terminal AgentResult generic dict full Bundle
capture/observer full Bundle
metadata/history/all_rag_outputs full Bundle
```

不是只检查字段名，而要检查 `model_dump()` / nested section evidence 等可形成完整 Bundle 的路径。

---

## AC-3 Persisted duplicate field retirement

4C 新 schema 中以下 Evidence-owned persisted duplicate 必须退出或改成按需 derive：

```text
document_rag_context persisted Evidence role
document_retrieved_chunks persisted Evidence role
document_citations RAG canonical-copy role
document_rag_output persisted Evidence role
document_evidence_assessment
document_evidence_available
document_assessment_status
active_assessment
ResearchState.assessment
all_retrieved_chunks long-lived Evidence role
```

`SectionEvidenceBundleSchema` 不再独立持久化：

```text
retrieval_scope
query
rag_context
retrieved_chunks
citations
generation_allowed
assessment-related copies
```

并证明需要这些值的 current production consumer 已迁到 canonical Bundle / Application-owned projection，而不是功能被删掉。

---

## AC-4 Citation authority + acceptance boundary

```text
DocumentCitationRegistry modifies canonical Bundle = 0
DocumentCitationRegistry rebuilds Bundle = 0
```

Canonical hash 在 Application citation remap 前后完全一致。

同时：

```text
candidate citation commit
→ only after Application accepts candidate
```

必须证明 direct recovery、ResearchAgent gateway、parallel fan-in 三条路径中：

```text
rejected candidate citation
→ document registry = 0
→ final all_citations = 0
→ final citation catalog = 0
```

且 rejected candidate 不得提前占用后续 accepted citation label。

---

## AC-5 Assessment authority

正常 typed production：

```text
assessment ← Bundle.assessment
```

不得再：

```text
assessment ← active_assessment fallback
assessment ← document_evidence_assessment fallback
assessment ← normalized["evidence"]
assessment ← RAGContext.extra
```

`DocumentAssembler` 原 `quality_mode_degraded` 能力必须在迁移后继续正确工作。

---

## AC-6 Hash / lineage authority

正常 typed production：

```text
hash ← evidence_bundle_sha256(Bundle)
lineage ← Bundle.lineage
```

Context/normalized projection-owned fallback = 0。

Application-owned integrity ref，例如 `WriterGateBinding.evidence_contract_sha256`，允许保留，但其值必须从 typed Bundle 计算。

---

## AC-7 SharedState + Graph contracts

```text
ContextBundle.evidence = removed
SharedStateWriter.set_evidence_context = removed
```

并且以下 stale declaration 全部清零：

```text
Graph read/write aliases
workflow observer dependency
scheme_generation write_paths / failure_write_paths
top-level business node graph_write_paths
production StageService duplicate Evidence write
```

workflow save/reload/resume 不依赖 `ContextBundle.evidence`。

---

## AC-8 Legacy ingress

唯一兼容入口必须实际闭环：

```text
legacy ToolResult/payload
→ LegacyEvidenceAdapter
→ EvidenceBundleSchema
→ same typed production projection path
```

Adapter 之后：

```text
RAGContext.extra full Bundle = 0
normalized full Bundle = 0
```

不能只在 Adapter 内部临时构造 Bundle，随后仍靠 full dict 给下游找回。

---

## AC-9 Checkpoint v2

版本必须明确为：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION = 2
SECTION_EXECUTION_GRAPH_STATE_VERSION = 2
```

验证：

```text
v1 top-level checkpoint → explicit unsupported-version rejection
v1 section checkpoint   → explicit unsupported-version rejection

v2 top-level
→ JSON/checkpointer round-trip
→ new instance reload
→ resume

v2 section
→ round-trip
→ new instance reload
→ resume
→ replan
→ recover_or_reuse incomplete
→ recover_or_reuse completed
```

不要求恢复 pre-4C v1 数据，但必须明确拒绝，而不是随机 schema failure。

---

## AC-10 Observability / Capture / final output

Observer/Capture/final output 继续得到当前真正需要的：

```text
trace id
contract hash/ref
status
counts
lineage summary
context/citation/chunk projection（仅在其现有采集 contract 确实需要时）
Application citation mapping
```

不得通过 full Bundle duplicate 实现。

`SchemeWriterOutput`、SharedState compatibility output 和 terminal `AgentResult` 必须证明不再形成新的 canonical Bundle carrier。

---

## AC-11 Recovery acceptance regression

Phase 4B Final Fix 必须继续通过：

```text
current permitted + candidate blocked → retain current
current blocked + candidate permitted → accept candidate
current permitted + candidate permitted → accept candidate
current blocked + candidate blocked → preserve existing failed-candidate semantics
```

并继续保证：

```text
rejected candidate
→ cannot directly or indirectly block current accepted Section state
```

Phase 4C 不得破坏 active Bundle acceptance。

---

# 9. Verification Evidence

代码实施完成后必须提供以下证据。

## 9.1 Static architecture scans

至少扫描：

```text
"evidence_contract"
normalized["evidence"]
ContextBundle.evidence
set_evidence_context
"context_bundle.evidence"
active_assessment
document_evidence_assessment
document_evidence_available
document_assessment_status
document_rag_output
all_retrieved_chunks
all_rag_outputs
EvidenceBundleSchema.model_validate
RAGEvidenceContractSchema.model_validate
evidence_contract_sha256
SCHEME_GENERATION_GRAPH_STATE_VERSION
SECTION_EXECUTION_GRAPH_STATE_VERSION
SectionEvidenceBundleSchema
"section_evidence"
model_dump
rag_tool_output
```

必须逐项解释剩余命中为什么合法，尤其要分类：

```text
canonical carrier
Application projection
lightweight diagnostic
legacy/test ingress
checkpoint codec
forbidden full-copy
```

不能只给 grep 数量。

---

## 9.2 Focused tests

至少覆盖：

1. typed RAG → `document_evidence_bundle` canonical carrier。
2. legacy ToolResult → Adapter → typed Bundle → same typed projection path。
3. ContextManager 只显式消费 typed Bundle，不从 RAGContext recover。
4. WriterGovernance production 无 normalized dict → Bundle fallback。
5. CitationRegistry 不修改/不重建 canonical Bundle。
6. Citation remap 前后 canonical hash 完全一致。
7. direct recovery rejected candidate citation 不进入 registry/final citations。
8. ResearchAgent gateway rejected candidate citation 不进入 registry/final citations。
9. parallel fan-in diagnostic/rejected citation 不污染 final registry。
10. 后续 accepted citation label 不被 rejected candidate 提前占用。
11. typed normalized output 不含 full `evidence` copy。
12. `RAGContext.extra` 不含 full `evidence_contract`。
13. `ContextBundle.evidence` schema/write/read/alias/observer/write-contract 全部退出。
14. `document_evidence_assessment` consumer 迁移后 DocumentAssembler quality semantics 保持。
15. `active_assessment` 删除后 graph/coordinator/replan 只读 active Bundle assessment。
16. `ResearchState.assessment` 删除后 state projection 正常。
17. document context/chunks/citations/query/scope 从 Bundle 按需投影正确。
18. `all_retrieved_chunks` 删除长期 persisted role 后 assembly 聚合/去重结果一致。
19. `SectionEvidenceBundleSchema` 只保留 §5.3 冻结字段。
20. SectionEvidence 的 query/scope/context/chunks/citations 能从 final Bundle 正确 derive。
21. `generation_allowed` 不再在 SectionEvidence duplicate 保存，Gate 仍由 WriterGateBinding 表达。
22. final SchemeWriterOutput/extra 不复制 full Bundle。
23. SharedState `contexts.rag_tool_output` 不复制 full Bundle。
24. terminal AgentResult generic dict 不复制 full Bundle。
25. Capture/Observability 在没有 full copy 时仍能得到 hash/status/counts/lineage/projections。
26. top-level checkpoint v1 explicit reject。
27. section checkpoint v1 explicit reject。
28. top-level v2 checkpoint round-trip/new-instance resume。
29. section v2 checkpoint round-trip/new-instance resume。
30. section v2 replan。
31. section v2 `recover_or_reuse()` incomplete resume。
32. section v2 `recover_or_reuse()` completed reuse。
33. parallel section v2 checkpoint/fan-in。
34. Phase 4B recovery acceptance 四象限 regression。
35. rejected Gateway candidate 不间接污染 Section lifecycle regression。

---

## 9.3 Negative architecture tests

必须新增或更新负向测试锁死：

```text
Application production code
不得从 RAGContext.extra 恢复 Bundle
不得从 normalized["evidence"] 恢复 Bundle
不得在 CitationRegistry model_validate replacement Bundle
不得写 ContextBundle.evidence
不得声明 context_bundle.evidence Graph write/read path
不得在 SectionEvidence persisted duplicate query/scope/context/chunks/citations/generation_allowed
不得把 full Bundle dump 到 SchemeWriterOutput.extra
不得把 full Bundle dump 到 contexts.rag_tool_output
不得把 full Bundle dump 到 terminal AgentResult generic dict
不得把 full Bundle dump 到 observer/capture/history
不得让 rejected candidate citation 在 acceptance 前 commit document registry
```

并增加 full Bundle persistence whitelist 测试：

```text
full typed Bundle 只允许出现于：
document_evidence_bundle
active_evidence_bundle
SectionEvidenceBundleSchema.evidence_bundle
transient research invocation typed carrier
```

这些测试用于防止后续代码重新引入 compatibility authority / full-copy fan-out。

---

## 9.4 Checkpoint / recovery proof

必须单独提供：

```text
v1 top-level rejection evidence
v1 section rejection evidence
v2 top-level save/reload/resume evidence
v2 section save/reload/resume evidence
v2 replan evidence
v2 recover_or_reuse incomplete evidence
v2 recover_or_reuse completed evidence
parallel section checkpoint/fan-in evidence
```

不能只用一个 generic codec round-trip 代替真实 recovery path。

---

## 9.5 Broader regression

至少运行：

```text
Phase 3C Gate ownership
Phase 4B canonical carrier/final fix
section execution contracts
checkpoint/resume/replan/recover_or_reuse
section-agent mainline focused
parallel section focused
capture/observability focused
DocumentAssembler / citation binding focused
```

已知 Claim Audit / Document Hard Gate strict failure 如果仍存在，必须单独归因，不得为了绿测试恢复旧 Evidence authority、旧 RAG Gate 或 compatibility full copy。

---

## 9.6 Hygiene

```text
py_compile / equivalent syntax validation

git diff --check
```

必须 PASS；既有换行 warning 单独说明。

---

# 10. Non-goals

Phase 4C 明确不做：

```text
Phase 5 accepted_evidence_ids Citation/Chunk namespace
citation_id / citation_ref namespace redesign
Gate policy 重新设计
Claim Audit 规则修改
regex 清理
Document Hard Gate 松紧调整
Multi-Agent V2 实施
Evaluation/RAGAS
retrieval ranking/strategy redesign
EvidenceStore / database redesign
EvidenceBundle frozen/immutability model 重构
新微服务/API gateway
```

如果实施中遇到这些问题，只记录，不在 4C 越界解决。

---

# 11. 实施停止条件

Phase 4C 只有满足以下条件才可宣布关闭：

```text
Canonical Evidence authority          = EvidenceBundleSchema only
Projection → canonical recovery       = 0 (LegacyAdapter ingress excepted)
Full Bundle outside whitelist         = 0
Persisted duplicate Evidence fields   = retired / derived on demand
ContextBundle.evidence                = retired completely
Graph stale evidence contracts        = 0
CitationRegistry canonical rebuild    = 0
Rejected citation pre-accept commit   = 0
Assessment/hash/lineage fallback      = 0 in typed production
Legacy adapter typed handoff          = PASS
Top-level checkpoint state version    = 2
Section checkpoint state version      = 2
v1 explicit rejection                 = PASS
v2 save/reload/resume                 = PASS
v2 replan/recover_or_reuse            = PASS
Parallel fan-in citation/checkpoint   = PASS
Phase 4B recovery acceptance          = PASS
```

另外必须证明：

```text
SectionEvidenceBundleSchema
→ 已按 §5.3 收口

SchemeWriterOutput / SharedState compatibility output / terminal AgentResult
→ 不复制 full canonical Bundle
```

完成后必须停止，先进行独立 Phase 4C closure audit。

**不得自动进入 Phase 5。**

---

# 12. 实施建议的最小文件范围

以下是根据当前审计确认的主要目标文件；实施前仍需用静态扫描校验实际命中，不能把列表当成封闭事实。

```text
backend/apps/enterprise_document/services/scheme_writer/evidence_service.py
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py
backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
backend/apps/enterprise_document/services/scheme_writer/stage_service.py
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
backend/apps/enterprise_document/services/scheme_writer/document_assembler.py
backend/apps/enterprise_document/services/scheme_writer/capture_service.py
backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_ports.py
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/apps/enterprise_document/workflows/top_level_business_graph.py
backend/apps/enterprise_document/workflows/scheme_generation.py
backend/apps/enterprise_document/runtime/agent_rag_v1/mainline_recovery.py
backend/context_manager/policies.py
backend/agent/runtime/state_access.py
backend/agent/runtime/graph_state_ops.py
backend/agent/runtime/workflow_observer.py
backend/schemas/context.py
backend/apps/enterprise_document/schemas/scheme_generation_graph.py
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/apps/enterprise_document/schemas/scheme_writer/evidence.py
backend/apps/enterprise_document/schemas/scheme_writer/section_execution.py
backend/apps/enterprise_document/schemas/scheme_writer/document_assembly.py
backend/apps/enterprise_document/schemas/scheme_writer/output.py
backend/apps/enterprise_document/schemas/scheme_writer/state_projection.py
backend/observability/trace_summary.py
backend/observability/trace_reader.py
backend/rag/services/rag_service.py
backend/tools/fake_rag_tool.py
```

测试范围至少涉及：

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
backend/tests/test_rag_interface_v2_phase3c_gate_ownership.py
backend/tests/test_section_execution_contracts.py
backend/tests/test_agent_rag_v1_recovery_e2e.py
backend/tests/test_phase4_durable_resume_hitl.py
backend/tests/test_phase4_section_agents_mainline.py
backend/tests/test_scheme_writer_secondary_service_boundaries.py
backend/tests/test_step_12_evidence_contract.py
backend/tests/test_step_13_trace_v2.py
backend/tests/test_step_14_context_manager.py
backend/tests/test_decoupled_architecture.py
backend/tests/test_phase2_multi_agent_state_projection.py
parallel section / citation fan-in related tests
checkpoint recover_or_reuse related tests
capture/observability related tests
DocumentAssembler / citation binding related tests
```

实施时应新增独立的 Phase 4C architecture/compatibility-removal test file，而不是只改旧 Phase 4B 测试；Phase 4B 测试继续承担 regression 角色。

---

# 13. 最终方案结论

Phase 4C 不再讨论“哪份 projection 可以继续当 Evidence”。

最终原则已经冻结：

```text
EvidenceBundleSchema
= 唯一 canonical Evidence

Canonical
→ projection
= allowed

Projection
→ canonical
= forbidden

Legacy input
→ LegacyEvidenceAdapter
→ canonical
= only exception
```

Phase 4C 的工作不是再设计一套 Evidence 架构，而是把 Phase 4B 已经建立的 canonical 架构周围所有旧脚手架拆掉。

独立 Solution Audit 发现的 7 个确定性 blocker 已全部并入本方案：

```text
1. frozen field-retirement matrix
2. assessment current-consumer migration order
3. LegacyEvidenceAdapter typed ingress handoff
4. recovery citation transactional promotion
5. checkpoint state version 1 → 2
6. full Bundle persistence whitelist
7. expanded verification / graph-contract / recovery proof
```

这些修订没有改变已冻结的三项用户决策，也没有引入新的业务架构取舍。

当前状态：

```text
PHASE 4C AUDIT CLOSED
PHASE 4C SOLUTION REVISED AFTER AUDIT
7 AUDIT BLOCKERS INCORPORATED
SHORT SOLUTION RE-AUDIT PENDING
IMPLEMENTATION NOT STARTED
```
