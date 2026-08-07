# 企业建设方案 Agent-RAG Multi-Agent V1 完整建设方案与后续实施计划

日期：2026-08-03
状态：Active / 后续实施唯一主计划
适用仓库：`D:\MyCode\rag-agent`
生产主链：`MainlineApplicationService -> SupervisorAgent -> EnterpriseDocumentWorkflowEngine -> SectionExecutionGraph`
规格基线：《企业建设方案 Agent-RAG Multi-Agent V1 架构规格 v1.0》
历史参考：

- `docs/AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_20260729.md`
- `docs/AGENT_RAG_MULTI_AGENT_V1_MAINLINE_MIGRATION_ADR_20260731.md`
- `docs/AGENT_RAG_STANDALONE_RETIREMENT_MATRIX_20260801.md`

本文不删除或改写历史文档。历史文档用于说明旁路实现、迁移决策和退休过程；本文用于定义从当前代码基线继续推进到可演示、可验收、可面试陈述的完整 Multi-Agent V1。

---

## 1. 决策摘要

本项目不再以“功能数量最大化”为目标，而改为：

> 构建一个功能范围受控，但具有生产级复杂记忆、权威事实约束、逐段生成、局部恢复、跨 Agent 协作、可审计和可失效能力的建设方案 Multi-Agent。

V1 的首要目标不是生成所有文档类型，也不是堆叠更多 Agent，而是证明以下能力真实存在：

1. Planner、Research、Writer、Verifier 四个业务 Agent 跨调用无状态。
2. Agent 记忆外置、分层、版本化、可失效、可压缩、可审计。
3. Excel/FactSnapshot 是设备、型号、规格、数量、价格、公式、结果和建设范围的硬事实源。
4. Section 是最小业务执行与恢复单元，Paragraph 是最小生成、提交、修订和验证原子单元。
5. Agent 只负责推理和建议，状态迁移、预算、权限、装配、版本和恢复由确定性 Service 控制。
6. 多 Section 可以并行，但旧 Attempt、旧 Snapshot 和越权结果不能覆盖当前事实。
7. `success`、`partial_success`、`failed` 都产生实际文档和 Generation Report。
8. 任何正式版本不可覆盖，局部恢复生成新版本。

复杂记忆是 P0，不是 RAG、Checkpoint 或 Shared State 的附属能力。在 Memory Foundation 验收前，项目不能宣称“多 Agent 已完整实现”。

---

## 2. V1 范围与主动裁剪

### 2.1 V1 必须完成的业务范围

首轮只支持“建设方案”，优先打通以下章节：

```text
项目背景
建设目标
云算力技术方案
├── 计算资源
└── 网络资源
生成报告附录
```

其中真正用于证明复杂 Agent 能力的主场景是：

- 计算资源；
- 网络资源；
- 两个章节之间的 Verified Claim 共享；
- Excel 设备与数量冲突；
- Paragraph 错误熔断和恢复；
- Research 多轮记忆；
- Verifier Issue 闭环；
- Excel 更新后的记忆失效和局部重跑。

### 2.2 V1 暂缓范围

以下能力不进入当前 P0 主线：

- 可研报告、标书和全品类解决方案；
- 拓扑图自动绘制；
- CAD 机架图；
- 端口分配表；
- 布线计划表；
- 自动设备选型和硬件数量计算；
- 自动投资计算和 Excel 修改；
- 公网搜索；
- 全量复杂 DOCX 样式复刻；
- 完整 Major Version 精细影响分析；
- Neo4j、MongoDB 或微服务化；
- 多租户管理后台；
- 开放式自我反思记忆；
- 跨用户人格记忆；
- 后训练数据派生和蒸馏流程。

### 2.3 主动裁剪原则

当工期冲突时，按以下优先级保留：

```text
复杂记忆
> Excel 权威事实
> Claim Binding
> Paragraph 级熔断与恢复
> 跨章节 Verified Memory
> partial_success / failed 导出
> 完整章节数量
> 高级文档样式
> UI 和外围数据库
```

---

## 3. 当前仓库真实基线

### 3.1 已完成并进入生产主线的能力

当前生产唯一组合根为：

```text
scripts/*
-> MainlineApplicationService
-> SupervisorFactory
-> SupervisorAgent
-> EnterpriseDocumentWorkflowEngine
-> SectionExecutionGraph
```

已经存在并接入的能力包括：

- 唯一 `SupervisorAgent`；
- 顶层生产 LangGraph；
- Section 生产子图；
- 官方 SQLite LangGraph Checkpointer；
- TaskManager；
- Trace 和 Capture；
- ModelGateway；
- RAGService；
- Section 并行执行；
- PlannerAgent 生产 Adapter；
- ResearchAgent、WriterAgent、VerifierAgent 生产 Adapter；
- Paragraph 持久化节点；
- Paragraph Precheck 节点；
- Claim 基础结构；
- research_more / rewrite / replan；
- stale Attempt 拒绝；
- budget draining；
- crash resume；
- Human Review durable interrupt；
- document verify / conflict repair / incremental reverify；
- assembly / assembly validation / final artifact review；
- generation report / version decision / persist deliverable。

### 3.2 已实现但不能等同于生产完成的能力

旁路 `agent_rag_v1` 已经实现了大量强类型 Schema、Service 和专项测试，但 Standalone Runtime 已退休为 fixture/reference。以下文件不能被当成第二生产控制平面：

```text
runtime/agent_rag_v1/WorkflowOrchestrator
runtime/agent_rag_v1/RuntimeFactory
runtime/agent_rag_v1/MultiAgentApplicationService
Standalone WorkflowRouter
Standalone ResumeRouter
JsonCheckpointStore
Standalone TaskStatusCalculator
```

旁路代码仍可作为：

- 行为规范；
- Schema 来源；
- 测试 fixture；
- 迁移参考；
- E2E 场景生成器。

### 3.3 当前最大的真实缺口

#### Planner

生产 Planner 当前主要由确定性 `DocumentPlanningService` 构建初始计划。它已经进入 Agent 外壳，但完整的：

- `source_adapt`；
- `rag_generate`；
- `technical_generate`；
- `skip`；
- `human_review`；
- `local_replan`；
- `change_impact_analysis`；

尚未全部变成真实生产能力。

#### Research

生产 Research 已经通过 ToolGateway 调用真实 EvidenceService，但仍接近“一次检索结果整理器”，未完成完整有限 ReAct：

- Query 历史；
- 多轮检索；
- no-gain 停止；
- Evidence 去重；
- 厂商材料核验；
- 旧项目污染过滤；
- rejected evidence 记忆；
- Evidence Package Artifact。

#### Writer

当前生产 WriterAgent 更接近“控制/审阅 Agent”：现有 SectionGenerationService 先生成 canonical section，WriterAgent 再判断是否完成。WriterAgent 尚未真正成为 Paragraph 生成主体。

需要迁移到：

```text
WriterAgent
-> 生成一个 Paragraph
-> 立即持久化
-> Claim 抽取
-> paragraph_precheck
-> 通过后继续下一 Paragraph
```

#### Verifier

Verifier Schema 已支持四种模式，但生产 Model Reasoner 主要实现 `section_verify`。需要统一完成：

- `paragraph_precheck`；
- `section_verify`；
- `document_verify`；
- `final_artifact_review`。

#### Agent Memory

当前已有 State、Artifact、Event、Tool Store、Checkpoint、Paragraph 和 Claim 等记忆底座，但缺少显式统一的：

- Memory Schema；
- Memory Projection；
- Memory Retriever；
- Context Compiler；
- Memory Invalidator；
- Context Budget；
- Memory Trace。

生产 Verifier 调用中：

```python
binding_refs=[]
verified_section_summary_refs=[]
```

说明跨章节 Verified Memory 尚未真正接入。

### 3.4 当前完成度估计

| 能力域 | 当前成熟度 | 说明 |
| --- | ---: | --- |
| Supervisor 与顶层 Graph | 高 | 唯一生产主线已确立 |
| Section Graph 与并行 | 高 | 节点、并行、checkpoint 已存在 |
| Agent 强类型外壳 | 高 | 四 Agent、Context、Result、Error 已存在 |
| Checkpoint / Resume / stale | 中高 | 已有专项和主线测试 |
| Planner 真实业务推理 | 中低 | 初始计划已接，策略和变化分析不足 |
| Research Agentic 检索 | 中低 | 真实 RAG 已接，多轮记忆不足 |
| Writer 逐段接管 | 低 | 仍由旧整章服务产生 canonical section |
| Claim 与 Excel 真实绑定 | 中低 | 有基础规则，缺真实 FactSnapshot 和完整 Binding |
| Verifier 四模式 | 中低 | 主要 section verify，记忆输入未接全 |
| Agent Memory Layer | 低 | 底座存在，统一层未实现 |
| 最终可交付 DOCX | 低 | 需要模板装配和版本化验证 |
| 真实项目 E2E | 低 | 需要真实输入、真实模型和真实知识库闭环 |

---

## 4. 目标总体架构

```text
                               Existing SupervisorAgent
                                         |
                              EnterpriseDocumentWorkflowEngine
                                         |
                      +------------------+------------------+
                      |                                     |
               plan_document                         execute_sections
                      |                                     |
                PlannerAgent                  SectionExecutionGraph / DAG
                                                            |
                                  +-------------------------+-------------------------+
                                  |                         |                         |
                            ResearchAgent              WriterAgent              VerifierAgent
                                  |                         |                         |
                                  +-------------------------+-------------------------+
                                                            |
                                                   Agent Memory Layer
                                                            |
        +-------------------+-------------------+------------+-------------+-------------------+
        |                   |                   |                          |                   |
   State / Graph       Artifact Store       Event Log              Tool Execution       Checkpoint
        |                   |                   |                       Store                 |
        +-------------------+-------------------+------------+-------------+-------------------+
                                                            |
                                                   Context Compiler
                                                            |
                                               AgentMemoryPackage per call
```

### 4.1 不能改变的架构约束

1. 不增加第二个 Supervisor。
2. 不增加第二套生产 Shared State。
3. 不增加第二套生产 Router、Checkpoint、TaskStatus、Error 和 Artifact 发布链。
4. Agent 实例不得保存 Task 业务状态。
5. Agent 不直接访问底层数据库、向量库、文件系统或模型客户端。
6. Agent 不直接修改 Excel、发布文档或计算 Task 终态。
7. Memory Layer 是现有 State、Artifact、Event、Tool Store、Checkpoint 的统一投影和访问层，不成为第二事实源。
8. 生产验收必须从 `MainlineApplicationService -> SupervisorAgent -> Production Graph` 证明，Standalone E2E 不能代替生产验收。

---

## 5. Agent Memory Layer：核心设计

## 5.1 设计目标

复杂记忆必须解决：

- Agent 在多轮调用中不重复做无效工作；
- Writer 崩溃后能从已提交 Paragraph 继续；
- Verifier 能追踪旧 Issue 是否被修复；
- 后续章节能读取前序章节的 Verified Claim，而不是读取未验证 Draft；
- Excel 或模板变化后，旧记忆能够自动失效；
- 并发 Section 不互相污染；
- 旧 Attempt 和旧 Snapshot 返回的结果不能进入当前记忆；
- Prompt 不因全部历史注入而无限增长；
- 面试和生产审计都能解释一次 Agent 调用了哪些记忆。

## 5.2 记忆分类

### 工作记忆 Working Memory

服务于单次 Invocation 或当前 Section 阶段：

- 当前 SectionState；
- 当前 generation cursor；
- 当前预算；
- 当前 Evidence Package；
- 当前 Draft/Paragraph；
- 当前 Verifier Issue；
- 当前 Agent 调用上下文。

### 情景记忆 Episodic Memory

记录“发生过什么”：

- Attempt 历史；
- Research Query 历史；
- Tool Call；
- Writer 修订；
- Verifier Issue 创建和解决；
- Conflict Repair；
- Human Review；
- 版本发布。

### 语义记忆 Semantic Memory

记录经过治理的业务知识：

- FactSnapshot；
- 企业知识库；
- Evidence；
- Verified Claim；
- VerifiedSectionSummary；
- 项目术语；
- 已验证厂商能力；
- 政策依据。

### 决策记忆 Decision Memory

记录可复用且可审计的业务裁决：

- Planner Strategy；
- Evidence 接受/拒绝原因；
- Verifier Action；
- Re-plan 原因；
- Conflict Repair 决策；
- Version Policy 决策。

### 产物记忆 Artifact Memory

记录不可变产物：

- Paragraph Artifact；
- Draft；
- Evidence Package；
- Claim 集；
- Change Manifest；
- Verified Summary；
- 装配文档；
- Generation Report；
- 正式版本。

### 恢复记忆 Recovery Memory

记录恢复所需最小事实：

- Checkpoint；
- generation cursor；
- 已提交 Paragraph；
- active attempt；
- input snapshot version；
- running invocation；
- budget reservation/settlement；
- side-effect commit 状态。

### 程序性记忆 Procedural Memory

稳定的执行规则：

- Prompt 版本；
- Tool Schema；
- 权限策略；
- Workflow 规则；
- Section Strategy 规则；
- Claim 规则；
- Verification Policy。

程序性记忆属于版本化配置，不允许 Agent 自行修改。

## 5.3 MemoryRecord Schema

```yaml
MemoryRecord:
  schema_version: agent_memory_record_v1
  memory_id: string

  scope:
    tenant_id: string | null
    project_id: string | null
    task_id: string
    run_id: string
    section_id: string | null
    paragraph_id: string | null

  memory_type:
    - working
    - episodic
    - semantic
    - decision
    - artifact
    - recovery
    - procedural

  subject_type:
    - fact
    - evidence
    - rejected_evidence
    - claim
    - section_summary
    - verifier_issue
    - research_decision
    - plan_decision
    - terminology
    - repair_result
    - generation_progress
    - human_review
    - document_version

  content_ref: string
  content_hash: string
  source_refs: [string]

  authority_level:
    - excel_authoritative
    - current_project_authoritative
    - verified_claim
    - validated_vendor
    - enterprise_knowledge
    - historical_reference
    - procedural

  confidence: float | null
  relevance_keys: [string]

  input_snapshot_version: string
  plan_version: integer | null
  section_attempt_id: string | null
  paragraph_version: integer | null
  domain_version: integer | null

  lifecycle_status:
    - active
    - stale
    - superseded
    - invalidated
    - removed

  created_at: datetime
  expires_at: datetime | null
  supersedes: [string]
  invalidated_by: [string]
```

约束：

- `content_ref` 必须指向不可变 Artifact 或受控结构化对象；
- `content_hash` 必须在读取时复核；
- 记忆必须绑定 Snapshot、Plan、Attempt 或 Paragraph Version；
- 记忆不能脱离来源成为新的权威事实；
- 同一 Domain Event 的投影必须幂等；
- MemoryRecord 不保存模型私有思维链。

## 5.4 AgentMemoryPackage Schema

每次 Agent 调用前由 ContextCompiler 生成：

```yaml
AgentMemoryPackage:
  schema_version: agent_memory_package_v1
  invocation_id: string
  agent_role: planner | research | writer | verifier
  execution_mode: string
  task_id: string
  run_id: string
  section_id: string | null
  input_snapshot_version: string
  plan_version: integer
  attempt_id: string

  authoritative_facts: []
  active_evidence: []
  rejected_evidence_history: []
  verified_claims: []
  verified_section_summaries: []
  unresolved_issues: []
  resolved_issue_history: []
  prior_attempt_summaries: []
  generation_progress: {}
  terminology: {}
  relevant_decisions: []

  memory_refs: []
  omitted_memory_refs: []
  omitted_memory_count: integer
  context_budget_usage:
    max_tokens: integer
    estimated_tokens: integer
    selected_items: integer
    compressed_items: integer
```

## 5.5 记忆写入：Memory Projection

新增确定性 `MemoryProjectionService`，从已提交事件投影记忆。

允许形成记忆的事件：

```text
InputSnapshotCreated
FactSnapshotCreated
TemplateSnapshotCreated
PlanAccepted
PlanRevised
EvidencePackageCommitted
EvidenceRejected
ParagraphCommitted
ParagraphSuperseded
ClaimExtracted
ClaimVerified
ClaimInvalidated
VerifierIssueCreated
VerifierIssueResolved
SectionAccepted
SectionPartiallyAccepted
ConflictRepairCompleted
HumanReviewAnswered
DocumentVersionPublished
```

处理链：

```text
业务节点完成
-> Artifact 原子持久化
-> GraphState / Domain State 提交
-> Domain Event 提交
-> MemoryProjectionService 幂等投影
-> Memory Index 更新
```

禁止：

- 模型自己直接写长期记忆；
- 未提交 Token 写入 Memory；
- 未验证 Draft 形成跨章节语义记忆；
- Agent 自报 Claim 直接成为 Verified Memory；
- Memory 投影失败但业务提交被悄悄忽略。

投影失败策略：

- 业务事实已成功提交时，记录可恢复的 projection pending；
- 使用 event_id 作为幂等键重放；
- 不能逆向覆盖业务事实；
- 重放成功后补充 MemoryProjectedEvent。

## 5.6 记忆读取：MemoryRetriever

MemoryRetriever 接收：

```yaml
MemoryQuery:
  invocation_id: string
  agent_role: string
  execution_mode: string
  task_id: string
  run_id: string
  project_id: string | null
  section_id: string | null
  paragraph_id: string | null
  input_snapshot_version: string
  plan_version: integer
  attempt_id: string
  requested_memory_types: []
  requested_subject_types: []
  relevance_keys: []
  authorized_section_ids: []
  max_items: integer
  max_tokens: integer
```

检索顺序：

1. 精确身份过滤：task/project/snapshot/plan/attempt；
2. 生命周期过滤：只允许 active，特殊恢复场景可读取 superseded 摘要；
3. 权限过滤：Agent Role、Section、Artifact；
4. 权威性排序；
5. 依赖和引用范围过滤；
6. 结构化精确匹配；
7. 必要时语义检索；
8. Context Budget 裁剪。

向量库只负责语义候选召回，不能替代：

- Snapshot 查询；
- Version 查询；
- Issue 状态；
- Attempt 状态；
- 权限校验；
- 精确 Fact 查询；
- 生命周期判断。

## 5.7 Context Compiler

ContextCompiler 是复杂记忆的核心访问入口。Agent 不直接调用 MemoryStore。

流程：

```text
AgentInvocationRequest
-> 解析 Agent Role / Mode / Section / Snapshot / Budget
-> Memory Policy 生成查询计划
-> MemoryRetriever 获取候选
-> 权限和生命周期二次校验
-> ContextCompactor 压缩
-> 生成 AgentMemoryPackage
-> 记录 MemoryContextTrace
-> 调用 Agent
```

ContextCompiler 必须保证：

- Excel Fact 优先于所有自然语言历史；
- 未验证 Draft 不跨 Section 传播；
- Verified Claim 可以跨 Section 传播；
- 当前未解决 Issue 优先于已解决历史；
- 当前 Attempt 优先于旧 Attempt；
- 当前 Snapshot 的 Fact 优先于旧 Snapshot；
- Artifact 大正文按需读取，不默认全部展开；
- 每次调用记录 selected/omitted memory refs。

## 5.8 角色化记忆策略

### Planner Memory Policy

读取：

- 当前 TemplateSchema；
- 当前 FactSnapshot；
- SourceInventory；
- 旧 PlanRevision；
- FactDiff / TemplateDiff；
- accepted Section；
- 历史 Re-plan 原因；
- 旧 ImpactAnalysis；
- Section Dependencies。

不读取：

- 未验证正文全文；
- Writer 临时 Prompt；
- 与计划无关的 Tool Raw Result。

### Research Memory Policy

读取：

- 已执行 Query；
- Query fingerprint；
- 每轮 Evidence 增益；
- accepted Evidence；
- rejected Evidence 和 reason code；
- Evidence Gaps；
- conflicts；
- no_gain_rounds；
- 厂商材料核验状态；
- 当前 Section Fact 子集。

写入：

- ResearchRoundCompleted；
- QueryAttempted；
- EvidenceAccepted；
- EvidenceRejected；
- EvidenceGapUpdated；
- ResearchExhausted。

### Writer Memory Policy

读取：

- 当前 Section Strategy；
- Excel Fact 子集；
- Evidence Package；
- 上一版 Paragraph；
- 稳定 Paragraph ID；
- generation cursor；
- Verifier Issue；
- Change Manifest；
- Terminology；
- VerifiedSectionSummary；
- Verified Claims；
- 当前 Attempt 历史摘要。

不读取：

- 其他 Section 未验证 Draft；
- 未授权项目记忆；
- 旧 Snapshot 的 active-looking 记忆；
- 被 invalidated 的 Evidence。

### Verifier Memory Policy

读取：

- 当前 Paragraph/Draft；
- active Claims；
- Fact Bindings；
- Evidence Bindings；
- 历史 Issue；
- Issue 修复状态；
- DraftDiff；
- VerifiedSectionSummary；
- 跨章节 Verified Claims；
- ConflictRepairHistory；
- 当前验证预算。

写入：

- IssueCreated；
- IssueResolved；
- ClaimVerified/Rejected；
- SectionAccepted/PartiallyAccepted；
- DocumentConflictDetected；
- FinalArtifactIssueDetected。

## 5.9 记忆失效：MemoryInvalidator

记忆系统的关键不是“记住”，而是“不会继续使用已经过期的记忆”。

失效触发源：

- Excel Fact 变化；
- 模板变化；
- 项目名称或主体变化；
- 建设范围变化；
- Source Version 变化；
- Evidence 被撤销；
- Paragraph 修改、删除、合并或拆分；
- Claim 被 superseded；
- Plan Revision；
- Section Attempt superseded；
- Human Review 修改权威事实；
- Document Major Version。

失效链示例：

```text
Excel: 服务器数量 6 -> 8
-> FactDiff(device_count)
-> 旧 Fact Memory stale
-> 依赖旧 Fact 的 Claim invalidated
-> 依赖 Claim 的 VerifiedSectionSummary stale
-> 相关 Section Verification invalidated
-> 相关 Draft 标记 requires_revalidation
-> ImpactAnalyzer 计算 regenerate / reverify / reuse
```

必须保留：

- 无关政策 Evidence；
- 与设备数量无关的项目背景；
- 未受影响章节的 Verified Claim；
- 历史版本的不可变正式 Artifact。

MemoryInvalidator 输出：

```yaml
MemoryInvalidationPlan:
  trigger_ref: string
  invalidated_memory_refs: []
  stale_memory_refs: []
  preserved_memory_refs: []
  affected_claim_ids: []
  affected_section_ids: []
  required_actions:
    - regenerate
    - research_again
    - reverify_only
    - reuse
```

## 5.10 Context 压缩与预算

优先级：

```text
Excel Authoritative Fact
> 当前项目权威材料
> 未解决 Verifier Issue
> Verified Claim
> 当前 Evidence
> VerifiedSectionSummary
> 当前修订决策
> 历史 Attempt 摘要
> 已解决旧过程
```

压缩规则：

- Fact、金额、数量、型号和公式不能被摘要改写；
- Issue 保留 issue_id、scope、expected fix 和 status；
- Evidence 保留 evidence_id、source_ref、support span 和结论；
- Verified Summary 保留 Claim IDs，不只保留自然语言摘要；
- 历史 Attempt 只保留结果、停止原因和恢复入口；
- 已解决旧 Issue 默认不展开；
- 大文本通过 Artifact Ref 按需读取；
- 超预算时先删除低权威、低相关、已解决、旧 Attempt 过程记录。

## 5.11 权限与并发

MemoryRetriever 必须校验：

- tenant_id；
- project_id；
- task_id；
- run_id；
- agent_role；
- section_id；
- input_snapshot_version；
- plan_version；
- attempt_id；
- authorized_memory_types；
- authorized_artifact_refs。

并发规则：

- 同一 Section 同一时间只有一个 active Attempt；
- 不同 Section 可以并行写各自 Domain；
- 旧 Attempt Memory 只能进入 Trace 或 superseded history；
- 旧 Snapshot 的结果不能写 active memory；
- Memory Projection 使用 event_id 幂等；
- Verified Summary 必须由 accepted/partially accepted 的已验证 Paragraph 生成；
- Writer 不得读取其他 Section 的 raw Draft；
- Verifier 可以读取授权的跨章节 Verified Claim。

## 5.12 Memory Trace

每次 Agent 调用至少记录：

```yaml
memory_context:
  memory_query_id: string
  requested_types: []
  selected_memory_refs: []
  omitted_memory_refs: []
  rejected_memory_refs: []
  rejection_reasons: {}
  selected_authority_levels: []
  input_snapshot_version: string
  plan_version: integer
  attempt_id: string
  context_token_budget: integer
  estimated_context_tokens: integer
  compaction_applied: boolean
  memory_package_hash: string
```

面试演示应能从 Trace 证明：

- Research 为什么停止继续检索；
- Writer 为什么从 p003 恢复；
- Verifier 为什么判定旧 Issue 已解决；
- 网络章节读取了哪个计算资源 Verified Claim；
- Excel 更新后哪些 Memory 被失效；
- 旧 Attempt 为什么被拒绝。

---

## 6. 输入与事实层

## 6.1 文档解析入口

当前统一文档 Router 已支持：

```text
.doc / .docx -> Word COM Parser
.pdf         -> MinerU -> Parse Eval -> PaddleOCR fallback
```

输出作为原始解析层，不直接等于 RAG Chunk：

```text
raw_data
-> document_ingestion/router_cli.py
-> parsed JSONL
-> data middle platform cleaning
-> cleaned_text_unit_v1
-> chunk_v1
-> index version
-> Milvus / BM25
```

文档治理必须保留：

- project_id；
- source_id；
- source_version；
- document_role；
- should_index；
- is_gold_reference；
- parser；
- parse quality；
- content hash。

金标文档可以解析和保存，但 `should_index=false`，不能进入普通检索索引。

## 6.2 InputSnapshot

所有 Agent 运行前必须创建不可变 InputSnapshot：

```yaml
InputSnapshot:
  snapshot_version: string
  project_context_ref: string
  fact_snapshot_ref: string
  template_schema_ref: string
  source_inventory_ref: string
  retrieval_access_scope_ref: string
  terminology_seed_ref: string | null
  created_at: datetime
```

## 6.3 FactSnapshot

Excel 解析为结构化权威事实：

```yaml
FactRecord:
  fact_id: string
  fact_type: string
  normalized_value: any
  unit: string | null
  source:
    workbook_hash: string
    sheet_name: string
    cell_range: string
    formula: string | null
  authority_level: excel_authoritative
  snapshot_version: string
```

必须覆盖：

- 设备名称；
- 设备类型；
- 型号；
- 规格；
- 数量；
- 单价；
- 税费；
- 成本；
- 总投资；
- 建设范围；
- 公式；
- 计算参数；
- 计算结果；
- 资源配置结果。

冲突规则：

```text
Excel
> 当前项目客户材料
> Verified Claim
> Validated Vendor Evidence
> 企业知识库
> 历史方案
> 政策语料
> 通用表达
```

## 6.4 TemplateSchema

模板解析至少产生：

```yaml
TemplateSchema:
  template_id: string
  template_version: string
  title: string
  section_tree: []
  required_sections: []
  optional_sections: []
  heading_styles: {}
  placeholder_rules: []
  appendices: []
```

V1 可以先使用确定性模板配置，不要求 Planner 自由发现任意模板结构。

---

## 7. 四个业务 Agent

## 7.1 PlannerAgent

职责：

- 章节适用性；
- Section Strategy；
- Section DAG；
- hard dependency；
- consistency reference；
- 并行组；
- Section 目标；
- Fact/Evidence 需求；
- 局部 Re-plan；
- 变化影响分析。

V1 模式：

```text
initial_plan
local_replan
change_impact_analysis
```

V1 Strategy：

```text
source_adapt
technical_generate
skip
human_review
```

`rag_generate` 可以在第二轮补充；首轮先把 `technical_generate` 和 `source_adapt` 做扎实。

Planner 不负责：

- 正文；
- 主动知识检索；
- Excel 修改；
- 文档装配；
- Task 终态。

## 7.2 ResearchAgent

职责：

- 白名单知识源检索；
- Query 规划；
- Evidence 筛选和去重；
- 项目污染过滤；
- 厂商材料核验；
- Evidence Conflict；
- Evidence Gap；
- Evidence Package。

建议预算：

```yaml
max_research_rounds_per_section: 3
max_queries_per_round: 3
max_tool_calls_per_section: 9
max_evidence_items: 12
max_no_gain_rounds: 2
```

停止条件：

- sufficient；
- conflicted；
- exhausted；
- human_review；
- budget exhausted。

## 7.3 WriterAgent

职责：

- 根据 Strategy、Fact、Evidence 和 Memory 逐 Paragraph 生成；
- 稳定 Paragraph ID；
- Paragraph Artifact；
- generation cursor；
- Paragraph 局部修订；
- Writer Source Hints；
- Change Manifest。

每段流程：

```text
ContextCompiler
-> WriterAgent 生成一个 Paragraph
-> Paragraph Artifact 持久化
-> ParagraphGeneratedEvent
-> Claim Pipeline
-> paragraph_precheck
-> 通过：cursor 前移
-> 阻断：修订当前 Paragraph
```

Writer 不负责：

- 主动检索；
- 修改 Excel；
- 事实计算；
- 最终验证；
- 发布文档。

## 7.4 VerifierAgent

四种模式：

```text
paragraph_precheck
section_verify
document_verify
final_artifact_review
```

### Paragraph Precheck

阻断：

- Excel 设备冲突；
- 型号、规格、数量、金额冲突；
- Excel 中不存在的设备；
- 建设范围冲突；
- 旧项目关键事实；
- 后续内容会建立在错误基础上的硬错误。

### Section Verify

检查：

- Claim 完整性；
- Fact/Evidence Binding；
- 项目适配；
- 旧项目信息；
- 证据覆盖；
- 技术结论可靠性；
- 章节目标覆盖；
- 段落逻辑；
- accepted paragraph set。

### Document Verify

检查：

- 跨章节设备数量；
- 建设范围；
- 父子章节关系；
- 目标与技术方案；
- 术语；
- 重复结论；
- 跨章节引用。

### Final Artifact Review

检查用户最终看到的成品：

- 阅读断裂；
- 标题正文不一致；
- 旧项目名称；
- 厂商营销话术；
- Prompt 泄漏；
- 拼接重复；
- 占位语；
- 附录一致性。

Verifier 只能建议动作，不直接修改正文和 Task 状态。

---

## 8. Claim 与事实绑定

## 8.1 必须绑定的 Claim

- 设备；
- 型号；
- 规格；
- 数量；
- 金额；
- 总投资；
- 建设范围；
- 公式；
- 参数；
- 计算结果；
- 资源配置；
- 技术能力；
- 性能；
- 兼容性；
- 安全与合规。

## 8.2 Claim Pipeline

```text
Paragraph
-> DeterministicClaimExtractor
-> SemanticClaimExtractor
-> ClaimMerger
-> ClaimBindingResolver
-> Verifier
```

Writer 自报 Claim 只作为 Hint。

## 8.3 Claim 生命周期

```text
active
invalidated
removed
superseded
```

Paragraph 变更规则：

- 新增：完整抽取；
- 修改：旧 Claim invalidated，重新抽取；
- 删除：Claim removed；
- 未改：复用旧 Claim；
- 合并/拆分：旧 Claim superseded；
- 修改比例大于阈值：整章重抽取。

默认阈值：

```yaml
full_section_reextract_threshold: 0.4
```

## 8.4 Accept 硬规则

- 关键 Claim 未抽取：不能 accept；
- 关键 Claim 未绑定：不能 accept；
- Excel Claim 未绑定 Excel：不能 accept；
- 硬事实错误：不能 partial_accept；
- partially accepted 只保留正确且已验证 Paragraph。

---

## 9. 状态、Artifact、Event、Tool Store、Checkpoint 与 Memory 的关系

```text
GraphState / Domain State
-> 当前结构化业务事实

Artifact Store
-> 大文本和不可变产物

Event Log
-> 发生过什么

Tool Execution Store
-> 工具调用与标准化结果

Checkpoint
-> 快速恢复快照

Memory Layer
-> 对以上事实的可查询、可压缩、可失效、按角色授权投影
```

Memory 不能成为第六套独立事实源。

读取时必须校验：

- 引用存在；
- Hash；
- Schema；
- Task/Project/Section；
- Attempt；
- Snapshot；
- Lifecycle；
- 权限。

---

## 10. Workflow 主链

## 10.1 正常主链

```text
1. Supervisor 接收任务
2. Input Services 构建 InputSnapshot
3. MemoryProjection 创建输入记忆
4. Planner.initial_plan
5. PlanValidator
6. 注册完整 Section DAG
7. Router 调度可执行 Section
8. 每个 Section：
   a. ContextCompiler(Research)
   b. Research
   c. Evidence Package Commit
   d. ContextCompiler(Writer)
   e. Writer Paragraph Loop
   f. Claim Pipeline
   g. paragraph_precheck
   h. section_verify
   i. rewrite / research_more / replan / human_review
   j. accepted / partially_accepted / failed / skipped
9. SectionGroup Aggregator
10. document_verify(full)
11. ConflictRepairPlan 串行执行
12. document_verify(incremental)
13. DocumentAssembler
14. AssemblyValidator
15. final_artifact_review
16. TaskStatus
17. GenerationReport
18. VersionPolicy
19. Immutable Publish
20. final_result
```

## 10.2 降级主链

触发：

- Task 预算耗尽；
- Section 修复预算耗尽；
- Human Review 未返回；
- 证据持续不足；
- 不可恢复系统错误。

流程：

```text
禁止新调用
-> 等待当前原子操作结束
-> 冻结已验证内容
-> 聚合 Section
-> 有条件执行有限 Document Verify
-> 装配
-> 基本装配校验
-> Generation Report
-> 导出 partial_success / failed
```

降级路径不能跳过实际导出和 Generation Report。

---

## 11. 文档装配与版本

## 11.1 V1 最小交付

首轮允许简化样式，但必须生成真实 `.docx`：

- 固定标题层级；
- 目录；
- accepted 正文；
- partially accepted 正文和提示；
- failed 占位；
- skipped 移除；
- 生成报告附录；
- 文件可打开；
- 内容 Hash；
- 版本目录不可覆盖。

## 11.2 版本

```text
v1.0 partial_success
-> 人工补全网络材料
-> v1.1 success
```

Minor：

- 局部 Section 恢复；
- 局部修订；
- 同类 Evidence 补充；
- 装配修复。

Major：

- 建设范围变化；
- 新增/删除设备类别；
- 核心型号、数量变化；
- 模板结构变化；
- 项目边界变化。

首轮 Major Version 只需支持：

- 正确判定 Major；
- 失效相关 Memory；
- Planner 重新计算影响范围；
- 安全复用明确无关 Section。

不要求首轮覆盖所有复杂差异场景。

---

## 12. 后续实施路线

## Phase 0：冻结基线与输入解析验收

目标：得到稳定输入，禁止边做 Agent 边猜数据结构。

任务：

- `P0-01` 完成当前 33 份 Word/PDF 批次；
- `P0-02` 验证 document manifest、parser quality、gold reference 隔离；
- `P0-03` 定义 `SourceInventorySchema`；
- `P0-04` 打通 parsed -> cleaned_text_unit_v1；
- `P0-05` 打通 chunk_v1 和 index version；
- `P0-06` 建立受控 RetrievalAccessScope；
- `P0-07` 建立一个真实项目最小材料集。

退出条件：

- 真实材料可检索；
- 金标不能被普通检索；
- 每个 Evidence 可追溯 source_id/version；
- Input Snapshot 可以稳定复现。

预计：3～5 个高强度工程日。

## Phase 1：InputSnapshot 与 Excel FactSnapshot

任务：

- `P1-01` 定义 `InputSnapshotSchema`；
- `P1-02` Excel Workbook Parser；
- `P1-03` Fact normalization；
- `P1-04` Fact ID / Source Cell / Formula；
- `P1-05` Excel 内部冲突检测；
- `P1-06` TemplateSchema；
- `P1-07` Snapshot Hash / Version；
- `P1-08` FactDiff / TemplateDiff；
- `P1-09` 将 Snapshot 接入生产 Graph。

退出条件：

- 设备、型号、数量和公式可以精确查询；
- 同一输入产生稳定 Snapshot Version；
- Excel 更新产生确定性 FactDiff；
- Agent 不直接读取原始 Excel。

预计：4～6 个工程日。

## Phase 2：Memory Contract 与 Store

任务：

- `M2-01` `MemoryRecordSchema`；
- `M2-02` `MemoryQuerySchema`；
- `M2-03` `AgentMemoryPackageSchema`；
- `M2-04` `MemoryInvalidationPlanSchema`；
- `M2-05` `MemoryStorePort`；
- `M2-06` SQLite V1 Store；
- `M2-07` scope/lifecycle 索引；
- `M2-08` content hash 校验；
- `M2-09` event_id 幂等约束；
- `M2-10` Memory read/write audit。

建议代码落点：

```text
backend/apps/enterprise_document/schemas/agent_memory/
backend/apps/enterprise_document/services/agent_memory/
```

不能放到 Agent 实例中。

退出条件：

- MemoryRecord 可持久化和精确查询；
- 重放同一事件不重复创建；
- stale/invalidated 不被普通读取；
- Hash 错误 fail closed。

预计：2～3 个工程日。

## Phase 3：Memory Projection 与生命周期

任务：

- `M3-01` Domain Event -> Memory Projection 映射；
- `M3-02` Input/Fact/Plan projection；
- `M3-03` Evidence projection；
- `M3-04` Paragraph/Claim projection；
- `M3-05` Issue/Verification projection；
- `M3-06` Section Summary projection；
- `M3-07` Version projection；
- `M3-08` projection pending/replay；
- `M3-09` supersede/remove semantics；
- `M3-10` MemoryProjectedEvent。

退出条件：

- 业务提交成功后可查询对应记忆；
- Projection 失败可重放；
- Memory 不反向覆盖业务事实；
- 只有已提交事件产生长期记忆。

预计：2～3 个工程日。

## Phase 4：MemoryRetriever 与 ContextCompiler

任务：

- `M4-01` role-based Memory Policy；
- `M4-02` exact filter；
- `M4-03` dependency-aware filter；
- `M4-04` semantic candidate adapter；
- `M4-05` authority ranking；
- `M4-06` Context Budget；
- `M4-07` Context Compactor；
- `M4-08` Package Hash；
- `M4-09` Memory Trace；
- `M4-10` Agent Adapter 注入 `memory_package_ref`。

退出条件：

- 每次 Agent 调用都有可审计 Memory Package；
- 不再传空的 `verified_section_summary_refs` 和 `binding_refs`；
- 其他 Section raw Draft 不进入 Writer Context；
- 超预算时确定性压缩。

预计：3～4 个工程日。

## Phase 5：Research 记忆闭环

任务：

- `R5-01` Query fingerprint；
- `R5-02` Query History Memory；
- `R5-03` Research Round；
- `R5-04` Evidence gain 计算；
- `R5-05` no-gain stop；
- `R5-06` accepted/rejected Evidence；
- `R5-07` vendor validation；
- `R5-08` old-project pollution；
- `R5-09` Evidence Package Artifact；
- `R5-10` exhausted/conflicted/human-review 路由。

退出条件：

- 同一或等价 Query 不无意义重复；
- rejected Evidence 带原因保留；
- 连续无增益自动停止；
- Research 崩溃后能从已提交 Round 恢复；
- Tool Call 数受预算约束。

预计：3～5 个工程日。

## Phase 6：Writer 逐 Paragraph 接管

任务：

- `W6-01` 移除“旧 Section 先生成、Writer 只判断”的主路径；
- `W6-02` Paragraph Generation Prompt；
- `W6-03` stable paragraph_id；
- `W6-04` Paragraph Artifact；
- `W6-05` ParagraphGeneratedEvent；
- `W6-06` generation cursor；
- `W6-07` source_adapt；
- `W6-08` technical_generate；
- `W6-09` paragraph revision；
- `W6-10` Change Manifest；
- `W6-11` DraftDiff；
- `W6-12` crash resume；
- `W6-13` Memory Package 注入。

退出条件：

- WriterAgent 真正生成 Paragraph；
- 每段成功后立即持久化；
- p003 崩溃时 p001/p002 不丢失；
- 恢复不重复生成已提交 Paragraph；
- 修改范围可由 DraftDiff 校验；
- Excel 中不存在的设备不能进入 accepted Paragraph。

预计：7～10 个工程日。

## Phase 7：Claim Binding 与 Verifier Issue Memory

任务：

- `V7-01` Deterministic Claim；
- `V7-02` Semantic Claim；
- `V7-03` Claim Merge；
- `V7-04` Fact Binding；
- `V7-05` Evidence Binding；
- `V7-06` Incremental Claim；
- `V7-07` Issue stable ID；
- `V7-08` IssueCreated/Resolved Memory；
- `V7-09` Paragraph Precheck Agent Mode；
- `V7-10` Section Verify Agent Mode；
- `V7-11` unauthorized Writer change；
- `V7-12` partial accept Paragraph set。

退出条件：

- Excel Claim 必须绑定 Excel；
- 关键 Claim 未绑定不能 accept；
- 旧 Issue 修复后被标记 resolved，不重复创建；
- 修改一个 Paragraph 只重抽该段 Claim；
- 越界修改可识别。

预计：5～8 个工程日。

## Phase 8：跨章节 Verified Memory

任务：

- `C8-01` VerifiedSectionSummary Artifact；
- `C8-02` Verified Claim Index；
- `C8-03` hard dependency memory；
- `C8-04` consistency reference memory；
- `C8-05` terminology memory；
- `C8-06` document_verify Memory Package；
- `C8-07` ConflictRepairPlan；
- `C8-08` serial repair；
- `C8-09` ImpactAnalyzer；
- `C8-10` incremental reverify。

退出条件：

- 网络章节可以读取计算资源“8 台服务器”的 Verified Claim；
- 网络章节不能读取计算资源未验证 Draft；
- 跨章节冲突生成串行 RepairTask；
- 修复后只复验影响范围；
- 连续修复产生新冲突时升级 full verify。

预计：4～6 个工程日。

## Phase 9：Memory Invalidator 与版本复用

任务：

- `I9-01` FactDiff invalidation；
- `I9-02` TemplateDiff invalidation；
- `I9-03` Paragraph/Claim invalidation；
- `I9-04` Evidence version invalidation；
- `I9-05` Plan/Attempt invalidation；
- `I9-06` Minor Version reuse；
- `I9-07` Major Version impact；
- `I9-08` stale result Trace；
- `I9-09` invalidation audit report。

退出条件：

- 6 台 -> 8 台使相关 Claim/Summary stale；
- 无关章节安全复用；
- 旧 Attempt 返回不能写 active memory；
- v1.0 和 v1.1 绑定各自 Memory Snapshot 和 Generation Report。

预计：4～6 个工程日。

## Phase 10：真实 DOCX 与终态闭环

任务：

- `D10-01` 最小 DOCX Template Assembler；
- `D10-02` 标题/目录；
- `D10-03` accepted Paragraph；
- `D10-04` partial/failed placeholder；
- `D10-05` Generation Report Appendix；
- `D10-06` Assembly Validator；
- `D10-07` final_artifact_review；
- `D10-08` immutable version path；
- `D10-09` openability test；
- `D10-10` success/partial/failed export。

退出条件：

- 三种终态都有 `.docx`；
- 文档可以打开；
- skipped 不出现；
- partial/failed 原因和恢复建议存在；
- 旧版本不覆盖。

预计：4～7 个工程日。

## Phase 11：E2E 与面试资产

必须跑通：

1. Research 不重复检索；
2. Writer p003 崩溃恢复；
3. Verifier Issue 闭环；
4. 计算 -> 网络跨章节 Verified Claim；
5. Excel 6 -> 8 记忆失效；
6. 旧 Attempt 结果拒绝；
7. Context Budget 压缩；
8. Partial Success 文档；
9. Failed 仍导出；
10. Minor Version 局部恢复；
11. Major Version 影响分析；
12. Human Review 后新版本。

输出面试资产：

```text
Trace JSONL
Memory Context Trace
Memory Invalidation Report
Paragraph Timeline
Issue Lifecycle
Cross-section Claim Graph
Version Diff
Generation Report
最终 DOCX
```

预计：4～6 个工程日。

---

## 13. 推荐执行顺序与依赖

```text
P0 输入解析验收
  -> P1 InputSnapshot / FactSnapshot
      -> M2 Memory Contract / Store
          -> M3 Projection
              -> M4 Retriever / ContextCompiler
                  -> R5 Research Memory
                  -> W6 Writer Paragraph
                      -> V7 Claim / Verifier Issue
                          -> C8 Cross-section Memory
                              -> I9 Invalidation / Version
                                  -> D10 DOCX Closure
                                      -> Phase 11 E2E
```

并行建议：

- P0 文档解析运行时，可以写 P1/M2 Schema 和测试；
- R5 与 W6 可以在 M4 完成后并行开发，但共享 Memory Package Contract；
- D10 可在 C8 期间提前实现最小模板装配；
- UI、数据库迁移和性能优化不得抢占上述主线。

---

## 14. 工期估计

按单人 + AI 高强度开发估计：

| 阶段 | 工程日 |
| --- | ---: |
| 输入与 Snapshot | 7～11 |
| Memory Contract / Projection / Context | 7～10 |
| Research Memory | 3～5 |
| Writer Paragraph 接管 | 7～10 |
| Claim / Verifier | 5～8 |
| Cross-section / Invalidation / Version | 8～12 |
| DOCX / E2E | 8～13 |
| 合计 | 45～69 |

范围严格裁剪、AI 辅助有效、已有基础设施复用良好时，可以压缩到约 5～7 周；若同时追求全章节、完整样式、完整 Major Version 和 UI，工期会显著增加。

---

## 15. 必须通过的 Memory 验收用例

### MEM-01 Research 不重复搜索

输入：第一轮已执行 Query，后续同义 Query 无新增 Evidence。
预期：Query fingerprint 命中，no_gain_rounds 增加，达到 2 后停止，不继续调用 Tool。

### MEM-02 Writer 崩溃恢复

输入：p001、p002 已提交，p003 生成中崩溃。
预期：恢复读取 generation_progress，p001/p002 不重写，从 p003 重新开始。

### MEM-03 Verifier Issue 闭环

输入：Verifier 创建 issue-001，Writer 修改目标 Paragraph。
预期：Verifier 读取旧 Issue 和 DraftDiff，issue-001 resolved，不重复创建同类 Issue。

### MEM-04 跨章节共享

输入：计算资源已验证 8 台服务器，网络资源开始生成。
预期：Writer 读取 Verified Claim；不能读取计算资源 raw Draft。

### MEM-05 Excel 更新失效

输入：服务器数量 6 -> 8。
预期：旧 Fact、Claim、Summary、Verification 失效；无关章节复用。

### MEM-06 旧 Attempt 拒绝

输入：attempt-v1 尚未返回，attempt-v2 已激活。
预期：v1 只进入 Trace，不能写 GraphState、Artifact active index 或 active Memory。

### MEM-07 Context Budget

输入：历史 Memory 超预算。
预期：Fact、未解决 Issue、Verified Claim、当前 Evidence 保留；旧过程压缩；记录 omitted count。

### MEM-08 权限隔离

输入：Writer 请求其他项目或其他 Section 未验证 Draft。
预期：permission_rejected，写审计事件，不返回内容。

### MEM-09 Minor Version 复用

输入：v1.0 网络章节失败，新增网络材料。
预期：v1.1 只恢复网络章节，其他 accepted Section 复用 Verified Memory。

### MEM-10 Major Version 失效

输入：新增安全设备类别。
预期：v2.0，Planner 读取旧 Memory 做影响分析，失效受影响记忆，安全复用无关记忆。

---

## 16. 业务验收用例

### Excel 权威

历史方案 6 台，Excel 8 台。最终只能写 8 台，不允许 6～8 台折中。

### 厂商污染

旧项目名称和与 Excel 冲突的型号被拒绝，通用能力可以适配。

### Paragraph 硬错误

错误数量触发 precheck blocking，后续 Paragraph 不生成，修复通过后继续。

### Incremental Claim

修改一个 Paragraph，只重抽该段；未改 Paragraph Claim 保留。

### Unauthorized Change

Verifier 只允许改 p003，Writer 同时改 p001。DraftDiff 识别越界并触发重新抽取/验证。

### Cross-section Conflict

建设范围 6 台，计算资源 8 台，Excel 8 台。Verifier 选择修复建设范围，执行串行 RepairTask。

### Partial Success

网络章节失败，其他章节成功。导出 partial_success DOCX，网络保留标题和失败说明，附录有恢复建议。

### Failed

没有可交付正文仍导出模板骨架和失败附录。

### Human Review

一个 Section 等待人工，其他 Section 继续；当前版本 partial_success，人工回答后生成新版本。

### Budget Draining

预算耗尽后禁止新调用，在途 Paragraph 允许完成，随后聚合并导出。

---

## 17. Definition of Done

只有同时满足以下条件，Multi-Agent V1 才能标记完成：

### 生产主线

- 从 MainlineApplicationService 进入；
- 唯一 Supervisor；
- 唯一生产 GraphState/Checkpoint；
- Standalone Runtime 不作为生产入口。

### Agent

- 四 Agent 都有真实业务输入和强类型输出；
- Writer 真正逐 Paragraph 生成；
- Research 真正多轮检索并使用记忆；
- Verifier 四模式至少完成 V1 所需路径；
- Agent 跨调用无状态。

### Memory

- MemoryRecord、Projection、Retriever、ContextCompiler、Invalidator 完成；
- 角色化 Memory Policy 完成；
- 跨章节 Verified Memory 完成；
- Snapshot/Attempt/权限/Hash 校验完成；
- Context Budget 和 Memory Trace 完成；
- MEM-01 ～ MEM-10 全部通过。

### Fact / Claim

- Excel FactSnapshot 不可变；
- 关键 Claim 可追溯；
- Excel Claim 必须绑定 Excel；
- 旧 Claim 正确失效；
- 跨章节 Claim 可复用。

### Recovery

- Paragraph crash resume；
- stale result rejection；
- Human Review resume；
- budget draining；
- minor version reuse；
- input update invalidation。

### Deliverable

- success / partial_success / failed 都有 DOCX；
- Generation Report 存在；
- 正式版本不可覆盖；
- 文件可打开；
- Trace 和 Memory Audit 可查看。

---

## 18. 面试陈述主轴

项目完成后，统一回答：

> 我们没有把 Agent 记忆做成实例内 history，也没有把所有历史都塞进向量库。四个 Agent 跨调用无状态，复杂记忆外置为 State、Artifact、Event、Tool Store 和 Checkpoint 的统一投影。MemoryProjection 只把已提交事件转成带 Snapshot、Plan、Attempt、Hash 和生命周期的 MemoryRecord；ContextCompiler 在每次调用前按照 Agent Role、Section、权限、权威性和 Context Budget 生成 AgentMemoryPackage。Excel 更新时，MemoryInvalidator 根据 FactDiff、Claim Binding 和 Section Dependency 精确失效旧 Claim、Summary 和 Verification。并行场景下旧 Attempt 结果只能进入 Trace，不能覆盖 active memory。这样 Research 能避免重复检索，Writer 能从 Paragraph cursor 崩溃恢复，Verifier 能追踪 Issue 生命周期，后续章节只能共享 Verified Claim 而不是未验证 Draft。

必须配合真实 Trace 演示，不只背概念。

---

## 19. 下一批可直接执行的任务

当前下一批不应继续增加外围功能。建议依次执行：

```text
TASK-001  审核 33 份文档解析结果和统一 manifest
TASK-002  定义 SourceInventorySchema
TASK-003  定义 InputSnapshotSchema
TASK-004  定义 FactSnapshotSchema 和 Excel Parser Port
TASK-005  定义 MemoryRecord / MemoryQuery / AgentMemoryPackage
TASK-006  实现 SQLiteMemoryStore
TASK-007  实现 MemoryProjectionService 最小三类事件
TASK-008  实现 MemoryRetriever 精确过滤
TASK-009  实现 ContextCompiler V1
TASK-010  把 verified_section_summary_refs / binding_refs 接入生产 Adapter
TASK-011  实现 Research Query History + no-gain memory
TASK-012  选“计算资源”做第一个真实垂直切片
```

TASK-001～TASK-004 可以并行分析；TASK-005 是后续 Memory 主线的契约冻结点；TASK-010 前必须完成 TASK-005～TASK-009。

---

## 20. 最终原则

1. 复杂记忆是核心，不是附件。
2. 功能可以少，记忆、事实和恢复不能浅。
3. Agent 无状态，Memory 外置。
4. Memory 是已提交事实的投影，不是第二事实源。
5. 写入必须受事件、版本和权限控制。
6. 读取必须经 ContextCompiler。
7. 失效比存储更重要。
8. 只有 Verified Memory 可以跨章节传播。
9. 旧 Attempt、旧 Snapshot 和越权结果不能污染当前状态。
10. 面试价值来自真实 Trace、恢复和失效演示，不来自 Agent 数量。
