# RAG–Agent Interface V2 Phase 4A Canonical Evidence Recovery Path Audit

日期：2026-08-14  
阶段：Phase 4A — Canonical Evidence Recovery Path Audit / Freeze  
代码基线：`f43f959 checkpoint: complete rag-agent interface v2 phases 1-3`  
审计性质：只审计，不修改生产代码  

---

# 1. Executive Summary

Phase 1–3 已关闭，Phase 4 可以正式进入。本轮审计确认：**Phase 4 的真实问题不是单独删除 `RAGContext.extra["evidence_contract"]`，而是当前同一 EvidenceBundle 在 Application / SharedState / Checkpoint / Research / Section 中被复制、恢复并再次作为事实源使用。**

核心结论：

1. `EvidenceBundleSchema` 已是冻结的唯一 canonical EvidencePackage，定义和校验均已具备，Phase 4 不需要设计新 Evidence Schema。
2. `SchemeEvidenceService.extract_rag_output()` 是当前主要 duplicate-authority 扇出点：它从 typed `EvidenceBundleSchema` 生成 Context/Chunk/Citation projection，同时把完整 Bundle dump 写入 `RAGContext.extra["evidence_contract"]` 和 `normalized["evidence"]`。
3. `DocumentCitationRegistry.remap_bundle()` 当前从 `RAGContext.extra` 恢复完整 Bundle，修改 Citation、Evidence item citation_ids、Context 和 extra，再 `model_validate()` 生成新的 Bundle。这是确认的非法 canonical recovery/rewrite path。
4. `SectionGenerationContextPolicy` 当前从 `RAGContext.extra["evidence_contract"]` 恢复 Bundle，并据此读取 selected Evidence 与 lineage。这是确认的非法 canonical recovery consumer。
5. `EvidenceContextSchema.contract` 当前文档语义仍明确声明为“结构化证据真相”；`SharedStateWriter.set_evidence_context()` 将完整 Bundle dict 写入该字段。它与冻结的 `EvidenceBundleSchema = 唯一 canonical EvidencePackage` 冲突，是第二 authority。
6. Durable checkpoint 当前会同时持久化多个 Evidence full-copy / projection：`document_rag_context`、`document_rag_output`、`all_rag_outputs`、`shared_state_payload.context_bundle.evidence.contract`、Section `rag_outputs`、Section `evidence.rag_context` 等。Checkpoint 本身没有坏，但 Evidence authority 在 checkpoint 内重复。
7. `MainlineResearchRetrieval` 当前没有 typed `EvidenceBundleSchema` 字段；Research 只能消费 `normalized/assessment/hash` 等 projection。
8. `ResearchResultPayload.evidence_package_ref` 当前由 `state://section-evidence/...` 合成，但仓库内没有对应 resolver/load path；它是逻辑标签，不是可恢复的 package reference。
9. `evidence_contract_sha256` 当前会在 `DocumentCitationRegistry` 改写 Citation ID 后重新计算，因此 Application citation remap 会改变 canonical Evidence hash。该语义必须在 Phase 4 冻结：canonical Evidence hash 应绑定 canonical EvidenceBundle，不应因 Application 的文档级 Citation 编号变化而改变。
10. `LegacyEvidenceAdapter` 从旧 ToolResult / Chunk / Citation 升级为 canonical EvidenceBundle 属于允许的集中 compatibility exception；但升级后再次把完整 Bundle 塞入 `RAGContext.extra` 不应继续保留。
11. Phase 4 不需要新增数据库、ArtifactStore、Durable Memory 或新 EvidencePackage Schema。现有 LangGraph/Pydantic checkpoint 已能直接序列化 typed `EvidenceBundleSchema`，可以作为本轮 canonical persistence 载体。

最终判断：

```text
PHASE 4A AUDIT: PASS
PHASE 4 IMPLEMENTATION PRECONDITIONS: SATISFIED
PHASE 4B: READY AFTER HUMAN FREEZE
```

---

# 2. Frozen Authority

Phase 4 必须继续遵守前三阶段已经冻结的结论：

```text
EvidenceBundleSchema = 唯一 canonical EvidencePackage
RAGContext            = deterministic projection
RetrievedChunk        = projection / compatibility
Citation              = projection / binding identity
ResearchEvidencePackage = Agent compatibility projection
SectionEvidenceBundle   = Application compatibility projection
```

合法方向：

```text
EvidenceBundle
    -> RAGContext
    -> RetrievedChunk
    -> Citation
    -> Research summary
    -> Section view
```

非法方向：

```text
RAGContext / Section / Research / arbitrary dict
    -> rebuild / rewrite canonical Evidence truth
```

Legacy upgrade 只允许集中在 `LegacyEvidenceAdapter`，且属于 compatibility exception，不代表 compatibility object 获得 canonical authority。

---

# 3. Current Canonical Producer

## 3.1 正常生产路径

当前正常 RAG 路径：

```text
RAGResultMapper
    -> RAGEvidenceContractBuilder.build(...)
    -> EvidenceBundleSchema
    -> RAGServicePort.retrieve(...)
```

`EvidenceBundleSchema` 自身已经校验：

- Evidence ID 唯一；
- selected/dropped 互斥；
- Evidence ID 必须存在；
- disposition 与 selected/dropped 一致；
- Citation ID 唯一；
- Evidence citation refs 有效；
- dropped Evidence 不暴露 Citation；
- Context 字符/token预算一致；
- Context item 数与 selected Evidence 数一致；
- Assessment count 从 canonical facts 重算。

因此 Phase 4 不需要重新设计 EvidenceBundle。

## 3.2 Legacy compatibility producer

`LegacyEvidenceAdapter` 存在两条兼容升级：

1. legacy payload 已有 `payload["evidence"]`：校验为 `RAGEvidenceContractSchema`；
2. legacy payload 只有 Chunk/Citation：通过 `RAGEvidenceContractBuilder` 构造 canonical Bundle。

该行为允许保留，原因是它是集中 compatibility boundary。

但其输出后的 canonical propagation 必须与正常 RAG path 使用同一 Phase 4 authority 规则。

---

# 4. Current Duplicate / Recovery Path Map

分类：

```text
A = canonical producer
B = legitimate projection
C = hash/ref/lineage / observability
D = illegal canonical recovery/rewrite
E = legacy compatibility
```

| Path / Object | 当前行为 | 分类 | Phase 4 判断 |
|---|---|---:|---|
| `RAGResultMapper -> EvidenceBundleSchema` | 正常 RAG canonical producer | A | 保留 |
| `LegacyEvidenceAdapter -> EvidenceBundleSchema` | legacy 升级 | E/A | 保留集中兼容 |
| `RAGEvidenceContractReader.projections()` | Bundle -> Context/Chunk/Citation | B | 保留 |
| `SchemeEvidenceService.extract_rag_output -> context.extra["evidence_contract"]` | 完整 Bundle dump 写入 projection extra | D producer | 移除 |
| `SchemeEvidenceService.extract_rag_output -> normalized["evidence"]` | 完整 Bundle serialized copy | compatibility / currently authority consumer input | 迁移期可保留；生产决策迁离后降级为 E/B |
| `DocumentCitationRegistry -> context.extra.get("evidence_contract")` | 从 projection 恢复 Bundle | D consumer | 移除 |
| `DocumentCitationRegistry -> model_validate(contract_payload)` | 修改 Citation/items/context 后生成新 Bundle | D rewrite | 移除 canonical rewrite |
| `ContextManager._evidence_contract()` | 从 RAGContext.extra 恢复 Bundle | D consumer | 改为显式消费 canonical Bundle / projection input |
| `EvidenceContextSchema.contract` | 文档语义声明为“结构化证据真相” | D authority | 降级 compatibility；不得继续作为 truth |
| `SharedStateWriter.set_evidence_context -> evidence.contract` | 写完整 Bundle dict | D producer | 停止作为 canonical storage |
| `normalized["context"] / chunks / citations` | compatibility projection | B/E | 可保留 |
| `evidence_contract_sha256` | lineage/integrity | C | 保留，但 hash owner 需冻结 |
| `SectionEvidenceBundleSchema.rag_context/chunks/citations` | Section projection | B | 保留；不得反向恢复 Bundle |
| `ResearchEvidencePackageSchema` | summary / decision | B | 保留 projection；Evidence content 不得成为 truth |
| `ResearchResultPayload.evidence_package_ref` | 当前仅逻辑标签 | C-like but unresolved | 不得声称可恢复 package ref |
| `contexts["rag_tool_output"]` | final legacy compatibility output | E | 可保留；不得成为生产 authority |
| `all_rag_outputs / rag_outputs` | capture/observability compatibility snapshots | E/B | 可暂留；生产决策迁离后不得反向成为 authority |

---

# 5. Main Duplicate Authority Fan-out

当前主链实际是：

```text
RAGResultMapper
    -> EvidenceBundleSchema
        |
        v
SchemeEvidenceService.extract_rag_output
        |
        +-> RAGContext projection
        |      +-> extra["evidence_contract"] = full Bundle dump
        |      +-> extra["evidence_contract_sha256"]
        |
        +-> RetrievedChunk projection
        +-> Citation projection
        +-> normalized["evidence"] = full Bundle dump
        |
        v
DocumentCitationRegistry.remap_bundle
        |
        +-> restore Bundle from RAGContext.extra
        +-> rewrite citations/items/context
        +-> model_validate(new Bundle)
        +-> replace normalized["evidence"]
        +-> put new Bundle dump back into RAGContext.extra
```

此时 RAG 原始 typed Bundle 不再是 downstream 唯一被消费的对象；Application 生成的 reconstructed Bundle 实际成为新的 de facto authority。

这正是 Phase 4 的根问题。

---

# 6. SharedState Second Authority

`backend/schemas/context.py::EvidenceContextSchema` 当前明确写着：

```text
contract 是结构化证据真相
context_text / chunks / citations 是投影视图
```

但 Phase 1 已冻结：

```text
EvidenceBundleSchema = 唯一 canonical EvidencePackage
```

因此两者直接冲突。

当前生产写入：

```text
StageService
  -> SharedStateWriter.set_evidence_context(... evidence_contract=contract.model_dump())
  -> state.context_bundle.evidence.contract = dict(...)
```

生产直接 consumer 很少，目前主要继续被 legacy graph-state projection 暴露为 `contexts["evidence_contract"]`；因此 Phase 4 有条件在不引入新存储的情况下把它降级。

Phase 4 后 `ContextBundle.evidence` 应只描述 Evidence 的 context/projection/assessment view，不再自称 canonical Evidence truth。

---

# 7. Durable Checkpoint Audit

## 7.1 Checkpoint 本身可承载 typed EvidenceBundle

`SchemeGenerationGraphStateCodec` 与 `SectionExecutionGraphStateCodec` 都采用：

```text
Pydantic model
-> model_dump(mode="json", round_trip=True)
-> JSON
-> checkpoint
-> model_validate(...)
```

因此本轮**不需要新增数据库、ArtifactStore 或新的 persistence service**。

现有 typed payload 直接增加/持有 `EvidenceBundleSchema` 即可被 durable checkpoint 序列化和恢复。

## 7.2 当前 top-level checkpoint 重复 Evidence

`SchemeGenerationGraphPayloadSchema` 当前同时持有：

```text
document_rag_context
  -> extra.evidence_contract (full Bundle)

document_rag_output
  -> evidence (full Bundle)

all_rag_outputs[]
  -> each may contain evidence (full Bundle)

section_evidence[]
  -> rag_context may again carry full Bundle in extra

shared GraphState.context_bundle.evidence.contract
  -> full Bundle dict
```

因此 durable resume 虽然能够工作，但 checkpoint 中 canonical Evidence 不是单一 authority。

## 7.3 当前 Section checkpoint 重复 Evidence

`SectionExecutionGraphPayloadSchema` 当前同时持有：

```text
shared_state_payload
  -> context_bundle.evidence.contract

document_rag_context
  -> extra.evidence_contract

document_rag_output
  -> evidence

active_context
  -> extra.evidence_contract

rag_outputs[]
  -> evidence

evidence: SectionEvidenceBundleSchema
  -> rag_context.extra.evidence_contract

research_agent_payloads[]
  -> evidence_package_ref/hash/summary
```

Phase 4 迁移必须保持 checkpoint/resume 能力，但 canonical package 只能有一个明确 typed carrier；其余字段必须是 projection、compatibility snapshot 或 hash/ref，且禁止反向覆盖 canonical carrier。

---

# 8. ContextManager Audit

`SectionGenerationContextPolicy.build_request()` 当前：

```text
RAGContext
    -> _evidence_contract(rag_context)
    -> context.extra["evidence_contract"]
    -> RAGEvidenceContractSchema.model_validate(...)
    -> selected_evidence_ids/items/lineage
```

这条路径会影响真正 Prompt Context 构建，因此是 Phase 4 的关键 D 类 consumer，不是纯观测代码。

Phase 4 后 ContextManager 必须获得显式 canonical EvidenceBundle 或其明确 deterministic projection；不能继续从 `RAGContext.extra` 猜回 Evidence truth。

注意：ContextManager 当前需要 canonical package 的原因是真实存在的：

- 只允许 selected Evidence 进入模型；
- 逐 Evidence 生成 ContextItem；
- 保留 evidence_id；
- 保留 Citation refs；
- 读取 lineage。

因此不能简单删除 `_evidence_contract()` 后退化为仅使用 `rag_context.context_text`，否则会丢失结构化 selected Evidence / lineage 语义。

---

# 9. Citation Registry Audit

`DocumentCitationRegistry` 的真实职责是：

```text
local citation id
-> document-global citation id
```

原因真实存在：文档级、章节级、recovery 多次 RAG 调用都可能返回局部 `C1`。

因此 Registry 本身必须保留。

需要移除的是：

```text
Citation remap
-> restore canonical Bundle from Context.extra
-> rewrite canonical Bundle
```

Phase 4 后推荐冻结：

```text
EvidenceBundle = immutable canonical RAG Evidence identity
DocumentCitationRegistry = Application citation projection/binding identity
```

Registry 可以继续产生：

- remapped Citation projection；
- remapped RAGContext text projection；
- `citation_id_map`；
- retrieval scope/query metadata。

但不得通过 projection 重新制造 canonical EvidenceBundle。

---

# 10. Canonical Evidence Hash Freeze

当前 `DocumentCitationRegistry` 在改写 Citation 后重新计算：

```text
evidence_contract_sha256 = sha256(rewritten Bundle)
```

这会造成：

```text
同一次 RAG Evidence
仅仅因为 Application 文档级 Citation 编号变化
-> canonical Evidence hash 变化
```

这与 Evidence/Citation identity separation 不一致。

Phase 4 必须冻结：

```text
canonical evidence_contract_sha256
= canonical EvidenceBundle serialized content hash
= 在 canonical package 创建 / legacy upgrade 完成后确定
= Application citation remap 不修改该 hash
```

Application prompt / citation projection 的完整性由已有独立字段承担，例如：

```text
prompt_evidence_contract_sha256
context_sha256
citation registry identity / id_map
```

不要把 canonical Evidence identity 与 Application Citation marker identity 再混为一体。

---

# 11. Research Path Audit

## 11.1 MainlineResearchRetrieval 缺 canonical package

当前：

```text
MainlineResearchRetrieval
  context
  chunks
  citations
  citation_registry
  normalized
  assessment
  retrieval_trace_id
```

没有：

```text
EvidenceBundleSchema
```

因此 Section graph 收到 Research tool 结果后只能依靠 normalized dict / context extra / hash 投影。

这是 Phase 4 的 typed carrier 缺口。

## 11.2 evidence_package_ref 当前不可解析

Research reasoner 当前合成：

```text
state://section-evidence/{section_id}/{scope}/r{round}
```

仓库扫描没有发现对应：

```text
resolve/load/fetch evidence_package_ref
```

因此当前 `evidence_package_ref` 只能理解为逻辑 lineage label，不能宣称它是 canonical persistence reference。

Phase 4 不需要为此新增 ArtifactStore。若继续保留该字段，应让其语义与现有 checkpoint 中真实 canonical carrier 对齐，或明确标为 compatibility ref；不得留下“看似可取回、实际不可取回”的假 authority。

---

# 12. `normalized["evidence"]` 的处理原则

当前多个生产 consumer 还直接读取：

```text
StageService._evidence_assessment
SectionExecutionGraph._assessment
MainlineResearchToolGateway/_assessment
SectionExecutionCoordinator writer gate helper
writer_governance.extract_writer_gate_binding
```

因此 Phase 4 不能第一步暴力删除 `normalized["evidence"]`。

正确迁移顺序：

1. 先给真正生产节点显式 typed EvidenceBundle carrier；
2. Gate / Assessment / ContextManager / Research 改为从 typed canonical package 单向读取；
3. `normalized["evidence"]` 降级为 capture / compatibility snapshot；
4. 确认生产控制逻辑不再从该 dict `model_validate` 恢复 canonical package；
5. 是否最终删除 compatibility snapshot 留给独立 retirement，不在 Phase 4 强制清零。

---

# 13. Phase 4 Recommended Minimal Split

根据真实 consumer 数量，Phase 4 建议拆为 **4A + 4B + 4C + 4D**，无需继续细分。

## Phase 4A — Recovery Path Audit / Freeze

本文件。状态：完成。

## Phase 4B — Typed Canonical Package Carrier

唯一目标：让 Application / Graph / Research 主链显式持有同一 `EvidenceBundleSchema`，不再需要从 Context/dict 反解。

最小范围应覆盖：

- Document-level active package；
- Section/recovery active package；
- MainlineResearchRetrieval；
- Gate / Assessment consumers；
- ContextManager input；
- checkpoint serialization round-trip。

不得新建 `EvidencePackageV2Schema`。

## Phase 4C — Remove Illegal Recovery / Second Authority

处理确认的 D 类：

- `RAGContext.extra["evidence_contract"]` full dump producer；
- `DocumentCitationRegistry` canonical recovery/rewrite；
- `ContextManager._evidence_contract()` recovery；
- `EvidenceContextSchema.contract` truth semantics；
- `SharedStateWriter.set_evidence_context()` full canonical copy；
- 生产 Gate/Research 从 `normalized["evidence"]` 恢复 authority 的路径。

Legacy adapter 升级能力保留，但升级后输出进入同一 typed package carrier。

## Phase 4D — Checkpoint / Compatibility / Architecture Verification

验证：

- top-level checkpoint typed Bundle round-trip；
- Section checkpoint typed Bundle round-trip；
- durable resume 不 replay 已完成 retrieval；
- citation registry restore 正常；
- compatibility outputs 仍可生成；
- production canonical recovery path = 0；
- package hash 不因 citation remap 改变。

Phase 4D 只做本阶段验证和必要 cleanup，不进入 Phase 5 ID namespace closure。

---

# 14. Phase 4 Non-goals

严格禁止：

- 不修改 Phase 1–3 authority；
- 不重新设计 EvidenceBundle；
- 不新增 EvidencePackageV2；
- 不新增数据库 / ArtifactStore / Memory store；
- 不实现 Durable Memory；
- 不实现 RetrievalAction；
- 不实现 Research multi-round V2；
- 不实现 ContextCompiler V2；
- 不修 `accepted_evidence_ids <- citation_id`，该问题留 Phase 5；
- 不清 Phase 6 Application -> RAG internal imports；
- 不处理 LangChain facade `__getattr__`，留 Phase 7；
- 不修改 Gate policy；
- 不修改 strict/shadow；
- 不修 Document Hard Gate 既有失败；
- 不顺手重构 Writer / Verifier / Evaluation。

---

# 15. Phase 4 Acceptance Criteria

Phase 4 完成必须至少满足：

```text
[ ] EvidenceBundleSchema 仍是唯一 canonical EvidencePackage
[ ] Application 主链显式持有 typed EvidenceBundle，不依赖 projection recovery
[ ] document / section / recovery package 可以通过现有 checkpoint round-trip
[ ] ContextManager 不从 RAGContext.extra 恢复 canonical Bundle
[ ] DocumentCitationRegistry 不从 RAGContext.extra 恢复/重写 canonical Bundle
[ ] RAGContext.extra 不保存完整 evidence_contract 作为 canonical recovery channel
[ ] SharedState ContextBundle 不再声明/承担第二 canonical Evidence truth
[ ] Research 主链获得明确 canonical package carrier 或真实可解析 ref
[ ] normalized["evidence"] 不再被生产控制逻辑当作 canonical recovery source
[ ] LegacyEvidenceAdapter 仍可集中升级 legacy payload
[ ] canonical evidence hash 不因 Application citation remap 改变
[ ] Citation remap / prompt citation binding 行为保持
[ ] durable resume 不因 Evidence authority 迁移失效
[ ] Phase 5/6/7 未偷跑
```

---

# 16. Verification Baseline

审计前 Git：

```text
HEAD = f43f959 checkpoint: complete rag-agent interface v2 phases 1-3
```

工作树审计开始时仅有：

```text
?? docs/MULTI_AGENT_V2_CURRENT_STATE_GAP_AUDIT_20260813.md
```

本轮未修改生产代码。

相关基线测试使用项目 `.venv`：

```text
./.venv/Scripts/python.exe -m pytest -q \
  backend/tests/test_step_12_evidence_contract.py \
  backend/tests/test_step_14_context_manager.py \
  backend/tests/test_scheme_writer_secondary_service_boundaries.py \
  backend/tests/test_phase3_section_execution_subgraph.py \
  backend/tests/test_phase4_durable_resume_hitl.py
```

结果：

```text
27 passed
3 failed
```

3 个失败均进入既有 `evaluate_document_gate` 的 `mode=strict` 后因 Document Hard Gate 阻断：

- `test_production_path_uses_section_subgraph_not_legacy_coordinator`
- `test_new_engine_resumes_without_replaying_completed_nodes`
- `test_application_service_inspects_and_approves_across_new_instance`

日志未出现：

- EvidenceBundle validation failure；
- RAGContext recovery exception；
- checkpoint serialization/restore exception；
- CitationRegistry restore exception；
- ContextManager Evidence reconstruction exception。

因此这些失败按 Phase 3C 已知结论继续判定为 Gate/test expectation drift，**不是 Phase 4A blocker**。Phase 4 不允许为通过这些测试而修改 Gate strictness。

---

# 17. Final Decision

Phase 4A 已完成，真实边界已经明确。

当前不能再把 Phase 4 简化成：

```text
删除 DocumentCitationRegistry 中一处 context.extra.get("evidence_contract")
```

真正需要做的是：

```text
先建立 typed canonical EvidenceBundle carrier
        ↓
让 Gate / ContextManager / Research / checkpoint 显式消费它
        ↓
再移除 RAGContext / SharedState / normalized dict 的反向 canonical recovery
        ↓
保留必要 projection / hash / legacy compatibility
```

Phase 4 不需要新基础设施，也不需要新 Evidence Schema。

推荐下一步：

```text
Phase 4B — Typed Canonical Package Carrier
```

在实施 4B 前只需要人工冻结两点：

1. canonical Evidence hash 绑定 RAG/legacy-upgrade 后的 `EvidenceBundleSchema`，Application citation remap 不改变该 hash；
2. 使用现有 top-level / Section checkpoint 直接持久化 typed `EvidenceBundleSchema`，不新增 ArtifactStore/DB。

其余 Phase 4 implementation 可在这两条冻结后按本审计执行。
