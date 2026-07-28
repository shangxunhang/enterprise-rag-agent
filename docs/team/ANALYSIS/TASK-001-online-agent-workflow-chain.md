# TASK-001 Online Agent / Workflow Chain — As-Is 调查结果

## 0. 结论摘要

**调查结论：当前真实生产主链不会调用 `SchemeWriterAgent.run()`，也不会调用 `SchemeGenerationUseCase.run()`。**

当前组合根会执行以下构造链：

```text
SupervisorFactory.build
  -> SchemeWriterAgent.__init__
    -> SchemeGenerationUseCase.__init__
      -> SchemeGenerationStageService.__init__
    -> scheme_writer.stage_service = scheme_writer.use_case.stage_service
  -> EnterpriseDocumentWorkflowEngine(stage_service=scheme_writer.stage_service)
```

随后真实执行链为：

```text
Query ingress
  -> MainlineApplicationService.run / stream_run
  -> SupervisorAgent.run / stream
  -> EnterpriseDocumentWorkflowEngine.execute / stream_events
  -> explicit LangGraph business nodes
  -> ProjectInputNormalizationUseCase.execute
  -> SchemeGenerationStageService.prepare_document_plan
  -> SchemeGenerationStageService.retrieve_document_evidence
  -> ...
```

因此，`SchemeGenerationUseCase` 在当前主链中的事实状态必须拆分判断：

- `SchemeGenerationUseCase.__init__`：**active（仅构造阶段）**；
- `SchemeGenerationUseCase.run`：**legacy，当前生产主链不可达**；
- `SchemeWriterAgent.__init__`：**active（作为服务组合壳）**；
- `SchemeWriterAgent.run`：**legacy，当前生产主链不可达**。

现有运行测试在把上述两个 `run` 方法替换成“只要调用就抛出 `AssertionError`”后，生产主链仍成功完成，构成独立运行证据。

这意味着 TASK-001 原任务文本要求的“`SchemeGenerationUseCase` 实际调用完成并返回”在当前基线中不存在。不能为满足任务格式而虚构该调用边。

---

## 1. 调查范围与停止边界

### 1.1 已调查

- 当前仓库内可执行 Query 接收入口；
- Query / `ProjectInput` / `TaskSchema` / `GraphStateSchema` 的转换；
- `MainlineApplicationService`、`SupervisorAgent`、路由器、Workflow 定义、Workflow Engine、Agent 候选实现之间的真实关系；
- `SchemeGenerationUseCase` 的定义、构造、调用点和测试引用；
- 同步、流式、暂停、恢复、拒绝和取消路径；
- 顶层业务图的路由、失败、重试配置和状态变化；
- 当前生产组合根中 active / inactive / legacy / experimental / unknown 的判定；
- 现有测试提供的运行证据。

### 1.2 未展开

按照 TASK-001 非目标，没有继续展开：

- `SchemeEvidenceService.retrieve` 内部在线 RAG 实现；
- Dense、BM25、Rerank、RAG-Fusion、HyDE、CRAG、Self-RAG 等算法；
- Evidence 内部 Contract 和所有权；
- 章节生成、Prompt、引用和质量门禁内部算法。

本文只定位到当前真实 RAG 触发边界，用于说明为什么 TASK-002 不能从一个不存在的 `SchemeGenerationUseCase.run` 调用点开始。

---

## 2. 调查基线

| 项目 | 结果 | 证据 |
|---|---|---|
| Workspace | `D:\MyCode\rag-agent` | DevSpace checkout workspace |
| Branch | `main` | `git branch --show-current` |
| Commit | `07dda2e80645cac67f8b5f5e30002d21c2ab01eb` | `git rev-parse HEAD` |
| HEAD 摘要 | `feat: complete LangGraph workflow takeover` | `git log -5 --oneline --decorate` |
| Exact tag | 无 | `git describe --tags --exact-match` 返回 `NO_EXACT_TAG` |
| 最近描述 | `langgraph-phase1-serializable-state-v1-1-g07dda2e-dirty` | `git describe --tags --always --dirty` |
| 工作区 | **dirty** | `git status --short` |
| 调查前状态计数 | modified 42、added 6、deleted 1、untracked 38 | 调查前状态统计 |
| Python 约束 | `>=3.10,<3.11` | `pyproject.toml` |
| 测试入口 | `backend/tests`，pythonpath=`backend`,`scripts` | `pytest.ini`、`pyproject.toml` |
| 完整测试基线 | **unknown**；本轮未运行全量测试 | 本轮仅运行 TASK-001 相关定向测试 |
| 定向运行证据 | 5 tests passed | 见第 11 节 |

### 2.1 基线限制

当前代码包含大量未提交修改和未跟踪文件。因此本文结论对应的是：

```text
HEAD 07dda2e + 调查时工作区未提交内容
```

而不是仅靠 commit `07dda2e` 可完全复现的纯净版本。任何后续核验都必须同时确认工作区内容是否发生变化。

---

## 3. 已读取的项目事实源

### 3.1 团队文档

- `docs/team/PROJECT.md`
- `docs/team/ORGANIZATION.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- `docs/team/STATUS.md`
- `docs/team/TASKS/TASK-001-online-agent-workflow-chain.md`

### 3.2 主要代码证据

- `scripts/run_pipeline.py`
- `scripts/run_demo.py`
- `scripts/workflow_control.py`
- `scripts/mainline_runtime.py`
- `backend/application/mainline_service.py`
- `backend/application/project_input_factory.py`
- `backend/application/task_factory.py`
- `backend/bootstrap/supervisor_factory.py`
- `backend/agent/supervisor_agent.py`
- `backend/agent/services/workflow_router.py`
- `backend/agent/services/task_lifecycle.py`
- `backend/task/task_manager.py`
- `backend/apps/enterprise_document/workflows/scheme_generation.py`
- `backend/apps/enterprise_document/workflows/top_level_business_graph.py`
- `backend/apps/enterprise_document/agents/project_input_normalizer_agent.py`
- `backend/apps/enterprise_document/agents/scheme_writer_agent.py`
- `backend/apps/enterprise_document/services/project_input_normalization/use_case.py`
- `backend/apps/enterprise_document/services/scheme_writer/use_case.py`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`
- `backend/agent/runtime/langgraph_workflow_engine.py`

### 3.3 主要测试证据

- `backend/tests/test_phase2_top_level_business_graph.py`
- `backend/tests/test_phase4_durable_resume_hitl.py`
- `backend/tests/test_phase5_langchain_takeover_and_legacy_retirement.py`

---

## 4. `SchemeGenerationUseCase` 全仓候选登记

全仓搜索结果中，能够影响运行判断的候选如下。

| 候选 | 类型 | 当前判定 | 证据与理由 |
|---|---|---|---|
| `backend/apps/enterprise_document/services/scheme_writer/use_case.py::SchemeGenerationUseCase` | definition | legacy facade | 模块 docstring 为 `Legacy scheme-generation facade`；类 docstring 为 `Compatibility entry point; production orchestration is owned by LangGraph` |
| `SchemeWriterAgent.__init__ -> SchemeGenerationUseCase(...)` | construction | active construction | `backend/apps/enterprise_document/agents/scheme_writer_agent.py` 真实构造对象 |
| `SchemeWriterAgent.run -> self.use_case.run(shared_state)` | call-site | legacy / inactive in production | 仓库内唯一 `self.use_case.run` 调用点；当前生产组合根不注册、不调用 `SchemeWriterAgent.run` |
| `SchemeGenerationUseCase.run -> stage_service.run_compat` | compatibility call | legacy | 方法明确调用 `run_compat`；生产图直接调用 stage methods |
| 测试中导入 `SchemeGenerationUseCase` | test reference | experimental | 部分测试验证兼容 seam；Phase 2/5 测试则显式证明 production 不调用 `run` |
| 文档中的 `SchemeGenerationUseCase.run` 调用图 | documentation | 非运行证据 | TASK/POLICIES 明确禁止把文档或命名当作真实调用事实 |

### 4.1 唯一仓库内 `run` 调用点

```python
# backend/apps/enterprise_document/agents/scheme_writer_agent.py
class SchemeWriterAgent(BaseAgent):
    def run(self, shared_state: SharedStateSchema) -> AgentResultSchema:
        return self.use_case.run(shared_state)
```

没有发现第二个业务代码调用点。

### 4.2 为什么该调用点不是当前生产主链

`backend/bootstrap/supervisor_factory.py::SupervisorFactory.build`：

1. 创建 `AgentRegistry()`；
2. 只注册 `ProjectInputNormalizerAgent()`；
3. 构造 `scheme_writer = SchemeWriterAgent(...)`；
4. **不注册 `scheme_writer`**；
5. 把 `scheme_writer.stage_service` 传给 `EnterpriseDocumentWorkflowEngine`；
6. 把该显式 engine 注入 `SupervisorAgent`。

所以 `SchemeWriterAgent.run -> SchemeGenerationUseCase.run` 没有可达调用者。

---

## 5. Query 真实入口判定

### 5.1 当前仓库未发现 HTTP API 入口

对 `backend` 和 `scripts` 全仓搜索以下模式无结果：

- `FastAPI(`
- `APIRouter(`
- `@app.get/post/put/delete`
- `@router.get/post/put/delete`

结论：**当前仓库内没有被注册证明的 HTTP Query 入口。** 外部仓库或未纳入当前项目的部署层是否存在 API，标记为 unknown。

### 5.2 已确认入口

| 入口 | 文件 / 函数 | 输入 | 下游 | 分类 |
|---|---|---|---|---|
| 结构化生产 CLI | `scripts/run_pipeline.py::main` | `--project-input-file`、`--retrieval-access-scope-file`；Query 来自 `ProjectInput.user_query` | `mainline_runtime.run_mainline` | active |
| 交互 / Demo CLI | `scripts/run_demo.py::main` | `--user-input` 或控制台输入；可选 `--project-input-file` | `run_demo -> run_mainline` | experimental adapter；下游主链 active |
| 初始流式 CLI | `scripts/workflow_control.py::main`, command=`stream` | `--user-input`、`--run-id`、`--output-root` | `MainlineApplicationService.stream_run` | active |
| 应用层同步边界 | `MainlineApplicationService.run` | `user_input` + 可选严格 `project_input` + 可选授权 scope | `SupervisorAgent.run` | active |
| 应用层流式边界 | `MainlineApplicationService.stream_run` | 同步边界同类输入 + `runnable_config` | `SupervisorAgent.stream` | active |

### 5.3 不是初始 Query 入口的控制命令

`scripts/workflow_control.py` 的以下命令不接收新 Query，而是恢复已有 `task_id/run_id`：

- `inspect`
- `resume`
- `cancel`
- `stream-resume`
- `stream-cancel`

它们属于 active continuation/control path，不属于初始 Query ingress。

---

## 6. 当前真实调用链

## 6.1 生产同步入口

```text
scripts/run_pipeline.py::main
  输入：ProjectInput JSON + RetrievalAccessScope JSON
  user_query = project_input["user_query"]
  -> scripts/mainline_runtime.py::run_mainline
  -> MainlineApplicationService.run
```

## 6.2 Demo 同步入口

```text
scripts/run_demo.py::main
  -> _resolve_console_question
  -> run_demo
  -> scripts/mainline_runtime.py::run_mainline
  -> MainlineApplicationService.run
```

## 6.3 流式入口

```text
scripts/workflow_control.py::main(command="stream")
  -> MainlineApplicationService.stream_run
  -> SupervisorAgent.stream
  -> EnterpriseDocumentWorkflowEngine.stream_events
```

## 6.4 同步公共主链

```text
MainlineApplicationService.run
  -> validate ingress / IDs / tenant consistency
  -> RunWorkspace(...)
  -> TaskFactory.build
       -> ProjectInputFactory.build
       -> TaskSchema(...)
  -> JsonlTaskManager.create_task          [PENDING]
  -> build_scheme_generation_workflow      [workflow identity/config]
  -> RuntimeOptions.resolve
  -> SupervisorFactory.build
       -> AgentRegistry()
       -> register(ProjectInputNormalizerAgent())
       -> SchemeWriterAgent.__init__
            -> SchemeGenerationUseCase.__init__        [active construction]
                 -> SchemeGenerationStageService.__init__
            -> self.stage_service = self.use_case.stage_service
       -> EnterpriseDocumentWorkflowEngine(
            stage_service=scheme_writer.stage_service
          )
       -> SupervisorAgent(workflow_engine=EnterpriseDocumentWorkflowEngine)
  -> SupervisorAgent.run(task)
       -> WorkflowRouter.route                          [single_workflow_direct]
       -> TaskLifecycleService.mark_running             [RUNNING]
       -> ContextBundleFactory.build
       -> GraphStateSchema(...)
       -> EnterpriseDocumentWorkflowEngine.execute
            -> build_graph(...).invoke(...)
            -> normalize_project_input
                 -> ProjectInputNormalizationUseCase.execute
            -> plan_document
                 -> SchemeGenerationStageService.prepare_document_plan
            -> retrieve_document_evidence
                 -> SchemeGenerationStageService.retrieve_document_evidence
                 -> SchemeEvidenceService.retrieve      [TASK-002 应调查的实际 RAG 边界]
            -> 后续节点（本任务不展开内部实现）
       -> TaskLifecycleService.mark_terminal
  -> supervisor.close()
  -> MainlineApplicationService 汇总 AgentResult / TaskState / paths
  -> caller
```

## 6.5 被绕过的兼容链

```text
SchemeWriterAgent.run
  -> SchemeGenerationUseCase.run
  -> SchemeGenerationStageService.run_compat
```

当前生产组合根中，该链没有可达调用者。

---

## 7. 逐边证据矩阵

下表中的 “direct evidence” 均来自调用语句或注册/构造语句；不是依据名称推测。

| # | 上游调用者 | 下游调用者 | 输入 | 输出 / 状态变化 | 异常行为 | 证据类型 | 状态 |
|---|---|---|---|---|---|---|---|
| E01 | `scripts/run_pipeline.py::main` | `mainline_runtime.run_mainline` | `user_query`、完整 `project_input`、可信 `RetrievalAccessScopeSchema` | 主链 summary；非 success/partial 时退出码 1 | 文件不存在、顶层 JSON 非 object、缺少 `user_query` 直接抛异常 | call-site | active |
| E02 | `scripts/run_demo.py::main` | `run_demo` | 控制台/CLI Query、可选 ProjectInput、fake/real runtime flags | summary + E2E artifacts/console output | 单实例锁冲突退出 2；strict 失败退出 1 | call-site/runtime adapter | experimental |
| E03 | `scripts/run_demo.py::run_demo` | `mainline_runtime.run_mainline` | 原样传递 Query/IDs/config | 在 summary 增加 experiment seed 字段 | 下游异常传播 | call-site | experimental -> active mainline |
| E04 | `scripts/workflow_control.py::main(stream)` | `MainlineApplicationService.stream_run` | `--user-input`、run/task IDs、output root | 逐行输出 `WorkflowStreamEventSchema` | 流生成异常传播；finally 关闭 stream/supervisor | call-site | active |
| E05 | `mainline_runtime.run_mainline` | `_mainline_service.run` | `user_input` 等运行参数 | `Dict[str, Any]` summary | 下游异常传播 | call-site | active compatibility facade |
| E06 | `MainlineApplicationService.run` | `ProjectInputSchema` / demo seed validation | raw `project_input`、`allow_demo_defaults` | validated ingress、`input_task_id` | 类型不合法、task ID/tenant 冲突抛 `TypeError/ValueError` | call-site | active |
| E07 | `MainlineApplicationService.run` | `TaskFactory.build` | task/run IDs、Query、时间、ProjectInput | `TaskSchema` | strict production ProjectInput 与 Query 不一致抛 `ValueError` | call-site | active |
| E08 | `TaskFactory.build` | `ProjectInputFactory.build` | Query + raw ProjectInput | canonical `ProjectInputSchema` | production strict validation；demo 才注入默认 task type/sections/title | call-site | active |
| E09 | `TaskFactory.build` | `TaskSchema(...)` | canonical ProjectInput | `TaskSchema.user_input=normalized.user_query`；派生 file/doc/kb IDs | Pydantic 校验异常传播 | construction | active |
| E10 | `MainlineApplicationService.run` | `JsonlTaskManager.create_task` | `TaskSchema` | append-only task state，初始 `PENDING/current_step=created` | 重复 task ID 且 immutable request hash 不同抛 `ValueError` | call-site/state | active |
| E11 | `MainlineApplicationService.run` | `build_scheme_generation_workflow` | `created_at` | workflow ID=`scheme_generation_v2`、version=`v2.0`、task type=`scheme_generation` | schema 构造异常传播 | call-site | active identity；step topology inactive for current engine |
| E12 | `MainlineApplicationService.run` | `RuntimeOptions.resolve` | settings/project root | RAG/model/quality/concurrency options | 配置错误传播 | call-site | active |
| E13 | `MainlineApplicationService.run` | `SupervisorFactory.build` | workflow、workspace dirs、task manager、settings/options/scope | `SupervisorAgent` | real RAG + production + 无可信 scope 抛 `ValueError` | call-site | active |
| E14 | `SupervisorFactory.build` | `AgentRegistry.register(ProjectInputNormalizerAgent())` | normalizer agent instance | registry 中存在 normalizer | 注册错误传播 | registration | active registration；execution inactive in production |
| E15 | `SupervisorFactory.build` | `SchemeWriterAgent.__init__` | RAG/model/capture/prompt/quality/scope/options | 服务组合对象 `scheme_writer` | 子服务配置异常传播 | construction | active construction |
| E16 | `SchemeWriterAgent.__init__` | `SchemeGenerationUseCase.__init__` | input/evidence/section/document/capture/runtime services | `self.use_case` | StageService 构造异常传播 | construction | active construction |
| E17 | `SchemeGenerationUseCase.__init__` | `SchemeGenerationStageService.__init__` | 同一组服务依赖 | `self.stage_service` | concurrency 不在 1..32 抛 `ValueError` | construction | active construction |
| E18 | `SchemeWriterAgent.__init__` | `self.stage_service = self.use_case.stage_service` | UseCase 内持有的 stage service | 暴露同一实例 | 无单独异常 | reference assignment | active |
| E19 | `SupervisorFactory.build` | `EnterpriseDocumentWorkflowEngine(...)` | `scheme_writer.stage_service`、trace、side-effect store、checkpointer | explicit business workflow engine | 构造错误传播 | construction | active |
| E20 | `SupervisorFactory.build` | `SupervisorAgent(...)` | workflows map + custom engine | Supervisor；custom engine 取代默认 `LangGraphWorkflowEngine` | 构造错误传播 | construction/injection | active |
| E21 | `MainlineApplicationService.run` | `SupervisorAgent.run(task)` | `TaskSchema` | `AgentResultSchema` | Supervisor 内部捕获大部分执行异常并返回 failed/retryable result | call-site/runtime | active |
| E22 | `SupervisorAgent.run` | `WorkflowRouter.route` | Task | `RoutingDecision` | 当前仅一个匹配 workflow，直接返回 `single_workflow_direct`；不调用 LLM | call-site/runtime | active |
| E23 | `SupervisorAgent.run` | `TaskLifecycleService.mark_running` | Task | task `PENDING -> RUNNING` | 非法状态迁移抛 `ValueError`，由 Supervisor except 转失败结果 | call-site/state | active |
| E24 | `SupervisorAgent.run` | `GraphStateSchema(...)` | Task、workflow、context bundle、routing metadata | 初始 GraphState | schema 异常由 Supervisor 捕获 | construction/state | active |
| E25 | `SupervisorAgent.run` | `EnterpriseDocumentWorkflowEngine.execute` | workflow + GraphState | `WorkflowEngineResultSchema`；GraphState 被投影为终态/暂停态 | engine 异常由 Supervisor 捕获并映射为 `SUPERVISOR_EXECUTION_FAILED` | call-site/runtime | active |
| E26 | `EnterpriseDocumentWorkflowEngine.execute` | `build_graph(workflow).invoke` | serialized runtime + business payload | terminal/paused runtime | LangGraph/节点异常进入节点失败或向上抛出 | call-site/runtime | active |
| E27 | explicit node `normalize_project_input` | `_run_stage(..., self._normalize_project_input)` | GraphState + payload | normalization state delta | 节点错误分类并路由 technical failure | graph registration/call | active |
| E28 | `_normalize_project_input` | `ProjectInputNormalizationUseCase.execute` | GraphState | project input、table analysis、structured facts | reader/validation 异常进入 `_run_stage` 失败处理 | call-site | active |
| E29 | explicit node `plan_document` | `SchemeGenerationStageService.prepare_document_plan` | normalized GraphState + payload | document plan、scheme input、required sections | 缺少/空字段抛异常，进入 technical failure | graph registration/call | active |
| E30 | explicit node `retrieve_document_evidence` | `SchemeGenerationStageService.retrieve_document_evidence` | project input + document plan payload | evidence fields写入 payload / GraphState | RAG failure封装为 `SchemeGenerationStageFailure`，进入 technical failure | graph registration/call | active |
| E31 | `SchemeGenerationStageService.retrieve_document_evidence` | `SchemeEvidenceService.retrieve` | `shared_state`、canonical `project_input` | RAG result；后续形成 context/chunks/citations | RAG 无结果或 success=false 时结构化失败 | call-site | active；TASK-002 起点候选 |
| E32 | `SchemeWriterAgent.run` | `SchemeGenerationUseCase.run` | `SharedStateSchema` | compatibility `AgentResultSchema` | compatibility method内部捕获 stage exception | sole call-site | legacy/inactive production |
| E33 | `SchemeGenerationUseCase.run` | `SchemeGenerationStageService.run_compat` | `SharedStateSchema` | compatibility terminal result | `run_compat` 负责兼容顺序执行和失败收敛 | call-site | legacy |

---

## 8. Query 与上下文转换

| 边界 | 输入形态 | 关键转换 | 输出形态 |
|---|---|---|---|
| `run_pipeline.main` | ProjectInput JSON | `user_query = project_input["user_query"]`；授权 scope 来自独立文件 | `run_mainline(user_input=user_query, project_input=..., scope=...)` |
| `run_demo.main` | 字符串 Query；可选 seed JSON | 无完整 ProjectInput 时允许 demo defaults | `run_demo(...)` |
| `ProjectInputFactory.build` | `task_id`、`user_input`、raw ProjectInput | production：严格校验且要求 `canonical.user_query == user_input`；demo：可补 task type、sections、title、metadata | `ProjectInputSchema` |
| `TaskFactory.build` | canonical ProjectInput | Query 固化到 `TaskSchema.user_input`；完整 ProjectInput 保存到 `TaskSchema.project_input`；提取 file/doc/kb IDs | `TaskSchema` |
| `SupervisorAgent.run/stream` | Task | `GraphState.user_input=task.user_input`；`task=task.model_dump()`；`requirements={generation_requirements, project_input}` | `GraphStateSchema` |
| normalization node | GraphState | 从 `requirements/task` 读取 ProjectInput，生成 table analysis 和 structured facts，并写 context bundle | normalization output + GraphState delta |
| plan node | normalized state/payload | `SchemeWriterInputSchema.user_input=project_input.user_query`；构造 document plan、required evidence sections | `SchemeGenerationGraphPayloadSchema` |
| retrieval node | payload.project_input | Query 为 `project_input.user_query` | 调用 `SchemeEvidenceService.retrieve` |

### 8.1 ID 来源

- `run_id`：调用方提供，或 `run_demo_<timestamp>`；
- `task_id`：调用方提供；否则优先 `ProjectInput.task_id`；再否则 `task_<run_id>`；
- production 下 runtime task ID 与 ProjectInput task ID 不一致时抛 `ValueError`；
- resume/inspect/cancel 必须复用原 task/run identity，并与 checkpoint/task state 双重核对。

### 8.2 授权上下文

- production real RAG 要求 `RetrievalAccessScopeSchema`；
- scope 不是从 caller-controlled ProjectInput 推导；
- `ProjectInput.tenant_id` 必须与可信 scope tenant 一致；
- demo defaults 可通过 `allow_legacy_unscoped_rag=True` 进入 fake/demo 路径。

---

## 9. Workflow、分支、失败、重试和返回路径

## 9.1 路由

`SupervisorFactory` 当前只向 `workflows` 注册：

```python
{"scheme_generation": workflow}
```

`WorkflowRouter.route` 在“catalog 只有一个 workflow 且 task type 匹配”时：

```text
routing_mode = single_workflow_direct
reason = only_one_workflow_registered
```

当前正常 `scheme_generation` 请求不会调用 Supervisor LLM 路由。

若严格 ProjectInput 提供不支持的 task type：

- single-workflow direct 条件不成立；
- 后续可能进入 LLM/fallback 分支；
- 最终 `catalog.get(unsupported_type)` 可抛 `KeyError`；
- `SupervisorAgent.run` 捕获后返回 failed/retryable failed 结果。

## 9.2 顶层显式业务图

当前 engine 的成功序列：

```text
normalize_project_input
-> plan_document
-> retrieve_document_evidence
-> initialize_generation
-> execute_sections
-> assemble_document
-> evaluate_document_gate
-> persist_deliverable
-> finalize_workflow_result
```

门禁路由：

- `deliverable -> persist_deliverable`
- `rejected -> persist_rejected`
- `technical_failure -> finalize_workflow_failure`
- 配置要求人工审批且尚无决定：`human_review_interrupt -> apply_human_review`

每个普通阶段后：

- `business_route == technical_failure`：进入 `finalize_workflow_failure`；
- 否则继续下一节点。

## 9.3 Human-in-the-loop

触发条件位于：

```python
project_input.generation_requirements.extra["require_human_approval"]
```

暂停时：

- workflow status=`PAUSED`；
- task state=`PAUSED`；
- checkpoint 持久化 interrupt；
- `workflow_complete=False`。

恢复：

- `approved`：继续 persist deliverable；
- `rejected`：route=`rejected`，最终 status=`FAILED`，有 rejected artifact；
- `cancelled`：最终 status=`CANCELLED`，有 rejected artifact；
- 已完成节点不会重放。

## 9.4 顶层重试

`EnterpriseDocumentWorkflowEngine._run_stage` 实现了 attempt loop：

- `max_attempts = contract.max_retries + 1`；
- 只有错误 `retryable=True`、仍有次数、非 cancelled、worker 不再运行时才重试；
- 重试前不提交业务状态；
- 失败后记录 attempt history。

但当前 `_default_node_contracts()` 没有为顶层节点显式设置 `max_retries`，使用 dataclass 默认值 `0`。因此：

**当前默认生产配置下，每个顶层业务节点最多执行 1 次；顶层重试机制存在，但未启用额外重试次数。**

章节内部 retry 属于下游生成实现，本任务未展开。

## 9.5 异常收敛

### 应用边界前置异常

以下异常通常在 Supervisor 运行前直接传播给 CLI/caller：

- ProjectInput strict validation；
- runtime task ID / ProjectInput task ID 冲突；
- tenant 与可信 retrieval scope 冲突；
- production real RAG 缺少可信 scope；
- workspace/task manager/factory 构造错误。

### Supervisor 内异常

`SupervisorAgent.run` 用 `except Exception` 捕获路由、状态、GraphState、engine 等异常：

- 创建 `SUPERVISOR_EXECUTION_FAILED`；
- 根据 failure category 计算 retryable；
- 返回 `FAILED` 或 `RETRYABLE_FAILED`；
- 调用 task lifecycle 写终态；
- 完成 trace。

### 节点异常

`EnterpriseDocumentWorkflowEngine._run_stage`：

- worker outcome 转为 `ErrorSchema`；
- 允许时重试；
- 否则写 failure payload；
- route 到 `finalize_workflow_failure`；
- 不把未声明状态变化提交到 canonical state。

### 资源释放

- 同步：`MainlineApplicationService.run` 在 `finally` 调用 `supervisor.close()`；
- 流式：先关闭 inner generator，再关闭 supervisor；
- timeout worker 尚未退出时，资源关闭可延后到 idle。

---

## 10. 状态变化

### 10.1 TaskState

初始请求：

```text
PENDING
  -> RUNNING
  -> SUCCESS | PARTIAL_SUCCESS | PAUSED | FAILED | RETRYABLE_FAILED | CANCELLED
```

暂停恢复：

```text
PENDING -> RUNNING -> PAUSED -> RUNNING
  -> SUCCESS | PARTIAL_SUCCESS | FAILED | RETRYABLE_FAILED | CANCELLED
```

`JsonlTaskManager._ALLOWED` 明确限制非法迁移；终态不可再次迁移。

### 10.2 GraphState

- `SupervisorAgent` 初建 GraphState；
- `EnterpriseDocumentWorkflowEngine.execute` 设置 engine name/version 和 RUNNING；
- 每个成功提交的业务节点增加 graph/business revision；
- 节点写入必须符合 `BusinessNodeContract` 声明路径；
- 暂停时状态投影为 `PAUSED`；
- terminal node 将 `workflow_complete=True`，控制权进入 END。

### 10.3 返回路径

同步：

```text
WorkflowEngineResultSchema
-> Supervisor AgentResultSchema
-> MainlineApplicationService summary dict
-> CLI/caller
```

流式：

```text
LangGraph stream
-> WorkflowStreamEventSchema
-> terminal event payload.result = Mainline summary
-> CLI逐行 JSON
```

`MainlineApplicationService` 从 `sub_agent_results` 中查找 `agent_name` 为 `SchemeWriterAgent/FakeSchemeWriterAgent` 的 terminal result。这里的 `agent_name` 是 `SchemeGenerationStageService.finalize_workflow_result` 使用配置字段生成的结果标签，**不能反推 `SchemeWriterAgent.run` 被调用**。

---

## 11. 独立运行证据

### 11.1 生产主链绕过 legacy coarse path

执行命令：

```text
.venv/Scripts/python.exe -m pytest -q \
  backend/tests/test_phase2_top_level_business_graph.py::test_production_composition_root_no_longer_executes_coarse_scheme_writer \
  backend/tests/test_phase2_top_level_business_graph.py::test_production_composition_executes_app_graph_without_coarse_fallback \
  backend/tests/test_phase5_langchain_takeover_and_legacy_retirement.py::test_production_mainline_uses_runnables_and_retires_legacy_paths
```

结果：

```text
3 passed in 8.27s
```

关键运行守卫：

- patch `SchemeWriterAgent.run`：调用即 `AssertionError`；
- patch `SchemeGenerationUseCase.run`：调用即 `AssertionError`；
- Phase 5 还 patch `SectionExecutionCoordinator.execute` legacy path；
- 在这些守卫存在时，`MainlineApplicationService.run` 仍返回 success/partial success；
- metadata 证明 engine=`enterprise_document_langgraph_engine`；
- `legacy_workflow_path_retired=True`；
- RAG/model Runnable 均有实际 invocation count。

### 11.2 暂停、拒绝和取消

执行命令：

```text
.venv/Scripts/python.exe -m pytest -q \
  backend/tests/test_phase4_durable_resume_hitl.py::test_human_review_interrupt_is_paused_and_task_is_not_terminal_failure \
  backend/tests/test_phase4_durable_resume_hitl.py::test_application_service_rejects_and_cancels_through_durable_graph
```

结果：

```text
2 passed in 10.62s
```

证明：

- Human review interrupt 产生 workflow/task `PAUSED`，不是 terminal failure；
- reject 通过 durable graph 返回 `FAILED` + rejected artifact；
- cancel 返回 `CANCELLED` + rejected artifact；
- task/run identity 保持不变。

### 11.3 未执行的运行验证

- 未运行全量 `pytest`；完整测试状态为 unknown；
- 未运行 real provider / real Milvus，避免外部服务、共享索引、费用和持久副作用；
- 未为调查新增临时测试、埋点或业务代码。

---

## 12. 状态分类

## 12.1 Active

| 对象/路径 | 判定依据 |
|---|---|
| `scripts/run_pipeline.py::main` | 明确接收生产 ProjectInput 和可信 retrieval scope，并调用当前 mainline |
| `scripts/workflow_control.py::main(stream/inspect/resume/cancel/stream-*)` | README 当前运行命令；代码直连 `MainlineApplicationService`；相关测试通过 |
| `scripts/mainline_runtime.py::run_mainline` | run_demo/run_pipeline 的真实 facade；调用 singleton `MainlineApplicationService.run` |
| `MainlineApplicationService.run/stream_run` | 当前同步/流式应用入口 |
| `TaskFactory` / `ProjectInputFactory` | 当前 Query/ProjectInput 到 Task 的实际转换 |
| `JsonlTaskManager` / `TaskLifecycleService` | 当前 task state 持久化与迁移 |
| `SupervisorFactory.build` | 当前组合根 |
| `SupervisorAgent.run/stream/resume/cancel` | 当前路由、GraphState、生命周期和结果边界 |
| `WorkflowRouter.route` | 当前实际执行；正常请求走 single-workflow direct |
| `EnterpriseDocumentWorkflowEngine` | 当前 production workflow engine；测试运行证据 |
| `ProjectInputNormalizationUseCase.execute` | explicit graph normalizer node直接调用 |
| `SchemeGenerationStageService` 各显式 stage method | explicit graph节点直接调用 |
| `SchemeWriterAgent.__init__` | 当前组合根实际构造，用于组装 stage service |
| `SchemeGenerationUseCase.__init__` | 当前 `SchemeWriterAgent.__init__` 实际构造；仅构造作用 |

## 12.2 Inactive（当前生产主链）

| 对象/路径 | 判定依据 |
|---|---|
| `ProjectInputNormalizerAgent.run` | agent被注册，但 custom `EnterpriseDocumentWorkflowEngine` 不读取 AgentRegistry；active graph直接调用独立 `ProjectInputNormalizationUseCase.execute` |
| `AgentRegistry` 在当前 production engine 中的节点调度作用 | registry传给 Supervisor 只用于默认 engine 分支；已注入 custom engine，因此不参与当前节点调度 |
| `build_scheme_generation_workflow().steps` 的 target topology | `EnterpriseDocumentWorkflowEngine.build_graph` 显式声明节点；metadata/test证明 `workflow_definition_topology_ignored=True` |
| `LangGraphWorkflowEngine` 作为 Supervisor 默认 engine | `SupervisorFactory` 显式注入 `EnterpriseDocumentWorkflowEngine`；默认表达式不执行 |

## 12.3 Legacy

| 对象/路径 | 判定依据 |
|---|---|
| `SchemeGenerationUseCase.run` | 模块/类明确标为 Legacy/Compatibility；生产测试证明不调用 |
| `SchemeGenerationStageService.run_compat` | 仅由 `SchemeGenerationUseCase.run` 调用；方法 docstring 为 thin legacy facade |
| `SchemeWriterAgent.run` | 唯一功能是转调 legacy UseCase；生产组合根不注册该 Agent；测试证明不调用 |
| 基于 workflow steps + AgentRegistry 的 coarse SchemeWriter 执行链 | Phase 2/5 测试与 metadata 明确 retired/ignored |

## 12.4 Experimental

| 对象/路径 | 判定依据 |
|---|---|
| `scripts/run_demo.py` 入口适配层 | 明确是 Demo；允许 demo defaults / fake runtime；但其下游共用 active mainline |
| `backend/tests/**` 中直接构造 Agent/UseCase/Engine | 测试专用调用，不证明生产可达 |
| acceptance/system-test scripts | 验收/专项测试路径，不等同于生产 Query 入口 |
| 测试中直接实例化 `LangGraphWorkflowEngine` | 用于 runtime contract/安全性测试；当前 production composition root 不选它 |

## 12.5 Unknown

| 项目 | 原因 |
|---|---|
| 仓库外调用者是否直接调用 `SchemeWriterAgent.run` / `SchemeGenerationUseCase.run` | 当前仓库搜索无法证明外部消费者行为 |
| 仓库外是否存在 HTTP/API adapter | 当前仓库无注册证据，外部部署仓库不在调查范围 |
| dirty workspace 中未提交内容最终是否会进入正式基线 | 当前未提交，无法作为未来稳定版本事实 |
| 全量测试是否通过 | 本轮仅执行 5 个定向测试 |
| real provider / real Milvus 路径在当前环境是否可运行 | 本轮未执行有外部副作用的命令 |

---

## 13. Findings

### F-001：TASK-001 / TASK-002 的起点假设与当前代码不一致

- 任务文本要求终点落在 `SchemeGenerationUseCase` 的实际调用与返回；
- 当前主链只构造该对象，不调用其 `run`；
- 当前真实 RAG 触发来自 explicit graph 对 `SchemeGenerationStageService.retrieve_document_evidence` 的直接调用；
- 因此不存在可以诚实交给 TASK-002 的“active `SchemeGenerationUseCase.run` 调用点”。

状态：**已记录，未处理。**

### F-002：WorkflowDefinition 的 step topology 存在，但当前 engine 不使用它调度

- `build_scheme_generation_workflow` 声明 `ProjectInputNormalizerAgent` 和 `SchemeWriterAgent` step；
- `EnterpriseDocumentWorkflowEngine.build_graph` 明确使用显式业务节点；
- runtime metadata 设置 `workflow_definition_topology_ignored=True`；
- 当前 workflow object仍用于 ID/version/task type/checkpoint namespace，不等于完全未使用。

状态：**已记录，未处理。**

### F-003：ProjectInputNormalizerAgent 被注册，但其 `run` 不在当前生产执行链

- `SupervisorFactory` 注册该 Agent；
- custom engine不接收 AgentRegistry；
- normalization节点直接创建/调用 `ProjectInputNormalizationUseCase`；
- 注册事实不能写成 Agent 执行事实。

状态：**已记录，未处理。**

### F-004：终态结果标签 `agent_name=SchemeWriterAgent` 不代表 Agent 方法被调用

- terminal `AgentResultSchema` 由 `SchemeGenerationStageService.finalize_workflow_result/failure` 创建；
- `agent_name` 来自 StageService 构造参数；
- Mainline 按该标签提取 payload；
- 运行测试已证明 `SchemeWriterAgent.run` 未调用。

状态：**已记录，未处理。**

### F-005：当前调查基线不是干净 commit

- 工作区存在大量未提交修改和未跟踪文件；
- 当前结论不能仅通过 checkout `07dda2e` 保证复现。

状态：**已记录，未处理。**

---

## 14. TASK-002 的准确边界事实

### 14.1 原任务要求的边界

```text
SchemeGenerationUseCase actual call -> online RAG
```

当前状态：**不存在 active 调用点。**

### 14.2 当前代码中唯一已确认的实际在线 RAG 触发边界

```text
File:
backend/apps/enterprise_document/workflows/top_level_business_graph.py

Class:
EnterpriseDocumentWorkflowEngine

Function:
build_graph

Registration:
"retrieve_document_evidence"
  -> self.stage_service.retrieve_document_evidence
```

运行时调用包装：

```text
EnterpriseDocumentWorkflowEngine._run_stage
  -> SchemeGenerationStageService.retrieve_document_evidence(
       graph_state,
       payload
     )
```

直接 RAG 调用：

```text
File:
backend/apps/enterprise_document/services/scheme_writer/stage_service.py

Class:
SchemeGenerationStageService

Function:
retrieve_document_evidence

Call:
self.evidence_service.retrieve(shared_state, project_input)
```

传入上下文：

- `shared_state / GraphStateSchema`：task/run/tenant/task payload/context bundle；
- `payload.project_input`：canonical `ProjectInputSchema`；
- Query：`project_input.user_query`；
- StageService 持有的 `evidence_service` 已由 `SchemeWriterAgent.__init__` 组装；
- `evidence_service.rag_service` 由 `SupervisorFactory` 注入 `LangChainRAGServiceFacade`；
- production real RAG scope 来自独立 `RetrievalAccessScopeSchema`。

TASK-002 若严格遵守当前任务文本，不得自行把该边界改写成 `SchemeGenerationUseCase.run`。需要项目管理/任务定义层先确认起点修订。

---

## 15. 未确认问题与证据缺口

1. 仓库外是否仍有兼容调用者依赖 `SchemeWriterAgent.run` / `SchemeGenerationUseCase.run`：unknown。
2. 当前 dirty workspace 中哪些改动将被纳入最终基线：unknown。
3. 全量测试结果：unknown。
4. real provider、real Milvus、真实业务 ProjectInput 的运行结果：本轮未执行，unknown。
5. TASK-002 是否允许把起点改为 `SchemeGenerationStageService.retrieve_document_evidence`：需项目管理/Owner确认；本任务不修改 TASK 文件。

---

## 16. 验收项自检

| 验收项 | 结果 |
|---|---|
| 从真实 Query 入口开始 | 满足：确认生产、Demo、流式 CLI；未发现仓库内 HTTP API |
| 每条主链边有文件/类/函数/直接证据 | 满足，见第 7 节 |
| Query 字段来源可追踪 | 满足，见第 8 节 |
| 分支、异常、暂停、恢复、取消已登记 | 满足，见第 9、10、11 节 |
| active/inactive/legacy/experimental/unknown 分类 | 满足，见第 12 节 |
| 静态事实与运行事实区分 | 满足，见第 7、11 节 |
| 不评价或重构设计 | 满足 |
| 不修改业务/测试/配置 | 满足 |
| 不修改 ARCHITECTURE/CONTRACTS/STATUS/TASK | 满足 |
| 终点为 active `SchemeGenerationUseCase.run` | **无法满足：当前基线不存在该调用；已作为 F-001 明确记录** |
| TASK-002 可按原文本直接开始 | **无法满足：起点假设与当前代码不一致；Handoff 明确阻断虚假起点** |
