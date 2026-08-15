# RAG-Agent Interface V2 Phase 4B Final Fix 交接文档

> 日期：2026-08-14  
> 仓库：`D:\MyCode\rag-agent`  
> 分支：`checkpoint/controlled-architecture-20260728`  
> 当前状态：**Phase 4B 主体完成；上一轮 3 个 implementation defects 已关闭；复审发现 1 个新的 production blocker；Phase 4B 暂不能正式 CLOSE。**

---

# 1. 本文档目的

本文档用于新窗口直接继续执行 **Interface V2 Phase 4B Final Fix**。

本轮不是重新设计 Phase 4B，也不是进入 Phase 4C，而是只修复最后一个已经被真实 production business chain 证实的 active Evidence acceptance bug。

当前最终判断：

```text
Phase 4B 主体架构                PASS
上一轮 3 个 Phase 4B Fix         CLOSED
Production recovery acceptance   OPEN

=> PHASE 4B NOT CLOSED
```

只有本交接文档中的最后一个 blocker 关闭后，才允许重新写：

```text
PHASE 4B CLOSED
```

---

# 2. 必须先阅读的文档

新窗口开始后，先读取：

1. `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_CANONICAL_EVIDENCE_HANDOFF_20260814.md`
2. `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4A_CANONICAL_EVIDENCE_RECOVERY_AUDIT_20260814.md`
3. `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_EVIDENCE_AUTHORITY_FIELD_MATRIX_AUDIT_20260814.md`
4. `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FIX_CLOSURE_20260814.md`
5. **本文件** `docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FINAL_FIX_HANDOFF_20260814.md`

其中：

- Phase 4 总交接描述冻结架构；
- Phase 4A 文档记录 Canonical Evidence recovery / authority 审计；
- Phase 4B Fix Closure Report 中记录的上一轮 3 个 defects 确实已关闭；
- 但其 `PHASE 4B CLOSED` 结论已被后续 production recovery 复审推翻；
- 本文件是当前 Phase 4B 最终状态的最新 authority。

---

# 3. Phase 4 总体冻结原则

必须继续保持：

```text
EvidenceBundleSchema = sole canonical Evidence authority
```

RAG 负责：

```text
Evidence facts
```

Application 负责：

```text
Gate / workflow / document citation projection / acceptance decision
```

每次 RAG invocation 都产生一份新的 canonical `EvidenceBundleSchema`。

Bundle 跨 RAG → Application public boundary 后：

- 不修改 canonical Evidence facts；
- 不重新编号 canonical citation local labels；
- 不重新计算一个被 Application projection 改写后的 canonical hash；
- 不从 projection/dict 恢复出第二套 canonical truth；
- Application 服务按字段需求消费；
- Application 可以保存自己产生的 workflow / Gate / citation mapping / integrity state。

---

# 4. Canonical Carrier 冻结

## 4.1 Document lifecycle

Document-level RAG invocation 结束后保存：

```python
document_evidence_bundle: EvidenceBundleSchema | None
```

Document lifecycle owner 持有该 canonical Bundle。

## 4.2 Section lifecycle

Section graph 保存：

```python
active_evidence_bundle: EvidenceBundleSchema | None
```

Section 开始时可继承 document Bundle。

后续 section retrieval / self-RAG / corrective retrieval 每次都产生新的 Bundle，但：

> **新的 Bundle 只有被 Application 业务逻辑明确接受后，才能替换 `active_evidence_bundle`。**

## 4.3 Research lifecycle

Research retrieval 只临时 typed 携带：

```python
MainlineResearchRetrieval.evidence_bundle
```

如果被 Section 接受，则成为 Section active Bundle。

ResearchState 不保存完整 Bundle 副本。

---

# 5. Citation / Hash 冻结

Citation 三层身份：

```text
citation_ref     = stable canonical source identity
citation_id      = Bundle-local C1/C2/... label
document label   = Application document-level C17/C18/... label
```

DocumentCitationRegistry 只做：

```text
citation_ref -> document label
```

不得修改 canonical Bundle。

Canonical Evidence hash：

```text
evidence_contract_sha256
=
hash(final canonical EvidenceBundle)
```

Application citation/document projection 不得改变该 hash。

---

# 6. 上一轮 3 个 Phase 4B Fix 已确认关闭

后续不要重复设计，也不要重新打开。

---

## 6.1 Defect 1 — `active_assessment` production authority

已修复。

新增统一 helper：

```python
bundle_first_assessment_projection(
    contract,
    legacy_assessment,
)
```

规则：

```text
Bundle exists
=> assessment 必须来自 Bundle.assessment
=> legacy active_assessment 完全忽略

Bundle missing
=> legacy checkpoint fallback
```

复审结果：

```text
typed Bundle 存在时，legacy active_assessment 不能覆盖 canonical assessment
```

已通过双向冲突型负向测试。

**状态：CLOSED。**

---

## 6.2 Defect 2 — `ResearchState.assessment` duplicate persistence

已修复。

`ResearchStateSchema.assessment` 字段暂时保留兼容旧 checkpoint，但新 production writers 显式写：

```python
"assessment": {}
```

不再保存 Bundle assessment 副本。

**状态：CLOSED。**

---

## 6.3 Defect 3 — Replan / Recovery initialization not Bundle-first

已修复。

`MainlineReplanAttempt.prepare()` 当前：

```text
if document_evidence_bundle exists:
    active_context     <- Bundle projection
    active_chunks      <- Bundle projection
    active_citations   <- Bundle projection
    active_assessment  <- Bundle.assessment projection
    active_query       <- Bundle.query
    active_scope       <- Bundle.retrieval_scope
    canonical hash     <- hash(Bundle)
else:
    legacy checkpoint projection fallback
```

并记录：

```text
evidence_projection_source = canonical_bundle
```

或：

```text
evidence_projection_source = legacy_checkpoint_projection
```

**状态：CLOSED。**

---

# 7. 当前唯一 Open Blocker

## Production Section recovery 会在 acceptance 前提前替换 active Bundle

核心文件：

```text
backend/apps/enterprise_document/workflows/section_execution_graph.py
```

这是 production 主链，不是 legacy fallback。

`stage_service.py` 当前优先执行：

```python
self.section_execution_graph.execute(...)
```

只有 Section graph 不存在时才 fallback 到旧：

```python
section_execution_coordinator.execute(...)
```

因此本问题必须以 `SectionExecutionGraph` 为准修复。

---

# 8. Blocker 的真实业务链

假设当前 Section 已有：

```text
Bundle A
assessment = sufficient
Writer Gate = permitted / grounded
```

随后 verifier / self-RAG / corrective retrieval 触发一次新 RAG：

```text
Bundle B
```

可能出现：

```text
retrieval technical success = true
context 非空
但 Bundle B assessment / Application Writer Gate = blocked
```

当前 `SectionExecutionGraph` 的部分路径会：

```text
Bundle B retrieval success
        ↓
先写：
active_evidence_bundle = B
active_context = B context
active_chunks = B chunks
active_citations = B citations
active_assessment = B assessment
active_writer_gate = Gate B
        ↓
后续才判断 Gate B
        ↓
Gate B blocked
        ↓
Section 进入 blocked path
```

此时原本可生成的 Bundle A 已经丢失为 active Evidence。

---

# 9. 为什么这是确定性 Phase 4B Defect

Phase 4B 已经明确冻结：

> **只有业务逻辑接受新的 retrieval 后，才能切换 `active_evidence_bundle`。**

并明确冻结：

> **recovery 被拒绝时，retained active Bundle 不变。**

因此：

```text
RAG retrieval success
!=
Application accepts candidate Evidence as active Evidence
```

这是两层不同业务语义：

```text
candidate EvidenceBundle
active EvidenceBundle
```

当前 production graph 把两者混成了一个动作。

所以这个问题不是优化项，也不是架构偏好，而是确定性的 implementation defect。

---

# 10. 正确目标语义

Recovery 开始时：

```text
current_bundle = Bundle A
current_gate   = Gate A
```

新 retrieval 得到：

```text
candidate_bundle = Bundle B
candidate_gate   = Gate B
```

在 Application acceptance decision 完成之前：

```text
active_evidence_bundle 必须仍然是 Bundle A
```

只有 acceptance 后才能：

```text
active_evidence_bundle = Bundle B
```

---

# 11. 四种业务 Case 冻结

## Case 1 — current permitted / candidate blocked

```text
A = permitted
B = blocked / human_review / invalid
```

必须：

```text
reject B
retain A
```

最终保持：

```text
active_evidence_bundle = A
active_context         = A projection
active_chunks          = A projection
active_citations       = A projection
active_assessment      = A assessment projection
active_writer_gate     = Gate A
active_query           = A query
active_scope           = A scope
```

同时把 B 作为一次失败/rejected recovery trace 记录，而不是作为 active Evidence。

---

## Case 2 — current blocked / candidate permitted

```text
A = blocked
B = permitted
```

必须：

```text
accept B
active = B
```

---

## Case 3 — current permitted / candidate permitted

保持现有 recovery 业务语义：

```text
accept B
active = B
```

本轮不要重新设计“B 是否比 A 更优”的 ranking policy。

---

## Case 4 — current blocked / candidate blocked

沿现有业务语义处理即可。

可以采用最新 B 作为最终 failed Evidence，也可以沿现有 blocked path 保持最新失败事实，但必须以当前已存在业务逻辑为准，不发明新 recovery policy。

本轮重点必须保证 Case 1 不再错误覆盖。

---

# 12. 推荐最小实现方式

不要新增新的持久化 schema 或复杂 CandidateEvidenceStore。

candidate 可以只是函数局部变量。

Recovery 前保留 current active state：

```python
previous_bundle
previous_context
previous_chunks
previous_citations
previous_assessment
previous_writer_gate
previous_query
previous_scope
```

新 retrieval 完成后先形成 candidate：

```python
candidate_bundle
candidate_context
candidate_chunks
candidate_citations
candidate_assessment
candidate_writer_gate
candidate_query
candidate_scope
```

计算：

```text
current_generation_permitted
candidate_generation_permitted
```

然后：

```text
if candidate accepted:
    active = candidate
else:
    active remains previous
```

禁止：

```text
先 active = candidate
再判断 candidate 是否接受
```

---

# 13. 可参考的既有正确业务逻辑

旧：

```text
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
```

已经存在：

```text
current_generation_permitted
recovery_generation_permitted
```

并有类似：

```text
current permitted + recovery blocked
=> KEEP current
```

本轮可以复用其业务判断原则。

但禁止：

- 把旧 coordinator 整体搬回 production；
- 恢复 legacy architecture；
- 让旧 coordinator 重新成为 production authority。

目标只是把已经正确的 recovery acceptance 语义补进当前 `SectionExecutionGraph`。

---

# 14. 必须扫描的生产代码

重点：

```text
backend/apps/enterprise_document/workflows/section_execution_graph.py
```

必须扫描：

- `_invoke_research_agent(...)`
- section retrieval
- `retrieve_more`
- self-RAG recovery
- `corrective_retrieval`
- verifier-driven research_more
- 所有 active Evidence 更新点

搜索：

```text
active_evidence_bundle
active_context
active_chunks
active_citations
active_assessment
active_writer_gate
active_query
active_scope
```

逐个确认：

```text
candidate accepted 前是否写 active state
```

最终目标：

```text
candidate recovery Bundle 在 Application acceptance 前写入 active state = 0
```

---

# 15. 必须补的 production graph 测试

旧 `SectionExecutionCoordinator` 测试不能作为当前 production graph 的唯一验收证据。

必须直接测试：

```text
SectionExecutionGraph
```

---

## Test 1 — blocked candidate 不得覆盖 permitted active Bundle

构造：

```text
Bundle A:
assessment = sufficient
Writer Gate = permitted / grounded

Bundle B:
retrieval success = true
context != empty
assessment = insufficient
Writer Gate = blocked
```

触发真正 production Section graph recovery。

验证：

```text
active_evidence_bundle == Bundle A
active_writer_gate      == Gate A
active_query            == A.query
active_scope            == A.scope
active_context           == A context projection
active_chunks            == A chunk projection
active_citations         == A citation projection
```

并验证 metadata / trace：

```text
candidate B recovery rejected
retained active scope/bundle = A
```

---

## Test 2 — permitted candidate 可以替换 blocked active Bundle

```text
A = blocked / insufficient
B = sufficient / permitted
```

验证：

```text
active_evidence_bundle == B
```

---

## Test 3 — permitted candidate 可以替换 permitted active Bundle

```text
A = permitted
B = permitted
```

验证 recovery 没被修成“永远保留旧 Bundle”。

最终：

```text
active_evidence_bundle == B
```

---

# 16. 上一轮专项测试基线

独立复审已经确认：

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
12 passed
```

Phase 3C：

```text
backend/tests/test_rag_interface_v2_phase3c_gate_ownership.py
4 passed
```

这两个基线修完后必须保持。

---

# 17. Broader Regression 的已知情况

不要把 broader E2E 红项混成当前 blocker，也不要隐瞒。

复审扩大运行：

```text
test_phase4_section_agents_mainline.py
```

排除一个已经明确过时的 legacy RAG Gate fixture 后，仍存在旧 Claim Audit / Document Hard Gate 相关失败。

`test_phase4_durable_resume_hitl.py` 也存在部分最终 strict Gate / Claim Audit 相关旧红项。

现有证据显示这些并不是上一轮 3 个 Fix 引入的 carrier regression。

因此最终报告必须区分：

```text
Focused Phase 4B acceptance
Broader regression
Known unrelated failures
```

不得只挑选一组通过测试写成“全部通过”。

也不得为了 broader E2E 全绿而：

- 恢复 RAG Gate authority；
- 修改 Gate policy；
- 放松 Claim Audit；
- 放松 Document Hard Gate；
- 恢复 legacy `gate_decision/generation_allowed` fixture。

---

# 18. Phase 4B 当前仍允许存在的 compatibility path

本轮仍不是 Phase 4C。

以下 compatibility path 可以继续存在：

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
ContextBundle.evidence.contract
legacy active_assessment field
legacy document_evidence_assessment field
ResearchState.assessment schema field
document_rag_context / chunks / citations compatibility projections
ContextManager projection -> Bundle compatibility recovery
WriterGovernance projection -> Bundle compatibility recovery
DocumentCitationRegistry projection -> Bundle compatibility recovery
```

其中：

```text
LegacyEvidenceAdapter
```

仍是正式允许的 legacy → canonical compatibility boundary。

这些真正清理安排在 Phase 4C。

---

# 19. Phase 4B 本轮明确 Non-goals

禁止进入：

- Phase 4C compatibility removal；
- Phase 5 ID Namespace Closure；
- accepted_evidence_ids Citation/Chunk ID 混用；
- Gate policy 重构；
- regex / claim classification 清理；
- Multi-Agent V2；
- Durable Memory；
- ContextCompiler；
- Evaluation / RAGAS；
- RAG V2 retrieval quality；
- 新数据库 / EvidenceStore / ArtifactStore；
- 模型替换；
- 部署；
- 大规模 Schema rename；
- Citation `citation_ids` → `citation_labels` rename。

不要 stage，不要 commit。

---

# 20. 关于 EvidenceBundle immutable 的边界说明

当前：

```text
EvidenceBundle immutable
```

主要依靠：

- ownership 规则；
- production path 不修改；
- typed carrier；
- architecture tests；
- mutation scan。

当前 `EvidenceBundleSchema` 的 Pydantic `SchemaBase` 并没有全局 `frozen=True`。

复审没有发现 Application 对 canonical Bundle 直接字段 mutation，所以这不是当前 Phase 4B blocker。

不要在本轮为了“物理冻结”大规模改所有 Schema config。

Phase 4D 最终应继续验证：

```text
Application canonical EvidenceBundle mutation = 0
```

---

# 21. 本轮验收

至少运行：

```bash
.venv/Scripts/python.exe -m pytest backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py -q
```

必须：

```text
12 passed 或更多
0 failed
```

Phase 3C：

```bash
.venv/Scripts/python.exe -m pytest backend/tests/test_rag_interface_v2_phase3c_gate_ownership.py -q
```

然后运行新增加的：

```text
SectionExecutionGraph recovery acceptance focused tests
```

以及相关：

```text
section_execution_graph
section recovery
checkpoint/resume
```

focused regression。

最后：

```bash
git diff --check
```

---

# 22. 最终静态验收条件

必须证明：

```text
candidate recovery Bundle 在 Application acceptance 前写入 active state = 0
```

必须证明：

```text
current permitted + candidate blocked
=> current Bundle retained
```

必须证明：

```text
current blocked + candidate permitted
=> candidate Bundle accepted
```

必须证明：

```text
current permitted + candidate permitted
=> candidate Bundle accepted
```

同时前一轮 3 个 Fix 的静态条件继续保持：

```text
typed Bundle 存在时 legacy active_assessment 不能成为 Evidence authority

ResearchState.assessment production canonical writer = 0

Replan typed path legacy document projection override = 0
```

---

# 23. 结果落盘要求

修复完成后新增一份最终 closure report，例如：

```text
docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FINAL_CLOSURE_20260814.md
```

必须包含：

1. blocker 根因；
2. 当前 production chain；
3. 修复后的 candidate → acceptance → active 语义；
4. 修改文件；
5. production graph 负向测试；
6. focused acceptance；
7. broader regression；
8. known unrelated failures；
9. `git diff --check`；
10. Phase 4C 仍存在的 compatibility paths；
11. 是否存在新的 Phase 4B blocker。

只有所有 Phase 4B acceptance invariants 满足，才写：

```text
PHASE 4B CLOSED
```

否则必须写：

```text
PHASE 4B NOT CLOSED
```

完成后停止，不自动进入 Phase 4C。

---

# 24. 新窗口直接执行提示词

可以直接把下面这段给新窗口：

```text
继续执行 D:\MyCode\rag-agent 的 Interface V2 Phase 4B Final Fix。

先读取：
1. docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4_CANONICAL_EVIDENCE_HANDOFF_20260814.md
2. docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FIX_CLOSURE_20260814.md
3. docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FINAL_FIX_HANDOFF_20260814.md

当前上一轮 3 个 Phase 4B defects 已经关闭，不要重开。

本轮只修 1 个 production blocker：
SectionExecutionGraph 的 recovery/self-RAG/corrective retrieval 在 Application acceptance 前提前把 candidate EvidenceBundle 写成 active_evidence_bundle。

冻结规则：
- retrieval success != acceptance；
- candidate Bundle 必须先经过 Application acceptance；
- current permitted + candidate blocked => reject candidate，retain current；
- current blocked + candidate permitted => accept candidate；
- current permitted + candidate permitted => accept candidate；
- 不重新设计 recovery ranking policy；
- 不进入 Phase 4C；
- 不改 Gate policy；
- 不修 Phase 5 ID namespace；
- 不 stage / commit。

重点审计并修改：
backend/apps/enterprise_document/workflows/section_execution_graph.py

对照旧 section_execution_coordinator.py 已有的 current_generation_permitted / recovery_generation_permitted 业务语义，但不要恢复旧 coordinator architecture。

必须补直接针对 production SectionExecutionGraph 的负向测试：
1. permitted current + blocked candidate => current retained；
2. blocked current + permitted candidate => candidate accepted；
3. permitted current + permitted candidate => candidate accepted。

完成后运行 Phase 4B canonical carrier、Phase 3C Gate ownership、Section graph recovery focused tests、checkpoint/resume focused tests、git diff --check。

结果落盘到新的 Phase 4B Final Closure 文档。

完成后只汇报，不进入 Phase 4C。
```

---

# 25. 一句话交接

> **Phase 4B 的 canonical carrier、assessment authority、ResearchState assessment、Replan Bundle-first 均已正确落地；当前最后一个 blocker 是 production `SectionExecutionGraph` 把 recovery retrieval technical success 错当成 active Evidence acceptance。修复原则确定：新 Bundle 先是 candidate，Application acceptance 后才能替换 active Bundle；被拒绝的 recovery 绝不能覆盖原本可用的 canonical Evidence。**
