# RAG-Agent Interface V2 Phase 4C Solution Audit

日期：2026-08-14  
阶段：Interface V2 Phase 4C Solution Audit  
审计对象：`RAG_AGENT_INTERFACE_V2_PHASE4C_COMPATIBILITY_REMOVAL_SOLUTION_20260814.md`  
代码基线 HEAD：`f43f959`  
审计性质：**独立方案审计；未实施 Phase 4C；未修改 production code / tests**

---

# 1. 最终结论

```text
PHASE 4C SOLUTION NOT READY TO FREEZE
```

当前 Phase 4C solution 的**总方向正确**，且以下核心原则与冻结的 Phase 4 架构一致：

```text
EvidenceBundleSchema = sole canonical Evidence authority
Canonical → projection = allowed
Projection → canonical = forbidden
Legacy input → LegacyEvidenceAdapter → canonical = only compatibility ingress
ContextBundle.evidence = retire
normal production normalized["evidence"] = retire
pre-4C development checkpoint compatibility = not required
```

但是，按当前代码事实和已冻结 Phase 4 handoff 对照，solution 仍存在 **7 个确定性缺口**。这些缺口不是新的业务方向争议，也不需要用户重新做架构取舍；它们属于“冻结方案必须把已决定的方向写完整”的问题。

其中最重要的 blocker 是：

1. solution 没有完整落实 Phase 4 已冻结的 field-retirement matrix，尤其漏掉 document-level / section-level 多个持久化 duplicate projection；
2. `LegacyEvidenceAdapter` 当前虽然在内部构造 canonical Bundle，但现有返回接口不会把 typed Bundle 交回 production carrier；如果直接删除 full-copy egress，legacy ToolResult 入口会失去 canonical Bundle；
3. rejected recovery candidate 的 citation 当前在 acceptance 之前已经进入 `DocumentCitationRegistry`，可以继续污染最终 document citation projection；
4. checkpoint schema 将发生明确的 breaking change，但 top-level / section state version 仍为 `1`，solution 没有要求版本升级；
5. AC-2 只检查三类 hidden copy，无法证明“canonical Bundle 保存一次 / full Bundle duplicate 清零”，因为 final output / terminal result 仍可再次序列化 section canonical Bundle。

因此当前文档**不能直接冻结并进入实施**。

---

# 2. 审计依据

完整对照：

- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4C_COMPATIBILITY_REMOVAL_SOLUTION_20260814.md`
- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_CANONICAL_EVIDENCE_HANDOFF_20260814.md`
- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_EVIDENCE_AUTHORITY_FIELD_MATRIX_AUDIT_20260814.md`
- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4A_CANONICAL_EVIDENCE_RECOVERY_AUDIT_20260814.md`
- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FIX_CLOSURE_20260814.md`
- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FINAL_FIX_HANDOFF_20260814.md`
- `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FINAL_CLOSURE_20260814.md`

代码扫描覆盖：

```text
backend/rag/
backend/schemas/
backend/context_manager/
backend/agent/runtime/
backend/apps/enterprise_document/runtime/agent_rag_v1/
backend/apps/enterprise_document/schemas/
backend/apps/enterprise_document/services/scheme_writer/
backend/apps/enterprise_document/workflows/
backend/observability/
backend/data_capture/
backend/tools/
backend/tests/
```

本轮没有把 solution 文档自身的结论当作事实；所有关键结论都重新沿 producer → carrier → consumer → fallback → checkpoint / recovery 路径核对。

---

# 3. Canonical reconstruction site 独立分类

当前 production 范围内找到的 `EvidenceBundleSchema / RAGEvidenceContractSchema.model_validate(...)` 关键位置：

| 位置 | 当前行为 | 分类 | 4C 结论 |
|---|---|---|---|
| `backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py:50` | legacy payload dict → typed Bundle | A. 合法 legacy → canonical migration boundary | KEEP，但必须只做 ingress |
| `backend/context_manager/policies.py:339` | `RAGContext.extra["evidence_contract"]` → Bundle | B. Application projection → canonical reconstruction | REMOVE |
| `backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py:216` | remapped context contract dict → replacement Bundle | B. Application projection → canonical reconstruction | REMOVE |
| `backend/apps/enterprise_document/services/scheme_writer/writer_governance.py:430` | `normalized["evidence"]` → Bundle fallback | B. Application projection → canonical reconstruction | REMOVE |

没有发现第五个 production reconstruction authority site。

因此 solution 对“当前 reconstruction 位置”的主判断基本正确；问题不在于漏掉第五个 `model_validate()`，而在于 **legacy ingress API、field retirement、full-copy fanout、citation acceptance 和 checkpoint versioning 仍未写完整**。

---

# 4. 真实字段角色审计

## 4.1 `RAGContext.extra["evidence_contract"]`

当前 producer：

- `evidence_service.py:191`
- `legacy_evidence_adapter.py:54,139`
- `document_citation_registry.py:221`

当前 consumer / fallback：

- `document_citation_registry.py:184`
- `context_manager/policies.py:335`
- `stage_service.py:1918`（generation report legacy lineage fallback）
- 间接 hash / lineage compatibility paths

结论：

```text
可以完全退出 normal production。
```

没有发现当前真实业务必须依赖 full contract 塞在 `RAGContext.extra` 中。

---

## 4.2 `normalized["evidence"]`

当前 full Bundle writer：

- `document_citation_registry.py:234`
- `evidence_service.py:202`
- `legacy_evidence_adapter.py:59,150`

当前 production 主要 consumer 是 compatibility reconstruction / old assessment helper。

结论：

```text
可以完全退出 normal typed production。
```

Legacy / Fake ToolResult boundary 可以保留旧输入形状，但 adapter 之后不能再把 full copy 带回 production。

---

## 4.3 `ContextBundle.evidence`

当前真实挂载点不仅是 schema：

- `backend/schemas/context.py`
- `backend/agent/runtime/state_access.py:200+` `set_evidence_context()`
- `backend/agent/runtime/graph_state_ops.py:67` write alias
- `backend/agent/runtime/graph_state_ops.py:312-313` read aliases
- `backend/agent/runtime/workflow_observer.py` observer read
- `backend/apps/enterprise_document/workflows/scheme_generation.py:47,62` workflow write paths
- `backend/apps/enterprise_document/workflows/top_level_business_graph.py:214` graph write contract
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py:488` production write

没有发现无法迁移的真实业务 consumer。

结论：

```text
整体退休可行。
```

但实施必须同步删除 schema、writer、alias、observer、workflow declared write paths / business node contract，而不能只删 `ContextBundleSchema.evidence`。

---

## 4.4 Assessment compatibility

### `document_evidence_assessment`

**不能按“纯旧 checkpoint fallback”描述。**

当前 typed production 仍会从 `document_evidence_bundle.assessment` 生成该 dict，并传给 `DocumentAssembler`。`document_assembler.py:200+` 读取其 `details.quality_mode_degraded`。

因此它当前是：

```text
current production lightweight projection
+
legacy checkpoint fallback
```

确定性方向：先把 `DocumentAssembler` consumer 改为直接读取 canonical Bundle assessment 或明确的最小 quality projection，再删除 persisted `document_evidence_assessment`。

### `active_assessment`

当前仍在 Section graph / coordinator / replan 中被写入并参与 `bundle_first_assessment_projection(...)` fallback。

它不是 authority，因为 Bundle 存在时 Bundle wins；但它仍是当前 production persisted projection。

确定性方向：迁移所有 current typed consumer 到 `active_evidence_bundle.assessment` 后删除 persisted `active_assessment`。

### `ResearchState.assessment`

当前新 production projection 已明确写 `{}`，并且本轮未找到真实 production read consumer。

结论：

```text
ResearchState.assessment 可以直接进入 removal；
document_evidence_assessment / active_assessment 必须先迁 consumer 再删。
```

---

# 5. Issues

## Issue P4C-SA-001 — Frozen Phase 4 field-retirement matrix 未完整进入 4C solution

### 代码位置

- `backend/apps/enterprise_document/schemas/scheme_generation_graph.py:120-139`
- `backend/apps/enterprise_document/schemas/scheme_writer/evidence.py:20-38`
- `backend/apps/enterprise_document/schemas/section_execution_graph.py:90-130`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`
- Frozen source: `RAG_AGENT_INTERFACE_V2_PHASE4_CANONICAL_EVIDENCE_HANDOFF_20260814.md` §10.6 / §10.7

### 现状

Phase 4 已经冻结为“EvidenceBundle 已拥有的 Evidence fact，不在 Application 再独立保存”，并明确列出目标删除 / derive：

```text
document_rag_context
document_retrieved_chunks
document_citations (RAG canonical copy role)
document_rag_output
document_evidence_assessment
document_evidence_available
document_assessment_status
section query / retrieval_scope / rag_context / retrieved_chunks / citations / assessment
all_retrieved_chunks long-lived state
SectionEvidence.generation_allowed duplicate
```

当前 code 仍持久化上述字段。

### 为什么属于 Phase 4C

这些正是 Phase 4B 建立 typed canonical carrier 后剩余的 duplicate Evidence state / compatibility scaffolding。

### solution 文档是否遗漏 / 描述错误

**YES。**

solution 明确处理了 `document_evidence_assessment` / `active_assessment` 等，但没有把完整的 frozen field matrix 写成 4C 最终 schema state。

尤其：

- 没有明确处理 `document_evidence_available`；
- 没有明确处理 `document_assessment_status`；
- 对 `document_rag_output` 只按 full normalized copy 间接讨论；
- 对 `all_retrieved_chunks` 使用“retrieved_chunks history KEEP if diagnostic”的笼统表述，容易违背 Phase 4 已冻结的“不要长期独立保存，按 canonical bundles 聚合”的结论；
- `SectionEvidenceBundleSchema` 的 frozen target 明确要求不再独立保存 `retrieval_scope/query/rag_context/retrieved_chunks/citations`，并删除 `generation_allowed` duplicate；当前 4C solution 只说“其他 context/chunks/citations 只允许是 projection”，没有明确要求退出 persisted SectionEvidence state。

### 风险

实施者可以满足当前 AC-1/AC-2，但仍保留大量 persisted duplicate projection，最终宣布 4C closed，却没有达到 Phase 4 frozen architecture。

### 确定性修正方向

在 solution 增加**最终 persisted field matrix**，逐字段冻结：

- canonical lifecycle carrier KEEP；
- Application-owned citation mapping/hash/gate/workflow refs KEEP；
- Evidence-owned facts REMOVE / derive on demand；
- diagnostic history 仅允许 lightweight projection，不得包含 full Bundle / reconstructable canonical state；
- `SectionEvidenceBundleSchema` 按 Phase 4 §10.7 精确收口。

### 是否需要用户决策

```text
NO
```

---

## Issue P4C-SA-002 — Assessment 字段角色描述不准确

### 代码位置

- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py:1533-1559`
- `backend/apps/enterprise_document/services/scheme_writer/document_assembler.py:84-92, 99+, 200+`
- `backend/apps/enterprise_document/schemas/section_execution_graph.py:306-311`
- `backend/apps/enterprise_document/runtime/agent_rag_v1/mainline_recovery.py:236-269`
- `backend/apps/enterprise_document/schemas/scheme_writer/state_projection.py:164-179`

### 现状

`document_evidence_assessment` 和 `active_assessment` 仍有 current production consumer；`ResearchState.assessment` 基本只剩 compatibility field。

### 为什么属于 Phase 4C

4C 正在删除 assessment duplicate authority / checkpoint fallback，必须知道每个字段是“可直接删除”还是“先迁 consumer”。

### solution 文档是否遗漏 / 描述错误

**PARTIALLY YES。**

solution 把三者统一描述为“仍作为旧 checkpoint / compatibility projection 存在”，这个描述对 `document_evidence_assessment` / `active_assessment` 不够准确。

### 风险

按文档机械删除会破坏 DocumentAssembler 的 `quality_mode_degraded` 读取或 Section state projection；反过来，因为存在当前 consumer 而长期保留，又会继续 duplicate state。

### 确定性修正方向

冻结迁移顺序：

```text
document_evidence_assessment
→ migrate DocumentAssembler to Bundle.assessment / minimal quality projection
→ remove persisted field

active_assessment
→ migrate graph/coordinator/replan reads to active_evidence_bundle.assessment
→ remove persisted field

ResearchState.assessment
→ remove directly after schema/test cleanup
```

### 是否需要用户决策

```text
NO
```

---

## Issue P4C-SA-003 — LegacyEvidenceAdapter 当前没有把 canonical Bundle 交回 typed production carrier

### 代码位置

- `backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py`
- `backend/apps/enterprise_document/services/scheme_writer/evidence_service.py:174-211`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py:449+`

### 现状

`LegacyEvidenceAdapter.extract(...)` 内部可以：

```text
legacy payload
→ RAGEvidenceContractSchema.model_validate(...)
→ canonical Bundle
```

但是它当前最终返回的是：

```text
(context, chunks, citations, normalized)
```

而不是 canonical `EvidenceBundleSchema`。

`SchemeEvidenceService.extract_rag_output()` 对 legacy `ToolResultSchema` 直接返回这个四元组。

因此当前 legacy path 的 canonical Bundle 能被后续 production 继续“找到”，实质上依赖：

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
```

这两个 full-copy egress。

### 为什么属于 Phase 4C

Decision 3 已冻结：legacy/fake boundary 可以存在，但 adapter 后必须进入 typed production，不能重新依赖 full-copy compatibility state。

### solution 文档是否遗漏 / 描述错误

**YES，方向写到了，但接口契约没有闭合。**

solution 写了：

```text
old ToolResult → adapter → EvidenceBundleSchema → typed production
```

但没有明确指出当前 adapter API 事实上做不到最后一步，也没有冻结新的 ingress return contract。

### 风险

如果实施者先删除 adapter 的：

```text
context.extra["evidence_contract"]
normalized["evidence"]
```

legacy ToolResult 进入 production 后将没有 typed canonical Bundle carrier。

### 确定性修正方向

将 adapter 收敛为真正 one-way ingress：

```text
LegacyEvidenceAdapter.upgrade(...)
→ EvidenceBundleSchema
```

随后复用与 typed RAG 相同的 projection path：

```text
Bundle
→ RAGEvidenceContractReader.projections(...)
→ Application projections
```

禁止 adapter 自己拥有第二套 canonical serialization / normalized full-copy output。

### 是否需要用户决策

```text
NO
```

---

## Issue P4C-SA-004 — rejected recovery candidate citation 在 acceptance 前已经提交到 registry

### 代码位置

- `backend/apps/enterprise_document/workflows/section_execution_graph.py:2078-2230` 附近
- `backend/apps/enterprise_document/workflows/section_execution_graph.py:400-540` ResearchAgent gateway path
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py:667-795` parallel merge
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py:1390-1400`

### 现状

direct recovery path 当前顺序是：

```text
candidate retrieval
→ registry.remap_bundle(...)
→ registry 已 register candidate citations
→ updates["citation_registry"] = registry.all()
→ _recovery_candidate_decision(...)
→ candidate may be rejected
```

因此 Phase 4B 已经正确保护：

```text
rejected candidate cannot replace active Bundle
```

但 citation registry 并没有同样的 acceptance boundary。

ResearchAgent gateway path同样把 `retrieval.citation_registry` 写入 payload 后才做 candidate acceptance。

parallel merge 中还存在明确注释：会 map `diagnostic-only` citation，并把它们 register 到 document registry。

最后：

```text
payload.all_citations = citation_registry.all()
```

DocumentAssembler 再把它作为 final citation projection 使用。

### 为什么属于 Phase 4C

这不是 Phase 5 namespace redesign；它是 Phase 4C citation projection cleanliness，且用户审计要求明确要求检查 rejected recovery citation 是否污染 final document citation list。

### solution 文档是否遗漏 / 描述错误

**PARTIALLY YES。**

solution 的 matrix 和 Phase 4C-B 已经说“rejected diagnostic citation 不进入 final citation catalog”，但没有把真实 mutation point 和 acceptance boundary 写成确定性实施规则。

### 风险

- rejected candidate citation 进入 final document citation list；
- 即便最后过滤，它也可能提前占用 document label，影响后续 accepted citation numbering；
- parallel / ResearchAgent path 可能与 direct path 行为不一致。

### 确定性修正方向

冻结 citation registry promotion 规则：

```text
candidate citation mapping
→ candidate-local / temporary projection
→ Application acceptance
→ accepted 才 commit 到 document citation registry
```

rejected candidate：

```text
保留 diagnostic trace/hash/count/source refs
不得进入 document registry / final all_citations
```

验证至少覆盖 direct recovery、ResearchAgent gateway、parallel fan-in 三条路径。

### 是否需要用户决策

```text
NO
```

---

## Issue P4C-SA-005 — Breaking checkpoint schema change 未冻结 state version bump

### 代码位置

- `backend/apps/enterprise_document/schemas/scheme_generation_graph.py:60`
  - `SCHEME_GENERATION_GRAPH_STATE_VERSION = 1`
- `backend/apps/enterprise_document/schemas/section_execution_graph.py:46`
  - `SECTION_EXECUTION_GRAPH_STATE_VERSION = 1`

### 现状

Phase 4C 将删除多个 checkpoint payload 字段，并明确不兼容 pre-4C checkpoint。

但当前 codec 仍以 version `1` 作为支持版本。

### 为什么属于 Phase 4C

Decision 1 已经明确允许 breaking checkpoint schema。既然 schema contract 发生 breaking change，就需要 checkpoint envelope 明确表达版本边界。

### solution 文档是否遗漏 / 描述错误

**YES。**

solution 只要求“新 4C checkpoint round-trip/resume/replan”，没有要求更新 top-level / section checkpoint state version。

### 风险

- pre-4C checkpoint 仍声称是“支持的 version=1”，随后在 Pydantic extra/缺字段处随机失败；
- 某些旧 checkpoint 恰好形状可解析时，可能产生不清晰的 partial compatibility；
- 无法用版本号明确证明“不兼容是设计决定，而不是反序列化 bug”。

### 确定性修正方向

Phase 4C schema breaking change 同步：

```text
SCHEME_GENERATION_GRAPH_STATE_VERSION: 1 → 2
SECTION_EXECUTION_GRAPH_STATE_VERSION: 1 → 2
```

并增加：

```text
v1 checkpoint → explicit unsupported-version rejection
v2 checkpoint → save/reload/resume/replan/recover_or_reuse PASS
```

### 是否需要用户决策

```text
NO
```

---

## Issue P4C-SA-006 — AC-2 无法证明所有 production full Bundle duplicate 已清零

### 代码位置

- `backend/apps/enterprise_document/schemas/scheme_writer/output.py:57`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py:2007,2050-2052`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py:2120-2127`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py:2300+` terminal `AgentResult`

### 现状

`SectionEvidenceBundleSchema` 现在包含 typed `evidence_bundle`。

同一份 section evidence 随后又被：

```text
payload.section_evidence
→ SchemeWriterOutput.section_evidence
→ SchemeWriterOutput.extra["section_evidence"] model_dump
→ enriched_rag_output["section_evidence"] model_dump
→ SharedState contexts.rag_tool_output
→ terminal AgentResult.result["scheme_writer_output"] model_dump
```

因此即使 4C 删除：

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
ContextBundle.evidence
```

仍然可能在 final output / SharedState compatibility output / terminal result 中保存多份 full canonical Bundle serialization。

本轮代码扫描没有发现真实 production consumer 必须从这些 extra/terminal copies 读取完整 Section EvidenceBundle。

### 为什么属于 Phase 4C

用户最终验收要求包含：

```text
canonical Bundle 保存一次
projection 单向
full Bundle duplicate 清零
```

### solution 文档是否遗漏 / 描述错误

**YES。**

AC-2 当前只检查三种 full-copy 形式，范围太窄，不能证明“Hidden full Bundle copies = 0 in normal production”。

### 风险

4C 可以在现有 AC 下“全部通过”，但 canonical Bundle 仍通过 `SectionEvidenceBundle.model_dump()` 被复制到多个 output/checkpoint compatibility payload。

### 确定性修正方向

在 solution 冻结**允许出现 full typed Bundle 的位置白名单**。

建议规则：

```text
Allowed full Bundle persistence:
- document_evidence_bundle canonical carrier
- section lifecycle canonical carrier / final SectionEvidence canonical slot
- research invocation transient typed carrier（不进历史 full-copy）

Forbidden full Bundle serialization:
- SchemeWriterOutput.extra
- contexts.rag_tool_output compatibility snapshot
- terminal AgentResult generic dict
- capture/observer payload
- arbitrary normalized / metadata / diagnostic history
```

最终输出若需要 Evidence 信息，只保留明确 projection/hash/ref/count/status/citation mapping。

### 是否需要用户决策

```text
NO
```

---

## Issue P4C-SA-007 — Verification plan 缺少关键 breaking-path / acceptance-boundary 证明

### 代码位置

涉及：

- `backend/apps/enterprise_document/workflows/scheme_generation.py`
- `backend/apps/enterprise_document/workflows/top_level_business_graph.py`
- `backend/agent/runtime/graph_state_ops.py`
- top-level / section state codec
- direct / ResearchAgent / parallel recovery citation paths

### 现状

solution 当前 focused tests 很接近正确，但仍没有明确要求证明：

1. old checkpoint version 被显式拒绝；
2. `ContextBundle.evidence` 删除后旧 workflow `write_paths` / `_DEFAULT_WRITE_ALIASES` / business node contracts 全部同步清零；
3. serial + ResearchAgent gateway + parallel 三条 rejected-candidate citation 路径都不会污染 final citation registry；
4. SectionEvidence frozen field matrix 真正收口；
5. final output / AgentResult / SharedState compatibility payload 不再复制 full Bundle；
6. `recover_or_reuse()` 以及 completed-section reuse 在新 4C checkpoint schema 下继续工作，而不只是 generic round-trip / resume / replan。

### 为什么属于 Phase 4C

这些都是 4C 删除 compatibility scaffolding 后最容易发生的真实 regression，不属于 Phase 5 或新架构。

### solution 文档是否遗漏 / 描述错误

**YES。**

当前 AC/Verification 无法完整证明 4C closure condition。

### 风险

“静态 grep 归零 + 基础 checkpoint test 通过”仍可能遗漏真实 durable recovery、parallel fan-in 或 final output full-copy。

### 确定性修正方向

补充 negative / focused acceptance：

```text
checkpoint v1 rejected / v2 accepted
v2 top-level save/reload/resume
v2 section save/reload/resume/replan/recover_or_reuse
parallel section v2 checkpoint/fan-in
ContextBundle.evidence schema/write/read/alias/observer/write-contract = 0
SectionEvidence duplicate fields = removed/derived
rejected candidate citation not in registry/final citations
full Bundle serialization only in explicit canonical carrier whitelist
```

### 是否需要用户决策

```text
NO
```

---

# 6. 对 15 个最终问题的明确回答

## 1. Phase 4C solution 是否覆盖所有 production full Bundle duplicate？

```text
NO
```

已覆盖三大旧 full-copy：`RAGContext.extra["evidence_contract"]`、`normalized["evidence"]`、`ContextBundle.evidence.contract`；但没有完整覆盖 SectionEvidence → SchemeWriterOutput / extra / SharedState rag_tool_output / terminal AgentResult 的 full Bundle serialization，也没有完整冻结 Phase 4 §10.6/10.7 的 persisted field matrix。

## 2. 所有 Application projection → Bundle reconstruction 是否都被覆盖？

```text
当前已发现的 production reconstruction site 基本全部找到。
```

ContextManager、CitationRegistry、WriterGovernance 三个非法 reconstruction 都在 solution 范围内；LegacyEvidenceAdapter 是唯一合法 ingress。

但 legacy adapter 的**typed handoff contract 未闭合**，因此不能据此宣布 ready。

## 3. LegacyEvidenceAdapter 是否已经被限制为唯一 legacy → canonical ingress？

```text
方向上 YES，当前代码/方案闭环上 NO。
```

当前 adapter 内部 canonicalize 后仍通过 full copies 维持下游兼容，而且没有显式返回 typed Bundle 给 production carrier。

## 4. `RAGContext.extra["evidence_contract"]` 能否完全退出 production？

```text
YES
```

没有发现必须保留的真实业务 consumer。

## 5. `normalized["evidence"]` 能否完全退出 normal production？

```text
YES
```

旧 ToolResult 可在 adapter/test boundary 输入；adapter 后不得继续存在 full normalized copy。

## 6. `ContextBundle.evidence` 整体退休是否会破坏真实 production consumer？

```text
不会形成不可解决的业务破坏。
```

但必须同步迁移/删除 state writer、graph aliases、observer、workflow write paths 和 business node contracts。当前没有发现必须保留该 state 的真实业务事实。

## 7. pre-4C checkpoint 不兼容这个决定是否足以删除 legacy checkpoint fallback？

```text
YES，但必须同时做 checkpoint state version bump。
```

否则“不兼容”只表现为运行时 schema failure，而不是明确的版本边界。

## 8. `document_evidence_assessment / active_assessment / ResearchState.assessment` 哪些可以直接删除？

```text
ResearchState.assessment
→ 可直接 removal（同步 schema/tests）

document_evidence_assessment
→ 不能先删；先迁 DocumentAssembler current consumer，再删

active_assessment
→ 不能先删；先迁 graph/coordinator/replan current consumer，再删
```

## 9. hash / lineage 是否还有 projection-owned fallback？

```text
YES，当前代码仍有。
```

主要包括 ContextManager context lineage fallback、Section graph / coordinator context hash fallback、replan context hash fallback、generation report `evidence_contract` lineage fallback、observability legacy ToolResult fallback。

正常 typed production 的这些 fallback 均可清零；Application-owned `WriterGateBinding` / integrity hash ref 可以保留。

## 10. Capture / Observability 是否可以完全脱离 full Evidence copy？

```text
YES
```

`SchemeCaptureService` 当前真正采集的是 rag_context/chunks/citations/trace 等 projection；`ObservedRAGService` 已经能直接从 typed Bundle 输出 counts / lineage。legacy generic ToolResult summary 可限制在 tool/adapter boundary。

## 11. CitationRegistry 是否还能间接改变 canonical Bundle？

```text
当前 YES。
```

`remap_bundle()` 仍从 context.extra 读取 contract、改 items/citations/context、model_validate replacement Bundle，再写 normalized full copy。

solution 已要求删除此行为，但尚未实施。

## 12. rejected recovery citation 是否可能污染 final document citation list？

```text
YES，真实存在。
```

candidate citation 在 acceptance 之前已经 register；rejected candidate 只阻止 active Bundle promotion，没有回滚 citation registry。parallel merge 还会显式注册 diagnostic-only citation。

## 13. Phase 4C solution 的实施顺序是否安全？

```text
大方向安全，但还不够完整。
```

“先断 reverse consumer → 再停 producer → 再删 schema/state”是正确顺序。

必须补两条依赖：

```text
legacy adapter typed Bundle handoff
必须先完成
→ 才能删除 adapter full-copy egress

candidate citation acceptance boundary
必须先完成
→ 才能清理 final citation projection
```

同时 schema breaking change 必须与 state version bump 同步。

## 14. Acceptance Criteria 是否足以证明 canonical Bundle 保存一次、projection 单向、reconstruction 清零、checkpoint/resume 正常？

```text
NO
```

reconstruction AC 已接近充分；但 full-copy whitelist、SectionEvidence field retirement、final output serialization、checkpoint version、recover_or_reuse、parallel/rejected citation 仍缺验收证据。

## 15. 是否还有新的 Phase 4C blocker？

```text
YES
```

本报告记录 7 个 blocker，全部是确定性修正问题，**没有新的用户业务决策项**。

---

# 7. Freeze 前必须补进 solution 的最小修订集合

只需补方案，不需要重新设计 Phase 4：

1. 把 Phase 4 §10.6/§10.7 完整 field-retirement matrix 写入 4C final target；
2. 明确 `document_evidence_assessment / active_assessment / ResearchState.assessment` 的真实当前 consumer 和迁移顺序；
3. 冻结 LegacyEvidenceAdapter typed ingress API：legacy → `EvidenceBundleSchema` → common typed projection path；
4. 冻结 recovery citation transactional promotion：accepted 后才 commit document registry；
5. top-level / section checkpoint state version `1 → 2`，并验证 old version explicit reject；
6. 增加 full Bundle persistence whitelist，覆盖 SchemeWriterOutput / extra / SharedState compatibility output / AgentResult；
7. 扩充 verification，覆盖 graph write contracts、SectionEvidence field cleanup、new-version recover_or_reuse、parallel fan-in、rejected citation、full-copy serialization negative scan。

以上 7 项补齐后，不需要再次讨论 Phase 4 架构方向，可以重新做一次短的 solution re-audit；若代码事实未再出现新冲突，即可进入：

```text
PHASE 4C SOLUTION READY TO FREEZE
```

---

# 8. 当前不需要用户决定的问题

本轮没有发现新的业务取舍需要用户重新选择。

已冻结的三个用户决策足以确定所有修正方向：

```text
Decision 1: pre-4C checkpoint compatibility 不承担
Decision 2: ContextBundle.evidence 整体退休
Decision 3: normal production full normalized evidence copy 退出
```

因此上面的 7 个问题全部：

```text
USER DECISION REQUIRED = NO
```

---

# 9. Git / 实施纪律

本轮仅新增本审计报告。

未执行：

```text
production code 修改
test 修改
stage
commit
clean
Phase 4C implementation
Phase 5
```

当前审计基线 HEAD：

```text
f43f959
```

工作树原有 Phase 4B 修改和未跟踪文件全部保留，不删除 `nul` 或其他既有文件。

---

# 10. 最终停止状态

```text
CODE FACT AUDIT COMPLETE
SOLUTION CROSS-CHECK COMPLETE
ISSUE LIST COMPLETE
FREEZE READINESS = NOT READY
AUDIT REPORT WRITTEN
PHASE 4C IMPLEMENTATION NOT STARTED
```
