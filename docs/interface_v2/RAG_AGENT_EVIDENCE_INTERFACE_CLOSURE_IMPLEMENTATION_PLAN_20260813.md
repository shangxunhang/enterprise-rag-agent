# RAG–Agent Evidence Boundary Closure 实施方案

日期：2026-08-13  
状态：Implementation Plan / 待实施  
适用仓库：`D:\MyCode\rag-agent`  
基线提交：`94b2a5b checkpoint: freeze gate shadow closure before multi-agent v2`

关联架构：
- `docs/RAG架构_V2_收敛版_20260809.md`
- `docs/多agent架构_V2_收敛版_20260809.md`
- `docs/MULTI_AGENT_V2_IMPLEMENTATION_PLAN_20260809.md`
- `docs/MULTI_AGENT_V2_CURRENT_STATE_GAP_AUDIT_20260813.md`

---

# 0. 文档目的

本实施方案只解决一个问题：

> **把现有 RAG 与 Agent / Application 之间尚未彻底收口的 Evidence 语义边界接口化，使生产代码只能通过稳定 Contract / Port 交互，并切断双向内部依赖。**

本轮不是重新设计 RAG，也不是实现完整 RAG V2，更不是重写 Multi-Agent。

当前代码已经具备较成熟的 RAG 内部组件 Port、`RAGServicePort`、Fake / Real RAG 实现和 Evidence Contract 雏形。因此本轮重点不是“新增接口”，而是：

1. 冻结现有规划中的 RAG Public Contract；
2. 明确 Schema / Contract Authority；
3. 切断 Application → `rag.*` 内部依赖；
4. 切断 `rag.*` → Application 反向依赖；
5. 让 `EvidencePackage` 成为唯一 canonical Evidence；
6. 清理通过 `RAGContext.extra` 传递 Evidence Contract 的暗门；
7. 将 Research / Section 侧重复 Evidence Schema 降级为兼容投影；
8. 增加 Architecture Boundary Test，防止后续 AI Coding 再次破坏边界。

最终目标依赖方向固定为：

```text
Agent / Application
        |
        v
RAG Public Contract / Port
        |
        v
RAG Service / Adapter
        |
        v
RAG Internal Runtime
```

禁止重新形成：

```text
Application -> rag.internal.*
RAG -> apps.enterprise_document.*
```

---

# 1. 当前审计结论

## 1.1 已经存在且继续复用的接口能力

现有代码已经有：

```text
backend/contracts/rag.py
  RAGServicePort

backend/rag/services/rag_service.py
  RAGService
  FakeRAGService
  ObservedRAGService

backend/rag/integrations/langchain/runtime_facade.py
  LangChainRAGServiceFacade
```

当前公共调用形态已经是：

```python
RAGServicePort.retrieve(
    request: RAGToolInputSchema
) -> EvidenceBundleSchema
```

RAG 内部也已有 `CandidateRetrieverPort`、`SourceFusionPort`、`QueryFusionPort`、`CandidateEnricherPort`、`RetrieverPort`、`RerankerPort`、`ContextPackerPort`、`QueryTransformerPort`、`EvidenceAssessorPort`、`CorrectiveRetrievalGatePort`、`CorrectiveQueryPlannerPort` 等端口。

因此本轮明确：

> **不重构 Dense / BM25 / RRF / Parent-Child / Rerank / Context Packer 等底层能力。**

## 1.2 当前真正未收口的部分

核心问题集中在 RAG–Agent Evidence 语义链路，而不是 RAG 底层检索实现。

当前存在：

```text
A. Application / Agent 直接 import rag.evidence.* / rag.catalog.* / rag.schema.*
B. rag.evidence.governance_v2 反向 import apps.enterprise_document.schemas.gate_decision
C. EvidenceBundleSchema 已存在，但下游大量通过 RAGContext.extra["evidence_contract"] 再次传递/解析
D. ResearchEvidencePackageSchema / SectionEvidenceBundleSchema 与 canonical EvidenceBundle 并存
E. RAGToolInputSchema.extra 承担大量事实上已经稳定但未正式冻结的字段
F. MainlineResearchToolGateway 对 RAG 输出重新解释，并存在 evidence id / citation id 语义混用风险
G. LangChainRAGServiceFacade.__getattr__ 可以透明暴露 delegate 内部能力
```

当前准确状态：

```text
RAG 内部执行层：大部分已经接口化
RAG Service 调用层：大部分已经接口化
RAG <-> Agent Evidence 语义边界：尚未彻底接口化
```

---

# 2. Task Goal

本轮唯一 Task Goal：

> **在不改变现有 RAG 算法能力和不推进 Multi-Agent V2 功能实现的前提下，把 RAG–Agent Evidence 边界收口为唯一 Public Contract，使 Agent/Application 不依赖 RAG 内部实现，RAG 不依赖企业文档 Application，并以 EvidencePackage 作为唯一 canonical Evidence authority。**

---

# 3. Scope

本轮允许修改的主要范围：

```text
backend/contracts/rag.py
backend/schemas/rag.py
backend/rag/services/
backend/rag/mapping/
backend/rag/evidence/
backend/rag/integrations/langchain/
backend/apps/enterprise_document/services/scheme_writer/evidence_service.py
backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py
backend/apps/enterprise_document/services/scheme_writer/agent_integration/
backend/apps/enterprise_document/schemas/agent_rag_v1/
backend/apps/enterprise_document/schemas/scheme_writer/evidence.py
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/context_manager/
backend/bootstrap/
backend/tests/
```

如果实施过程中发现其他生产文件直接依赖 `rag.*` 内部模块，可纳入“边界迁移”修改，但修改原因只能是消除 RAG–Application 跨层依赖，不允许顺手重构该模块其他业务逻辑。

---

# 4. Non-goals

本轮严格禁止：

- 不重新设计 RAG V2；
- 不增加新的业务 Agent；
- 不实现 Multi-Agent V2 Memory；
- 不实现 ContextCompiler V2；
- 不实现 Research bounded multi-round loop；
- 不实现 3B Adaptive Router；
- 不实现新的 Self-RAG 流程；
- 不重写 Writer Paragraph Authority；
- 不修改 Verifier Issue lifecycle；
- 不新增 Vector DB；
- 不更换 Milvus / Embedding / Reranker；
- 不新增新的 Retrieval Strategy；
- 不为了“接口化”创建第四套 Evidence Schema；
- 不一次性删除全部 legacy compatibility；
- 不扩大到 Evaluation Foundation；
- 不重新设计 Gate；
- 不重新收紧当前 `shadow` Gate；
- 不做微服务拆分；
- 不新增 API Gateway / RPC / HTTP Service；
- 不因为“未来企业级”引入新基础设施。

---

# 5. Architecture Invariants

## INV-01 单向依赖

生产代码依赖方向：

```text
apps / agent / context_manager
        ↓
contracts + public schemas
        ↓
rag implementation
```

禁止 `apps -> rag.internal`、`agent -> rag.internal`、`context_manager -> rag.internal`、`rag -> apps.enterprise_document`。

## INV-02 RAGServicePort 是唯一服务入口

Agent / Application 不得持有 Milvus client、Retriever、BM25 Retriever、Embedding model、Reranker、RetrievalRuntime、EvidenceGovernance internal service。只允许通过 `RAGServicePort` 或 `ToolGateway -> RAGServicePort` 调用。

## INV-03 EvidencePackage 是唯一 canonical Evidence

```text
EvidencePackage / EvidenceBundle = source of truth
RAGContext = projection
RetrievedChunk = internal / compatibility projection
Citation = projection / binding view
```

禁止 `RAGContext.extra["evidence_contract"]` 成为 canonical 数据传输通道。

## INV-04 Fact != Evidence

本轮接口收口不得破坏 RAG V2 已冻结的 `Fact != Evidence` 原则。当前项目 Fact Authority 不允许被历史方案 / 企业知识库 Evidence 覆盖。

## INV-05 RAG 不拥有业务 Delivery 决策

RAG 只提供 Evidence status、Coverage、Missing requirements/topics、Conflict、Eligibility、Quality / lineage。

RAG 不决定 DeliveryMode、Human Review、Final Publish、Writer Hard Gate、Document Hard Gate。这些属于 Application / Gate。

## INV-06 ToolGateway 不重定义 RAG 语义

ToolGateway 只负责 permission、allowlist、budget、timeout、retry、idempotency、trace、schema validation、normalized error。

不得把 Citation ID、Chunk ID、Evidence ID 重新解释成另一种 Evidence authority。

## INV-07 Bootstrap 是允许知道具体实现的 Composition Root

`backend/bootstrap/**` 可以同时 import RAGServicePort 与具体 RAG implementation，因为它负责依赖注入。其余 Application 生产代码不得利用这一例外。

---

# 6. Schema / Contract Authority Freeze

在写代码前必须先冻结下表。

| 概念 | Authority | 当前代码 | 本轮动作 |
|---|---|---|---|
| EvidenceRequirement | RAG Public Contract | `EvidenceRequirementsV2Schema` 已存在，但位置与字段需核对 V2 文档 | 收口，不新建平行 Schema |
| EvidenceStatus | RAG Evidence Assessment | `rag.schema.evidence_assessment_v2.EvidenceStatus` | 复用语义；如需跨边界则迁移/公开导出，不复制 |
| EvidencePackage | RAG Public Contract | `EvidenceBundleSchema` / alias `RAGEvidenceContractSchema` | 确认为唯一 canonical authority |
| DeliveryMode | Application / Gate | `apps.enterprise_document.schemas.gate_decision.DeliveryMode` | 保留；解除 RAG 对其依赖 |
| RetrievalAction | RAG–Research Public Boundary | 规划概念存在，公共 Schema 未完全冻结 | 审计现有 Router / Research 字段后冻结最小 Contract |
| RetrievalAccessScope | Trusted Application Boundary + RAG Public Contract | `RetrievalAccessScopeSchema` | 复用，不下沉到 Agent 自由控制 |
| RAGContext | Derived Projection | `RAGContextSchema` | 保留，但禁止承载 canonical Evidence Contract |
| ResearchEvidencePackage | Agent compatibility / projection | `ResearchEvidencePackageSchema` | 降级为 compatibility projection 或逐步退役 |
| SectionEvidenceBundle | Application compatibility / projection | `SectionEvidenceBundleSchema` | 降级为 compatibility projection 或逐步退役 |

特别强调：本轮禁止为了命名统一而新建 `EvidencePackageV2Schema`、`AgentEvidencePackageSchema`、`PublicEvidenceBundleSchema` 等并行 authority。如果现有 `EvidenceBundleSchema` 能承载目标语义，优先升级现有 schema。

---

# 7. Public Contract 目标形态

本节描述“接口面应该承载什么语义”，不是要求机械按示例字段新增新 Schema。

最终 Public Contract 控制在以下概念内：

```text
EvidenceRequirement
RetrievalAction
RAGRetrievalRequest
EvidencePackage
RetrievalAccessScope
RAGServicePort
```

## 7.1 EvidenceRequirement

职责：描述当前 Run / Section “需要什么 Evidence”。Authority 属于 RAG。Planner 可以生产 runtime instance，但不能拥有第二份 Schema。

目标最小语义参考 RAG V2：

```yaml
EvidenceRequirement:
  requirement_id: string
  section_id: string
  topic: string
  purpose: string
  importance: required | optional
  expected_source_types: []
```

实际字段必须先与当前 `EvidenceRequirementsV2Schema` 做兼容映射后冻结。

## 7.2 RetrievalAction

职责：Research / Adaptive Router 表达“下一步希望 RAG 执行什么检索动作”。它不是底层检索配置。

允许表达的语义应保持有限，例如：

```text
retrieve
rewrite
expand
targeted_retrieve
stop
```

禁止向 Agent 暴露：

```text
dense_top_k
keyword_top_k
RRF 参数
Milvus Collection
HNSW / IVF
Embedding 模型
Reranker 模型
```

这些属于 RAG 内部实现与配置。

## 7.3 RAGRetrievalRequest

职责：将 requirement / action / access scope / budget 等正式跨边界输入交给 RAGServicePort。

本轮应优先升级现有 `RAGToolInputSchema`，而不是创建平行请求 Schema。

重点清理 `rewritten_queries` 与 `extra`：

- `rewritten_queries` 如果属于 RAG 内部 query transformation，不应由 Agent 主动控制；
- `extra` 中已经稳定的结构化字段应逐步提升为正式 Contract；
- `extra` 只保留真正尚未冻结、非权限敏感、非核心语义的扩展信息；
- 安全边界字段不得藏在任意 `extra` 中。

## 7.4 EvidencePackage

职责：RAG 向 Agent / Writer 提供唯一、受治理、可追溯的 Evidence 结果。

目标语义参考 RAG V2：

```yaml
EvidencePackage:
  section_id: string
  evidence_status: sufficient | insufficient | conflicted
  stop_reason: string | null
  requirements: []
  accepted_evidence: []
  rejected_evidence_summary: []
  coverage: {}
  conflicts: []
  missing_requirements: []
  research_rounds: integer
  tool_calls: integer
  package_hash: string
```

`evidence_status` 与 `stop_reason` 必须是两个不同维度。例如：

```text
evidence_status = insufficient
stop_reason = max_rounds
```

不能把预算耗尽、人工审核、交付模式混入 EvidenceStatus。

---

# 8. 实施阶段

本轮拆成五个阶段实施。

## P0-A Contract Freeze

### 目标

先冻结唯一 Public Contract 和 Schema Authority，不改业务行为。

### A1. 盘点并输出 Contract Mapping

必须对以下现有 Schema 逐字段比对：

```text
RAGToolInputSchema
EvidenceBundleSchema
RAGEvidenceContractSchema alias
RAGContextSchema
RetrievedChunkSchema
EvidenceRequirementsV2Schema
EvidenceStatus
ResearchEvidencePackageSchema
SectionEvidenceBundleSchema
GateDecisionSchema
DeliveryMode
```

输出表至少包含：

```text
field
current_owner
actual_consumers
V2_target_owner
keep / move / project / deprecate
compatibility_risk
```

### A2. 冻结名称与 Authority

完成后必须能够明确回答：

```text
EvidenceRequirement 到底谁定义？
EvidenceStatus 到底谁定义？
EvidencePackage 到底哪个是 canonical？
DeliveryMode 属于谁？
RetrievalAction 属于谁？
```

答案必须与第 6 节一致。

### A3. 冻结 Public Import Surface

建议外部统一从 `contracts.rag`、`schemas.rag` 或一个同等稳定 public export 层引用。

不允许业务层 import：

```text
rag.evidence.*
rag.catalog.*
rag.schema.*
rag.retriever.*
rag.runtime.*
```

### A4. 验收

- Contract Mapping 已形成；
- 没有新增平行 Evidence authority；
- 本阶段不改变 RAG E2E 行为。

## P0-B Dependency Direction Cleanup

### 目标

切断双向模块依赖。

### B1. 清理 Application -> rag.internal

当前重点文件包括但不限于：

```text
apps.enterprise_document.services.scheme_writer.evidence_service
apps.enterprise_document.services.scheme_writer.legacy_evidence_adapter
apps.enterprise_document.services.scheme_writer.grounding_repair_service
apps.enterprise_document.services.scheme_writer.model_service
apps.enterprise_document.services.scheme_writer.prompt_service
apps.enterprise_document.services.scheme_writer.semantic_claim_classifier
apps.enterprise_document.services.scheme_writer.writer_governance
apps.enterprise_document.quality.plugins
context_manager.policies
```

迁移原则：

```text
需要公共数据类型 -> 迁到/通过 public schema 暴露
需要 RAG 服务能力 -> 通过 RAGServicePort
需要纯确定性文本工具 -> 判断其真正 authority，避免从 Application 偷用 RAG internal helper
```

禁止用 try-import 等方式假装解耦。

### B2. 清理 RAG -> Application

当前核心问题：

```text
rag.evidence.governance_v2
    -> apps.enterprise_document.schemas.gate_decision
```

需要把 Evidence Assessment 与 Delivery Decision 拆开。

RAG 只输出：

```text
status
coverage
missing_topics
conflicts
eligible / rejected evidence
reason_codes
```

Application Gate 根据这些结果决定：

```text
DeliveryMode
human_review_required
generation_allowed
publish_allowed
```

禁止 RAG 内部构造业务 DeliveryMode。

### B3. Bootstrap Exception

`backend/bootstrap/**` 保持 Composition Root 特权，可以同时知道 `RAGServicePort` 与具体 RAG implementation，但不得把具体实现重新暴露给 Agent。

### B4. 验收

生产代码中：

```text
backend/apps/**
backend/agent/**
backend/context_manager/**
```

不得直接 import `rag.*` internal package。

生产代码中：

```text
backend/rag/**
```

不得 import `apps.enterprise_document.*`。

## P0-C Evidence Authority Cleanup

### 目标

让 EvidencePackage 真正成为唯一 source of truth。

### C1. 禁止 Evidence Contract 藏入 RAGContext.extra

当前模式：

```text
EvidenceBundle
    ↓
RAGContext
    ↓
RAGContext.extra["evidence_contract"]
    ↓
下游重新 model_validate
```

必须迁移为：

```text
EvidencePackage
    ├── canonical structured evidence
    └── RAGContext projection
```

Graph State / Section State 应直接持有 `evidence_package`，或 `evidence_package_ref + package_hash`，而不是只持有 RAGContext 再从 `extra` 反解。

### C2. RAGContext 只保留 Projection 语义

RAGContext 允许保存：

```text
context_text
used_context_chunk_ids
matched_chunk_ids
used_doc_ids
budget usage
context format
```

不允许承担 canonical evidence identity、canonical coverage、canonical conflict、canonical package lifecycle。

### C3. DocumentCitationRegistry 只做 Citation remap

它可以做 `C1/C2/... -> document-global citation id`，但不得通过修改 `RAGContext.extra` 创建第二 Evidence authority。

### C4. ContextManager 直接消费 Public EvidencePackage / projection

ContextManager 不应从 `RAGContext.extra` 恢复 Evidence Contract。应接收明确的 `EvidencePackage + context projection`，或未来由 ContextCompiler 统一处理。本轮只完成边界迁移，不提前实现 ContextCompiler V2。

### C5. 验收

生产代码中不得再依赖：

```python
(context.extra or {}).get("evidence_contract")
```

作为 canonical Evidence 恢复路径。兼容测试若确需保留，只能集中在明确 legacy adapter 中，并标注 retirement 条件。

## P0-D Compatibility Migration

### 目标

不暴力删除旧 Schema，而是先把它们降级为单向 Projection。

### D1. ResearchEvidencePackageSchema

当前 `ResearchEvidencePackageSchema` 包含 accepted_evidence_ids、rejected_evidence、supported_topics、evidence_gaps、conflicts、research_status、recommended_action，其中 Evidence 数据与状态和 canonical EvidenceBundle 重叠。

目标改为：

```text
ResearchResultPayload
    ├── evidence_package_ref / package hash
    ├── research_round
    ├── research_mode
    ├── recommended_action
    └── agent metadata
```

Evidence 内容不再由 Research Schema 重定义。

迁移期允许：

```text
EvidencePackage -> Research compatibility projection
```

禁止反向：

```text
ResearchEvidencePackage -> canonical EvidencePackage
```

### D2. SectionEvidenceBundleSchema

同样处理：

```text
canonical EvidencePackage
    ↓
Section compatibility projection
```

它可以继续服务旧 Document Assembly / Graph State，但必须明确 `not authority`。

### D3. LegacyEvidenceAdapter

所有旧 `ToolResultSchema` / legacy dictionary 兼容升级必须集中在 `LegacyEvidenceAdapter`，禁止兼容逻辑散落在 Writer、ResearchAgent、ContextManager、SectionGraph。

### D4. MainlineResearchToolGateway

当前 Gateway 对返回结果重新组装：

```text
context
chunks
citations
accepted_evidence_ids
normalized dict
```

本轮要收口为：

```text
RAGServicePort
    ↓
EvidencePackage
    ↓
ToolGateway schema validation / trace
    ↓
Research
```

必须核验并锁死：

```text
Evidence ID = E*
Citation ID = C*
Chunk ID = chunk identity
```

禁止互相代替。特别要修正当前 `accepted_evidence_ids` 可能优先使用 citation id 的语义污染。

### D5. LangChain Facade

`LangChainRAGServiceFacade.__getattr__` 会透明暴露 delegate。

本轮目标：

- 生产调用只使用明确 Public API；
- 测试若需 inspect 内部 runtime，应直接测试 RAG implementation，不通过 Application facade；
- 视测试影响移除或限制 `__getattr__`；
- 不允许 Application 依赖 `facade.retrieval_runtime` 等内部属性。

## P0-E Architecture Boundary Tests

### 目标

把架构原则变成自动化门禁，防止 Codex / AI 后续重新写坏。

建议新增：

```text
backend/tests/test_rag_architecture_boundaries.py
```

使用 Python AST 或稳定的 import parser，不依赖业务文本正则。

### E1. Rule: Application 不依赖 RAG Internal

扫描：

```text
backend/apps/**
backend/agent/**
backend/context_manager/**
```

禁止：

```python
import rag.xxx
from rag.xxx import ...
```

允许项必须显式白名单并有 migration reason，目标最终白名单为空。

### E2. Rule: RAG 不依赖 Application

扫描 `backend/rag/**`，禁止：

```python
from apps.enterprise_document... import ...
```

### E3. Rule: Composition Root Exception

`backend/bootstrap/**` 允许依赖具体 RAG implementation。

### E4. Rule: Evidence Authority

测试确认 canonical EvidencePackage 可以单向投影为 RAGContext、Research compatibility view、Section compatibility view，但不能从这些 compatibility object 反向生成新的 canonical authority。

### E5. Rule: Fake / Real substitution

同一 Application / Research 调用路径分别注入 `FakeRAGService` 与 `RAGService`，Agent / Application 代码不修改。

### E6. Rule: Internal provider substitution

通过 fake/stub internal ports 证明 Milvus / Embedding / Reranker implementation replacement 不需要修改 ResearchAgent、WriterAgent、Supervisor。

本轮不要求真正部署第二套 Milvus / Embedding / Reranker，只证明依赖边界允许替换。

---

# 9. 关键调用链目标

## 9.1 当前形态

```text
ResearchAgent
    ↓
knowledge.retrieve
    ↓
MainlineResearchToolGateway
    ↓
SchemeEvidenceService
    ↓
RAGServicePort
    ↓
EvidenceBundle
    ↓
Context + Chunks + Citations + normalized dict
    ↓
ResearchEvidencePackage / SectionEvidenceBundle
```

同时存在：

```text
Application -> rag.evidence.*
RAG -> Application Gate Schema
```

## 9.2 P0 完成后的形态

```text
Planner
    ↓
EvidenceRequirement

ResearchAgent
    ↓
RetrievalAction
    ↓
ToolGateway
    ↓
RAGRetrievalRequest
    ↓
RAGServicePort
    ↓
EvidencePackage
    ↓
State / Memory Projection
    ↓
Writer / Verifier / Gate
```

其中：

```text
RAGContext = EvidencePackage 的生成上下文投影
Citation = EvidencePackage 的引用投影
ResearchResult = EvidencePackage ref + Agent decision
DeliveryMode = Application 根据 EvidencePackage 决策
```

---

# 10. 迁移顺序与提交策略

建议分小提交，不做一次大爆炸重构。

## Commit 1 — Contract Authority Freeze

只做 public contract exports、schema ownership comments / aliases、contract mapping tests，不改变主链行为。

## Commit 2 — Break RAG -> Application Dependency

只处理 RAG Evidence Assessment 与 Application Gate Decision，先解除反向 import。

## Commit 3 — Break Application -> RAG Internal Imports

逐模块迁移到 Public Contract / Port。

## Commit 4 — Canonical Evidence State Migration

让 Graph / Service 显式持有 EvidencePackage，不再依赖 `RAGContext.extra["evidence_contract"]`。

## Commit 5 — Compatibility Projection Cleanup

处理 `ResearchEvidencePackageSchema`、`SectionEvidenceBundleSchema`、`LegacyEvidenceAdapter`、`MainlineResearchToolGateway`。

## Commit 6 — Architecture Boundary Tests

最后锁死边界。

每一个 Commit 必须保证：

```text
production import passes
contract tests pass
focused RAG tests pass
focused agent tests pass
```

禁止“先全部改完再统一修测试”。

---

# 11. Acceptance Criteria

本轮完成必须同时满足以下 7 条。

## AC-01 Agent/Application 只认识 RAG Public Contract

生产代码不直接依赖 RAG 内部实现模块。

## AC-02 RAG 不 import EnterpriseDocument Application

`backend/rag/**` 不依赖 `apps.enterprise_document.*`。

## AC-03 EvidencePackage 是唯一 canonical Evidence

`EvidenceBundleSchema` 或其最终冻结名称是唯一 source of truth。`RAGContext`、`ResearchEvidencePackage`、`SectionEvidenceBundle` 都只能是 projection / compatibility view。

## AC-04 不再从 RAGContext.extra 恢复 canonical Evidence

生产主链不存在 `context.extra["evidence_contract"]` 作为 canonical Evidence source。

## AC-05 Research / Section 不再创建第二 Evidence Authority

ResearchResult 只引用 canonical EvidencePackage，并保留 Agent 自己的 decision / round / status 信息。

## AC-06 FakeRAG / RealRAG 可替换

切换 `FakeRAGService <-> RAGService` 不修改 Agent / Application 业务代码。

## AC-07 Milvus / Embedding / Reranker 可替换而 Agent 零修改

通过 factory / internal ports / test double 证明替换内部 provider 不影响 Agent Contract。

---

# 12. Verification Evidence

实施完成后 AI / Codex 必须提供以下证据，不能只说“已完成”。

## 12.1 Git Evidence

```text
git diff --stat
git status --short
git diff -- <changed files>
```

说明每个改动对应哪个 P0 阶段。

## 12.2 Import Boundary Evidence

输出扫描结果：

```text
Application / Agent / ContextManager -> rag.internal imports = 0
RAG -> apps.enterprise_document imports = 0
```

Bootstrap composition root 不计入违规。

## 12.3 Schema Authority Evidence

列出最终 authority：EvidenceRequirement、EvidenceStatus、EvidencePackage、DeliveryMode、RetrievalAction。每个只能有一个 canonical owner。

## 12.4 Canonical Evidence Evidence

证明生产主链：

```text
RAGServicePort -> EvidencePackage -> State / Writer / Verifier
```

不再依赖 `RAGContext.extra["evidence_contract"]` 反向恢复。

## 12.5 Focused Tests

至少运行：

```text
RAG public contract tests
Evidence contract tests
RAG pipeline tests
Agent contract tests
Section / Research integration tests
Architecture boundary tests
```

必须报告 passed / failed / skipped，不能只写“测试通过”。

## 12.6 Fake / Real Substitution Evidence

同一调用方分别注入 `FakeRAGService`、`RAGService`，证明调用方代码路径不变。

## 12.7 E2E Smoke Evidence

本轮不要求解决 Gate 业务问题，但至少证明：

```text
Supervisor / Mainline
→ Research
→ ToolGateway
→ RAGServicePort
→ EvidencePackage
→ Writer/Section downstream
```

主链没有因接口迁移而断裂。

如果最终仍因当前 Gate policy 导致 document status 非 success，必须区分 architecture regression 与 gate policy / test drift，不能把已有 Gate 问题误判为接口化失败。

---

# 13. 必须保留的兼容策略

本轮不是 Legacy 清零项目。

允许暂时保留：

```text
RAGEvidenceContractSchema = EvidenceBundleSchema alias
LegacyEvidenceAdapter
ResearchEvidencePackageSchema compatibility projection
SectionEvidenceBundleSchema compatibility projection
```

前提只能是：

```text
canonical -> compatibility
```

禁止：

```text
compatibility -> canonical authority
```

每一个 compatibility 层必须明确 retirement 条件：当生产调用方与对应 focused tests 不再读取该兼容结构时，再在独立清理任务中删除。

---

# 14. 风险控制

## Risk-01 一次性 Schema 大改导致主链爆炸

处理：先 alias / projection，再迁移消费者，最后退役旧字段。

## Risk-02 Codex 把“接口化”理解为重写 RAG

处理：严格执行 Non-goals。底层 `Dense + BM25 -> RRF -> Parent-Child -> Rerank -> Context Pack` 本轮保持不动。

## Risk-03 为了消除 import 把所有 Schema 搬到同一巨大文件

禁止。接口化的目标是稳定 authority + 单向依赖，不是制造 `schemas_everything.py`。

## Risk-04 把 Application Gate 逻辑重新塞进 RAG Public Contract

禁止。EvidenceStatus 与 DeliveryMode 必须分开。

## Risk-05 把 RAG internals 暴露进 RetrievalAction

禁止。RetrievalAction 只能表达业务级 retrieval intent / action，不得暴露具体 provider 参数。

## Risk-06 Architecture Test 变成新的业务正则门禁

Architecture test 只分析 Python import dependency 与 contract ownership，不做业务文本正则判断，不加入现有 Gate 体系。

---

# 15. P0 完成后与 Multi-Agent V2 的衔接

本轮完成后，不继续扩 RAG 接口，直接回到 Multi-Agent V2：

```text
RAG–Agent Evidence Boundary Closure
        ↓
V2-01 Runtime Contract 收口
        ↓
V2-02 Minimal Run Memory Projection
        ↓
V2-03 ContextCompiler V2
        ↓
V2-04 Research Round Recovery / Retrieval Memory
        ↓
V2-05 Writer Paragraph Authority
        ↓
V2-06 Verifier Issue / Verified Claim
```

这时 ResearchAgent 的目标依赖关系固定为：

```text
ResearchAgent
    ↓
RetrievalAction
    ↓
ToolGateway
    ↓
RAGServicePort
    ↓
EvidencePackage
```

因此后续 Multi-Agent V2 不得再直接引用 Milvus、BM25、Dense Retriever、RRF、Reranker、CRAG implementation、ContextPacker。

---

# 16. Definition of Done

本轮只有达到以下全部条件才算完成：

```text
[ ] EvidenceRequirement authority 已冻结
[ ] EvidenceStatus authority 已冻结
[ ] EvidencePackage canonical authority 已冻结
[ ] DeliveryMode 明确归 Application / Gate
[ ] RetrievalAction 公共边界已冻结

[ ] RAGServicePort 保持唯一 Application-facing RAG Service Port
[ ] Application / Agent / ContextManager 不直接依赖 rag.internal
[ ] RAG 不依赖 apps.enterprise_document

[ ] EvidencePackage 成为唯一 canonical Evidence
[ ] RAGContext 只作为 projection
[ ] 生产主链不从 RAGContext.extra 恢复 canonical Evidence

[ ] ResearchEvidencePackage 降级为 compatibility / projection
[ ] SectionEvidenceBundle 降级为 compatibility / projection
[ ] Evidence ID / Citation ID / Chunk ID 语义不再混用

[ ] FakeRAG / RealRAG 可替换且 Agent 代码零修改
[ ] RAG 内部 provider 可替换且 Agent 代码零修改

[ ] Architecture Boundary Test 已加入
[ ] 关键 focused tests 通过
[ ] 主链 E2E smoke 未因接口迁移断裂
```

---

# 17. 最终冻结原则

1. 本轮是 Evidence Boundary Closure，不是 RAG 重构。
2. 已有 RAG 内部 Port 和 Retrieval Baseline 原样复用。
3. `RAGServicePort` 继续作为唯一 Application-facing 服务入口。
4. `EvidencePackage` 是唯一 canonical Evidence authority。
5. `RAGContext` 只是 EvidencePackage 的 Prompt Context projection。
6. Planner 生产 EvidenceRequirement instance，但 Schema authority 属于 RAG Public Contract。
7. Research 选择 RetrievalAction，但不控制 Milvus / BM25 / Reranker 等内部实现参数。
8. RAG 只负责 Evidence Assessment，不负责 DeliveryMode / Human Review / Final Gate。
9. Application 不得 import RAG internal package。
10. RAG 不得反向 import EnterpriseDocument Application。
11. ToolGateway 不重新定义 Evidence 语义，只做权限、预算、重试、幂等、Trace、Schema 校验。
12. Legacy Schema 可以暂时存在，但只能由 canonical contract 单向投影。
13. 不创建第四套 Evidence Schema。
14. 不借接口化任务推进 Adaptive Router、Memory、ContextCompiler、Writer V2 等后续能力。
15. Architecture Boundary Test 负责防止未来 AI Coding 再次破坏边界。

本轮完成后的判断标准不是“新增了多少接口”，而是：

> **RAG 内部可以自由替换实现，Agent / Application 只依赖稳定 Evidence Contract；双方再也不需要通过内部 import、extra dict 或重复 Schema 理解彼此内部实现。**
