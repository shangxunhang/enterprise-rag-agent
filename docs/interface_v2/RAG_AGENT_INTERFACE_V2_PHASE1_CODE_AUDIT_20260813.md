# RAG–Agent Interface V2 Phase 1 Code Audit

日期：2026-08-13  
基线 HEAD：`94b2a5b`  

## 结论

**FIX-P1-01 已完成并复审通过。Phase 1 正式 ACCEPTED，可进入 Phase 2。**

```text
PHASE 1 IMPLEMENTATION: PASS
PHASE 1 CONTRACT ACCEPTANCE: ACCEPTED
FIX-P1-01: PASS
PHASE 2: APPROVED TO START
```

## 已通过项

### ClaimType — PASS

- 唯一 canonical 定义：`backend/schemas/claim.py`。
- 13 个 enum value 未改变。
- `rag.catalog.document_profile` 改为 import/re-export。
- 旧路径与新路径指向同一个 Enum object。
- 未发现第二套 `ClaimType` 定义。

### EvidenceStatus — PASS

- 唯一 canonical 定义：`backend/schemas/rag.py`。
- 只保留：`sufficient / insufficient / conflicted`。
- 未发现 `EvidenceStatus.PARTIAL / ABSENT / NOT_ASSESSED` 生产成员引用。
- `partial / absent` 兼容折叠为 `insufficient`。
- `not_assessed` 已与 EvidenceStatus 分离，通过 `assessment_state = pending | completed` 表达。

### EvidenceRequirement — 主体 PASS

- Public authority 已建立：`EvidenceRequirementSchema`、`EvidenceRequirementsSchema`。
- 结构为 Section Envelope + item requirements。
- `requirement_id` 为确定性生成，不使用随机 UUID。
- `requested_claim_types` 仍只兼容读取，当前没有行为消费者。
- Application 已停止直接 import `resolve_evidence_requirements_v2`。

### Phase Scope — PASS

`RAGToolInputSchema` 尚未执行 Phase 2 字段提升；未开始 Gate 拆分、Canonical Evidence state migration、ToolGateway ID closure、Phase 6 import cleanup、RetrievalAction、Memory、ContextCompiler V2。

## 已关闭 Blocker：EvidenceRequirementsSchema canonical round-trip

初次审计时，`EvidenceRequirementsSchema.required_topics` 使用 `@computed_field`，同时 Schema 配置 `extra="forbid"`，导致 canonical dump 无法 round-trip。该问题现已通过 FIX-P1-01 关闭：`required_topics` 改为只读 `@property`，不再进入 canonical serialization。

修复后的 Public Contract 满足：

```text
construct -> serialize -> persist/wire -> validate -> same canonical meaning
```

否则 Phase 2 把 `evidence_requirements` 提升为正式 `RAGToolInputSchema` 字段后，持久化、传输、重放都存在风险。

### FIX-P1-01 复审结果

已确认继续保持：

```text
requirements[] = canonical authority
required_topics = one-way compatibility projection
```

禁止根据 `required_topics` 反向重建 canonical requirements。

实际采用最小修复：

1. `required_topics` 从 `@computed_field` 改为只读 `@property`；
2. canonical `model_dump(mode="json")` 不再包含 `required_topics`；
3. `extra="forbid"` 保持不变；
4. 显式传入 `required_topics` 仍被拒绝，因此不会形成第二套 input authority；
5. `required_topics` 读取值继续严格等于 `[item.topic for item in requirements]`；
6. 未发现生产消费者依赖 `EvidenceRequirementsSchema.model_dump()` 必须携带 `required_topics`。

## 非阻塞观察

1. `RAGEvidenceRequirementsPolicySchema` 仍带 `claim_risk_level / allow_framework_only`。它们已 Frozen 为 Application/Gate ownership，但 Phase 2 明确负责 D 类字段迁移，因此当前只记账，不扩张 Phase 1 hotfix。
2. `rag/evidence/governance_v2.py -> apps.enterprise_document.schemas.gate_decision` 的反向依赖仍存在，这是 Phase 3，不算 Phase 1 未完成。
3. `SchemeEvidenceService` 当前使用 public `EvidenceRequirementsSchema.from_topics()` 构造兼容 fallback。Phase 2 迁移 typed request 时不要继续把新的 Requirement reasoning 堆进 Application；最终仍应由 Planner V2 或 RAG internal fallback resolver 负责 runtime requirement 生成。

## Gate 行为复核

主链主要处置关系未发现主动改坏：

```text
sufficient -> grounded / qualified
partial-like insufficient -> qualified / human_review
absent-like insufficient -> blocked
conflicted -> human_review / blocked
```

变化只在 canonical status 从 `PARTIAL / ABSENT` 收敛为 `INSUFFICIENT + coverage/missing facts`。

## 测试复核

审计复跑核心 focused suite：

```text
66 passed in 0.73s
```

扩大后的相关测试：

```text
104 passed, 1 failed
```

失败用例是 grounding repair fake model 缺少 `model_gateway`，失败位置与本轮三项 Contract diff 无直接关系；未将其计为 Phase 1 regression。

另复跑 legacy absent mainline 用例通过。Stage F release 仍存在当前 Gate/fixture 既有失败，因此不声称全量测试通过。

## 最终放行验证

复审确认：

- `test_rag_interface_v2_phase1_contracts.py`：`13 passed`；
- `test_step_12_evidence_contract.py`：`9 passed`；
- `test_stage_d_evidence_governance_v2.py`：`12 passed`；
- canonical dump 不包含 `required_topics`；
- round-trip 测试通过；
- 非法 `required_topics` 第二 authority 输入被 `extra="forbid"` 拒绝；
- ClaimType / EvidenceStatus authority 未变化；
- `RAGToolInputSchema` 仍保持 Phase 1 前的 public input 形状，Phase 2 未偷跑；
- 生产 `required_topics` consumers 只通过 property 或 legacy governance fallback 使用，没有发现依赖 canonical dump 携带该字段的消费者。

最终状态：

```text
PHASE 1 ACCEPTED
READY FOR PHASE 2
```

