# Agent-RAG Multi-Agent V1 独立运行时说明

日期：2026-07-29
性质：Standalone Multi-Agent vertical E2E with injected test adapters

## 1. 定位与边界

本实现将现有 `agent_rag_v1` 组件组装成独立、可执行、可恢复的纵向闭环。
它不接入现有 `SupervisorAgent`、生产 LangGraph、`SchemeWriter` 主链或
`scripts/run_demo.py`，也不代表生产端到端已经完成。

独立运行时只导出 Markdown 和 JSON Generation Report。Markdown 是本轮可验收
Artifact，不是生产 DOCX。

## 2. 已完成

- `RuntimeFactory`：集中创建 Artifact、Event、Tool、Checkpoint、Budget、
  StateGateway、Router、ResumeRouter、DAG、四个 Agent、Claim/Precheck 和
  Document Closure 全部组件，并创建 `WorkflowOrchestrator` 会话。
- `MultiAgentApplicationService`：公开 `run(project_input)` 入口，初始化不可变
  Fact、Template、Inventory、Task Context 与 Source Snapshot。
- `WorkflowOrchestrator`：实际调度 Planner、Research、Writer、Verifier；Agent
  不互相调用。
- `StateGateway` 连续提交：每个 AgentResult 的 Patch 经过权限、Snapshot、
  Attempt、Domain Version 和状态迁移校验，并生成 Domain Event 和 Checkpoint。
- `WorkflowRouter` 循环：覆盖 `accept`、`partial_accept`、`rewrite`、
  `research_more`、`replan`、`human_review` 和 `reject`。
- `SectionDagScheduler`：按模板注册并选择 `compute`、`storage` Section 批次。
- `knowledge.retrieve`：Research Agent 只通过 ToolGateway 访问统一业务工具；
  工具 Handler 再调用注入的 `RetrievalServicePort`。
- Paragraph 闭环：逐段不可变落盘、Paragraph Event、Claim 增量更新和 Excel
  硬事实预检查。
- Re-plan：生成新 PlanRevision 和 Section Attempt，保留旧 Attempt 记录，并用
  StateGateway 实际拒绝旧 Attempt 的延迟 Patch。
- Budget draining：预算耗尽后停止启动新 Agent 调用，冻结已经验收的内容并降级
  导出。
- Document Closure：Section 聚合、Document Verify、Assembly、Assembly
  Validation、Final Artifact Review、Generation Report 和 Version Publish。
- `success`、`partial_success`、`failed` 均生成实际 Markdown 和 JSON Artifact。
- Crash Recovery：Writer 已提交 `p001`、`p002` 后从 Checkpoint 和 Paragraph
  Event 恢复，只继续生成 `p003`。
- 独立入口：`scripts/run_agent_rag_v1_standalone.py`。
- 测试层级：`unit`、`contract`、`component_integration`、
  `standalone_e2e`、`recovery_e2e`。

## 3. 运行链

```text
Standalone Runner
→ MultiAgentApplicationService
→ RuntimeFactory / RuntimeSession
→ Input Snapshot 初始化
→ Planner Agent
→ Section DAG
→ Research Agent
→ knowledge.retrieve
→ RetrievalServicePort
→ Writer Agent / Paragraph Artifact / Precheck
→ Incremental Claim Pipeline
→ Verifier Agent
→ WorkflowRouter 循环
→ Section 聚合
→ Document Verify
→ Assembly / Assembly Validation
→ Final Artifact Review
→ Generation Report
→ Version Publish
→ Markdown / JSON Export
```

所有外围实现通过 Port 注入：

```text
AgentReasonerPort
RetrievalServicePort
FactSnapshotProviderPort
TemplateProviderPort
DocumentExporterPort
```

Standalone 默认注入：

```text
ScenarioAgentReasoner
ScenarioRetrievalService
FixtureFactSnapshotProvider
FixtureTemplateProvider
MarkdownDocumentExporter
```

Business Agent 没有直接依赖 Self-RAG、C-RAG、Adaptive-RAG、Hybrid Retriever、
Reranker 或 Milvus。

## 4. 独立运行

```powershell
.venv\Scripts\python.exe scripts\run_agent_rag_v1_standalone.py `
  --scenario success `
  --output-dir .tmp\agent-rag-v1-demo
```

支持：

```text
success
research-more
rewrite
paragraph-repair
replan
partial-success
failed
budget-exhausted
human-review
crash-recovery
```

CLI 输出 Task Status、Document Version、Section Results、Document Artifact
Path、Generation Report Path、Checkpoint Path 和 Event Log Path。

## 5. 测试入口与分层

纵向测试只调用 `MultiAgentApplicationService.run()`；测试代码不手工串联四个
Agent。

```powershell
.venv\Scripts\python.exe -m pytest `
  backend/tests/test_agent_rag_v1_runtime_contracts.py `
  backend/tests/test_agent_rag_v1_standalone_e2e.py `
  backend/tests/test_agent_rag_v1_recovery_e2e.py `
  -q -p no:cacheprovider
```

已有 70 个组件底座测试继续作为回归层：

```powershell
.venv\Scripts\python.exe -m pytest `
  backend/tests/test_agent_rag_v1_contracts.py `
  backend/tests/test_agent_rag_v1_infrastructure.py `
  backend/tests/test_agent_rag_v1_workflow.py `
  backend/tests/test_agent_rag_v1_agents.py `
  backend/tests/test_agent_rag_v1_writer_claims.py `
  backend/tests/test_agent_rag_v1_document_closure.py `
  backend/tests/test_agent_rag_v1_recovery.py `
  -q -p no:cacheprovider
```

## 6. 尚未完成

- 现有 `SupervisorAgent` 接入。
- 现有生产 LangGraph 接入。
- 真实 ModelGateway Adapter。
- 真实 RAG Adapter 和 Retrieval Runtime。
- 真实 Excel Adapter。
- 生产 DOCX 导出。
- 真实模型、真实知识库、真实材料的生产端到端验证。

后续生产接入必须以 Adapter 方式替换 Standalone Fake，并单独实施、单独验收；
不得把本轮 injected-adapter E2E 直接表述成生产 E2E。
