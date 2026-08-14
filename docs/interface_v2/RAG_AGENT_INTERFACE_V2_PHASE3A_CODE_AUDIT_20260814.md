# RAG–Agent Interface V2 Phase 3A Code Audit

日期：2026-08-14  
基线 HEAD：`94b2a5b`  
审计范围：Phase 3A — Public Evidence Assessment Facts Closure

## 结论

Phase 3A 通过。Public Evidence Assessment 已显式承载未来 Application Gate 所需的 Evidence facts；未发现业务动作字段被提升进 Public Contract；旧 `_gate()` 行为保持不变；Phase 3B/3C 尚未开始。

```text
PHASE 3A IMPLEMENTATION: PASS
PHASE 3A CONTRACT ACCEPTANCE: ACCEPTED
PHASE 3B: APPROVED TO START
PHASE 3C: NOT STARTED
```

## 1. Public Evidence Assessment — PASS

`backend/schemas/rag.py` 直接演进现有 `RAGEvidenceAssessmentSchema`，未创建平行 Gate input / Assessment V2 schema。

新增稳定 Evidence facts：

```text
supported_claim_types
forbidden_claim_types
authoritative_evidence_present
historical_or_reference_evidence_present
required_qualifiers
prompt_evidence_source
prompt_evidence_contract_missing
```

现有 coverage/status/conflict facts 继续保留。

Public schema 中不存在：

```text
human_review_required
claim_risk_level
delivery_mode
generation_allowed
gate_decision
```

符合 Frozen Decision：RAG 只描述 Evidence；Application 决定业务动作。

## 2. Claim capability aggregation — PASS

`_set_evidence_facts()` 的行为与旧 `_gate()` 保持一致：

```text
supported_claim_types
= eligible item supported union

forbidden_claim_types
= all item forbidden union - supported
```

没有重新设计 ClaimType taxonomy，也没有恢复 `requested_claim_types` authority。

## 3. Authority / reference facts — PASS

旧 `_gate()` 中的：

```text
authoritative
historical_or_reference
```

被提取为 `_SetEvidenceFacts`，同时单向投影为 Public Assessment：

```text
authoritative_evidence_present
historical_or_reference_evidence_present
```

判定规则没有改变。

Application 后续不需要 import RAG internal Candidate/Profile schema 来重新计算这些事实。

## 4. FactStatus usage constraints — PASS

`required_qualifiers` 继续来自 eligible item 的 FactStatus evidence policy，并进入 Public Assessment。

例如 estimated/proposed/planned 的限定表达仍保持原语义。

`human_review_required` 没有进入 Public Assessment。

当前 RAG internal `_SetEvidenceFacts.fact_status_human_review_required` 仍仅用于旧 `_gate()` parity。这是 Phase 3A 允许的临时 internal implementation detail，不属于跨边界 authority。

审计确认：当前 eligible evidence 中导致该 internal flag 的情形，同时已有公开 `required_qualifiers` / `reason_codes` 事实，因此 Phase 3B 可以在 Application 中依据 Public facts 重建业务 review 决策，无需把该 flag 提升为 Public Contract。

Phase 3B 完成后不应继续依赖这个 internal action hint。

## 5. Prompt Evidence facts — PASS

以下已显式进入 Public Assessment：

```text
prompt_evidence_source
prompt_evidence_contract_missing
```

Application 后续无需从：

```text
assessment.details
EvidenceBundle.extra
gate_decision.metadata
```

恢复这两个 Evidence facts。

## 6. ResultMapper authority direction — PASS

`RAGResultMapper._assessment_from_governance()` 优先读取：

```text
governance.public_assessment
```

legacy fallback 只从 `set_assessment` 投影 status/coverage/topic/conflict/reason facts。

没有从旧：

```text
gate_decision.allowed_claim_types
gate_decision.metadata.authoritative_evidence_present
```

反向恢复 Public Evidence authority。

旧 `details["gate_decision"]` 仍作为 Phase 3C 前兼容信息保留，但不参与 Public fact authority。

## 7. Old Gate behavior parity — PASS

`_gate()` 仍由 RAG 内部构造，仅为了 Phase 3B 前行为基线。

3A 的结构变化为：

```text
internal evidence state
    -> _SetEvidenceFacts
        -> Public Assessment projection
        -> old _gate() parity consumer
```

未搬 `_gate()`、未搬 `_claim_risk_level()`、未改变 DeliveryMode 规则。

Stage D/Gate focused regression 独立复跑：

```text
36 passed
0 failed
```

覆盖 sufficient / partial-like / absent-like / conflict / prompt visibility / FactStatus 等既有行为。

## 8. Tests — PASS

Phase 3A focused：

```text
10 passed
0 failed
```

联合 Phase 1/2/3A + RAG pipeline + LangChain + Stage D focused：

```text
80 passed
0 failed
```

`git diff --check`：通过，仅有既有 CRLF -> LF 提示。

## 9. Architecture scope — PASS

当前 RAG -> Application 反向 import 仍只有 Phase 3 预期残留：

```text
backend/rag/evidence/governance_v2.py
-> apps.enterprise_document.schemas.gate_decision
```

这属于 Phase 3B/3C 待迁移对象，不是 3A blocker。

未发现 Phase 3B/3C 偷跑：

- 未新建 Application `evidence_gate_policy.py`；
- 未删除 RAG `_gate()`；
- 未删除 `DeliveryMode/GateDecisionSchema` import；
- 未删除 `generation_allowed` compatibility output。

## 10. Phase 3B 放行条件

Phase 3A 已满足 3B 前置条件。

Phase 3B 应严格执行：

```text
Public RAGEvidenceAssessmentSchema
+ Application-owned policy/context
-> GateDecisionSchema
```

并以当前 RAG `_gate()` 为 parity oracle。

Phase 3B 不得重新读取 RAG internal Candidate / EvidenceAssessmentV2 / DocumentProfile 以恢复 Evidence facts。

Phase 3B 需要重点证明：

1. old RAG `_gate()` vs new Application Gate result parity；
2. Application 独立计算 `claim_risk_level`；
3. Application 独立决定 `human_review_required`；
4. Public Evidence facts 足以完成上述决定；
5. 仍暂不删除旧 RAG Gate，删除动作留给 Phase 3C。
