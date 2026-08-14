# RAG–Agent Interface V2 Phase 3B Code Audit

日期：2026-08-14  
基线 HEAD：`94b2a5b`  
审计范围：Phase 3B — Application Evidence Gate Policy + Old/New Parity

## 结论

Phase 3B 主体实现正确：Application 已拥有独立 deterministic `EvidenceGatePolicy`，该 Policy 只依赖 Public `RAGEvidenceAssessmentSchema`、Shared ClaimType 与 Application Gate schema；没有读取 RAG internal candidate/assessment、`assessment.details` 或 `fact_status_human_review_required`。旧 RAG Gate 仍保持生产 authority，Phase 3C 未开始。

FIX-P3B-01 已完成：Application Gate 现在独立拥有 canonical policy identity `candidate_capability_union_v1`，parity helper 不再从 RAG internal `result.requirements.policy_id` 注入 policy id；默认 Application decision 的 policy identity 与当前旧 production oracle 一致。

```text
FIX-P3B-01: PASS
PHASE 3B IMPLEMENTATION: PASS
PHASE 3B PARITY LOGIC: PASS
PHASE 3B CONTRACT ACCEPTANCE: ACCEPTED
PHASE 3C: APPROVED TO START
```

---

## 1. Application Gate ownership — PASS

新增：

```text
backend/apps/enterprise_document/services/scheme_writer/evidence_gate_policy.py
```

输入：

```text
RAGEvidenceAssessmentSchema
+ Application-owned policy_id
```

输出：

```text
GateDecisionSchema
```

静态核验确认 Policy 不 import：

```text
rag.evidence.*
rag.schema.evidence_assessment_v2
rag.schema.retrieval_candidate_v2
rag.catalog.document_profile
```

也不读取：

```text
fact_status_human_review_required
assessment.details
```

允许依赖只有：

```text
schemas.rag
schemas.claim
apps.enterprise_document.schemas.gate_decision
```

这符合 Frozen ownership：RAG 产 Evidence Facts，Application 产 Business Actions。

---

## 2. Claim risk / Delivery / Human Review ownership — PASS

`EvidenceGatePolicy` 在 Application 内自行计算：

- current-project / procurement -> HIGH
- policy / technical specification -> MEDIUM
- 其他 -> LOW

`delivery_mode`、`human_review_required`、allowed/forbidden claims 以及 Gate reason codes 均由 Application Policy 生成。

尤其 `human_review_required` 没有从 RAG internal flag 搬运。

当前旧 `resolve_fact_claim_eligibility()` 中，真正同时满足：

```text
eligible == True
human_review_required == True
```

的 FactStatus 只有：

```text
estimated
proposed
planned
```

它们分别公开稳定 reason codes：

```text
fact_status_estimated
fact_status_proposed
fact_status_planned
```

新 Application Policy 使用这些 Public reason codes 重建 review action，当前语义完整。

`UNKNOWN`、非权威 `CONFIRMED`、无正式决策依据的 `DECIDED` 虽然旧 internal policy 标记 review，但同时是 ineligible，因此不会进入旧 `_gate()` 的 eligible `status_human_review` 聚合，不构成 Public Fact 缺失。

---

## 3. Old/New Gate parity — PASS

新增测试：

```text
backend/tests/test_rag_interface_v2_phase3b_gate_parity.py
```

覆盖：

1. sufficient authoritative
2. sufficient template-only
3. sufficient historical/reference
4. sufficient current-project non-authoritative
5. estimated
6. proposed
7. planned
8. insufficient partial high-risk
9. insufficient partial low-risk
10. insufficient absent-like
11. conflicted
12. prompt contract missing
13. legacy prompt fallback
14. architecture dependency rule

逐字段比较：

```text
evidence_status
delivery_mode
claim_risk_level
allowed_claim_types
forbidden_claim_types
required_qualifiers
covered_topics
missing_topics
human_review_required
reason_codes
```

以及核心 metadata：

```text
authoritative_evidence_present
historical_or_reference_evidence_present
prompt_evidence_source
```

独立复跑：

```text
Phase 3B parity: 14 passed
Phase 3A + existing Stage D/Gate regression: 46 passed
py_compile: PASS
git diff --check: PASS
```

仅存在现存 CRLF -> LF warning，无 whitespace error。

---

## 4. Production authority — PASS

生产代码中除 Policy 定义文件自身外没有 `EvidenceGatePolicy` consumer。

当前仍是：

```text
RAG old _gate()
-> authoritative GateDecision
```

仍存在：

```text
backend/rag/evidence/governance_v2.py::_claim_risk_level
backend/rag/evidence/governance_v2.py::_gate
EvidenceGovernanceV2Result.gate_decision
generation_allowed compatibility output
```

因此 Phase 3C 没有偷跑。

---

## 5. Blocker — Application policy_id default is not self-contained

### 当前代码事实

旧 RAG internal wrapper class 声明默认：

```text
conservative_section_policy_v1
```

但当前正常 `resolve_evidence_requirements_v2()` 生产路径在 Application 不再传 `policy_id` 后，实际赋值是：

```text
candidate_capability_union_v1
```

因此正常旧 Gate 的 `GateDecisionSchema.policy_id` 实际为：

```text
candidate_capability_union_v1
```

而新 Application Policy 当前默认：

```python
policy_id: str = "conservative_section_policy_v1"
```

### FIX-P3B-01 最终复审

Application 现在定义：

```text
APPLICATION_EVIDENCE_GATE_POLICY_ID = "candidate_capability_union_v1"
```

新 Gate path：

```python
EvidenceGatePolicy().evaluate(result.public_assessment)
```

不再读取或注入：

```text
result.requirements.policy_id
policy_id=result.requirements.policy_id
```

新增默认 identity 测试同时验证：

```text
Application default policy id
== candidate_capability_union_v1
== old normal production oracle GateDecision.policy_id
```

静态扫描确认 `EvidenceGatePolicy` 仍无 RAG internal import，且 production 中除 policy 定义本身外无 `EvidenceGatePolicy` consumer，因此 Phase 3C 尚未偷跑。

独立复跑证据：

```text
Phase 3B parity: 15 passed
Phase 3A + Stage D/Gate focused: 51 passed
py_compile: PASS
git diff --check: PASS
```

---

## 6. Final status

```text
FIX-P3B-01: PASS
PHASE 3B IMPLEMENTATION: PASS
APPLICATION GATE LOGIC: PASS
OLD/NEW BUSINESS DECISION PARITY: PASS
APPLICATION POLICY IDENTITY CLOSURE: PASS
PHASE 3B CONTRACT ACCEPTANCE: ACCEPTED
PHASE 3C: APPROVED TO START
```
