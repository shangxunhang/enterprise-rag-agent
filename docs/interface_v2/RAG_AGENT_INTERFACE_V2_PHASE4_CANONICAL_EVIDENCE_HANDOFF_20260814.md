# RAG-Agent Interface V2 Phase 4 Canonical Evidence State 交接文档

> 日期：2026-08-14  
> 仓库：`D:\MyCode\rag-agent`  
> 分支：`checkpoint/controlled-architecture-20260728`  
> 当前性质：**Phase 4A 审计已完成；Phase 4B 设计已冻结，可进入生产代码实施**

---

# 1. 本轮目标

Phase 4 的目标不是新增 RAG 能力，也不是改 Gate，而是修复 Canonical Evidence State 的历史设计缺陷：

> **`EvidenceBundleSchema` 必须成为 RAG → Application 边界及后续主链中唯一的 Evidence Authority。**

任何其他对象只能作为：

- typed projection；
- workflow state；
- citation projection；
- integrity reference/hash；
- compatibility view。

不得再复制、修改、重建一套新的 canonical Evidence truth。

---

# 2. 已冻结的总架构原则

## 2.1 Evidence Authority

最终唯一 Evidence Authority：

```text
EvidenceBundleSchema
```

其语义是：

> **一次 RAG retrieval 完成后，由 RAG 侧形成并跨边界返回的最终 Evidence Envelope。**

内部字段可由 Retrieval、Rerank、Evidence Builder、Context Packer、Evidence Governance、Lineage/Trace 等多个 RAG 模块共同提供，但跨 RAG/Application 边界后，Application 不得重新定义 Evidence truth。

---

## 2.2 Phase 3 与 Phase 4 的边界

Phase 3 已解决：

> **谁有权决定能不能生成。**

当前冻结：

```text
RAG → Evidence Facts
Application → GateDecision / generation action
```

Phase 4 解决：

> **谁有权定义 Evidence facts。**

当前冻结：

```text
EvidenceBundleSchema = sole canonical Evidence authority
```

Phase 4 不得重新引入 RAG Gate authority。

---

# 3. Phase 4 当前发现的三个重复 / 冲突 Authority 问题

本轮已逐个沿真实生产链审计并完成原则冻结。

---

# 4. 问题一：`RAGContext.extra["evidence_contract"]`

## 4.1 真实根因

旧 Application/ContextManager 接口主要接受 `RAGContext`。

后来下游又需要完整 Evidence 信息，例如：

- selected evidence；
- citation relation；
- lineage；
- assessment；
- evidence hash。

为避免一次性修改旧接口，历史实现采用：

```python
rag_context.extra["evidence_contract"] = evidence_bundle.model_dump()
```

导致真实链路变成：

```text
EvidenceBundle
    ↓
RAGContext projection
    ↓
把完整 EvidenceBundle 再塞进 RAGContext.extra
    ↓
下游读取 extra
    ↓
model_validate
    ↓
重新恢复 EvidenceBundle
```

这不是正常 projection，而是隐藏 canonical carrier。

---

## 4.2 已确认下游影响

当前生产代码中存在：

- `DocumentCitationRegistry` 从 `RAGContext.extra["evidence_contract"]` 恢复完整 contract；
- `ContextManager` 从 `RAGContext.extra["evidence_contract"]` 恢复 EvidenceBundle；
- Section / Research workflow 从 `RAGContext.extra` 获取 `evidence_contract_sha256`；
- GenerationReport 经由 `RAGContext.extra["evidence_contract"]["lineage"]` 获取版本血缘。

因此不能直接删除该字段，必须先建立显式 canonical carrier。

---

## 4.3 冻结决策

> **`RAGContext` 只承担 Context projection，不再携带完整 `EvidenceBundle`。**

`RAGContext` 应只描述：

- `context_text`；
- used/matched chunk IDs；
- used doc IDs；
- context budget；
- truncation；
- context format；
- 与“给生成模型看的上下文”直接相关的信息。

不得再承担：

- 完整 EvidenceBundle；
- Evidence assessment authority；
- Evidence lineage authority；
- canonical Evidence hash carrier；
- Gate state。

---

## 4.4 目标消费方式

未来主链应显式传递 canonical `EvidenceBundleSchema`。

不同模块按职责消费：

```text
EvidenceBundle
    ├── context      → ContextManager / Prompt
    ├── citations    → citation projection
    ├── assessment   → Application EvidenceGatePolicy
    ├── lineage      → GenerationReport / audit
    ├── items        → Research / audit
    └── canonical hash → workflow/checkpoint integrity
```

需要 Context 的模块不应为了获取 Context 去接收/恢复完整 Bundle；需要 Evidence facts 的模块也不应从 `RAGContext.extra` 反向恢复。

---

# 5. 问题二：`ContextBundle.evidence.contract`

## 5.1 真实根因

历史 SharedState / ContextBundle 收口过程中，`EvidenceContextSchema` 被设计为统一 evidence state，并明确写过：

```text
contract 是结构化证据真相
```

当前字段包括：

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

在 `EvidenceBundleSchema` 后来被确立为唯一 Evidence Authority 后，二者形成 authority 冲突。

---

## 5.2 实际生产链审计结果

当前实际写入链：

```text
RAG
↓
EvidenceBundle
↓
EvidenceService.extract_rag_output
↓
RAGContext + chunks + citations + normalized["evidence"]
↓
DocumentCitationRegistry
↓
Application citation remap
↓
StageService
↓
SharedStateWriter.set_evidence_context()
↓
ContextBundle.evidence
```

注意：

> `ContextBundle.evidence.contract` 当前保存的甚至不是原始 RAG canonical Bundle，而是经过 Application CitationRegistry 改写后的 Bundle dump。

---

## 5.3 字段分类结果

| `ContextBundle.evidence` 字段 | 实际来源 | 当前生产消费 | 判定 |
|---|---|---|---|
| `query` | project input / EvidenceBundle.query | 未发现独立生产消费者 | Evidence fact 重复 |
| `contract` | 完整 Bundle dump | 兼容 alias / observer | **非法第二 authority** |
| `context_text` | RAGContext / Application remap projection | 兼容 `rag_context` alias | Context projection |
| `retrieved_chunks` | selected evidence projection | 未发现独立生产消费者 | 重复 projection |
| `citations` | Application remapped citations | 未发现独立生产消费者 | Application citation projection |
| `used_doc_ids` | RAGContext | 未发现独立生产消费者 | 重复 projection |
| `evidence_available` | EvidenceBundle.assessment | 未发现独立生产消费者 | 重复 Evidence fact |
| `assessment_status` | EvidenceBundle.assessment.status | 未发现独立生产消费者 | 重复 Evidence fact |
| `evidence_sufficient` | assessment status 二次压缩 | 未发现独立生产消费者 | 冗余且可能语义有损 |
| `metadata` | projection metadata | 未发现关键业务消费者 | 非 authority metadata |

特别注意：

```text
sufficient   → True
insufficient → False
其他         → None
```

会把例如 `conflicted` 与“未评估”压到同一个 `None`，说明复制 canonical assessment 形成局部简化状态会产生语义漂移。

---

## 5.4 冻结决策

> **`ContextBundle.evidence` 不再拥有 Evidence truth。**

当前没有发现 `EvidenceContextSchema` 中必须由 ContextBundle 独立维护的 Evidence 业务事实。

因此后续它必须：

- 降级为 projection / ref/hash；或
- 在 canonical carrier 完成后退休。

具体采用“极薄 projection”还是“完全退休”，Phase 4B 实施前根据主链 carrier 最终 wiring 决定；但 authority 结论不再讨论。

不得因为“读取方便”继续保存完整 Evidence snapshot。

---

# 6. 问题三：`DocumentCitationRegistry` 为什么重新造 Bundle

这是当前三个问题中最严重的一个。

---

## 6.1 `DocumentCitationRegistry` 真正的业务职责

它本身是必要组件。

真实业务中会发生多次独立 RAG 调用：

```text
Document Retrieval
↓
Section Retrieval
↓
Self-RAG Recovery
↓
Corrective Retrieval
↓
Parallel Section Retrieval
↓
Document Assembly
```

每次 RAG 调用内部都会从局部：

```text
C1 / C2 / C3 / ...
```

重新编号。

因此整篇文档可能出现多个不同来源都叫 `C1`。

Application 必须维护整篇文档唯一 citation 编号。

`DocumentCitationRegistry` 的合法职责是：

1. 跨 RAG 调用识别同一来源；
2. citation 去重；
3. 为最终文档分配稳定 `C1/C2/...`；
4. 维护 local citation → document citation mapping；
5. 支撑 Prompt / Writer / Word 中 citation marker 渲染；
6. 支撑 CitationBinding / 最终 citation list；
7. checkpoint/resume 时恢复文档级 citation 编号。

可以概括为：

> **Application-owned Document Citation Identity / Dedup / Numbering / Mapping。**

---

## 6.2 为什么当前实现被迫修改整个 EvidenceBundle

RAG Evidence Builder 当前明确把 citation 重编号为：

```text
契约局部 C1/C2/...
```

同时同一个 `citation_id` 被写入多个 canonical 字段：

```text
EvidenceBundle.citations[*].citation_id
EvidenceBundle.items[*].citation_ids
EvidenceBundle.context.context_text 中的 [C1]
```

Application 文档级 Registry 又把同一个 `citation_id` 当作整篇文档最终引用编号。

于是同一个字段同时承担：

```text
RAG-local citation identity
+
Application document citation identity
```

这两个 namespace 发生冲突。

例如：

```text
RAG Bundle:
Citation C1
Evidence E1 → citation_ids=[C1]
Context → "...[C1]"
```

Application 发现前面已有 C1~C8，因此本次局部 C1 必须成为文档 C9。

如果只改 Citation：

```text
Citation C1 → C9
Evidence E1 → C1      ❌
Context → [C1]         ❌
```

Bundle 内部立即不一致。

因此当前实现只能继续：

1. 改 `citations`；
2. 改 `EvidenceItem.citation_ids`；
3. 改 `context_text` 中 marker；
4. `model_validate()` 重新构造 Bundle；
5. 重算 Evidence contract hash。

因此：

> **不是业务要求 Application 修改 EvidenceBundle，而是当前 ID 模型迫使它修改。**

---

# 7. Citation Identity 最终冻结方案

本轮不采用“直接在 CitationSchema 再塞一个 Application `document_citation_id`”的方案。

原因：这仍会把 RAG Domain 与 Application Domain 的状态混到同一 canonical Schema 内。

采用最小边界修复方案：

> **Citation Identity 与 Citation Rendering Label 分离。**

---

## 7.1 三层语义

### A. Canonical Citation Identity

稳定身份，例如概念上：

```text
citation_ref
```

可复用当前 Registry 已经计算的稳定 source identity / `citation_identity_sha256` 作为实现基础，避免无必要的大范围 Schema 扩张。

它表达：

> 这条 citation 到底对应哪个真实来源。

身份依据包括当前已有：

- source document；
- file；
- parent/child/general chunk；
- table/row；
- page range；
- quote；
- title/section。

Canonical citation identity 不由 Application 重写。

---

### B. RAG Bundle Local Label

例如：

```text
C1
C2
C3
```

仅表示：

> 本次 EvidenceBundle 内，为 context rendering 使用的局部 citation label。

它不是跨 Bundle identity。

---

### C. Application Document Label

例如：

```text
C17
C18
```

由 `DocumentCitationRegistry` 分配，只属于最终文档。

用于：

- Prompt marker；
- Writer output；
- Word 文档；
- CitationBinding；
- document citation list。

---

## 7.2 目标链路

```text
RAG Canonical Evidence
======================
Citation identity = cite_A
local label       = C1
Evidence E1       → cite_A / local C1
Context           → ...[C1]

             ↓ Application projection

DocumentCitationRegistry
========================
cite_A → document C17

             ↓

Prompt / Writer / Word
======================
...[C17]
```

Canonical EvidenceBundle 保持不变。

---

## 7.3 跨多次 RAG 的去重

例如：

```text
Bundle A: local C1 → cite_A
Bundle B: local C2 → cite_A
Bundle C: local C1 → cite_A
```

Application 只依据稳定 identity：

```text
cite_A → document C7
```

最终三个位置都渲染为：

```text
[C7]
```

不需要修改任何 canonical Bundle。

---

## 7.4 `DocumentCitationRegistry` 后续应保留的职责

保留：

```text
✓ 来源 identity 计算/复用
✓ 跨检索 citation 去重
✓ document citation 编号
✓ local/canonical → document mapping
✓ Prompt marker 渲染
✓ Writer/Word marker 渲染
✓ CitationBinding 支撑
✓ checkpoint restore
```

退出：

```text
✗ 修改 EvidenceBundle.citations
✗ 修改 EvidenceItem.citation_ids
✗ 修改 canonical EvidenceBundle.context
✗ 重新 model_validate EvidenceBundle
✗ 因 document citation remap 重算 canonical Evidence hash
```

---

# 8. Canonical Evidence Hash 语义

Phase 4 后应冻结：

```text
evidence_contract_sha256
=
hash(canonical RAG EvidenceBundle)
```

该 hash 在 RAG canonical Bundle 创建 / legacy upgrade 完成后确定。

Application 文档 citation numbering 不得改变该 hash。

必须区分：

```text
Evidence identity
!=
Application document citation identity
!=
Prompt rendering identity
```

Application 已经拥有或可以继续使用自己的完整性身份，例如：

- prompt evidence contract hash；
- context hash；
- writer gate binding hash；
- citation mapping/hash。

不得拿 Application projection hash 反向替换 canonical Evidence hash。

---

# 9. 当前三个问题的最终冻结结论

## Freeze 1 — RAGContext

> `RAGContext` 只作为 EvidenceBundle 的生成上下文投影；禁止再携带完整 EvidenceBundle，也禁止下游从 `RAGContext.extra["evidence_contract"]` 反向恢复 canonical Bundle。

---

## Freeze 2 — ContextBundle.evidence

> `ContextBundle.evidence` 不再拥有 Evidence truth。当前字段没有发现独立 Evidence 业务事实，其内容属于 EvidenceBundle 重复事实、Application projection 或 compatibility snapshot；后续降级/退休。

---

## Freeze 3 — CitationRegistry

> RAG-local citation rendering 与 Application document citation rendering 必须分层；稳定 citation identity 与显示 label 分离。`DocumentCitationRegistry` 只建立映射和 Application projection，不再修改或重建 canonical EvidenceBundle。

---

# 10. Phase 4B 最终设计冻结

Phase 4B 的前置讨论已经完成。下面内容是实施约束，不再作为待讨论候选。

## 10.1 Canonical Bundle 生命周期规则

`EvidenceBundleSchema` 的业务语义不是“一个对象在 document → section → research 过程中不断被修改”，而是：

> **每一次独立 RAG invocation 产生一份新的 canonical `EvidenceBundleSchema`；Bundle 一旦跨越 RAG/Application 边界即视为 immutable。Workflow 可以切换 active Bundle，但不得修改旧 Bundle。**

真实生命周期模型：

```text
Document retrieval
    ↓
Bundle D

Section retrieval
    ↓
Bundle S1

Self-RAG / corrective retrieval
    ↓
Bundle S2 / S3
```

Section workflow 允许：

```text
active_bundle = Bundle D
        ↓ section retrieval accepted
active_bundle = Bundle S1
        ↓ recovery accepted
active_bundle = Bundle S2
```

但禁止：

```text
修改 Bundle D 变成 Bundle S1
修改 Bundle S1 变成 Bundle S2
```

未被最终采用的中间 retrieval 不需要再以完整 Bundle history 复制保存；历史追踪由：

- `retrieval_trace_id`；
- `tool_call_ids`；
- 既有 RAG trace / rag_run ledger；
- checkpoint workflow state；

承担。

---

## 10.2 Canonical Carrier 最终位置

### Document lifecycle

Top-level document graph 必须显式保存：

```python
document_evidence_bundle: EvidenceBundleSchema | None
```

它是 document-level retrieval 的 canonical result。

### Section lifecycle

Section graph 必须显式保存：

```python
active_evidence_bundle: EvidenceBundleSchema | None
```

Section 初始可指向 `document_evidence_bundle`；新的 section/recovery retrieval 被业务接受后，将 `active_evidence_bundle` 切换到新的 Bundle。

Section 生命周期结束后，只保存**最终 active canonical Bundle**，不额外复制所有历史 Bundle。

### Research lifecycle

`MainlineResearchRetrieval` 应显式 typed 携带：

```python
evidence_bundle: EvidenceBundleSchema | None
```

Research retrieval 产生的 Bundle 先作为本次 Research invocation 的 typed result；如果被 Section 接受，则成为 `section.active_evidence_bundle`。

`ResearchState` 不再额外保存完整 Bundle，只保存 Research 自己的 workflow / decision / ref / hash 状态。

---

## 10.3 Consumer 规则

保存 canonical Bundle 不代表所有 Service 都依赖完整 Bundle。

冻结：

```text
Workflow/State 层
    → 保存 canonical EvidenceBundle

业务 Service
    → 只消费职责所需字段 / typed projection
```

例如：

```text
EvidenceGatePolicy      ← bundle.assessment
ContextManager          ← bundle.context
DocumentCitationRegistry← bundle.citations / citation_ref
GenerationReport        ← bundle.lineage
Research/Audit          ← bundle.items / assessment
```

禁止为了消费单一字段，把完整 Bundle 再 dump 到另一个 state。

---

## 10.4 EvidenceBundle 新增字段冻结

Phase 4B 允许且只允许新增以下两个 provenance 字段：

```python
retrieval_scope: str = "document"
evidence_requirements: Optional[EvidenceRequirementsSchema] = None
```

### `retrieval_scope`

语义：这份 Bundle 对应的 RAG invocation 属于 `document / section / recovery / self_rag_recovery ...` 哪个检索范围。

来源链：

```text
RAGToolInputSchema.retrieval_scope
        ↓
RAG runtime
        ↓
final Bundle assembler / ResultMapper
        ↓
EvidenceBundle.retrieval_scope
```

Application 只读，不得回写。

### `evidence_requirements`

语义：

> **本次 RAG invocation 实际使用的 `EvidenceRequirementsSchema` immutable provenance snapshot。**

它不意味着 EvidenceBundle 取得 Evidence Requirements 的业务 authority。

业务 authority 仍来自 invocation request；Bundle 只是保存“本次检索/评估究竟针对哪套 requirements”。

禁止再重复增加：

```text
section_id
section_title
section_type
```

因为这些已经存在于 `evidence_requirements` 中。

当前 `EvidenceService` 上游如何生成 requirements 的质量问题不在 Phase 4 修复范围内。

---

## 10.5 Citation Identity 最终冻结

Phase 4B 采用三层模型：

```text
canonical citation_ref
    = 真正稳定来源身份

bundle-local citation_id = C1/C2/...
    = 本 Bundle 内 rendering label

Application document label = C17/C18/...
    = document-level rendering projection
```

现有 `DocumentCitationRegistry._identity()` 已使用 source document / file / chunk / table-row / page / quote / title / section 等来源事实计算 `canonical_sha256(payload)`。

实施原则：

> **复用/抽取现有 identity 算法，不重新发明第二套 source identity 算法。**

目标：

```python
CitationSchema.citation_ref
```

由 RAG canonical Citation producer 产生；Application 只读。

当前：

```python
CitationSchema.citation_id
RAGEvidenceItemSchema.citation_ids
```

Phase 4B 不做大规模 rename。它们的语义冻结为 **Bundle-local rendering label**。

因此：

```text
CitationSchema
├── citation_ref = stable canonical identity
└── citation_id  = C1  # local label

EvidenceItem.citation_ids = ["C1"]
Canonical Context = "...[C1]"
```

Application：

```text
local C1
  ↓ bundle citation lookup
citation_ref cite_A
  ↓ DocumentCitationRegistry
cite_A → document C17
  ↓
Prompt / Writer / Word render [C17]
```

`DocumentCitationRegistry` 不得再修改：

- canonical `CitationSchema.citation_id`；
- `EvidenceItem.citation_ids`；
- canonical `EvidenceBundle.context`；
- canonical Evidence hash。

---

## 10.6 Application State 字段分类冻结

统一判据：

```text
A. EvidenceBundle 已拥有的 Evidence fact
   → Application 不再独立保存一份。

B. Application 根据 Evidence 新产生的业务事实
   → 可以保存。

C. checkpoint / integrity / trace 身份
   → 可以保存 ref/hash/id，但不得形成第二 Evidence authority。
```

### 目标删除 / 改为按需 derive 的 Evidence 重复状态

```text
document_rag_context
document_retrieved_chunks
document_citations              # 作为 RAG canonical copy 退出；document citation projection 另算
document_rag_output             # production authority 退出
document_evidence_assessment
document_evidence_available
document_assessment_status
RAGContext.extra["evidence_contract"]
ContextBundle.evidence.contract
ContextBundle.evidence 中重复 Evidence facts
section 内重复的 query / retrieval_scope / rag_context / retrieved_chunks / citations / assessment
all_rag_outputs                 # 完整 normalized shadow copies 退出 production state
```

### 必须保留的 Application-owned state / projection

```text
Document citation mapping / document citation list
tool_call_ids
recovery_count
research_rounds
Research status / recommended_action
WriterGateBinding
evidence_contract_sha256
checkpoint/ref
Application prompt/context/gate/citation mapping hash
```

### `all_retrieved_chunks`

当前确有业务消费者：`DocumentAssembler` 用它建立 `known_chunk_ids` 并做 binding/source integrity。

Phase 4B 目标不是丢掉这个能力，而是：

> **不再作为长期独立 Evidence state 保存；DocumentAssembler 需要时，从 `document_evidence_bundle` + 各 section final active Bundle 按需投影、聚合、去重。**

### `all_citations`

当前已经承担 document-level citation projection，因此其**业务能力保留**，但不能继续表现为“Application 改写后的 canonical RAG Citation”。

目标语义应转为：

```text
citation_ref → document_label
```

以及最终 document citation list / CitationBinding 支撑。

---

## 10.7 `SectionEvidenceBundleSchema` 目标职责

当前 `SectionEvidenceBundleSchema` 混合了 Evidence facts 与 Application workflow state。

Phase 4B 不要求大规模重命名，可最小化调整为“Section Evidence Workflow State”，核心形状语义上为：

```text
section_id                       KEEP
section_title                    KEEP
evidence_bundle                  ADD canonical typed Bundle
tool_call_ids                    KEEP Application workflow
recovery_count                   KEEP Application workflow
evidence_contract_sha256         KEEP integrity ref
writer_gate_binding              KEEP Application Gate state
metadata                         KEEP only section workflow diagnostics
```

以下字段不再独立保存，由 `evidence_bundle` 读取：

```text
retrieval_scope
query
rag_context
retrieved_chunks
citations
assessment-related copies
```

`generation_allowed` 不再作为 SectionEvidence 的独立 duplicate state；其 canonical Application owner 是 `WriterGateBindingSchema`。

`metadata` 中禁止再次复制：

-完整 assessment；
- `writer_gate_binding`；
- citation identity map；
- 完整 Evidence contract；
- 任何已有 typed owner 的正式业务事实。

---

## 10.8 ResearchState 分类冻结

`ResearchState` 保留：

```text
status
research_rounds
tool_call_ids
recommended_action
evidence_package_ref
evidence_contract_sha256
```

`assessment` 不再作为第二 Evidence assessment authority；需要时读取 active canonical Bundle。

`accepted_evidence_ids / rejected_evidence_ids` 当前存在 Citation ID / Chunk ID namespace 混用，明确留到 Phase 5，不在 Phase 4B 顺手修复。

`evidence_gaps`：

- 如果只是 `EvidenceBundle.assessment` 的机械复制，应退出；
- 如果后续明确表达 Research Agent 自己的新业务判断，才能作为 Application/Agent projection 保留。

Phase 4B 不扩展该问题。

---

## 10.9 Canonical Hash 冻结

```text
evidence_contract_sha256
=
hash(final canonical EvidenceBundle)
```

计算时点：

1. RAG final Bundle assembly 完成；或
2. LegacyEvidenceAdapter 将 legacy result 升级为 canonical Bundle 完成；
3. 在任何 Application citation/document projection 之前。

Application document citation numbering、Prompt marker remap、Writer/Word rendering 均不得改变此 hash。

---

# 11. 推荐 Phase 4 实施顺序

## Phase 4A — Audit / Freeze

状态：**已完成**。

已完成：

- Canonical Evidence recovery audit；
- Evidence Authority Field Matrix 初步审计；
- 三个重复 authority 问题业务链审计；
- RAGContext authority freeze；
- ContextBundle.evidence authority freeze；
- Citation identity / rendering boundary freeze。

相关文档：

- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4A_CANONICAL_EVIDENCE_RECOVERY_AUDIT_20260814.md`
- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_EVIDENCE_AUTHORITY_FIELD_MATRIX_AUDIT_20260814.md`
- 本交接文档。

---

## Phase 4B — Typed Canonical Evidence Carrier

状态：**设计已冻结，可以实施。**

### Phase 4B-1：Schema 最小变更

优先修改：

- `backend/schemas/citation.py`
- `backend/schemas/rag.py`
- `backend/rag/evidence/contract.py`
- `backend/rag/mapping/result_mapper.py`

实施：

1. `CitationSchema` 增加 stable `citation_ref`；
2. 抽取/复用现有 `DocumentCitationRegistry._identity()` 的 source identity 算法，使 canonical Citation producer 在 RAG 侧生成 `citation_ref`；
3. `citation_id` 继续作为 Bundle-local `C1/C2/...` label；
4. `EvidenceBundleSchema` 增加：
   - `retrieval_scope`；
   - `evidence_requirements`；
5. ResultMapper / final Bundle assembler 显式写入 request 对应的：
   - task/run/retrieval trace；
   - retrieval_scope；
   - evidence_requirements；
6. Bundle 跨边界以后不再被 Application 修改。

不要在本步骤 rename `citation_ids`，不要进入 ID Namespace Closure。

### Phase 4B-2：Document canonical state

主要涉及：

- `backend/apps/enterprise_document/schemas/scheme_generation_graph.py`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`
- 必要的 top-level graph contract / codec / checkpoint wiring

实施：

1. Top-level graph payload 增加 typed：

```python
document_evidence_bundle: EvidenceBundleSchema | None
```

2. `retrieve_document_evidence()` 直接保存 RAG 返回的 canonical Bundle；
3. document-level Context/chunks/citations/assessment 需要时从 Bundle 投影；
4. 本步骤允许旧字段暂时并存用于迁移，但**新的 production control path 必须优先读取 typed Bundle**；
5. checkpoint serialization / restore 必须能够 round-trip typed Bundle。

### Phase 4B-3：Section canonical state

主要涉及：

- `backend/apps/enterprise_document/schemas/section_execution_graph.py`
- `backend/apps/enterprise_document/schemas/scheme_writer/section_execution.py`
- `backend/apps/enterprise_document/schemas/scheme_writer/evidence.py`
- `backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py`
- `backend/apps/enterprise_document/workflows/section_execution_graph.py`

实施：

1. Section graph 增加 typed：

```python
active_evidence_bundle: EvidenceBundleSchema | None
```

2. 初始化：

```text
active_evidence_bundle = document_evidence_bundle
```

3. 每次 section/self-rag/recovery retrieval 产生新的 immutable Bundle；
4. 只有业务逻辑接受新 retrieval 时才切换 `active_evidence_bundle`；
5. recovery 被拒绝时 retained active Bundle 不变；
6. Section lifecycle 完成时，将 final active Bundle 保存进 section evidence/result；
7. 不保存完整 Bundle history；历史仅保留 trace/tool-call/workflow 信息。

### Phase 4B-4：Research typed result

主要涉及：

- `backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_ports.py`
- `section_agent_adapter.py`
- `section_agent_reasoners.py`
- Section workflow integration

实施：

1. `MainlineResearchRetrieval` 增加：

```python
evidence_bundle: EvidenceBundleSchema | None
```

2. Research tool result 不再从 `normalized["evidence"]` 重新解释 canonical Evidence；
3. assessment / context / citations / items 直接由 typed Bundle 投影；
4. Research Bundle 被 Section 接受后切换 `section.active_evidence_bundle`；
5. `ResearchState` 不保存完整 Bundle 副本。

### Phase 4B-5：按字段消费迁移

逐个迁移下游：

```text
Gate              → bundle.assessment
ContextManager    → bundle.context
GenerationReport  → bundle.lineage
CitationRegistry  → bundle.citations / citation_ref
Research          → bundle.items / assessment
DocumentAssembler → document bundle + section final bundles 的按需 chunk projection
```

要求：

- Service 尽量接收最小 typed field/projection；
- 不要为了方便让所有 Service 都接收完整 Bundle；
- 但禁止从 projection 反向重建 Bundle。

### Phase 4B-6：Compatibility coexistence

本阶段允许旧路径短暂存在，但必须满足：

```text
新 typed Bundle path = production authority
旧 dict/extra path   = compatibility only
```

不得出现：

```text
新 carrier + 旧 carrier 同时作为 authority
```

真正删除旧字段安排在 Phase 4C。

### Phase 4B 明确不做

- 不删除所有 compatibility 字段；
- 不大规模 rename Citation schema；
- 不修 accepted_evidence_ids namespace；
- 不改 Gate policy；
- 不改 claim classification / regex；
- 不引入新数据库或 EvidenceStore；
- 不保存完整 Bundle history；
- 不进入 Multi-Agent V2 / Memory / Evaluation / RAG V2。

---

## Phase 4C — Remove Illegal Recovery / Rebuild

Typed path 验证通过后再：

1. 删除 `RAGContext.extra["evidence_contract"]` 完整 Bundle dump；
2. 删除 ContextManager 反向 `model_validate` 恢复；
3. 让 `DocumentCitationRegistry` 停止重建 Bundle；
4. 降级/退休 `ContextBundle.evidence.contract`；
5. 将 production control path 从 `normalized["evidence"]` 迁到 typed Bundle；
6. 将 GenerationReport lineage 改成显式 canonical lineage consumption；
7. 将 evidence hash 从 RAGContext.extra 迁到 canonical package/ref。

---

## Phase 4D — Verification

至少验证：

1. top-level checkpoint JSON serialization / restore；
2. section checkpoint/resume；
3. durable cross-instance resume；
4. CitationRegistry projection restore/remap；
5. ContextManager prompt context 行为不退化；
6. LegacyEvidenceAdapter 能升级旧结果到 canonical Bundle；
7. document citation remap 前后 canonical Evidence hash 不变；
8. Application 不再重建 EvidenceBundle；
9. 不存在从 projection 反向恢复 canonical Bundle；
10. Phase 1–3 Gate semantics 不变。

---

# 12. Phase 4 明确 Non-goals

本轮禁止扩张到：

- Phase 5 ToolGateway / ID Namespace Closure 中的其他 ID 问题；
- accepted_evidence_ids 的 Citation ID / Chunk ID 混用修复；
- Phase 6 Application → RAG internal import cleanup；
- Phase 7 facade / `__getattr__`；
- Gate 规则重构；
- regex / claim classification 清理；
- Multi-Agent V2；
- Durable Memory；
- ContextCompiler；
- Evaluation / RAGAS；
- RAG V2 retrieval strategy；
- 模型替换；
- 部署；
- MongoDB / EvidenceStore / ArtifactStore / 新 DB；
- 任何新持久化技术。

现有 Pydantic → JSON → LangGraph checkpoint 能力足以承载 typed `EvidenceBundleSchema`。

---

# 13. 特别注意：Research ID 问题不要混入本轮

当前 Research 链还存在：

```text
accepted_evidence_ids
```

有时承载 Citation ID，有时承载 Chunk ID。

这是后续 Phase 5 ID Namespace Closure 的问题。

Phase 4 不顺手修复。

未来目标可考虑：

```text
accepted_evidence_ids
← EvidenceBundle.selected_evidence_ids
```

但本轮只保证 canonical Evidence carrier 和 authority 正确。

---

# 14. 当前代码状态

本轮交接文档生成前，没有修改生产代码。

已知工作树此前包含未跟踪审计文档：

```text
docs/MULTI_AGENT_V2_CURRENT_STATE_GAP_AUDIT_20260813.md
docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4A_CANONICAL_EVIDENCE_RECOVERY_AUDIT_20260814.md
docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_EVIDENCE_AUTHORITY_FIELD_MATRIX_AUDIT_20260814.md
```

本交接新增：

```text
docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_CANONICAL_EVIDENCE_HANDOFF_20260814.md
```

不要自动 stage / commit。

---

# 15. 新窗口推荐起始指令

可以直接把下面这段给新窗口：

```text
继续执行 `D:\MyCode\rag-agent` 的 Interface V2 Phase 4B。

先阅读并以最新交接为实施 Source of Truth：
1. docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_CANONICAL_EVIDENCE_HANDOFF_20260814.md
2. docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4A_CANONICAL_EVIDENCE_RECOVERY_AUDIT_20260814.md
3. docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_EVIDENCE_AUTHORITY_FIELD_MATRIX_AUDIT_20260814.md

Phase 4B 设计已经冻结，可以直接实施，不要重新设计。

严格按交接文档第 10、11 节执行：

1. Schema 最小变更：
   - Citation stable citation_ref；
   - citation_id 保持 Bundle-local label；
   - EvidenceBundle 增加 retrieval_scope / evidence_requirements。

2. Document typed canonical state：
   - document_evidence_bundle。

3. Section typed canonical state：
   - active_evidence_bundle；
   - 每次 RAG invocation 新建 immutable Bundle；
   - 只切换 active，不修改旧 Bundle；
   - 生命周期结束保存 final active Bundle；
   - 不保存完整 Bundle history。

4. Research typed result：
   - MainlineResearchRetrieval.evidence_bundle；
   - accepted 后进入 section.active_evidence_bundle；
   - ResearchState 不复制完整 Bundle。

5. 下游按字段需求消费：
   - Gate → assessment；
   - ContextManager → context；
   - Report → lineage；
   - CitationRegistry → citations/citation_ref；
   - DocumentAssembler 的 chunks 从 canonical bundles 按需投影。

6. Phase 4B 允许 compatibility coexistence，但 typed Bundle path 必须成为 production authority。
   Phase 4C 再删除旧 recovery/shadow paths。

架构不变量：
- EvidenceBundleSchema = sole canonical Evidence authority；
- Application 不复制完整 Evidence truth；
- Application 不修改 canonical Bundle；
- projection 不得反向 model_validate/rebuild canonical Bundle；
- RAGContext = context projection only；
- ContextBundle.evidence 不再拥有 Evidence truth；
- Citation identity ≠ RAG local label ≠ document rendering label；
- DocumentCitationRegistry 只负责 document mapping/projection；
- evidence_contract_sha256 = hash(final canonical Bundle)，document citation remap 不得改变；
- Phase 1–3 Gate semantics 不变。

Non-goals：
- 不修 accepted_evidence_ids Citation/Chunk ID 混用；
- 不进入 Phase 5/6/7；
- 不改 Gate/regex/claim classification；
- 不做 Multi-Agent/Memory/Evaluation/RAG V2/deployment/new storage；
- 不自动 stage/commit。

实施完成后必须汇报：
- 修改文件清单；
- canonical Bundle producer/carrier/consumer 链；
- 仍保留的 compatibility paths；
- 是否存在任何 projection → Bundle reconstruction；
- 是否存在 Application post-boundary Evidence mutation；
- checkpoint round-trip 结果；
- focused tests / regression tests / py_compile / git diff --check 结果。
```

---

# 16. 一句话交接

> **Phase 4A 审计与设计冻结已经完成。Phase 4B 现在可以直接实施：每次 RAG invocation 产生 immutable canonical `EvidenceBundleSchema`；document lifecycle 保存 `document_evidence_bundle`，section lifecycle 保存 final `active_evidence_bundle`，Research 只临时 typed 携带 Bundle；Application 其他位置不得复制或重建 Evidence truth，只能按字段需求消费并保存自己真正产生的 workflow / Gate / citation projection / integrity state。**
