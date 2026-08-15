# RAG-Agent Interface V2 Phase 4B Final Closure

日期：2026-08-14  
代码基线 HEAD：`f43f959`  
阶段：Interface V2 Phase 4B Final Fix  
结论：`PHASE 4B CLOSED`  

---

# 1. 本轮范围

本轮只关闭 Phase 4B 复审发现的最后一个 production blocker：

```text
backend/apps/enterprise_document/workflows/section_execution_graph.py
```

中的 recovery / self-RAG / corrective retrieval active Bundle acceptance 顺序错误。

本轮没有重新设计 Phase 4B，没有进入 Phase 4C，没有修改 Gate policy、Claim Audit、regex、Phase 5 ID namespace、Multi-Agent V2、Evaluation/RAGAS、retrieval strategy 或存储架构。

没有 stage，没有 commit。

---

# 2. Blocker 根因

Phase 4B 已冻结：

```text
EvidenceBundleSchema = sole canonical Evidence authority

每次 RAG invocation
-> 新 immutable EvidenceBundle

Application
-> 决定新的 retrieval 是否被接受为 active Evidence
```

因此：

```text
retrieval technical success
!=
Application accepts candidate Bundle
```

复审确认 production `SectionExecutionGraph` 存在两个同类缺口。

## 2.1 Research Tool Gateway recovery path

旧顺序实际为：

```text
Research Tool retrieval success
-> 得到 candidate Bundle B
-> _invoke_research_agent() 内直接把 B 写入 active_evidence_bundle / context / chunks / citations / assessment / writer_gate / query / scope
-> _retrieve_recovery() 再读取已经被替换后的 active_writer_gate
-> Gate B blocked 时 Section 被 B 阻断
```

因此当：

```text
A = current permitted
B = candidate blocked
```

旧代码无法再比较 A 与 B，因为 A 已经被 B 覆盖。

## 2.2 Direct recovery path

非 Tool Gateway recovery 也存在同样顺序：

```text
candidate retrieval success
-> candidate Writer Gate
-> 先 updates.update(active_... = candidate)
-> 再执行 writer enforcement
```

同样违反：

> 只有 Application 接受 candidate 后，candidate 才能成为 active Bundle。

---

# 3. 最终 acceptance 语义

本轮没有新增 ranking policy，而是复用旧 `SectionExecutionCoordinator` 已有且已经运行过的 recovery 业务原则。

Production graph 现在统一区分：

```text
current active Evidence
candidate recovery Evidence
```

并统一先计算：

```text
current_generation_permitted
candidate_context_available
candidate_generation_permitted
```

再决定是否 promotion。

## Case 1 — current permitted / candidate blocked

```text
A permitted
B blocked
```

结果：

```text
REJECT B
RETAIN A
```

保持：

```text
active_evidence_bundle = A
active_context         = A
active_chunks          = A
active_citations       = A
active_assessment      = A
active_writer_gate     = Gate A
active_query           = A query
active_scope           = A scope
```

candidate B 仍保留 retrieval trace / Agent payload / lightweight history，metadata 明确记录 rejected，但不取得 active authority。

## Case 2 — current blocked / candidate permitted

```text
A blocked
B permitted
```

结果：

```text
ACCEPT B
active = B
route = generate
```

## Case 3 — current permitted / candidate permitted

```text
A permitted
B permitted
```

保持现有 recovery 语义：

```text
ACCEPT B
active = B
```

本轮没有引入 Evidence ranking / score comparison。

## Case 4 — current blocked / candidate blocked

保持旧 coordinator 语义：

```text
如果 candidate 有 meaningful context 或 Application Gate result
-> 可以接受最新 failed candidate B 作为最终 blocked Evidence

否则
-> 不进行无意义 active promotion
```

本轮没有发明新的失败优选策略。

---

# 4. Production 修改

本轮直接修改 production：

```text
backend/apps/enterprise_document/workflows/section_execution_graph.py
```

## 4.1 新增统一 recovery candidate decision

新增：

```python
_recovery_candidate_decision(...)
```

输入：

```text
current_writer_gate
candidate_writer_gate
candidate_context
```

输出：

```text
candidate_accepted
current_generation_permitted
candidate_generation_permitted
rejection_reason
```

它只表达已经冻结的 recovery acceptance 语义，不创建新 Schema，不创建新持久化 owner。

## 4.2 `_invoke_research_agent(...)` 增加 recovery candidate 模式

增加局部控制参数：

```python
recovery_candidate: bool = False
```

它不是新业务 Schema，仅用于区分：

```text
initial Research retrieval
vs
recovery candidate retrieval
```

对于 recovery candidate：

```text
retrieval result
-> candidate Writer Gate
-> _recovery_candidate_decision()
-> candidate_accepted ?
   YES -> active promotion
   NO  -> active state untouched
```

因此 Gateway path 中：

```text
candidate acceptance 前 active_evidence_bundle 写入 = 0
```

## 4.3 Gateway rejected candidate 不再污染 Section state

进一步复核发现：即使 active Bundle 不再提前替换，`ResearchAgent` 的 `outcome.section_state` 仍可能基于 rejected candidate 把 Section lifecycle/projected Research status 改成：

```text
BLOCKED / INSUFFICIENT
```

这会形成间接的 candidate rejection 泄漏。

因此本轮同一 blocker 内补充冻结：

```text
candidate rejected
-> 保留 recovery 前 section_state
-> 但保留 candidate Research Agent payload / trace / recovery_count
```

从而满足：

```text
B rejected
-> B 不能直接或间接把 Section 阻断
```

这没有改变 ResearchState 的 canonical authority 规则，也没有新增 state owner。

## 4.4 Direct recovery path 改为 acceptance 后 promotion

非 Gateway path 现在顺序为：

```text
candidate Bundle
-> candidate projections
-> Application Writer Gate
-> _recovery_candidate_decision()
-> if rejected:
       retain current active state
   else:
       update active_* = candidate
```

原先“先 active update，后 writer enforcement”的顺序已移除。

---

# 5. Recovery metadata

本轮只增加 workflow diagnostics，不增加第二 Evidence authority。

主要包括：

```text
recovery_candidate_accepted
recovery_candidate_generation_permitted
recovery_candidate_current_generation_permitted
recovery_candidate_scope
recovery_candidate_chunk_count
recovery_candidate_citation_count
recovery_candidate_writer_gate_bound
recovery_candidate_rejection_reason
recovery_candidate_retained_scope
recovery_candidate_retained_query
recovery_candidate_section_state_retained

corrective_recovery_accepted
corrective_recovery_rejected
self_rag_recovery_accepted
self_rag_recovery_rejected
```

这些字段只记录 Application acceptance / workflow 诊断，不保存新的 canonical Evidence facts。

---

# 6. 新增 production graph 负向测试

本轮扩展：

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
```

该文件最终：

```text
16 passed
0 failed
```

其中新增的 recovery acceptance 直接覆盖真实 `SectionExecutionGraph._retrieve_recovery()`。

## Test 1 — permitted active + blocked corrective candidate

构造：

```text
Bundle A = sufficient / permitted / scope=section
Bundle B = insufficient / blocked / technical success / context non-empty
```

触发 production corrective recovery。

验证：

```text
active_evidence_bundle == A
active_writer_gate == Gate A
active_query == A.query
active_scope == A.scope
active_context == A projection
active_chunks == A projection
active_citations == A projection
active_assessment.status == sufficient
```

同时验证：

```text
corrective_retrieval_success == true
corrective_recovery_accepted == false
corrective_recovery_rejected == recovery_not_ordinary_generation_permitted
```

即明确证明：

```text
technical success != business acceptance
```

## Test 2 — Gateway blocked candidate 不得提前 promotion

直接走 production Research Tool Gateway recovery path。

候选 Research outcome 被测试故意构造成：

```text
candidate Bundle = insufficient / blocked
candidate outcome.section_state.lifecycle = BLOCKED
candidate outcome.section_state.research = INSUFFICIENT
```

当前 active A 为 permitted。

验证最终：

```text
active Bundle/Gate/query/scope/assessment 仍为 A
Section lifecycle 不为 BLOCKED
Research projection 不为 INSUFFICIENT
self_rag_recovery_accepted == false
```

这覆盖 `_invoke_research_agent()` 中最关键的提前 promotion blocker。

## Test 3 — blocked active + permitted corrective candidate

构造：

```text
A blocked
B permitted
```

验证：

```text
active_evidence_bundle == B
active_writer_gate == Gate B
active_scope == recovery
active_assessment.status == sufficient
corrective_recovery_accepted == true
```

## Test 4 — permitted active + permitted self-RAG candidate

防止修复过度。

构造：

```text
A permitted
B permitted
```

验证：

```text
active_evidence_bundle == B
active_scope == self_rag_recovery
self_rag_recovery_accepted == true
```

因此新逻辑不是“永远 retain current”，而是只拒绝不应覆盖当前有效 Evidence 的 candidate。

---

# 7. Focused acceptance

本窗口按交接文档使用当前仓库虚拟环境：

```text
.venv/Scripts/python.exe
```

打开工作区时 Final Fix 的未提交 production/test 修改已经存在；本窗口没有重复覆盖实现，而是对现有候选实现进行独立 production-path 审计、focused acceptance、broader regression 与静态复核。

## 7.1 Phase 4B canonical carrier / Final Fix

```text
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py

16 passed
0 failed
```

## 7.2 Phase 3C Gate ownership

```text
backend/tests/test_rag_interface_v2_phase3c_gate_ownership.py

4 passed
0 failed
```

Phase 3C 冻结保持：

```text
RAG -> Evidence facts
Application -> Gate authority
```

## 7.3 联合 focused regression

本窗口实际联合覆盖：

- Phase 4B canonical carrier + Final Fix（含 direct recovery 与 Research Tool Gateway recovery acceptance）；
- Phase 3C Gate ownership；
- Mainline recovery E2E entrypoints；
- Section execution public contracts；
- legacy insufficient assessment 不能覆盖 typed final writer gate；
- durable SQLite cross-instance saver；
- human-review pause state；
- task paused/resume terminal transition；
- durable application reject/cancel path。

本窗口实际结果：

```text
33 passed
0 failed
```

该 focused 集合刻意排除了已被交接文档标记为旧 Claim Audit / Document Hard Gate 期望漂移的 E2E success assertions，以及过期 RAG Gate fixture。

---

# 8. Broader regression 与已知无关红项

本窗口额外执行 broader regression：

```text
backend/tests/test_phase4_section_agents_mainline.py
backend/tests/test_phase4_durable_resume_hitl.py
backend/tests/test_phase3_section_execution_subgraph.py
```

其中前两个文件联合结果：

```text
6 passed
6 failed
```

分文件可归类为：

```text
test_phase4_section_agents_mainline.py : 1 passed / 4 failed
test_phase4_durable_resume_hitl.py      : 5 passed / 2 failed
```

另外：

```text
test_phase3_section_execution_subgraph.py : 0 passed / 1 failed
```

这些不是 Phase 4B focused acceptance 全绿声明的一部分，明确分类如下。

## 8.1 Claim Audit / Document Hard Gate 既有 strict failures

以下测试最终 `summary["status"]` 期待 success，但当前 FakeLLM 输出在现有 strict Claim Audit / Document Hard Gate 下失败：

```text
test_mainline_invokes_three_projected_section_agents
test_section_checkpoint_persists_typed_agent_payloads
test_verifier_research_more_drives_real_section_route
```

其中 `test_verifier_research_more_drives_real_section_route` 的运行日志明确显示：

```text
SelfRAGRetrieveMore START
SelfRAGRetrieveMore END success=True
第二次 scheme_section_generation 执行
最终 evidence_scope=self_rag_recovery
```

说明 recovery route 本身完成；最终 workflow 失败发生在后续 strict Claim Audit / Document Hard Gate。

本轮未修改 Claim Audit、Document Hard Gate、strict mode 或 FakeLLM classifier。

## 8.2 过期 RAG Gate fixture

```text
test_final_writer_gate_blocked_stops_before_writer_and_verifier
```

仍通过修改 legacy RAG output：

```text
extra["gate_decision"]
extra["generation_allowed"]
```

来制造 Gate。

这与 Phase 3C 冻结：

```text
Application = Gate authority
RAG = Evidence facts only
```

不一致。

本轮没有为了该测试恢复 RAG Gate authority。

## 8.3 Durable resume / Section subgraph 的旧 strict Gate 红项

本窗口额外确认：

```text
test_phase4_durable_resume_hitl.py::test_new_engine_resumes_without_replaying_completed_nodes
test_phase4_durable_resume_hitl.py::test_application_service_inspects_and_approves_across_new_instance
test_phase3_section_execution_subgraph.py::test_production_path_uses_section_subgraph_not_legacy_coordinator
```

均能执行到既有 strict Claim Audit / Document Hard Gate，最终 workflow status 因该 Gate 为 `failed`，而测试仍期待 `success/partial_success`。

运行日志没有显示 candidate recovery acceptance、typed Bundle checkpoint restore 或 SectionExecutionGraph serialization 异常；因此与交接文档记录的已知 Gate/test expectation drift 一致。

因此 broader E2E 红项保持记录，不纳入 Phase 4B Final Fix blocker 判定，也没有为了消除这些红项修改 Gate policy、Claim Audit 或 strict mode。

---

# 9. 静态验收

对 production `SectionExecutionGraph` 最终扫描：

## 9.1 Gateway recovery

candidate active promotion 只存在于：

```python
if candidate_accepted:
    updates.update(
        active_evidence_bundle=...
        active_context=...
        active_chunks=...
        active_citations=...
        active_assessment=...
        active_writer_gate=...
        active_query=...
        active_scope=...
    )
```

`elif` 中的另一处 `retrieval.evidence_bundle` active update 只属于：

```text
recovery_candidate == false
```

即 initial/normal Research retrieval，不属于 recovery candidate path。

## 9.2 Direct recovery

顺序为：

```text
_recovery_candidate_decision()
-> if not candidate_accepted: retain
-> else: active promotion
```

因此最终静态结论：

```text
production SectionExecutionGraph 中
candidate recovery Bundle 在 Application acceptance 前写入 active state
= 0
```

同时 runtime 负向测试证明：

```text
current permitted + candidate blocked
-> retained current Bundle
```

---

# 10. Static / diff check

本窗口逐点复核：

```text
Gateway recovery active promotion
-> 仅位于 if candidate_accepted 分支

Direct recovery active promotion
-> 位于 _recovery_candidate_decision() 之后的 accepted 分支

ResearchState.assessment production writers
-> 两处均显式写 {}

MainlineReplanAttempt typed path
-> document_evidence_bundle 存在时只从 Bundle 投影 context/chunks/citations/assessment/query/scope/hash
-> legacy document projections 只存在于 else compatibility fallback
```

最终：

```text
git diff --check
PASS
```

只有仓库既有：

```text
CRLF will be replaced by LF the next time Git touches it
```

warning，无 whitespace error。

---

# 11. Phase 4C 仍保留的 compatibility paths

本轮明确没有进入 Phase 4C。

仍保留：

```text
RAGContext.extra["evidence_contract"]
normalized["evidence"]
ContextBundle.evidence.contract

document_evidence_assessment
active_assessment
ResearchState.assessment schema field
旧 document_rag_context / retrieved_chunks / citations projection fields
```

当前 projection/dict -> Bundle compatibility reconstruction 扫描仍包括：

```text
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py
    RAGEvidenceContractSchema.model_validate(...)

backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py
    RAGEvidenceContractSchema.model_validate(...)

backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
    EvidenceBundleSchema.model_validate(...)

backend/context_manager/policies.py
    RAGEvidenceContractSchema.model_validate(...)
```

其中 `LegacyEvidenceAdapter` 是冻结允许的 legacy -> canonical upgrade boundary；其余 compatibility reconstruction 的真正清理属于 Phase 4C。

本轮没有顺手删除这些路径。

---

# 12. 本轮直接修改文件

本次 Final Fix 直接修改：

```text
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/tests/test_rag_interface_v2_phase4b_canonical_carrier.py
```

新增本 closure 文档：

```text
docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE4B_FINAL_CLOSURE_20260814.md
```

工作树仍包含此前 Phase 4B 累计修改、既有/并行未跟踪文档与 `nul`；本轮未清理、未 stage、未 commit。

---

# 13. 最终结论

本轮唯一 production blocker 已关闭：

```text
Recovery new Bundle
-> candidate
-> Application acceptance
-> accepted only then active promotion
```

验证同时覆盖：

```text
permitted -> blocked     = retain current
blocked   -> permitted   = accept candidate
permitted -> permitted   = accept candidate
blocked   -> blocked     = preserve existing failed-candidate semantics
Gateway rejected state   = cannot indirectly block Section
```

Focused acceptance：

```text
16/16 Phase 4B canonical/final-fix
4/4   Phase 3C Gate authority
33/33 combined focused regression
```

Broader known unrelated regression：

```text
Phase 4 section agents : 1 passed / 4 failed
Phase 4 durable resume : 5 passed / 2 failed
Phase 3 section subgraph: 0 passed / 1 failed
```

已按 Claim Audit / Document Hard Gate / stale RAG Gate fixture 单独记录，没有越界修改。

最终：

```text
PHASE 4B CLOSED
```

到此停止。

**没有自动进入 Phase 4C，没有 stage，没有 commit。**
