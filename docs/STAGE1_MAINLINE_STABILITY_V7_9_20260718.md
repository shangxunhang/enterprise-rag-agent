# Stage 1 主链稳定性加固 v7.9.0（2026-07-18）

## 1. 本阶段解决的问题

本阶段只处理主链运行时契约，不把语料不足、检索内容不匹配、引用缺失和小模型生成质量计入技术主链故障。

修复范围：

1. Demo 默认输入不再注入“企业级 RAG-Agent 系统建设方案”。
2. Workflow 的 `max_retries`、`timeout_seconds`、`on_success`、`on_failure` 从声明字段变为真实运行行为。
3. 节点只能修改 `write_paths` 声明的 GraphState 路径。
4. 失败节点默认不提交业务状态；指定业务门禁错误可保留明确声明的部分结果。
5. E2E 报告拆分技术执行完整性与业务质量结果。

## 2. 位于主链的位置

```text
ProjectInputFactory
  → SupervisorService
  → NativeWorkflowEngine
  → LegacyAgentNodeAdapter（隔离状态副本）
  → GraphStateDiffer（写契约校验）
  → GraphStateApplier（原子提交）
  → Hard Gate / E2E Report
```

## 3. 核心对象

- `WorkflowStepSchema.write_paths`：节点允许修改的物理状态路径。
- `commit_policy`：失败节点状态提交策略。
- `failure_write_paths`：业务失败时允许保留的部分结果。
- `failure_commit_error_codes`：允许部分提交的错误代码白名单。
- `GraphStateWriteContract`：逻辑输出和物理写路径的统一校验器。
- `GraphStateDeltaSchema.declared_write_paths`：本次 Delta 的实际写边界。

## 4. 输入输出

### 输入

```text
WorkflowDefinitionSchema
GraphStateSchema
WorkflowStepSchema
AgentResultSchema
```

### 输出

```text
WorkflowEngineResultSchema
GraphNodeOutputSchema
GraphStateDeltaSchema
Trace v2 attempt/commit metadata
E2E execution_integrity/business_quality
```

## 5. 失败影响

- 越权写状态：返回 `STATE_WRITE_CONTRACT_VIOLATION`，越权内容不提交。
- 节点超时：返回 `WORKFLOW_NODE_TIMEOUT`，迟到结果不能写入主状态。
- 可重试失败：失败尝试不提交，直到成功或重试耗尽。
- 技术失败：默认丢弃节点业务 Delta，只保留结构化错误。
- `DOCUMENT_HARD_GATE_FAILED`：保留方案正文、证据、模型调用和 `final_result`，但任务业务状态仍为失败。

## 6. 验收结果

```text
pytest：260 passed
run_mainline_closure_acceptance.py：通过
Fake E2E：通过
政务云请求标题：政务云建设方案
业务 Hard Gate 失败：execution_integrity=passed，business_quality=failed
```

## 7. 当前仍不在本阶段处理

- 检索语料覆盖率和引用命中率。
- 1.5B 模型章节生成质量。
- Generation Checker、Repair Strategy、Evidence Grader 的 noop 实现。
- SchemeWriter 粗粒度节点拆分。
- LangChain / LangGraph 迁移。
