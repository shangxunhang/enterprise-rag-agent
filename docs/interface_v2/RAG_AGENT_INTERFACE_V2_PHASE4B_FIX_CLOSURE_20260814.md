# RAG-Agent Interface V2 Phase 4B Fix Closure Report

日期：2026-08-14  
工作区：`D:\MyCode\rag-agent`  
代码基线 HEAD：`f43f959`  
阶段：Interface V2 Phase 4B Fix  
结论：**PHASE 4B CLOSED**

---

## 1. 本轮范围

本轮只修复 Phase 4B 主体完成后审计确认的 3 个 implementation defects：

1. `active_assessment` 仍可能参与 typed production Evidence authority；
2. `ResearchState.assessment` 仍被新链路持久化为 canonical assessment 副本；
3. Replan / Recovery 初始化虽然已有 `document_evidence_bundle`，但 active Evidence projections 仍从 legacy document projection 初始化。

本轮没有进入 Phase 4C，没有修改 Gate policy、claim classification、regex、Phase 5 ID namespace、Multi-Agent V2、Evaluation、RAG retrieval strategy 或存储架构。

---

## 2. 冻结原则保持情况

本轮保持以下 Phase 1–4B 已冻结不变量：

```text
EvidenceBundleSchema = sole canonical Evidence authority
RAG                    = Evidence facts producer
Application            = Gate authority
```

每次 RAG invocation 仍产生独立 immutable `EvidenceBundleSchema`；Application 可以保存 canonical Bundle 和自己的 workflow / Gate / citation projection / hash 状态，但不能让 legacy projection 在 typed Bundle 存在时覆盖 canonical Evidence facts。

---

# 3. Defect 1 — active_assessment production authority

## 3.1 原问题

Section graph 已有：

```python
active_evidence_bundle: EvidenceBundleSchema | None
```

但 production path 仍存在直接业务消费：

```python
dict(payload.active_assessment)
payload.active_assessment.get(...)
```

这意味着 typed Bundle 与 legacy projection 冲突时，旧 projection 仍可能影响 Research invocation、Section lifecycle projection 或 blocked fallback。

## 3.2 修复

在：

```text
backend/rag/evidence/contract.py
```

新增统一 helper：

```python
bundle_first_assessment_projection(
    contract,
    legacy_assessment,
)
```

规则冻结为：

```text
if typed Bundle exists:
    assessment = projection(Bundle.assessment)
    ignore legacy assessment completely
else:
    assessment = normalized legacy checkpoint projection
```

迁移位置包括：

- `SectionExecutionGraphPayloadSchema._project_section_lifecycle()`；
- `SectionExecutionGraph._invoke_research_agent()`；
- Section final writer-gate missing fallback；
- Research-tool recovery branch；
- `SectionExecutionCoordinator` document initialization；
- Coordinator writer-gate missing fallback；
- Coordinator self-RAG fallback。

`active_assessment` 字段仍保留，因为旧 checkpoint 需要兼容；但 typed Bundle 存在时它只作为 helper 的 legacy fallback 参数，不再独立决定业务事实。

## 3.3 负向验证

新增双向冲突：

```text
Bundle = sufficient
legacy active_assessment = insufficient
=> effective = sufficient
```

反向：

```text
Bundle = insufficient
legacy active_assessment = sufficient
=> effective = insufficient
```

并验证 `SectionState.research.status` 也跟随 Bundle，而不是 legacy projection。

现有生产链测试：

```text
test_legacy_insufficient_assessment_cannot_override_final_writer_gate
```

同步修正了一个过期断言。该测试名称本来就要求 legacy insufficient 不能覆盖 final typed authority，但旧断言仍要求 checkpoint `active_assessment == insufficient`。Phase 4B Fix 后正确行为为：

```text
active_assessment.status == sufficient
active_writer_gate.delivery_mode == grounded
generation_allowed == true
evidence_blocked == false
```

测试已通过。

## 3.4 静态验收

扫描：

```text
dict(payload.active_assessment)
dict(updated.active_assessment)
dict(self.active_assessment)
*.active_assessment.get(...)
```

结果：

```text
typed Bundle 存在时直接读取 active_assessment 的 production consumer = 0
```

仍能搜索到 `active_assessment` 字段、赋值和 helper fallback 参数，这是 Phase 4B compatibility carrier，不再是独立 authority。

**Defect 1：CLOSED。**

---

# 4. Defect 2 — ResearchState.assessment duplicate persistence

## 4.1 原问题

`ResearchStateSchema` 为旧 checkpoint compatibility 仍包含：

```python
assessment: dict[str, Any] = {}
```

但 Section state projection 仍会把 active assessment 写回该字段，形成第二 assessment copy。

## 4.2 修复

当前只有两个 production `research.model_copy(...)` writer：

```text
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_adapter.py
```

两处现在均显式执行：

```python
"assessment": {}
```

因此即使恢复的旧 checkpoint 中：

```python
ResearchState.assessment = { ...legacy assessment... }
```

新 production state projection / ResearchAgent writer 也会将其归零，不再把 canonical Bundle assessment 复制进去。

ResearchState 继续保存的仍是 Application/Agent workflow state：

- status；
- evidence_package_ref；
- evidence_contract_sha256；
- research_rounds；
- tool_call_ids；
- recommended_action；
- accepted/rejected IDs；
- evidence_gaps；
- metadata。

`accepted/rejected IDs` namespace 问题仍冻结到 Phase 5；本轮未修改。

## 4.3 负向验证

测试专门构造：

```text
legacy checkpoint ResearchState.assessment != {}
canonical Bundle.assessment = sufficient
```

经过 typed Section state projection 后验证：

```text
ResearchState.assessment == {}
Bundle.assessment.status == sufficient
```

结果通过。

静态扫描确认：

```text
production research.model_copy writers = 2
两处 assessment write = {}
新 production path 写 canonical assessment copy = 0
```

**Defect 2：CLOSED。**

---

# 5. Defect 3 — Replan / Recovery initialization not Bundle-first

## 5.1 原问题

`MainlineReplanAttempt.prepare()` 已经保存：

```python
active_evidence_bundle = payload.document_evidence_bundle
```

但 active projections 仍从 legacy document state 初始化：

```text
document_rag_context
document_retrieved_chunks
document_citations
document_evidence_assessment
active_query = ""
active_scope = "document"
```

这允许同一个 checkpoint 中 typed Bundle 与旧 projection 冲突。

## 5.2 修复

文件：

```text
backend/apps/enterprise_document/runtime/agent_rag_v1/mainline_recovery.py
```

现在明确分成两条互斥路径。

### Typed path

当：

```python
payload.document_evidence_bundle is not None
```

执行：

```text
active_evidence_bundle = document Bundle
active_context         = RAGEvidenceContractReader.projections(Bundle).context
active_chunks          = Bundle selected chunk projection
active_citations       = Bundle citation projection
active_assessment      = bundle_first_assessment_projection(Bundle)
active_query           = Bundle.query
active_scope           = Bundle.retrieval_scope
evidence hash          = evidence_bundle_sha256(Bundle)
```

并记录：

```text
retrieval_metadata.evidence_projection_source = canonical_bundle
```

### Legacy checkpoint fallback

只有：

```python
payload.document_evidence_bundle is None
```

才读取：

```text
document_rag_context
document_retrieved_chunks
document_citations
document_evidence_assessment
```

并标记：

```text
retrieval_metadata.evidence_projection_source = legacy_checkpoint_projection
```

`citation_registry` 仍可使用 document-level Application citation projection；它是 Application-owned document rendering mapping，不是 active canonical Evidence authority。

## 5.3 负向验证

测试故意构造冲突：

```text
Bundle.query = canonical query
legacy context = WRONG LEGACY CONTEXT
legacy chunks = wrong_child
legacy citations = WRONG
Bundle.assessment = sufficient
legacy document_evidence_assessment = insufficient
```

Replan 后验证：

```text
active_query == canonical query
active_scope == Bundle.retrieval_scope
active_assessment.status == sufficient
active_context == Bundle context projection
active_chunks == Bundle chunk projection
active_citations == Bundle citation projection
evidence_contract_sha256 == hash(Bundle)
evidence_projection_source == canonical_bundle
```

全部通过。

**Defect 3：CLOSED。**

---

# 6. 本轮修改文件

本次 Phase 4B Fix 直接修改：

```text
backend/rag/evidence/contract.py
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_adapter.py
backend/apps/enterprise_document/runtime/agent_rag_v1/mainline_recovery.py
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
backend/tests/test_phase4_section_agents_mainline.py
```

工作树仍包含之前 Phase 4B 主体和其他并行审计的累计未提交修改，本轮没有 stage / commit，也没有清理不属于本轮的未跟踪文件。

---

# 7. 测试结果

## 7.1 Phase 4B Fix / canonical carrier focused

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py

12 passed
0 failed
```

其中新增三类 Fix 负向测试：

1. Bundle assessment 双向压过 conflicting legacy `active_assessment`；
2. typed Section projection 清空旧 `ResearchState.assessment`；
3. Replan 在 conflicting document projections 下强制 Bundle-first。

## 7.2 Gate / recovery / section / checkpoint focused

最终联合验收：

```text
26 passed
0 failed
```

覆盖：

- Phase 4B canonical carrier Fix；
- Phase 3C Gate ownership；
- Mainline recovery E2E entrypoints；
- Section graph declared business nodes/routes；
- independent Section checkpoint namespaces；
- workflow budget snapshot restore；
- CitationRegistry stable restore；
- legacy insufficient assessment cannot override typed final writer gate；
- durable SQLite saver across instance；
- paused/resume task transition。

## 7.3 Syntax

对本轮 8 个直接修改文件执行 AST parse：

```text
AST_PARSE_PASS 8
```

## 7.4 git diff --check

结果：

```text
PASS
```

只有仓库既有 CRLF/LF warning：

```text
CRLF will be replaced by LF the next time Git touches it
```

没有 whitespace error。

---

# 8. 非验收旧测试红项说明

本轮曾额外扩大运行旧 E2E，出现两个与本轮 3 defect 无关的红项，未通过越界修改消除：

1. `test_phase3_section_execution_subgraph.py::test_production_path_uses_section_subgraph_not_legacy_coordinator`
   - Section subgraph 本身执行完成；
   - 最终失败来自当前 Fake LLM Claim Audit / Document Hard Gate；
   - 属于此前已知 fake claim-classifier / strict gate 行为，不属于 Phase 4B Fix。

2. `test_phase4_section_agents_mainline.py::test_final_writer_gate_blocked_stops_before_writer_and_verifier`
   - 测试仍通过修改 legacy RAG output `gate_decision/generation_allowed` 制造 Gate；
   - Phase 3C 已冻结 Application 是 Gate authority，RAG 不再拥有该 Gate authority；
   - 该 fixture 与当前 Gate ownership 不一致，本轮禁止修改 Gate policy，因此不纳入 Phase 4B Fix 验收。

没有为了让这两个旧测试全绿而恢复 legacy Evidence/Gate authority。

---

# 9. 仍保留、明确属于 Phase 4C 的 compatibility paths

本轮没有进入 Phase 4C，因此以下 compatibility path 仍存在：

### 9.1 Full contract / projection compatibility

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
ContextBundle.evidence.contract
```

### 9.2 Legacy state fields

```text
document_evidence_assessment
active_assessment
ResearchState.assessment schema field
旧 document_rag_context / chunks / citations projection fields
```

其中：

- `active_assessment` 只允许 legacy fallback；typed Bundle 存在时不参与业务 authority；
- `ResearchState.assessment` schema 字段仍存在，但新 production writer 固定写 `{}`。

### 9.3 Projection → Bundle compatibility reconstruction sites

当前扫描仍有：

```text
DocumentCitationRegistry          -> RAGEvidenceContractSchema.model_validate(...)
LegacyEvidenceAdapter             -> RAGEvidenceContractSchema.model_validate(...)
WriterGovernance compatibility    -> EvidenceBundleSchema.model_validate(...)
ContextManager compatibility      -> RAGEvidenceContractSchema.model_validate(...)
```

其中 `LegacyEvidenceAdapter` 是冻结允许的 legacy → canonical compatibility boundary；其余非法/过渡 reconstruction 的真正删除属于 Phase 4C。

本轮没有删除这些路径。

---

# 10. 最终静态结论

```text
typed Bundle 存在时直接读取 active_assessment 的 production consumer = 0

新 production path 写 ResearchState.assessment canonical copy = 0

Replan 在 document_evidence_bundle 存在时依赖旧 document Evidence projection 作为 active authority = 0
```

三个 implementation defects 均已通过代码修改、冲突型负向测试和 focused regression 关闭。

---

# 11. 最终结论

```text
PHASE 4B CLOSED
```

Phase 4B Fix 到此停止。

**没有自动进入 Phase 4C，没有 stage，没有 commit。**
