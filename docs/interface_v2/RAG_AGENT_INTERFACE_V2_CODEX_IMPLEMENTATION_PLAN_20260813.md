# RAG–Agent Interface V2 Codex 实施方案

日期：2026-08-13  
状态：**Frozen / Ready for Implementation**  
适用仓库：`D:\MyCode\rag-agent`  
基线：`94b2a5b checkpoint: freeze gate shadow closure before multi-agent v2`

关联文档：

- `docs/interface_v2/RAG_AGENT_EVIDENCE_CONTRACT_AUTHORITY_AUDIT_20260813.md`
- `docs/interface_v2/RAG_AGENT_EVIDENCE_INTERFACE_CLOSURE_IMPLEMENTATION_PLAN_20260813.md`
- `docs/RAG架构_V2_收敛版_20260809.md`
- `docs/多agent架构_V2_收敛版_20260809.md`

> 本文覆盖前一版接口化实施方案中尚待 Human Freeze 的部分。Codex 实施时以本文的冻结结论为准；不得自行重新设计 Schema、Ownership、Gate 或 RetrievalAction。

---

# 1. Task Goal

本轮唯一目标：

> **完成 RAG–Agent Evidence Boundary Closure：切断 RAG 与 Application 的双向内部依赖；统一 Evidence / EvidenceRequirement / EvidenceStatus / ClaimType 的唯一 authority；将业务 Gate 从 RAG 中移出；收口 `RAGToolInputSchema.extra`；使 Agent/Application 只通过稳定 Public Contract + `RAGServicePort` 使用 RAG。**

本轮不是 RAG V2 功能扩展，不实现 Multi-Agent V2 新能力。

---

# 2. 已冻结的架构决策

以下结论已完成 Human Freeze，Codex 不得重新讨论或改写。

## F-01 EvidencePackage 唯一 canonical

```text
EvidenceBundleSchema
= 全系统唯一 canonical EvidencePackage
```

`RAGEvidenceContractSchema` 继续只是 `EvidenceBundleSchema` 的 alias，不得新建：

- `EvidencePackageV2Schema`
- `PublicEvidencePackageSchema`
- `CanonicalEvidencePackageSchema`
- 任何第四套 Evidence Schema

允许：

```text
EvidenceBundleSchema -> Context / Citation / Research / Section projection
```

禁止：

```text
projection -> 重新构造新的 canonical Evidence truth
```

## F-02 EvidenceRequirement：Section Envelope + Item Requirement

最终语义：

```text
一个 Section
    ↓
EvidenceRequirements Envelope
    └── requirements[]
          ├── EvidenceRequirement
          ├── EvidenceRequirement
          └── ...
```

Public Contract 的目标最小结构：

```yaml
EvidenceRequirements:
  section_id: string
  section_title: string | null
  section_type: string
  requirements:
    - requirement_id: string
      topic: string
      purpose: string | null
      importance: required | optional
      expected_source_types: []
```

规则：

1. `required_topics[]` 不再是 canonical，只能由 `requirements[].topic` 单向投影。
2. `requirement_id` 必须稳定、确定性生成；禁止随机 UUID 破坏重放。
3. P0 阶段 Planner 尚未升级时，RAG internal fallback resolver 可以根据 Section/query 生成兼容 Requirement instance。
4. 本轮不实现 Planner V2 的 Requirement reasoning。

### 旧 `EvidenceRequirementsV2Schema` 字段处理

| 旧字段 | Frozen 归属 | 本轮动作 |
|---|---|---|
| `schema_version` | Public Contract | 保留 |
| `section_id` | Public Contract | 保留到 Section Envelope |
| `section_type` | Public Contract | 保留到 Section Envelope |
| `required_topics` | compatibility projection | 由 `requirements[].topic` 派生；不再作为 authority |
| `requested_claim_types` | legacy / currently ignored | **不提升为 V2 public semantics**；兼容读取可保留，但不得影响行为 |
| `policy_id` | RAG Internal | 收回 RAG policy/config |
| `coverage_strategy` | RAG Internal | 收回 RAG assessment policy |
| `claim_risk_level` | Application/Gate | 从 Requirement 移出 |
| `allow_framework_only` | Application/Gate | 从 Requirement 移出 |
| `metadata` | extra/diagnostic | 仅保留真正扩展信息 |

## F-03 EvidenceStatus 统一为三态

全系统 canonical EvidenceStatus 只允许：

```text
SUFFICIENT
INSUFFICIENT
CONFLICTED
```

语义：

```text
SUFFICIENT   = 当前 Evidence 满足当前 EvidenceRequirements
INSUFFICIENT = 当前 Evidence 无法满足当前 EvidenceRequirements
CONFLICTED   = 当前 Evidence 存在尚未解决的实质性冲突
```

旧状态迁移：

```text
PARTIAL      -> INSUFFICIENT + coverage/missing_requirements 表达原因
ABSENT       -> INSUFFICIENT + evidence_available=false / coverage=0 表达原因
NOT_ASSESSED -> 不属于 EvidenceStatus；由 Assessment lifecycle 表达
```

Assessment lifecycle 只需最小两态：

```text
pending
completed
```

推荐 Public Assessment 形状：

```text
assessment_state = pending | completed
evidence_status  = EvidenceStatus | null
```

约束：

- `pending` 时 `evidence_status` 必须为 null；
- `completed` 时 `evidence_status` 必须非 null。

不要增加 FAILED/CANCELLED 等新 lifecycle；执行失败继续由现有 `ExecutionStatus/ErrorSchema` 表达。

## F-04 ClaimType 属于 Shared Public Domain Contract

```text
ClaimType
Authority = Shared Public Domain Contract
```

规则：

1. 全系统只能有一份 Enum 定义；
2. RAG / Gate / Writer / SemanticClaimClassifier 全部消费同一份；
3. 不再以 `rag.catalog.document_profile.ClaimType` 作为 canonical 定义位置；
4. 不归 `apps.enterprise_document`；
5. 迁移期旧路径只允许 re-export/alias，不允许复制 Enum；
6. 本轮**不修改 13 类 taxonomy**；
7. 本轮**不修改 DocumentType -> allowed/forbidden ClaimType 业务规则**。

目标公共位置：

```text
backend/schemas/claim.py
```

至少迁移：

```text
ClaimType
CURRENT_PROJECT_CLAIM_TYPES
TEMPLATE_ONLY_CLAIM_TYPES
```

`rag/catalog/document_profile.py` 可在迁移期从 `schemas.claim` re-export，直到所有生产消费者迁完。

## F-05 DeliveryMode / GateDecision 属于 Application

```text
RAG = Evidence Retrieval + Evidence Assessment
Application/Gate = Delivery Decision
```

RAG 不得最终生产：

- `DeliveryMode`
- `GateDecisionSchema`
- `generation_allowed`
- `publish_allowed`
- `human_review_required`

Application Gate 可以读取 RAG 的 Evidence Assessment 后，根据现有规则产生这些决策。

**本轮必须保持现有 Gate policy 业务效果和 `GATE_ENFORCEMENT_MODE=shadow` 行为，不重新设计 Gate。**

当 canonical `EvidenceStatus=INSUFFICIENT` 时，Application Gate 若需要保持旧 `PARTIAL` / `ABSENT` 分支行为，必须使用结构化事实区分：

```text
partial-like:
  evidence_available = true
  coverage_ratio > 0
  missing requirements/topics 非空

absent-like:
  evidence_available = false
  或 coverage_ratio == 0
```

不得为了 Gate 方便把 `PARTIAL/ABSENT` 重新塞回 canonical EvidenceStatus。

## F-06 RetrievalAction 本轮只冻结 ownership，不实现 V2

```text
RetrievalAction = Research <-> RAG Public Boundary concept
```

但本轮：

- 不新增正式 `RetrievalActionSchema`；
- 不冻结 action values；
- 不实现 bounded Research loop；
- 不把 `RetrievalPlan` / `QueryTransformMode` / `ResearchRecommendedAction` 合并成 RetrievalAction；
- 不向 Agent 暴露 Dense/BM25/RRF/Milvus/Reranker 参数。

RetrievalAction 的正式实现留给后续 Multi-Agent V2 Research 阶段。

## F-07 Evidence / Citation / Chunk ID namespace 严格分离

```text
Evidence ID  = canonical Evidence item identity，例如 E1/E2
Citation ID  = citation/binding identity，例如 C1/C2 或 document-global citation id
Chunk ID     = retrieval unit identity
```

任何字段名为：

```text
evidence_id
evidence_ids
accepted_evidence_ids
selected_evidence_ids
```

都只能放 canonical Evidence ID。

禁止 Citation ID / Chunk ID 混入 Evidence ID 字段。

---

# 3. `RAGToolInputSchema.extra` Frozen 分类

分类规则：

```text
A = Public Contract：跨边界稳定语义/约束
B = RAG Internal：只控制 RAG 内部实现
C = extra/metadata：非核心、可选、诊断/实验信息
D = Application/Gate：根本不属于 RAG 职责
```

## 3.1 Frozen Field Mapping

| 当前字段 | Frozen 分类 | 目标动作 |
|---|---|---|
| `extra.retrieval_trace_id` | A | 提升为正式 request 字段 |
| `extra.retrieval_scope` | A | 提升为正式 request 字段；删除重复副本 |
| `extra_metadata.task_type` | A | 提升为正式 request 字段 |
| `extra_metadata.evidence_requirements` | A | 提升为 typed Public Contract |
| `evidence_requirements.schema_version` | A | 保留 |
| `evidence_requirements.section_id` | A | Envelope 字段 |
| `evidence_requirements.section_type` | A | Envelope 字段 |
| `evidence_requirements.required_topics` | A/legacy projection | 从 item `topic` 派生 |
| `evidence_requirements.requested_claim_types` | legacy ignored | 不提升；兼容读取后退役 |
| `evidence_requirements.policy_id` | B | 收回 RAG internal policy |
| `evidence_requirements.coverage_strategy` | B | 收回 RAG internal policy |
| `evidence_requirements.claim_risk_level` | D | 移到 Application/Gate |
| `evidence_requirements.allow_framework_only` | D | 移到 Application/Gate |
| `evidence_requirements.metadata` | C | 保留真正 metadata |
| `extra_metadata.retrieval_scope` | A/duplicate | 删除重复，只留正式字段 |
| `extra_metadata.section_id` | A/duplicate | 以 Requirement Envelope 为 authority |
| `extra_metadata.section_title` | A | 进入 Requirement Envelope |
| `context_requirements.model_context_window` | B | 使用 RAG 已有 typed config，不由 Application 传入 |
| `context_requirements.prompt_reserved_tokens` | B | 使用 RAG 已有 typed config，不由 Application 传入 |
| `context_requirements.section_token_budget` | A constraint | 提升为正式 caller budget（建议名称 `max_context_tokens`） |
| `context_requirements.max_evidence_items` | A/duplicate | 使用现有 `max_context_items`，删除 nested 副本 |
| `context_requirements.max_context_chars` | A/duplicate | 使用现有 `max_context_chars`，删除 nested 副本 |
| `context_requirements.model_capability_plan` | C | diagnostic only；不作为 RAG semantic input |
| `document_context.project_name` | A | typed document retrieval context |
| `document_context.project_type` | C | 暂留 metadata；当前无核心 reader |
| `document_context.document_title` | A | typed document retrieval context |
| `document_context.required_sections` | A | typed document retrieval context |
| `document_context.citation_required_sections` | A | typed document retrieval context |
| `document_context.target_documents` | C | metadata；当前无核心 RAG reader |
| `document_context.target_templates` | C | metadata；当前无核心 RAG reader |

## 3.2 RAG Internal Config 必须复用现有实现

以下字段已经在 RAG 内部存在 typed config：

```text
backend/rag/config/static_retrieval.py
backend/rag/context/context_gate.py
```

已有：

```text
model_context_window
prompt_reserved_tokens
section_token_budget
max_evidence_items
```

因此 Codex **禁止新建第二套 ContextConfig/ModelConfig**。

迁移目标：

```text
Application 不再通过 extra 控制 RAG internal context policy
RAG 使用已有 static_retrieval / ContextRequirements / factory 配置
```

对于 caller 明确提供的预算约束，只保留 public：

```text
max_context_chars
max_context_items
max_context_tokens   # 本轮允许新增的唯一 token budget public 字段
```

RAG 内部必须取：

```text
min(caller constraint, internal safe limit)
```

不能让 caller 放大内部安全上限。

---

# 4. Target Public Contract

本轮完成后，Application-facing RAG request 目标形状：

```text
RAGToolInputSchema
├── task_id
├── run_id
├── agent_name
├── query
├── kb_ids
├── access_scope
├── filters
├── need_citation
├── max_context_chars
├── max_context_items
├── max_context_tokens
├── retrieval_trace_id
├── retrieval_scope
├── task_type
├── evidence_requirements: EvidenceRequirementsSchema | null
├── document_context: RAGDocumentContextSchema | null
└── extra
```

其中 `extra` 只保留：

- diagnostic metadata；
- experimental metadata；
- 当前无稳定 RAG reader 的非核心业务 metadata。

禁止再把以下内容塞入 `extra` 作为核心控制：

- Evidence Requirement；
- Section identity；
- RAG policy id；
- coverage strategy；
- model context window；
- prompt reserved tokens；
- Gate policy；
- DeliveryMode。

建议新增/演进 Public Schema：

```text
backend/schemas/claim.py
  ClaimType

backend/schemas/rag.py
  EvidenceStatus
  EvidenceRequirementSchema
  EvidenceRequirementsSchema
  RAGDocumentContextSchema
  RAGToolInputSchema  # 演进现有类
  RAGEvidenceAssessmentSchema  # 演进现有类
  EvidenceBundleSchema          # 保持 canonical identity
```

禁止创建新的 `schemas/rag_v2.py` 或平行 public package。

---

# 5. Target Assessment Contract

`RAGEvidenceAssessmentSchema` 必须成为 Application Gate 可直接消费的 Public Evidence Assessment，而不是让 Gate 读取 RAG internal dict。

至少显式承载：

```text
assessment_state
status / evidence_status

evidence_available
selected_evidence_count
dropped_evidence_count
citation_count
coverage_ratio
required_topics
covered_topics
missing_topics
conflicts
reason_codes
```

如果 Application Gate 为保持现有业务行为还需要某个 Evidence Fact：

> **优先把“Evidence Fact”提升为 Public Assessment 字段；禁止把 `DeliveryMode/human_review_required/generation_allowed` 放回 RAG。**

例如可公开：

- Evidence 支持/禁止的 `ClaimType` 集合；
- Evidence authority / reference 属性的聚合事实；
- Evidence caveat/reason code。

但最终处置仍由 Application Gate 产生。

`assessment.details` 可以继续用于 diagnostics，但 Application Gate 不得依赖未冻结的 `details["gate_decision"]` 等 dict 路径。

---

# 6. Implementation Scope

允许修改：

```text
backend/schemas/claim.py                       # 新建，shared public ClaimType
backend/schemas/rag.py
backend/contracts/rag.py
backend/rag/catalog/document_profile.py        # compatibility re-export
backend/rag/schema/evidence_assessment_v2.py
backend/rag/evidence/governance_v2.py
backend/rag/mapping/request_mapper.py
backend/rag/mapping/result_mapper.py
backend/rag/services/rag_service.py
backend/rag/context/context_gate.py             # 只在复用内部限制需要时修改
backend/rag/config/static_retrieval.py          # 只在已有 config 兼容需要时修改
backend/rag/integrations/langchain/runtime_facade.py

backend/apps/enterprise_document/schemas/gate_decision.py
backend/apps/enterprise_document/schemas/writer_governance.py
backend/apps/enterprise_document/schemas/agent_rag_v1/payloads.py
backend/apps/enterprise_document/schemas/scheme_writer/evidence.py
backend/apps/enterprise_document/services/scheme_writer/evidence_service.py
backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
backend/apps/enterprise_document/services/scheme_writer/agent_integration/
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/context_manager/
backend/application/mainline_service.py
backend/bootstrap/
backend/tests/
```

如 import scan 发现其他生产文件属于同一边界迁移，可修改，但必须在最终报告解释原因。

---

# 7. Non-goals

严格禁止：

- 不实现 RetrievalAction V2；
- 不实现 bounded Research multi-round；
- 不实现 Memory；
- 不实现 ContextCompiler V2；
- 不修改 Writer Paragraph Authority；
- 不修改 Verifier lifecycle；
- 不修改 13 个 ClaimType taxonomy；
- 不修改 DocumentType capability policy 业务内容；
- 不新增 Retrieval Strategy；
- 不修改 Dense/BM25/RRF/Parent-Child/Reranker 算法；
- 不更换 Milvus / Embedding / Reranker；
- 不新增 Vector DB；
- 不重新设计 Gate policy；
- 不修改 shadow enforcement 模式；
- 不实现 Evaluation Foundation；
- 不做微服务/API Gateway/RPC 拆分；
- 不顺手清理与本接口边界无关的旧代码。

---

# 8. 实施顺序

必须按顺序小步提交/验证，禁止一次性大改。

## Phase 1 — Shared/Public Contract Convergence

目标：只收口类型 authority，不先动主链行为。

### 1.1 ClaimType

1. 新建 `backend/schemas/claim.py`；
2. 将唯一 `ClaimType`、`CURRENT_PROJECT_CLAIM_TYPES`、`TEMPLATE_ONLY_CLAIM_TYPES` 移到该公共位置；
3. `rag.catalog.document_profile` 改为 import/re-export；
4. 迁移 Application/Gate/Writer/RAG 生产 imports 到 `schemas.claim`；
5. 不复制 Enum。

### 1.2 EvidenceStatus

1. 在 `schemas.rag` 定义唯一三态 `EvidenceStatus`；
2. `rag.schema.evidence_assessment_v2` 删除/停止定义五态 enum，改用 public `EvidenceStatus`；
3. 将旧 `PARTIAL/ABSENT` 判定折叠为 `INSUFFICIENT`；
4. 保留 `coverage_ratio/evidence_available/missing_topics/conflicts` 表达诊断信息；
5. `NOT_ASSESSED` 改成 assessment lifecycle，而不是 EvidenceStatus。

### 1.3 EvidenceRequirement

1. 在 `schemas.rag` 建立 `EvidenceRequirementSchema + EvidenceRequirementsSchema`；
2. Section Envelope + item list；
3. 旧 aggregate `required_topics` 只保留 compatibility projection；
4. 旧 `requested_claim_types` 不进入 canonical V2 semantics；
5. Application 不再 import `resolve_evidence_requirements_v2`；
6. Planner 尚未提供 typed requirements 时，由 RAG internal fallback resolver 构造兼容 instance。

### Phase 1 验收

- 全仓 ClaimType canonical 定义只有一份；
- canonical EvidenceStatus 只有三态；
- Requirement public authority 不再定义在 `rag.evidence.governance_v2`；
- production import 可运行；
- Gate 行为尚未改变。

---

## Phase 2 — RAG Request `extra` Closure

目标：让公开输入接口名副其实。

### 2.1 提升 A 类字段

将以下从 `extra` 提升为正式字段：

```text
retrieval_trace_id
retrieval_scope
task_type
evidence_requirements
document_context
max_context_tokens
```

已有：

```text
max_context_chars
max_context_items
```

继续复用，禁止 nested duplicate。

### 2.2 收回 B 类字段

Application 停止传：

```text
policy_id
coverage_strategy
model_context_window
prompt_reserved_tokens
```

以及其他 RAG internal policy/config。

复用：

```text
rag/config/static_retrieval.py
rag/context/context_gate.py
runtime factory
```

### 2.3 D 类字段移出 Requirement

```text
claim_risk_level
allow_framework_only
```

不再进入 RAG Requirement public schema；保留/迁回 Application Gate policy。

### 2.4 C 类字段

只保留在真正 metadata/diagnostic channel：

```text
project_type
target_documents
target_templates
model_capability_plan（若仍需 trace）
```

不得影响 authorization 或 canonical Requirement。

### Phase 2 验收

- `RAGToolInputSchema.extra` 不再承载核心 Requirement/Section/Context policy；
- RequestMapper 不需要从 `extra_metadata` 恢复 canonical request semantics；
- caller constraint 不能突破 RAG internal safe limit。

---

## Phase 3 — Break RAG -> Application Gate Dependency

目标：恢复 RAG 单一职责。

### 3.1 RAG governance

`backend/rag/evidence/governance_v2.py` 不再 import：

```text
apps.enterprise_document.schemas.gate_decision
```

移除 RAG 内部对：

```text
DeliveryMode
GateDecisionSchema
```

的构造责任。

RAG 只产出 typed Evidence Assessment facts。

### 3.2 Application Gate producer

将现有 `_gate` / `_claim_risk_level` 的**业务决策责任**迁移到 Application 服务层。

允许新建一个最小 deterministic policy 文件，例如：

```text
backend/apps/enterprise_document/services/scheme_writer/evidence_gate_policy.py
```

要求：

1. 复制的是现有 policy behavior，不重新设计规则；
2. 输入使用 Public Evidence Assessment + Application policy/context；
3. 输出继续使用现有 `GateDecisionSchema`；
4. `GATE_ENFORCEMENT_MODE=shadow` 行为保持不变；
5. `INSUFFICIENT` 下使用 coverage/evidence_available 区分原 partial-like / absent-like 分支，保持旧行为。

### 3.3 ResultMapper

RAG `ResultMapper` 不再将：

```text
gate_decision
generation_allowed
DeliveryMode
```

塞入 `EvidenceBundle.extra` 或 `assessment.details` 作为稳定业务语义。

### Phase 3 验收

```text
backend/rag/** -> apps.enterprise_document.* imports = 0
```

且现有 Gate focused tests 保持等价结果。

---

## Phase 4 — Canonical Evidence State Migration

目标：EvidenceBundle 真正成为唯一 truth。

### 4.1 去掉 `RAGContext.extra["evidence_contract"]`

当前：

```text
EvidenceBundle
  -> RAGContext.extra["evidence_contract"]
  -> downstream rebuild
```

迁移后：

```text
EvidenceBundle = canonical object
RAGContext = deterministic projection only
```

`RAGContext.extra` 可以保留：

```text
package_hash / package_ref / diagnostic info
```

但不得保存完整 canonical dump 供恢复。

### 4.2 Section compatibility

`SectionEvidenceBundleSchema` 暂不暴力删除。

它只能：

```text
持有 canonical EvidenceBundle/ref
+ Context/Chunk/Citation projection
+ Application workflow metadata
```

`generation_allowed / writer_gate_binding` 归 Application/Gate，不得被解释为 Evidence truth。

### 4.3 Research compatibility

`ResearchEvidencePackageSchema` 降级为 compatibility projection。

ResearchResult 优先保留：

```text
evidence_package_ref / hash
research_round
research_mode
recommended_action
agent_metadata
```

Evidence 内容从 canonical package 投影；禁止反向重建 package。

### 4.4 Citation registry

`DocumentCitationRegistry` 只做 Citation remap/binding。

不得通过 `RAGContext.extra` 重建/重写一份独立 Evidence truth。

### Phase 4 验收

生产代码中：

```text
(context.extra or {}).get("evidence_contract")
```

作为 canonical 恢复路径 = 0。

---

## Phase 5 — ToolGateway / ID Namespace Closure

目标：ToolGateway 单一职责化。

### 5.1 Gateway

ToolGateway 只负责：

- permission；
- allowlist；
- timeout；
- retry；
- budget；
- idempotency；
- trace；
- schema validation；
- normalized error。

不得重新判断 accepted/rejected Evidence 语义。

### 5.2 修正真实 ID bug

当前：

```text
accepted_evidence_ids <- citation_id
```

必须改为：

```text
accepted_evidence_ids <- EvidenceBundle.selected_evidence_ids
```

禁止 fallback 到不存在的 `RetrievedChunkSchema.chunk_id`。

如果需要 Citation/Chunk refs，使用明确独立字段，不混装。

### Phase 5 验收

- `accepted_evidence_ids` 全链只包含 canonical E-ID；
- Citation IDs 不进入 Evidence ID 字段；
- Gateway 不再通过 citation/chunk 推断 Evidence truth。

---

## Phase 6 — Application -> RAG Internal Import Cleanup

目标：业务生产代码只认识 Public Contract/Port。

处理 P0-A 审计确认的生产 imports，包括：

```text
rag.judge.*
rag.catalog.*
rag.schema.*
rag.evidence.*
```

原则：

```text
共享类型 -> schemas.* public
RAG服务能力 -> RAGServicePort
compatibility -> LegacyEvidenceAdapter 集中
纯工具函数 -> 明确真实 owner，不能借 public schema 掩盖内部 helper 泄漏
```

`backend/bootstrap/**` 保留 composition-root exception。

### Phase 6 验收

```text
backend/apps/**       -> rag internal imports = 0
backend/agent/**      -> rag internal imports = 0
backend/context_manager/** -> rag internal imports = 0
```

测试目录不计，bootstrap 允许 composition root。

---

## Phase 7 — Facade + Architecture Boundary Tests

### 7.1 LangChain facade

先扫描生产/测试依赖后，移除或限制：

```python
LangChainRAGServiceFacade.__getattr__
```

Application 不得通过 facade 访问 `retrieval_runtime` 等 delegate internals。

### 7.2 Architecture tests

新增：

```text
backend/tests/test_rag_architecture_boundaries.py
```

必须至少锁死：

1. Application/Agent/ContextManager 不 import `rag.*` internal；
2. RAG 不 import `apps.enterprise_document.*`；
3. Bootstrap 是 composition root exception；
4. ClaimType 只有一个 canonical Enum；
5. canonical EvidenceStatus 只包含 3 个值；
6. Evidence/Citation/Chunk namespace 不混用；
7. Context/Research/Section projection 不能反向成为 canonical Evidence authority；
8. FakeRAG/RealRAG 对 caller 可替换。

Architecture Test 只检查依赖/contract，不加入业务 Gate 系统。

---

# 9. Commit / Checkpoint 建议

建议按以下小提交执行：

```text
Commit 1  shared ClaimType + public EvidenceStatus
Commit 2  EvidenceRequirement public contract
Commit 3  RAGToolInputSchema extra closure
Commit 4  RAG assessment / Application Gate split
Commit 5  canonical Evidence state migration
Commit 6  ToolGateway + ID namespace fix
Commit 7  remaining internal import cleanup + facade
Commit 8  architecture tests + E2E verification
```

每个 Commit 必须保证：

```text
import passes
focused tests pass
禁止“全部改完最后统一修”
```

若项目环境无法执行某组测试，必须明确报告环境阻塞，不能写“测试通过”。

---

# 10. Acceptance Criteria

以下全部满足才算 Interface V2 完成：

```text
[ ] RAGServicePort 仍是唯一 Application-facing RAG service port

[ ] ClaimType 全系统只有一个 canonical public definition
[ ] RAG/Gate/Writer/Classifier 均使用同一 ClaimType

[ ] EvidenceRequirement = Section Envelope + Item Requirement
[ ] required_topics 仅为 compatibility projection
[ ] requested_claim_types 不被偷偷实现成新语义

[ ] EvidenceStatus 只剩 SUFFICIENT / INSUFFICIENT / CONFLICTED
[ ] partial/absent 由结构化字段表达
[ ] not_assessed 由 assessment lifecycle 表达

[ ] EvidenceBundleSchema 是唯一 canonical EvidencePackage
[ ] RAGContext 不再保存完整 evidence_contract 作为恢复通道
[ ] Research/Section evidence schemas 只做 projection/compatibility

[ ] RAG 不构造 DeliveryMode / GateDecisionSchema
[ ] RAG -> apps.enterprise_document import = 0
[ ] 现有 Gate policy 行为与 shadow enforcement 保持等价

[ ] ToolGateway 不重新解释 Evidence
[ ] accepted_evidence_ids 只包含 canonical Evidence ID

[ ] RAGToolInputSchema.extra 不再承载核心 Requirement/Section/RAG policy
[ ] model_context_window / prompt_reserved_tokens / coverage_strategy 等属于 RAG internal
[ ] claim_risk_level / allow_framework_only 属于 Application/Gate

[ ] Application/Agent/ContextManager -> rag internal production imports = 0
[ ] Bootstrap composition root exception 清晰

[ ] FakeRAG / RealRAG 切换不修改 Agent/Application 业务代码
[ ] Milvus/Embedding/Reranker internal replacement 不需要修改 Agent Contract

[ ] Architecture Boundary Test 已加入并通过
[ ] Focused RAG/Agent/Gate tests 已通过或明确记录环境阻塞
[ ] 主链 E2E smoke 未因接口迁移断裂
```

---

# 11. Verification Evidence

Codex 最终必须提供，不得只回复“完成”：

## 11.1 Git

```text
git status --short
git diff --stat
git diff --name-only
```

并按 Phase 说明每个变更文件的目的。

## 11.2 Dependency scan

必须输出实际扫描结果：

```text
Application/Agent/ContextManager -> rag internal imports = 0
RAG -> apps.enterprise_document imports = 0
```

允许：

```text
backend/bootstrap/** -> concrete rag implementation
backend/tests/** -> test internals where explicitly needed
```

## 11.3 Contract evidence

列出最终 canonical 定义位置：

```text
ClaimType
EvidenceStatus
EvidenceRequirement
EvidenceRequirements Envelope
EvidenceBundleSchema
DeliveryMode
GateDecisionSchema
```

不得有重复 authority。

## 11.4 Side-channel scan

必须扫描并报告：

```text
evidence_contract
extra_metadata.evidence_requirements
coverage_strategy
allow_framework_only
claim_risk_level
accepted_evidence_ids
citation_id
__getattr__
```

说明残留是否仅为明确 compatibility/test。

## 11.5 Tests

至少覆盖：

```text
RAG public contract tests
Evidence contract tests
RAG pipeline tests
Evidence governance tests
Gate decision tests
Agent/RAG integration tests
Section graph focused tests
Architecture boundary tests
Fake/Real RAG substitution test
```

必须报告：

```text
passed / failed / skipped / environment-blocked
```

## 11.6 E2E Smoke

至少证明：

```text
Research -> ToolGateway -> RAGServicePort -> EvidenceBundle
-> Application Gate -> Writer/Section
```

主链仍可运行。

如果 DeepSeek/API E2E 本轮不执行，必须明确写“未执行”，不得用单元测试代替声称 E2E 已验证。

---

# 12. Codex 执行约束

Codex 可以直接按本文实施，不需要再自行做架构设计。

实施过程中若遇到冲突，处理优先级：

```text
1. 本文 Frozen Decisions
2. RAG架构_V2_收敛版_20260809.md
3. Multi-Agent V2 冻结架构
4. 当前代码兼容性
```

如果发现当前代码事实与本文关键前提冲突：

```text
STOP
记录文件/行号/冲突原因
不得自行改变 Frozen Decision
```

允许 Codex 自主决定的只有：

- 局部函数拆分；
- private helper 名称；
- import 排序；
- 测试 fixture 组织；
- 不影响 public contract 的内部实现细节。

不允许 Codex 自主决定：

- Schema authority；
- 新增 Evidence/Claim taxonomy；
- Gate policy；
- RetrievalAction values；
- 新服务/新基础设施架构；
- 是否保留第二套 canonical Evidence。

---

# 13. 最终目标调用链

```text
Planner / Application
    |
    | Public EvidenceRequirements (optional until Planner V2 fully owns instance)
    v
ResearchAgent
    |
    v
ToolGateway
    |  permission / budget / retry / trace only
    v
RAGServicePort
    |
    v
RAGService / Internal Retrieval
    |
    v
EvidenceBundleSchema
    |  canonical EvidencePackage
    v
Application Evidence Gate Policy
    |
    v
GateDecisionSchema
    |
    +--> Writer
    +--> Review / Publish policy
```

最终依赖方向：

```text
Application / Agent
        ↓
Shared/Public Contract + RAGServicePort
        ↓
RAG Implementation
```

最终数据 authority：

```text
ClaimType        -> Shared Public Domain
EvidenceRequest  -> RAG Public Contract
EvidenceStatus   -> RAG Public Contract
EvidencePackage  -> EvidenceBundleSchema canonical
DeliveryMode     -> Application/Gate
GateDecision     -> Application/Gate
```

本轮完成后，立即回到 Multi-Agent V2，不继续扩展 RAG 接口。
