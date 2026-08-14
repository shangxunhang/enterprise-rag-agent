# RAG–Agent Interface V2 Phase 3C Code Audit

日期：2026-08-14  
基线 HEAD：`94b2a5b`  
审计范围：Phase 3C — Remove RAG Gate Responsibility and Cut Reverse Dependency

## 结论

Phase 3C 通过。Application 已成为生产 Gate 唯一 authority；RAG 只输出 Evidence / Evidence Assessment facts。`backend/rag/** -> apps.enterprise_document.*` 反向依赖已经清零，RAG 内 GateDecision / DeliveryMode / generation_allowed / human_review_required 等业务动作语义已删除。

```text
PHASE 3C IMPLEMENTATION: PASS
PHASE 3C CONTRACT ACCEPTANCE: ACCEPTED
APPLICATION GATE IS PRODUCTION AUTHORITY
RAG PRODUCES EVIDENCE FACTS ONLY
RAG -> APPLICATION IMPORTS = 0
PHASE 3: COMPLETE
PHASE 4: NOT STARTED
```

---

## 1. Production Gate authority — PASS

真实生产调用链：

```text
RAG governance
  -> public RAGEvidenceAssessmentSchema
  -> EvidenceBundleSchema.assessment
  -> SectionExecutionCoordinator
  -> extract_writer_gate_binding(...)
  -> EvidenceGatePolicy().evaluate(evidence_bundle.assessment)
  -> WriterGateBindingSchema
  -> existing Writer / Document consumers
```

`backend/apps/enterprise_document/services/scheme_writer/writer_governance.py` 中 `extract_writer_gate_binding()`：

1. 从 `normalized_output["evidence"]` 验证为 `EvidenceBundleSchema`；
2. 直接将 `evidence_bundle.assessment` 传给 Application `EvidenceGatePolicy`；
3. 根据 Application `GateDecision.delivery_mode` 派生 `generation_allowed`；
4. 形成 Application-owned `WriterGateBindingSchema`。

生产代码搜索确认 `EvidenceGatePolicy` 的实际 consumer 已存在于 `writer_governance.py`，不再是 Phase 3B 时仅定义未接线。

Phase 3C focused test 还验证：即使输入 bundle 的 `extra` 人工塞入旧：

```text
gate_decision = BLOCKED
generation_allowed = false
```

Application 仍只根据 `bundle.assessment` 重新计算 GateDecision，旧 extra 无法覆盖 authority。

---

## 2. RAG reverse dependency — PASS

对 `backend/rag/**/*.py` 静态扫描：

```text
apps.enterprise_document                 0 files
GateDecisionSchema                       0 files
DeliveryMode                             0 files
generation_allowed                       0 files
gate_decision                            0 files
human_review_required                    0 files
fact_status_human_review_required        0 files
```

`governance_v2.py` 已删除：

```text
Application GateDecision import
DeliveryMode
GateDecisionSchema
_claim_risk_level()
_gate()
EvidenceGovernanceV2Result.gate_decision
business-action serialization
```

因此 Frozen dependency direction 已恢复为：

```text
Application / Agent
        -> Public Contract
        -> RAG
```

不存在 RAG -> Application Gate reverse dependency。

---

## 3. RAG governance semantics — PASS

`FactClaimPolicyResult` 当前只剩：

```text
eligible
certainty_level
required_qualifiers
reason_codes
```

已删除 `human_review_required`。

`_SetEvidenceFacts` 当前只承载：

```text
supported_claim_types
forbidden_claim_types
authoritative_evidence_present
historical_or_reference_evidence_present
required_qualifiers
prompt_evidence_source
prompt_evidence_contract_missing
reason_codes
```

无 review/delivery/generation action hint。

`RAGEvidenceRequirementsPolicySchema` 也不再包含 Phase 3 前属于 Application/Gate ownership 的：

```text
claim_risk_level
allow_framework_only
```

RAG 目前只做 Evidence eligibility / coverage / source authority / Evidence usage constraints / diagnostics。

---

## 4. ResultMapper boundary — PASS

`RAGResultMapper._assessment_from_governance()`：

```text
public_assessment -> RAGEvidenceAssessmentSchema
```

compatibility fallback 仅从 set assessment 投影 Evidence facts。

当前 assessment details 保留：

```text
evidence_governance_v2
legacy_corrective_assessment
presence_is_not_semantic_sufficiency
```

已不包含：

```text
gate_decision
```

`EvidenceBundle.extra` 当前保留：

```text
rag_service
retrieval_plan
evidence_quality
evidence_governance_v2
prompt_evidence_contract
prompt_evidence_source
```

已不包含：

```text
gate_decision
generation_allowed
delivery_mode
human_review_required
```

FakeRAG 同样无 Gate/action 输出。

---

## 5. Corrective retrieval naming — PASS

`backend/rag/application/parent_child_retrieval.py` 中 correction loop 仍然需要一个 RAG-internal decision：是否继续 corrective retrieval。

该语义现在使用：

```text
correction_decision
last_correction_decision
```

这是 RAG 内部 retrieval control decision，不是 Application delivery Gate。

静态扫描没有发现将其重新包装成 delivery/review/generation action，因此属于合法 RAG internal control。

---

## 6. Public Assessment boundary — PASS

`RAGEvidenceAssessmentSchema` 没有：

```text
human_review_required
claim_risk_level
delivery_mode
generation_allowed
publish_allowed
gate_decision
```

Phase 3C 没有通过 Public Contract 把业务动作偷偷塞回 RAG。

---

## 7. Phase 4 not started — PASS

Phase 4 的已知目标之一：移除通过 `RAGContext.extra["evidence_contract"]` 恢复 canonical Evidence truth 的路径。

当前仍可见：

```text
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py
(context.extra or {}).get("evidence_contract")
```

说明 Phase 4 尚未偷跑，符合本轮 Non-goal。

---

## 8. Independent test evidence

独立复跑 Phase 1–3C + Stage D/E + corrective/config-driven RAG：

```text
139 passed
0 failed
```

Phase 3C production wiring set：

```text
27 passed
0 failed
```

Phase 3C negative architecture / corrective retrieval set：

```text
29 passed
0 failed
```

关键修改文件 `py_compile`：PASS。

`git diff --check`：PASS，仅有既有 CRLF -> LF warning。

额外运行 facade/mainline 相关 28 tests：

```text
27 passed
1 failed
```

失败：

```text
test_phase5_langchain_takeover_and_legacy_retirement.py::
test_production_mainline_uses_runnables_and_retires_legacy_paths
```

实际失败点为既有 strict Document Hard Gate：`evaluate_document_gate` 在 `mode=strict` 下 block，最终 summary status 为 `failed`。日志未显示 RAG GateDecision 缺失、RAG->Application ownership 错误或 Application EvidenceGatePolicy 异常。本项不作为 Phase 3C blocker，且本轮不越界调整 Gate strictness。

---

## 9. Git state

审计时：

```text
HEAD = 94b2a5b
35 tracked files changed
1005 insertions(+)
728 deletions(-)
```

工作树包含 Phase 1–3C 累计未提交修改及相关 untracked tests/docs。

---

## 10. Final acceptance

Phase 3 的核心架构目标已经实现：

```text
RAG:
Evidence facts only

Application:
Gate policy / risk / delivery / human review / generation action
```

最终状态：

```text
PHASE 3A: ACCEPTED
PHASE 3B: ACCEPTED
FIX-P3B-01: PASS
PHASE 3C: ACCEPTED
PHASE 3: COMPLETE
PHASE 4: APPROVED TO START
```

下一阶段必须严格按 Frozen Phase 4 — Canonical Evidence State Migration 执行；不得在 Phase 3 收尾中提前处理 ToolGateway ID、Research loop、Memory、ContextCompiler、Writer/Verifier 等后续事项。
