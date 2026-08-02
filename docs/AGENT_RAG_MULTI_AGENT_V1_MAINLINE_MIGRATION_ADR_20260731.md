# ADR：Agent-RAG Multi-Agent V1 接入现有生产主链

日期：2026-07-31  
状态：Proposed for review  
适用仓库：`D:\MyCode\rag-agent`  
调查分支：`checkpoint/controlled-architecture-20260728`  
调查基线：`37b4ea3 fix: restore index lifecycle public contract`  
实施轮次：第 1 轮——仓库映射与迁移决策  
本轮代码变更：无；只新增本 ADR

---

## 1. 决策摘要

本 ADR 决定以当前生产主链为唯一承载面，把 Standalone `agent_rag_v1` 中已经验证的业务 Agent、Paragraph、Claim、文档闭环和恢复语义逐步迁移进现有 LangGraph，而不是把 Standalone Runtime 整体接到 Supervisor 后面。

必须同时满足以下约束：

1. 保留唯一的 `SupervisorAgent`。
2. 保留唯一的企业文档顶层生产图 `EnterpriseDocumentWorkflowEngine`。
3. 保留唯一的生产 Section 子图 `SectionExecutionGraph`。
4. 不引入第二套 Shared State。
5. 不引入第二套 Checkpoint、Router、TaskStatus、Error、Evidence、Assembler 或发布主链。
6. Planner、Research、Writer、Verifier 保持跨调用无状态，只通过生产组合根获得 RAG、ModelGateway、工具、预算和 Trace 能力。
7. 每一轮迁移都必须从 `MainlineApplicationService -> SupervisorAgent -> EnterpriseDocumentWorkflowEngine` 证明；Standalone E2E 不能作为生产接入验收。
8. 在第 8 轮生产验收完成前，Standalone 代码保留为 fixture、行为基线和迁移来源，不提前删除。

最关键的否定性决策是：

```text
禁止：SupervisorAgent
      -> WorkflowOrchestrator
      -> MultiAgentSharedStateSchema
      -> JsonCheckpointStore
```

目标接入方式是：

```text
MainlineApplicationService
-> SupervisorAgent
-> EnterpriseDocumentWorkflowEngine
   -> plan_document
      -> PlannerAgent
   -> execute_sections
      -> SectionExecutionGraph
         -> ResearchAgent
         -> WriterAgent
         -> VerifierAgent
   -> document_verify / repair / assembly / review
-> 现有任务终态、Checkpoint、Trace 与 Artifact 生命周期
```

---

## 2. 背景

### 2.1 原始目标架构

《企业建设方案 Agent-RAG Multi-Agent V1 架构规格》定义了四个业务 Agent：

- Planner Agent
- Research Agent
- Writer Agent
- Verifier Agent

并要求 Excel/事实快照、状态迁移、预算、文档装配、版本、恢复等确定性能力由 Service 和 Workflow 控制，而不是继续 Agent 化。

### 2.2 旁路实现现状

2026-07-29 已经在以下四个旁路包中实现了完整的 Multi-Agent 垂直闭环：

```text
backend/apps/enterprise_document/
├── agents/agent_rag_v1/
├── schemas/agent_rag_v1/
├── services/agent_rag_v1/
└── runtime/agent_rag_v1/
```

旁路已经具备：

- 四个无状态业务 Agent；
- 强类型 Invocation / Result；
- 独立 Multi-Agent Shared State；
- StateGateway 与分域版本；
- WorkflowRouter 与 Section DAG；
- 独立 Checkpoint / Event Log；
- Paragraph、Claim、Precheck；
- Document Verify、冲突修复、装配、报告、版本；
- Crash Recovery、Human Review、Budget Draining；
- 独立 CLI 与 Standalone E2E。

但旁路明确没有接入：

- `SupervisorAgent`；
- 生产顶层 LangGraph；
- 生产 Section LangGraph；
- 真实 ModelGateway；
- 真实 RAGService/Milvus；
- `scripts/run_demo.py`。

### 2.3 主链恢复后的新前提

第 0 轮已经恢复并验证当前生产主链：

- `top_level_business_graph` 可导入；
- Self-RAG、ParentStore、Milvus Builder、metadata、failure attribution、index lifecycle 等接口漂移已修复；
- 全量收集：`1025 tests collected`；
- 非 `real_provider`：`1024 passed, 1 deselected, 0 failed`；
- Fake 主链 E2E 生成 8 章节 deliverable；
- 真实 Milvus + M3E + BGE Reranker smoke 通过；
- 真实本地 Qwen 八章节主链完成；
- 真实运行最终因引用覆盖不足返回 `DOCUMENT_HARD_GATE_FAILED`，但 `execution_integrity=passed`；
- Trace、LangGraph SQLite Checkpoint、model_usage、task_state、raw_interactions、eval_samples 均可落盘；
- 调查开始时工作区干净。

因此当前问题已经从“先恢复主链”转为“如何不破坏主链地迁移旁路能力”。

---

## 3. 本轮范围与非目标

### 3.1 本轮输出

本 ADR 必须给出：

- 当前生产主链组件事实；
- Standalone V1 组件事实；
- 一一对应关系；
- `保留 / 合并 / 替换 / 退休 / fixture` 决策；
- 状态字段投影；
- 节点映射；
- 各轮允许修改的文件；
- 默认禁止修改的文件；
- 迁移验收路径；
- 旁路退休条件。

### 3.2 本轮禁止事项

第 1 轮不允许：

- 修改 Python 生产代码；
- 修改 Schema；
- 注册任何新 Agent；
- 修改 Supervisor Factory；
- 修改 LangGraph 节点；
- 新建 Adapter；
- 删除 Standalone 代码；
- 改写测试结果以满足目标架构描述。

---

## 4. 当前生产主链事实

### 4.1 唯一生产入口

```text
scripts/run_demo.py / scripts/run_pipeline.py / scripts/workflow_control.py
-> scripts/mainline_runtime.py
-> MainlineApplicationService
-> SupervisorFactory
-> SupervisorAgent
-> EnterpriseDocumentWorkflowEngine
```

`SupervisorFactory` 当前：

1. 创建 ModelGateway；
2. 创建 RAGService；
3. 创建 Trace 与 Capture；
4. 注册 `ProjectInputNormalizerAgent`；
5. 构造 `SchemeWriterAgent` 作为服务组合壳；
6. 不注册 `SchemeWriterAgent.run()`；
7. 把 `scheme_writer.stage_service` 注入 `EnterpriseDocumentWorkflowEngine`；
8. 注入官方 SQLite LangGraph Checkpointer；
9. 注入 `JsonlSideEffectCommitStore`。

因此当前生产主链不经过：

- `SchemeWriterAgent.run()`；
- `SchemeGenerationUseCase.run()`；
- `WorkflowOrchestrator`；
- `MultiAgentApplicationService`。

### 4.2 顶层生产图

当前顶层节点为：

```text
normalize_project_input
-> plan_document
-> retrieve_document_evidence
-> initialize_generation
-> execute_sections
-> assemble_document
-> evaluate_document_gate
-> persist_deliverable / persist_rejected / human_review
-> finalize_workflow_result / finalize_workflow_failure
```

每个节点都有显式：

- read keys；
- GraphState write paths；
- business payload write paths；
- timeout / retry contract；
- failure commit policy；
- Trace node identity；
- LangGraph checkpoint identity。

### 4.3 Section 生产子图

当前 Section 子图为：

```text
prepare_section
-> retrieve_section_evidence（条件）
-> evaluate_evidence_gate
-> generate_section
-> evaluate_generation
   -> retrieve_more
   -> corrective_retrieval
   -> finalize_section
```

该子图已经真实负责：

- 章节级检索；
- 证据充分性判断；
- 正常生成阻断；
- Section 生成；
- Self-RAG/Corrective Retrieval；
- 预算快照；
- 引用 Registry；
- Section Evidence Bundle；
- Section checkpoint namespace；
- 并行隔离与 fan-in。

### 4.4 当前状态事实源

| 层级 | 当前事实源 | 作用 |
|---|---|---|
| 通用生产业务状态 | `GraphStateSchema` / `SharedStateSchema` | 任务身份、规范上下文、节点结果、终态和显式 Artifact 生命周期 |
| 文档业务状态 | `SchemeGenerationGraphPayloadSchema` | Plan、文档 RAG、Section 结果、Assembly、Hard Gate、Human Review、终态载荷 |
| Section 业务状态 | `SectionExecutionGraphPayloadSchema` | Section 输入、证据、预算、恢复轮次、生成、评估、路由和结果 |
| LangGraph 运行状态 | `SchemeGenerationGraphState` / `SectionExecutionGraphState` | checkpoint-safe TypedDict 外壳 |
| 任务持久终态 | `JsonlTaskManager` 事件流 | PENDING/RUNNING/终态事实 |
| 工作流终态 | `WorkflowEngineResult` / `AgentResult` | 本次图执行事实 |
| Trace | 每次运行的 Trace JSONL | 节点、RAG、模型、工具和错误事件 |
| 模型用量 | model_usage ledger | logical call、provider attempt、token 和失败计数 |
| 检索证据 | `EvidenceBundleSchema` | 唯一 canonical RAG evidence contract |
| 错误 | `ErrorSchema` + `FailureCategory` | 唯一生产错误事实协议 |
| 重试 | `RetryDecisionPolicy` | 根据失败类别与上下文决定重试 |
| 文档装配 | `DocumentAssembler` | 确定性装配和证据要求汇总 |
| 最终质量 | `evaluate_scheme_draft` / document gate | 最终交付 Hard Gate |
| 外部副作用 | `SideEffectCommitStore` | 幂等 claim/commit/release |
| Checkpoint | 官方 LangGraph Checkpointer | inspect/resume/cancel/stream 的恢复事实源 |

---

## 5. Standalone V1 事实

### 5.1 Standalone 入口

```text
scripts/run_agent_rag_v1_standalone.py
-> MultiAgentApplicationService
-> RuntimeFactory
-> WorkflowOrchestrator
-> MultiAgentSharedStateSchema
```

### 5.2 Standalone 控制面

`WorkflowOrchestrator` 当前自己负责：

- 初始化和持有完整状态；
- 调用 Planner；
- Section DAG 调度；
- Research/Writer/Verifier 循环；
- Attempt 和 Re-plan；
- Router 决策；
- Budget Draining；
- Human Review；
- Checkpoint；
- Event Replay；
- Document Verify；
- Assembly；
- Final Review；
- TaskStatus；
- Version Publish；
- Export。

这与生产 Supervisor、顶层图、Section 图、Checkpoint 和终态计算形成重复控制面。

### 5.3 Standalone 的高价值资产

并非全部旁路代码都应删除。以下能力具有迁移价值：

- 四个无状态业务 Agent；
- AgentExecutionContext / AgentExecutionResult 的身份、权限、预算和 Trace 思想；
- Planner/Research/Writer/Verifier 强类型 Payload；
- Section Attempt 语义；
- Research decision summary；
- Paragraph Artifact、稳定 ID 和 generation cursor；
- Incremental Claim Pipeline；
- Paragraph Precheck；
- Evidence Sanitizer；
- Document Verify；
- Conflict Repair Plan；
- Generation Report；
- Assembly Validation；
- Final Artifact Review；
- Version Policy；
- Impact Analysis；
- Running Invocation Recovery 和 Human Review Resume 的确定性决策规则。

---

## 6. 约束性架构决策

### ADR-01：生产 Supervisor 不替换

**决定：保留 `SupervisorAgent`。**

V1 不增加第二个 Supervisor，也不把 `WorkflowOrchestrator` 包装成一个 Agent 注册进 Supervisor。

### ADR-02：顶层生产 LangGraph 不替换

**决定：保留 `EnterpriseDocumentWorkflowEngine`。**

Planner、Document Verify、Assembly Review 等能力必须成为现有图的节点或现有节点内部调用。

### ADR-03：Section 生产子图不替换

**决定：保留并增强 `SectionExecutionGraph`。**

Research、Writer、Verifier 必须接入该子图；禁止创建 `MultiAgentSectionGraph` 与其并行存在。

### ADR-04：不引入新的 Shared State

**决定：`MultiAgentSharedStateSchema` 不进入生产主链。**

其细粒度业务字段投影到：

- `SchemeGenerationGraphPayloadSchema`；
- `SectionExecutionGraphPayloadSchema`；
- 必要的强类型嵌套 Schema。

`GraphStateSchema` 继续作为唯一生产 canonical state family。

### ADR-05：不引入第二套 StateGateway

**决定：旁路 `StateGateway` 不接入生产。**

生产写入继续由：

- 节点 candidate state；
- `GraphStateDiffer`；
- `GraphStateWriteContract`；
- `GraphStateApplier`；
- Payload codec；
- LangGraph checkpoint

控制。

Agent 不直接修改 GraphState。Agent 返回强类型业务结果，由节点 Adapter/Projector 更新当前 Payload，之后进入现有写契约。

### ADR-06：不引入第二套 Checkpoint/Event 恢复源

**决定：旁路 `JsonCheckpointStore`、`DomainEventLog` 和基于它们的完整状态恢复不进入生产。**

生产恢复继续使用 LangGraph checkpoint。Paragraph 等细粒度外部副作用必须使用稳定逻辑 ID、Artifact Ref 和现有 SideEffect Commit Ledger 保证幂等。

### ADR-07：不引入第二套 Router

**决定：旁路 `WorkflowRouter` 不作为运行时 Router 接入。**

它的确定性优先级规则可以迁入现有 LangGraph conditional edge / route policy，但最终路由事实必须由生产图产生。

### ADR-08：不引入第二套 TaskStatus

**决定：旁路 `TaskStatus` 和 `TaskStatusCalculator` 不成为生产终态事实源。**

生产终态继续由：

- Section/Document 业务结果；
- Hard Gate；
- 顶层图 finalize 节点；
- `JsonlTaskManager`

共同闭合。

“至少一个已验证 Paragraph 可交付则 partial_success”的算法可以迁入现有 Gate/Artifact Route，但不得产生另一套终态枚举。

### ADR-09：不引入第二套错误协议

**决定：旁路 `WorkflowErrorSchema` 不进入生产。**

映射规则：

- category -> `FailureCategory`；
- retryability -> `RetryDecisionPolicy` 的输入或明确策略字段；
- scope、affected_section_ids、resume hint -> `ErrorSchema.details/extra`；
- source -> `ErrorSourceSchema`；
- failed node -> `ErrorSchema.failed_node`。

### ADR-10：不引入第二套 Evidence Contract

**决定：`EvidenceBundleSchema` 保持唯一 canonical RAG evidence。**

旁路 `ResearchEvidencePackageSchema` 只作为 Research Agent 的业务决策摘要，不得复制或替代 canonical chunks/citations/context/lineage。

### ADR-11：不引入第二套 Assembler

**决定：保留并增强现有 `DocumentAssembler`，退休 `MultiAgentDocumentAssembler`。**

旁路中以下语义迁入现有 assembler：

- 只装配已验证 Paragraph；
- partial/failed 占位；
- Generation Report 附录；
- skipped section 移除；
- 用户可见降级说明。

### ADR-12：Agent 保持无状态

**决定：保留 Planner/Research/Writer/Verifier 的无状态外壳。**

允许实例持有：

- Reasoner/ModelGateway Adapter；
- Tool session factory；
- Prompt/Schema/只读配置；
- 进程级模型连接和缓存。

禁止持有：

- Task/Run 进度；
- Section 状态；
- Attempt 计数；
- Draft/Evidence 的跨调用业务副本；
- 独立客户端生命周期。

### ADR-13：生产组合根负责外部依赖

Research 不直接初始化 Milvus；Writer/Verifier 不直接加载模型。

```text
Research Agent
-> Tool Adapter
-> SchemeEvidenceService / RAGService / Milvus

Writer / Verifier Agent
-> Reasoner Adapter
-> ModelGateway
```

### ADR-14：节点 ID 优先保持稳定

顶层和 Section 节点名参与 checkpoint、Trace 和测试契约。初始迁移优先在现有节点内部替换实现：

- `plan_document` 内调用 PlannerAgent；
- `retrieve_section_evidence` 内调用 ResearchAgent；
- `generate_section` 内调用 WriterAgent；
- `evaluate_generation` 内调用 VerifierAgent。

不得仅为了目标命名把 `plan_document` 直接改为 `plan_scheme_generation`。若未来必须改名，需单独设计 checkpoint namespace/schema 迁移并明确旧运行不可恢复的边界。

### ADR-15：Mainline E2E 是唯一生产验收

以下不构成生产接入成功：

- 直接调用 Agent；
- 手工串联四个 Agent；
- 调用 `WorkflowOrchestrator`；
- Standalone CLI 成功；
- 只跑旁路测试；
- Fake Runtime 未经过 Supervisor/生产图。

每轮验收至少必须证明：

```text
MainlineApplicationService
-> SupervisorAgent
-> EnterpriseDocumentWorkflowEngine
-> 目标生产节点
-> LangGraph Checkpoint
```

---

## 7. 当前主链组件清单

| 当前组件 | 当前状态 | 迁移角色 | 决策 |
|---|---|---|---|
| `MainlineApplicationService` | active | 唯一应用入口 | 保留 |
| `SupervisorFactory` | active | 唯一生产组合根 | 保留，按轮次小改 |
| `SupervisorAgent` | active | 唯一 Supervisor | 保留 |
| `EnterpriseDocumentWorkflowEngine` | active | 顶层生产图 | 保留并增强 |
| `SectionExecutionGraph` | active | 单 Section 生产子图 | 保留并增强 |
| `SectionParallelExecutionGraph` | active | Section fan-out/fan-in | 保留 |
| `SchemeGenerationStageService` | active | 顶层业务 Stage | 保留并逐步委托 Agent/Service |
| `DocumentPlanningService` | active | 当前确定性 Planner | Round 3 被 PlannerAgent 增强/替代 |
| `SchemeEvidenceService` | active | 当前 RAG 业务边界 | 保留，作为 Research Tool Adapter 后端 |
| `SectionGenerationService` | active | 当前 Writer 生成后端 | 保留，逐步被 Writer Reasoner/Paragraph 流程使用 |
| `SectionQualityEvaluator` | active | 当前 Section 质量判断 | 保留，映射到 Verifier 确定性检查 |
| `DocumentAssembler` | active | 唯一生产装配器 | 保留并增强 |
| `evaluate_scheme_draft` | active | 唯一最终 Hard Gate | 保留并增强 |
| `EvidenceBundleSchema` | active | canonical RAG evidence | 保留 |
| `ErrorSchema` / `FailureCategory` | active | canonical error/failure | 保留 |
| `WorkflowBudget` | active | 当前节点/Section 预算 | 保留并扩展 |
| `ToolExecutor` | active | 标准工具执行/Trace/错误边界 | 保留 |
| `SideEffectCommitStore` | active | 外部副作用幂等事实源 | 保留 |
| 官方 LangGraph Checkpointer | active | 唯一恢复事实源 | 保留 |
| `JsonlTaskManager` | active | 任务持久状态 | 保留 |
| Run Trace / model_usage / capture | active | 可观测与数据闭环 | 保留 |

---

## 8. V1 旁路组件处置矩阵

### 8.1 Agents 与 Agent Contract

| V1 组件 | 决策 | 迁移目标 | 说明 |
|---|---|---|---|
| `StatelessBusinessAgent` | 保留并适配 | 生产 Agent Invocation 外壳 | 去掉旁路 StatePatch/Error 依赖 |
| `PlannerAgent` | 保留并适配 | `plan_document` | 输出 canonical `DocumentPlanSchema` |
| `ResearchAgent` | 保留并适配 | Section RAG 节点 | 使用 production EvidenceBundle |
| `WriterAgent` | 保留并适配 | `generate_section` / Paragraph 子流程 | Round 4 先整章，Round 6 再 Paragraph 化 |
| `VerifierAgent` | 保留并适配 | Section Verify / Document Verify / Final Review | 四种 mode 分阶段接入 |
| `AgentExecutionContextSchema` | 合并 | 新生产 Agent Context Schema | 保留 identity/attempt/auth/budget/trace |
| `AgentExecutionResultSchema` | 合并 | 新生产 Agent Result Schema | 取消直接 StatePatch authority |
| `PlannerInvocationInputSchema` | 合并 | 生产 Planner 输入 | 不能只传 plan_ref；需承载可验证快照 |
| `ResearchInvocationInputSchema` | 合并 | Section Research 输入 | refs + canonical evidence request |
| `WriterInvocationInputSchema` | 合并 | Section Writer 输入 | Round 6 增加 paragraph cursor |
| `VerifierInvocationInputSchema` | 合并 | Section/Document Verifier 输入 | 复用现有事实与 Gate 输出 |
| 角色 Payload Schemas | 保留并调整 | 生产 Payload 嵌套模型 | 映射到 canonical plan/evidence/section/gate |

### 8.2 State 与控制面

| V1 组件 | 决策 | 替代事实源 |
|---|---|---|
| `MultiAgentSharedStateSchema` | 退休 | `GraphStateSchema` + 文档/Section Payload |
| `DomainVersionsSchema` | 退休 | `graph_revision`、payload revision、attempt/snapshot 校验 |
| `StatePatchRequestSchema` | 替换 | Agent Result -> Node Projector -> 现有 GraphState write contract |
| `StateGateway` | 退休 | `GraphStateDiffer/WriteContract/Applier` + Payload codec |
| `WritePermissionValidator` | 合并语义 | Agent context authorized paths + Node contract |
| `StateTransitionValidator` | 合并语义 | LangGraph route policy + Pydantic state validation |
| `SupervisorStateAdapter` | 退休 | 无需建立第二套状态 |
| `WorkflowRouter` | 退休实现 | 现有 conditional edges / route policy |
| `SectionDagScheduler` | 替换 | `DocumentPlanSchema` + Section parallel graph |
| `TaskStatusCalculator` | 退休实现 | Hard Gate + finalize node + TaskManager |

### 8.3 持久化、工具和预算

| V1 组件 | 决策 | 迁移目标 |
|---|---|---|
| `JsonCheckpointStore` | 退休 | 官方 LangGraph Checkpointer |
| `DomainEventLog` | 退休为恢复事实源 | Run Trace、Task events、LangGraph checkpoint |
| `EventReplayRecoveryService` | 退休实现 | LangGraph resume + 幂等 side effects |
| `FileArtifactStore` | 条件保留 | 仅作为大对象不可变 Artifact 后端，挂到 RunWorkspace |
| `ToolGateway` | 合并 | 授权会话前端 + production ToolExecutor/RAG/ModelGateway |
| `ToolExecutionStore` | 退休为独立事实源 | ToolResult、Trace、capture/side-effect ledger |
| `BudgetManager` | 合并语义 | `WorkflowBudget`、Parallel allocator、model_usage |
| `RunningInvocationRecoveryService` | 保留决策规则 | 现有 resume/execution control/side-effect ledger |
| `HumanReviewResumeService` | 保留决策规则 | 顶层/Section LangGraph resume route |

### 8.4 Writer、Claim 与文档闭环

| V1 组件 | 决策 | 迁移目标 |
|---|---|---|
| `ParagraphIdentityService` | 保留 | Round 6 production Writer pipeline |
| `StreamingWriterService` | 保留并改造 | Section 子图 Paragraph loop |
| `WriterProgressEventLog` | 合并 | Trace + checkpoint payload + Artifact ref |
| `DraftDiffService` | 保留 | Round 6 Writer revision |
| `ClaimPipeline` | 保留并合并 | 现有 HighRiskClaim/TrustedFact/Citation 体系 |
| `IncrementalClaimPipeline` | 保留 | Round 6 incremental update |
| `ParagraphPrecheckService` | 保留 | Round 6 deterministic precheck node/service |
| `EvidenceSanitizer` | 保留并合并 | Research Agent 后处理 |
| `SectionGroupAggregator` | 合并 | 现有 Section fan-in/Document assembly |
| `DocumentVerifierService` | 保留并接入 | Round 7 document_verify |
| `ConflictRepairPlanService` | 保留并接入 | Round 7 serial repair |
| `MultiAgentDocumentAssembler` | 退休实现 | 现有 `DocumentAssembler` 增强 |
| `DocumentAssemblyValidator` | 保留并接入 | Round 7 assembly validation |
| `FinalArtifactReviewService` | 保留并接入 | Round 7 final review |
| `GenerationReportService` | 保留并接入 | Round 7 report |
| `VersionPolicyService` | 保留并接入 | Round 7/8 version decision |
| `DocumentVersionPublisher` | 合并 | 现有 persist nodes / artifact lifecycle |
| `ImpactAnalyzer` | 保留 | Planner local replan / version impact |

### 8.5 Standalone Runtime

| V1 组件 | 决策 | 删除时机 |
|---|---|---|
| `WorkflowOrchestrator` | fixture/reference，最终退休 | 第 8 轮 Mainline E2E 覆盖其场景后 |
| `RuntimeFactory` | fixture，最终退休 | 第 8 轮 |
| `MultiAgentApplicationService` | fixture，最终退休 | 第 8 轮 |
| `ScenarioAgentReasoner` 等 fake adapters | 保留必要 fixture | 第 8 轮后按测试需要保留 |
| `run_agent_rag_v1_standalone.py` | 历史验收入口，最终归档/删除 | 第 8 轮 |
| Standalone E2E | 改写为 Mainline E2E | 第 8 轮 |

---

## 9. 一一对应关系

| 目标能力 | 当前生产承载点 | V1 来源 | 迁移动作 |
|---|---|---|---|
| Planner | `plan_document` + `DocumentPlanningService` | `PlannerAgent` | Agent 增强现有节点，输出 `DocumentPlanSchema` |
| Plan validation | `DocumentPlanSchema` model validation | DAG validation ideas | 复用唯一性/依赖/DAG 环校验；不新建第二事实源 |
| Research initial | `retrieve_section_evidence` | `ResearchAgent` | Agent 调用 Tool Adapter，返回 canonical evidence + decision summary |
| Research recovery | `retrieve_more` / `corrective_retrieval` | Research action loop | 复用相同 ResearchAgent，不另建循环 |
| Evidence Gate | `evaluate_evidence_gate` | Research status/action | 保持确定性节点，消费 Research 输出 |
| Writer | `generate_section` + SectionGenerationService | `WriterAgent` | Round 4 先替换调用边，Round 6 Paragraph 化 |
| Section Verify | `evaluate_generation` + quality services | `VerifierAgent(section_verify)` | Verifier 输出 action/issues，由图路由 |
| Paragraph Precheck | 尚未成为独立生产节点 | `ParagraphPrecheckService` | Round 6 加入 Writer 子流程 |
| Claims | HighRiskClaim/TrustedFact/Citation checks | Claim Pipeline | 合并，不复制 Claim truth |
| Section Attempt | generation_attempt/recovery_count/section checkpoint | V1 Attempt | Round 2 建立强类型 Attempt 嵌套状态 |
| Section parallel | `SectionParallelExecutionGraph` | SectionDagScheduler | 保留生产 fan-out/fan-in，迁移依赖语义 |
| Document Verify | Hard Gate 前缺独立业务节点 | DocumentVerifierService | Round 7 插入 `document_verify` |
| Conflict Repair | 当前主要为 Section corrective | ConflictRepairPlanService | Round 7 串行 repair route |
| Assembly | `DocumentAssembler` | MultiAgentDocumentAssembler | 增强现有，退休旁路实现 |
| Assembly Validation | 当前部分在 schema/gate | DocumentAssemblyValidator | Round 7 独立确定性检查 |
| Final Artifact Review | 当前无独立 mode | FinalArtifactReviewService/Verifier mode | Round 7 接入 |
| Generation Report | E2E report/capture 为运行报告 | GenerationReportService | Round 7 增加业务 GenerationReport |
| Version Policy | 当前 artifact 未形成正式文档版本闭环 | VersionPolicyService | Round 7/8 接入 persist |
| Human Review | 顶层 LangGraph interrupt/resume | HumanReviewResumeService | 保留生产机制，迁移最小恢复决策 |
| Crash Recovery | LangGraph checkpoint + side-effect ledger | Standalone checkpoint/event replay | 使用现有恢复，迁移 Paragraph 幂等语义 |
| Task Status | finalize + TaskManager | TaskStatusCalculator | 保留生产 authority，吸收 verified paragraph 规则 |

---

## 10. 状态字段投影

### 10.1 总体原则

不把 `MultiAgentSharedStateSchema` 嵌套进 `GraphState.extra`，也不把它序列化后作为一个 opaque payload 字段。

应新增或迁移细粒度强类型模型，并直接成为现有文档/Section Payload 的字段。

### 10.2 文档级投影

| V1 状态 | 生产目标 | 说明 |
|---|---|---|
| `state_meta.task_id/run_id` | `GraphStateSchema.task_id/run_id`、document payload identity | 不复制 authority |
| `task_context` | `ProjectInputSchema`、GraphState context bundle、TaskManager | 不新建 task status |
| `source_snapshot` | ProjectInput/table_analysis/structured_facts/source refs | Round 2 明确 source snapshot identity/hash |
| `execution_plan` | `document_plan` + 新 plan revision/impact fields | `DocumentPlanSchema` 是 canonical plan |
| `document_validation` | 新 `DocumentValidationStateSchema` 嵌入 document payload | Round 7 使用 |
| `artifact_state` | `assembly/generated_draft/rejected_artifact/deliverable_final_result` | 保持显式 artifact lifecycle |
| `runtime_control` | Graph runtime、WorkflowBudget、execution control | 不复制 cancelled/draining truth |
| `human_reviews` | 现有 human review fields，未来增加 section-scoped review | 使用 LangGraph interrupt/resume |
| `final_result` | 现有 terminal_agent_result/error/artifact route | TaskManager 保持终态 authority |

### 10.3 Section 级投影

建议第 2 轮在 `SectionExecutionGraphPayloadSchema` 中增加强类型嵌套字段，名称可以在实现时微调，但语义固定：

```yaml
section_lifecycle:
  execution:
    status
    execution_id
    active_attempt_id
    input_snapshot_version
    plan_revision
    hard_dependencies
    consistency_references
    generation_cursor
  research:
    decision_summary
    evidence_contract_ref_or_hash
    evidence_gap_count
    research_rounds
    tool_call_ids
  writing:
    draft_version
    paragraph_refs
    paragraph_versions
    expected_paragraph_ids
    generation_complete
    change_manifest_ref
  claims:
    claim_ids
    active_claim_ids
    claim_set_version
    extraction_ref
  verification:
    execution_mode
    recommended_action
    issue_ids
    accepted_paragraph_ids
    rejected_paragraph_ids
    verification_rounds
  attempt:
    attempt_id
    attempt_number
    plan_revision
    input_snapshot_version
    status
    started_at
    finished_at
```

现有字段的迁移关系：

| 现有字段 | 新强类型域 | 兼容处理 |
|---|---|---|
| `generation_attempt` | `attempt.attempt_number` / writing rounds | 迁移期单向投影，不双向任意写 |
| `recovery_count` | research rounds / attempt metadata | 保留兼容视图 |
| `tool_call_ids` | research.tool_call_ids | canonical ID 仍来自 RAG/Tool result |
| `active_assessment` | research.decision_summary + canonical evidence assessment | EvidenceBundle 保持 authority |
| `retrieval_metadata` | research/verification metadata | 后续逐步收窄 dict |
| `generation_check` | verification decision | Verifier action 不替代确定性 checks |
| `budget_state` | attempt/section runtime budget | `WorkflowBudget` snapshot 仍为 authority |
| `section` | writing + verification 的最终业务投影 | `SchemeSectionSchema` 保持交付边界 |
| `evidence` | final active Section Evidence Bundle | 保持 canonical projection |
| `route/current_node/executed_nodes` | LangGraph runtime | 不进入 Agent-owned state |

### 10.4 Agent Context 投影

保留：

- invocation_id；
- logical_operation_id；
- task_id/run_id/section_id；
- role/mode；
- execution_id/attempt_id；
- input_snapshot_version；
- authorized tools/artifacts；
- budget；
- trigger；
- trace context。

替换：

| V1 字段 | 生产映射 |
|---|---|
| `expected_domain_versions` | document `business_revision`、section `transition_count`、attempt/snapshot 校验 |
| `writable_state_paths` | Node Contract + Adapter 固定 projector，不允许 reasoner自由声明路径 |
| `task_context_ref` | 受控 ProjectInput/source snapshot 或 Artifact ref |
| `section_state_ref` | Section payload snapshot/ref |

### 10.5 Agent Result 投影

保留：

- invocation/role/mode/attempt identity；
- typed payload；
- emitted signals；
- artifact refs；
- tool call refs；
- budget usage；
- trace ref。

替换：

| V1 字段 | 生产映射 |
|---|---|
| `proposed_state_patches` | 删除 authority；由节点 Adapter 固定投影到 Payload |
| `WorkflowErrorSchema` | `ErrorSchema` |
| `AgentInvocationStatus` | Agent-local status，映射到 `ExecutionStatus`，不产生 Task 终态 |

---

## 11. 节点映射

### 11.1 顶层节点

| 当前节点 | 迁移后职责 | Agent/Service | 节点 ID 策略 |
|---|---|---|---|
| `normalize_project_input` | 输入/Fact/Source Snapshot 规范化 | 确定性 Service | 保持 |
| `plan_document` | Planner invocation + canonical plan validation | PlannerAgent + schema validation | 保持 |
| `retrieve_document_evidence` | 文档级初始 evidence，可保留为全局上下文 | EvidenceService/Research policy | 初期保持 |
| `initialize_generation` | 初始化 Section lifecycle/attempt state | 确定性 Service | 保持 |
| `execute_sections` | 调用生产 Section 子图 | Section graph | 保持 |
| 新 `document_verify` | 跨章节验证 | Verifier(document_verify) + deterministic service | Round 7 增加 |
| 新 `repair_document_conflicts` | 串行冲突修复 | ConflictRepairPlan + Section resume | Round 7 增加 |
| `assemble_document` | 只装配已验证内容、附录和占位 | 增强 DocumentAssembler | 保持 |
| 新 `validate_document_assembly` | 目录/层级/占位/附录一致性 | deterministic validator | Round 7 增加 |
| 新 `final_artifact_review` | 成品轻量审阅 | Verifier(final_artifact_review) | Round 7 增加 |
| `evaluate_document_gate` | 最终 Hard Gate | 现有 gate | 保持最终 authority |
| persist/finalize | 报告、版本、artifact route、task result | 现有 nodes + version/report service | 保持 |

### 11.2 Section 节点

| 当前节点 | 迁移后职责 | Agent/Service | ID 策略 |
|---|---|---|---|
| `prepare_section` | 初始化 Attempt、授权和输入快照 | deterministic | 保持 |
| `retrieve_section_evidence` | Research initial invocation | ResearchAgent | 保持 |
| `evaluate_evidence_gate` | Research action/证据充分性硬规则 | deterministic | 保持 |
| `generate_section` | Writer invocation | WriterAgent | 保持 |
| `evaluate_generation` | Verifier section mode + route decision | VerifierAgent + deterministic checks | 保持 |
| `retrieve_more` | Research recovery invocation | ResearchAgent | 保持 |
| `corrective_retrieval` | Research corrective invocation | ResearchAgent | 保持 |
| 新 Paragraph loop | generate -> persist -> claim -> precheck -> continue/repair | Writer + deterministic services | Round 6 在子图内细分 |
| `finalize_section` | 固化 Section Result/Evidence/Attempt | deterministic | 保持 |

### 11.3 路由所有权

Agent 可以返回建议：

```text
write
research_more
rewrite
replan
human_review
partial_accept
accept
reject
```

但真正 conditional edge 必须由生产图根据：

- Agent typed result；
- budget；
- attempt/snapshot；
- deterministic gate；
- failure category；
- cancellation/draining

共同计算。

---

## 12. 分轮文件白名单

本白名单是默认修改边界。超出范围必须在该轮开始前更新 ADR 或单独审批。

### 第 1 轮：ADR

允许：

```text
docs/AGENT_RAG_MULTI_AGENT_V1_MAINLINE_MIGRATION_ADR_20260731.md
```

禁止：所有 Python、配置、测试和脚本文件。

### 第 2 轮：Schema 和状态投影

允许：

```text
backend/apps/enterprise_document/schemas/scheme_generation_graph.py
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/apps/enterprise_document/schemas/scheme_writer/planning.py
backend/apps/enterprise_document/schemas/scheme_writer/section_execution.py
backend/apps/enterprise_document/schemas/scheme_writer/*.py（仅新增/拆分强类型 Schema）
backend/tests/ 中与 Schema、codec、checkpoint round-trip 直接相关的测试
```

条件允许：

```text
backend/agent/runtime/langgraph_state.py
backend/agent/runtime/graph_state_ops.py
```

仅当新增字段不能由现有 codec 自动序列化时修改；禁止改变现有提交语义。

禁止：Agent、RAG、ModelGateway、Workflow 节点和业务服务实现。

### 第 3 轮：Planner 接入

允许：

```text
backend/apps/enterprise_document/agents/agent_rag_v1/base.py
backend/apps/enterprise_document/agents/agent_rag_v1/planner.py
backend/apps/enterprise_document/schemas/agent_rag_v1/ 中 Planner/Execution 相关文件
backend/apps/enterprise_document/services/scheme_writer/document_planning_service.py
backend/apps/enterprise_document/services/scheme_writer/stage_service.py
backend/apps/enterprise_document/workflows/top_level_business_graph.py
backend/bootstrap/supervisor_factory.py（仅依赖注入）
backend/tests/ 中 Planner mainline 验收文件
```

允许新增：

```text
backend/apps/enterprise_document/services/scheme_writer/agent_integration/
```

禁止：Section Graph、Paragraph、Document closure 和 Standalone 删除。

### 第 4 轮：Research / Writer / Verifier 接入 Section 子图

允许：

```text
backend/apps/enterprise_document/agents/agent_rag_v1/{base,research,writer,verifier}.py
backend/apps/enterprise_document/schemas/agent_rag_v1/ 对应 invocation/payload 文件
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py
backend/apps/enterprise_document/services/scheme_writer/section_quality_evaluator.py
backend/bootstrap/supervisor_factory.py（仅注入 Fake Reasoner/Agent dependencies）
backend/tests/ 中 Section mainline 测试
```

禁止：真实 RAG/Model Adapter 改造、Paragraph 和文档级闭环。

### 第 5 轮：真实 RAG 和 ModelGateway Adapter

允许：

```text
backend/apps/enterprise_document/services/scheme_writer/agent_integration/
backend/apps/enterprise_document/services/scheme_writer/evidence_service.py
backend/apps/enterprise_document/services/scheme_writer/model_service.py
backend/bootstrap/rag_service_factory.py
backend/bootstrap/model_factory.py
backend/bootstrap/supervisor_factory.py
backend/agent/runtime/tool_executor.py（仅统一授权/结果适配）
backend/contracts/ 中相关 Port
backend/tests/ 中真实 Adapter 和 mainline 测试
```

禁止：Agent 直接导入 MilvusClient、Transformers loader 或创建独立 ModelGateway。

### 第 6 轮：Paragraph 和 Claim Pipeline

允许：

```text
backend/apps/enterprise_document/services/agent_rag_v1/{paragraph_identity,streaming_writer,draft_diff,claim_pipeline,paragraph_precheck}.py
backend/apps/enterprise_document/schemas/agent_rag_v1/{claims,writer_pipeline,payloads}.py
backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py
backend/apps/enterprise_document/services/scheme_writer/section_quality_evaluator.py
backend/apps/enterprise_document/services/scheme_writer/high_risk_claim_service.py
backend/apps/enterprise_document/services/scheme_writer/trusted_fact_ledger.py
backend/apps/enterprise_document/services/scheme_writer/citation_service.py
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/tests/ 中 Paragraph/Claim/Mainline recovery 测试
```

允许新增 production-owned Paragraph Artifact Adapter；禁止引入第二套 checkpoint。

### 第 7 轮：文档级闭环

允许：

```text
backend/apps/enterprise_document/services/agent_rag_v1/{document_verifier,conflict_repair,assembly_validator,final_artifact_review,generation_report,version_policy,impact_analyzer}.py
backend/apps/enterprise_document/services/scheme_writer/document_assembler.py
backend/apps/enterprise_document/services/document_gate.py
backend/apps/enterprise_document/services/scheme_writer/stage_service.py
backend/apps/enterprise_document/workflows/top_level_business_graph.py
backend/apps/enterprise_document/schemas/scheme_generation_graph.py
backend/apps/enterprise_document/schemas/scheme_writer/*.py
backend/tests/ 中 document closure/Mainline E2E 测试
```

禁止：并存第二个 assembler 或直接从旁路发布 TaskStatus。

### 第 8 轮：恢复、预算和旁路退休

允许：

```text
backend/apps/enterprise_document/workflows/*
backend/agent/runtime/*（仅必要的恢复/幂等集成）
backend/core/runtime/side_effects.py（仅必要增强）
backend/apps/enterprise_document/quality/budget.py
backend/apps/enterprise_document/runtime/agent_rag_v1/*
backend/apps/enterprise_document/services/agent_rag_v1/ 中已被 production 替代的控制面文件
scripts/run_agent_rag_v1_standalone.py
backend/tests/test_agent_rag_v1_*.py
```

删除或归档前必须证明所有 Standalone 关键场景已经改写为 Mainline E2E。

---

## 13. 默认保护文件

以下文件默认不修改。只有对应轮次明确列入白名单且有独立回归时才允许改动：

```text
backend/application/mainline_service.py
backend/application/task_factory.py
backend/agent/supervisor_agent.py
backend/agent/runtime/official_checkpoint_saver.py
backend/task/task_manager.py
scripts/run_demo.py
scripts/mainline_runtime.py
backend/rag/vector_store/*
backend/rag/offline/*
backend/rag/runtime/*
backend/model_gateway/*
```

特别约束：

- 不通过修改 `run_demo.py` 绕过生产组合根；
- 不通过更换 active index 掩盖 Agent 接入错误；
- 不通过放宽 Hard Gate 让 E2E 表面成功；
- 不通过 fake fallback 吞掉真实 RAG/Model 配置错误；
- 不删除旧 checkpoint 或默认数据以让测试通过。

---

## 14. 后续实施顺序与退出条件

### 第 2 轮：Schema 和状态投影

进入条件：本 ADR 审核通过。

交付：

- Plan/Section/Research/Writing/Claim/Verification/Attempt/DocumentValidation 强类型投影；
- 现有顶层与 Section Payload 承载；
- LangGraph checkpoint round-trip；
- 无业务行为变化。

退出条件：

- 当前 E2E 输出和节点顺序不变；
- 新字段可序列化、恢复；
- 没有 `MultiAgentSharedStateSchema` 进入生产调用链。

### 第 3 轮：Planner 接入顶层主链

交付链：

```text
plan_document
-> PlannerAgent
-> DocumentPlanSchema
-> Pydantic DAG validation
-> execute_sections
```

退出条件：

- 从 Supervisor 进入；
- Planner Agent 被 Trace 记录；
- Fake Planner 可替换当前确定性规划；
- invalid/环形计划在进入 Section 前被拒绝；
- 生产 checkpoint 包含 canonical plan；
- Standalone Planner 直测不算验收。

### 第 4 轮：Research / Writer / Verifier 接入 Section 子图

先用 Fake Reasoner。

退出条件：

- 真实 Mainline 入口；
- 真实 GraphState；
- 真实 Section checkpoint namespace；
- 真实 Section conditional routing；
- Fake Agent typed payload；
- Agent 不直接改 Shared State；
- Section parallel fan-in 不回归。

### 第 5 轮：真实 RAG / ModelGateway Adapter

退出条件：

- Research 只经授权 Tool Adapter 调用现有 EvidenceService/RAGService；
- Writer/Verifier 只经 Reasoner Adapter 调用现有 ModelGateway；
- Agent 不创建 Milvus/模型客户端；
- Trace 能区分 Agent invocation、RAG call、model call；
- configuration/timeout/cancel/evidence-insufficient 分类不回归。

### 第 6 轮：Paragraph / Claim Pipeline

退出条件：

- Paragraph 是最小生成与持久化原子；
- stable paragraph_id；
- Claim 增量抽取；
- Excel/权威事实 precheck；
- blocking 错误停止后续段落；
- crash/resume 不重复已提交 Paragraph；
- Section Result 仍兼容现有 assembler/gate。

### 第 7 轮：文档级闭环

退出条件：

- document_verify；
- ConflictRepairPlan 串行执行；
- incremental reverify；
- 增强现有 assembler；
- assembly validator；
- final artifact review；
- GenerationReport；
- VersionPolicy；
- Hard Gate 保持最终交付 authority；
- partial_success/failed 按业务契约产生实际文档或 rejected artifact。

### 第 8 轮：恢复、预算和旁路退休

退出条件：

- Attempt/snapshot stale result 被生产主链拒绝；
- Human Review 最小恢复；
- running invocation 条件恢复；
- budget draining 停止新调用但完成在途原子操作；
- Standalone success/research-more/rewrite/paragraph-repair/replan/partial/failed/budget/human/crash 场景都有 Mainline E2E；
- 生产代码不再依赖 Standalone Runtime；
- 删除或归档重复 Router/Checkpoint/TaskStatus/Orchestrator；
- 必要 fake reasoner/fixtures 保留。

---

## 15. 正式验收路径

### 15.1 环境约束

正式 Python：

```text
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe
```

所有 pytest basetemp、E2E 输出、SQLite 文件必须使用唯一时间戳目录：

```text
D:\Temp\rag_agent_<round>_<timestamp>\...
```

禁止：

- 使用 C 盘 TEMP；
- 复用 pytest basetemp；
- 在仓库目录落临时 SQLite/E2E 输出；
- 以 IDE 默认环境替代正式解释器。

### 15.2 每轮最低回归

1. `git diff --check`；
2. 目标测试文件；
3. 顶层图和 Section 图测试；
4. `test_phase2_runtime_safety_closure.py`；
5. `test_architecture_hardening.py`；
6. `test_day2_architecture_closure.py`；
7. `test_p0_real_rag_runtime.py`；
8. `test_workflow_budget_exhaustion_semantics.py`；
9. 当前轮所有新增 Mainline 测试；
10. `git status --short`。

### 15.3 里程碑回归

第 3、4、5、6、7、8 轮结束时至少执行：

```text
pytest --collect-only
全部非 real_provider tests
Fake Mainline E2E
真实 RAG smoke
```

第 5、7、8 轮还必须执行：

```text
真实 8 章节 Mainline E2E
```

### 15.4 E2E 判定规则

必须区分：

- 技术运行失败；
- 环境失败；
- 测试收集失败；
- 业务 Hard Gate 拒绝；
- evidence_insufficient 的安全阻断。

以下结果不是技术失败：

```text
DOCUMENT_HARD_GATE_FAILED
failure_category=business_quality
execution_integrity=passed
```

真实八章节验收至少检查：

- section_count；
- failed_section_count；
- RAG call count；
- model call count；
- Trace event count；
- failed_node；
- failure_category；
- generated/rejected/deliverable 生命周期；
- checkpoint；
- model_usage；
- task_state；
- raw_interactions；
- sft_candidates lifecycle；
- eval_samples。

### 15.5 必须保留的两条业务样例

1. `evidence_insufficient` 被安全拒绝或生成 blocked section；
2. 低引用约束下仍能生成 deliverable，且 citation marker/binding/lineage/grounding 全部通过。

---

## 16. Commit、回滚与变更纪律

1. 每一轮单独提交；
2. Schema、节点接线、真实 Adapter、Paragraph、文档闭环、退休不混在同一提交；
3. 每个提交有明确的 Mainline 测试证据；
4. 不从 Standalone 大批复制代码后再修；
5. 先抽取纯业务算法，再接生产依赖；
6. 每次扩大修改范围前确认工作区干净；
7. checkpoint schema/version 变化必须显式记录；
8. 节点 ID/namespace 变化必须说明旧运行能否恢复；
9. 失败时回退当前轮，不回退已验证主链基线；
10. 不通过 `reset --hard` 或删除数据掩盖迁移失败。

---

## 17. 风险与待决事项

### 17.1 当前 active index 无 tenant/kb 字段

当前真实 RAG E2E 使用 legacy-unscoped 兼容路径。正式多租户生产验收前必须：

- 用当前 Builder 重建 tenant/kb scoped index；
- 激活新版 index；
- 验证 trusted access scope；
- 验证跨 tenant/kb 不泄漏。

这不是 Round 2–4 的阻塞，但属于 Round 5/生产发布门槛。

### 17.2 单文件测试收集循环导入

`test_p1_security_durability_identity_closure.py` 单独收集存在 `document_gate -> scheme_writer.__init__ -> use_case -> stage_service -> document_gate` 顺序依赖；全量收集可以完成。

这不是本 ADR 的代码范围，但会影响 CI 分片和定向测试，应单独修复。

### 17.3 Paragraph Artifact 后端

旁路 `FileArtifactStore` 具有 immutable/hash/identity 校验价值，但生产当前以 RunWorkspace、checkpoint、capture 和显式 output artifact 为主。

第 6 轮开始前必须选择：

- 把 FileArtifactStore 改为 production-owned Artifact port；或
- 在现有 RunWorkspace/side-effect infrastructure 上实现等价不可变 Artifact Adapter。

禁止两套 Artifact authority 并存。

### 17.4 Plan node 命名

需求文本曾写：

```text
plan_scheme_generation -> PlannerAgent
```

当前真实节点是 `plan_document`。本 ADR 决定初期保持 `plan_document`，以保护 checkpoint 和 Trace 契约。

### 17.5 SectionStatus 与 ExecutionStatus

旁路细粒度 SectionStatus 有业务价值，但不能替代全局 `ExecutionStatus`。

建议：

- Section lifecycle 使用专用强类型状态；
- Agent invocation 使用 local status；
- Workflow/Task 继续使用 `ExecutionStatus`；
- 只在明确边界做单向映射。

### 17.6 Partial Success 文档语义

当前生产 Hard Gate 通过时形成 deliverable，失败时形成 rejected artifact；旁路要求 `failed` 也导出模板骨架和失败占位。

第 7 轮需明确：

- failed 文档是否属于 rejected artifact；
- partial_success 的哪些 Paragraph 可进入 deliverable；
- 用户可见 Generation Report 如何与内部 E2E report 分离。

不得通过把 rejected artifact 政名为 deliverable 绕过 Hard Gate。

---

## 18. 被本 ADR 替代的旧决策

以下旧建议不再适用于生产迁移：

```text
在现有 Supervisor 创建输入快照后，
使用 SupervisorStateAdapter 建立 V1 MultiAgentSharedState，
再运行 V1 Router/StateGateway/Checkpoint。
```

替代方案为：

```text
在现有 GraphState 和现有文档/Section Payload 中
增加强类型业务投影，
由现有 LangGraph 节点、写契约和 checkpoint 承载。
```

以下文档继续作为历史和组件说明保留，但不能作为当前迁移决策来源：

- `docs/AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_20260729.md`
- `docs/AGENT_RAG_MULTI_AGENT_V1_STANDALONE_RUNTIME_20260729.md`

---

## 19. 第 1 轮退出清单

- [x] 确认当前主链入口与组合根；
- [x] 确认生产不调用 Standalone Runtime；
- [x] 确认生产不调用 `SchemeWriterAgent.run()`；
- [x] 确认唯一 Supervisor；
- [x] 确认唯一顶层生产图；
- [x] 确认唯一 Section 生产图；
- [x] 确认两个 Shared State family 当前隔离；
- [x] 决定不引入新的 Shared State；
- [x] 完成主链/旁路组件映射；
- [x] 完成保留/合并/替换/退休决策；
- [x] 完成状态字段投影；
- [x] 完成节点映射；
- [x] 完成分轮文件白名单；
- [x] 完成默认保护文件清单；
- [x] 完成 Mainline 验收路径；
- [x] 标记旧决策被替代；
- [x] 本轮未修改生产代码。

第 2 轮开始条件：本 ADR 经人工审阅并确认，没有新增第二套状态/控制面需求。

---

## 20. 最终结论

Standalone V1 已经证明四 Agent 业务分工、Paragraph/Claim、文档闭环和恢复语义具有可实现性，但它不是可直接上线的第二条主链。

正确迁移路线不是：

```text
把旁路整体接进 Supervisor
```

而是：

```text
保留现有生产控制面
+ 迁移四个无状态 Agent
+ 把细粒度业务状态投影进现有 Payload
+ 把纯业务算法合并进现有 Section/Document 服务
+ 用现有 LangGraph、Checkpoint、Error、Evidence、Budget、Trace 和 Artifact 生命周期闭合
```

该决策保证后续第 2–8 轮每一步都有单一事实源、明确修改边界和可重复的 Mainline 验收路径。
