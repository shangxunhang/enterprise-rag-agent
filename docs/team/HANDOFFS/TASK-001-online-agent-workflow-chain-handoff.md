# TASK-001 Online Agent / Workflow Chain — Handoff

## 1. Handoff 状态

- Source Task：`TASK-001`
- Source Role：Agent / Workflow Engineer
- Primary Handoff Target：**Project Manager**（先处理任务边界不一致）
- Conditional Next Target：**RAG Engineer / TASK-002**
- Secondary Target：Architect
- Verification Target：QA / Reviewer
- 建议 TASK-001 状态：**review**
- 建议 TASK-002 状态：**保持 draft，边界澄清前不要进入 discovery**

### 1.1 必须先读的结论

当前真实生产主链中：

```text
SchemeGenerationUseCase.__init__  被调用
SchemeGenerationUseCase.run       未被调用
```

因此，TASK-002 原文要求的“从 TASK-001 确认的 `SchemeGenerationUseCase` 实际调用点开始”目前没有合法起点。

不得把对象构造、成员引用或结果中的 `agent_name="SchemeWriterAgent"` 当作 `SchemeGenerationUseCase.run` 已执行的证据。

---

## 2. 已确认事实

## 2.1 当前 Query 入口

已确认的初始 Query 入口：

1. 生产结构化 CLI
   - 文件：`scripts/run_pipeline.py`
   - 函数：`main`
   - Query 来源：`ProjectInput.user_query`
   - 下游：`scripts/mainline_runtime.py::run_mainline`

2. Demo / 交互 CLI
   - 文件：`scripts/run_demo.py`
   - 函数：`main -> run_demo`
   - Query 来源：`--user-input` 或控制台输入
   - 下游：`scripts/mainline_runtime.py::run_mainline`
   - 分类：入口适配层 experimental；共用下游主链 active

3. 初始流式 CLI
   - 文件：`scripts/workflow_control.py`
   - 函数：`main`，command=`stream`
   - Query 来源：`--user-input`
   - 下游：`MainlineApplicationService.stream_run`

当前仓库中未找到 FastAPI、APIRouter 或 HTTP route 注册证据。

## 2.2 当前同步主链

```text
run_pipeline.main / run_demo.main
-> mainline_runtime.run_mainline
-> MainlineApplicationService.run
-> TaskFactory.build
-> ProjectInputFactory.build
-> JsonlTaskManager.create_task
-> build_scheme_generation_workflow
-> RuntimeOptions.resolve
-> SupervisorFactory.build
-> SupervisorAgent.run
-> WorkflowRouter.route
-> EnterpriseDocumentWorkflowEngine.execute
-> explicit LangGraph business nodes
```

正常 `scheme_generation` 请求只有一个已注册 workflow，路由结果为：

```text
routing_mode = single_workflow_direct
reason = only_one_workflow_registered
```

正常路径不会调用 Supervisor LLM 路由。

## 2.3 `SchemeGenerationUseCase` 的真实状态

### 构造点：active construction

- 文件：`backend/apps/enterprise_document/agents/scheme_writer_agent.py`
- 类：`SchemeWriterAgent`
- 函数：`__init__`
- 上游调用者：`backend/bootstrap/supervisor_factory.py::SupervisorFactory.build`
- 直接构造语句：

```python
self.use_case = SchemeGenerationUseCase(
    input_service=self.input_service,
    evidence_service=self.evidence_service,
    section_execution_coordinator=self.section_execution_coordinator,
    document_assembler=self.document_assembler,
    document_planning_service=self.document_planning_service,
    capture_service=self.capture_service,
    runtime_support=self.runtime_support,
    agent_name=self.agent_name,
    agent_type=self.agent_type,
    rag_service_name=rag_service_name,
    enable_semantic_gate=enable_semantic_gate,
    section_max_concurrency=section_max_concurrency,
)
self.stage_service = self.use_case.stage_service
```

输入：SchemeWriter 的 input/evidence/section/document/capture/runtime 等依赖。

输出：

- `self.use_case` 对象；
- UseCase 内构造的 `SchemeGenerationStageService`；
- 同一 StageService 实例通过 `scheme_writer.stage_service` 暴露给组合根。

状态变化：仅对象关系建立，不发生 workflow/task/GraphState 执行状态变化。

异常行为：StageService 构造参数非法时直接传播，例如 `section_max_concurrency` 不在 1..32。

### 执行点：legacy / 当前生产不可达

- 文件：`backend/apps/enterprise_document/agents/scheme_writer_agent.py`
- 类：`SchemeWriterAgent`
- 函数：`run`
- 唯一调用语句：

```python
return self.use_case.run(shared_state)
```

- 文件：`backend/apps/enterprise_document/services/scheme_writer/use_case.py`
- 类：`SchemeGenerationUseCase`
- 函数：`run`
- 下游：

```python
return self.stage_service.run_compat(shared_state)
```

当前生产组合根不注册 `SchemeWriterAgent`，显式 Workflow Engine 也不读取它，因此上述执行链当前不可达。

## 2.4 当前真实 Workflow Engine

- 文件：`backend/bootstrap/supervisor_factory.py`
- 类：`SupervisorFactory`
- 函数：`build`
- 直接注入：

```python
workflow_engine=EnterpriseDocumentWorkflowEngine(
    stage_service=scheme_writer.stage_service,
    run_trace_recorder=trace,
    side_effect_commit_store=side_effect_commit_store,
    checkpointer=checkpoint_resource.saver,
)
```

- 文件：`backend/apps/enterprise_document/workflows/top_level_business_graph.py`
- 类：`EnterpriseDocumentWorkflowEngine`
- engine name：`enterprise_document_langgraph_engine`

该 engine 在 `build_graph` 中显式注册业务节点，不依据 `workflow.steps` 调用 `AgentRegistry` 中的 target Agent。

运行 metadata 已确认：

```text
workflow_definition_topology_ignored = True
legacy_workflow_path_retired = True
```

## 2.5 当前真实在线 RAG 触发边界

这是 TASK-002 可能采用的**唯一已确认实际边界**，但采用前必须由 Project Manager 修订或确认 TASK-002 起点。

### 上游图注册

- 文件：`backend/apps/enterprise_document/workflows/top_level_business_graph.py`
- 类：`EnterpriseDocumentWorkflowEngine`
- 函数：`build_graph`
- 注册语句：

```python
builder.add_node(
    "retrieve_document_evidence",
    business_node(
        "retrieve_document_evidence",
        self.stage_service.retrieve_document_evidence,
    ),
)
```

### 运行包装

- 文件：`backend/apps/enterprise_document/workflows/top_level_business_graph.py`
- 类：`EnterpriseDocumentWorkflowEngine`
- 函数：`_run_stage`
- 上游调用者：LangGraph 节点 `retrieve_document_evidence`
- 被调用者：`SchemeGenerationStageService.retrieve_document_evidence`
- 输入：
  - `GraphStateSchema`；
  - `SchemeGenerationGraphPayloadSchema`；
  - payload 中已存在 normalized `project_input` 和 `document_plan`。
- 输出：更新后的 `SchemeGenerationGraphPayloadSchema` 和声明范围内 GraphState delta。
- 状态变化：成功后记录 node result、business revision、graph revision，并路由下一节点。
- 异常行为：异常转为结构化节点错误；按 contract 判断是否重试；最终可路由 `finalize_workflow_failure`。

### 直接 RAG 调用

- 文件：`backend/apps/enterprise_document/services/scheme_writer/stage_service.py`
- 类：`SchemeGenerationStageService`
- 函数：`retrieve_document_evidence`
- 上游调用者：`EnterpriseDocumentWorkflowEngine._run_stage`
- 直接调用语句：

```python
rag_result = self.evidence_service.retrieve(
    shared_state,
    project_input,
)
```

输入：

- `shared_state`：当前 `GraphStateSchema`，包含 task/run/tenant/task payload/context bundle；
- `project_input`：`payload.project_input`，类型为 canonical `ProjectInputSchema`；
- Query：`project_input.user_query`；
- `evidence_service`：`SchemeEvidenceService`；
- production real RAG 授权：在组合阶段注入的独立 `RetrievalAccessScopeSchema`；
- RAG port 实现：`SupervisorFactory` 注入的 `LangChainRAGServiceFacade`。

输出：

- `rag_result`；
- 该函数随后形成 document context、chunks、citations、normalized RAG output 和 evidence assessment；
- 返回更新后的 `SchemeGenerationGraphPayloadSchema`。

后续控制权：

```text
成功
-> _run_stage 提交 payload/GraphState delta
-> route=continue
-> initialize_generation

技术失败
-> _run_stage 形成 failure payload
-> route=technical_failure
-> finalize_workflow_failure
```

注意：TASK-002 可继续调查 `SchemeEvidenceService.retrieve` 内部，但 TASK-001 未展开其内部 RAG 链。

---

## 3. 运行证据

## 3.1 Legacy path 不被调用

执行：

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

测试在运行前设置：

```text
SchemeWriterAgent.run            调用即 AssertionError
SchemeGenerationUseCase.run      调用即 AssertionError
```

生产 mainline 仍完成，证明这两个方法没有进入当前运行链。

## 3.2 暂停、拒绝、取消

执行：

```text
.venv/Scripts/python.exe -m pytest -q \
  backend/tests/test_phase4_durable_resume_hitl.py::test_human_review_interrupt_is_paused_and_task_is_not_terminal_failure \
  backend/tests/test_phase4_durable_resume_hitl.py::test_application_service_rejects_and_cancels_through_durable_graph
```

结果：

```text
2 passed in 10.62s
```

确认：

- human review interrupt：workflow/task 为 `PAUSED`；
- reject：`FAILED` + rejected artifact；
- cancel：`CANCELLED` + rejected artifact；
- durable continuation 保持原 task/run identity。

---

## 4. 状态分类交接

| 对象/路径 | 分类 | 说明 |
|---|---|---|
| `MainlineApplicationService.run/stream_run` | active | 当前同步/流式应用边界 |
| `SupervisorAgent.run/stream/resume/cancel` | active | 当前路由、状态、执行和返回边界 |
| `EnterpriseDocumentWorkflowEngine` | active | 当前生产 Workflow Engine |
| `SchemeGenerationStageService.retrieve_document_evidence` | active | 当前实际在线 RAG 上游调用者 |
| `SchemeEvidenceService.retrieve` 调用边 | active | 当前实际 RAG 触发边界 |
| `SchemeWriterAgent.__init__` | active | 当前服务组合壳 |
| `SchemeGenerationUseCase.__init__` | active | 当前构造 StageService |
| `SchemeWriterAgent.run` | legacy | 当前生产不可达 |
| `SchemeGenerationUseCase.run` | legacy | 当前生产不可达 |
| `SchemeGenerationStageService.run_compat` | legacy | 只被 legacy UseCase.run 调用 |
| `ProjectInputNormalizerAgent.run` | inactive | Agent 被注册，但当前 custom engine 不调用 registry target |
| workflow definition step target topology | inactive | 当前 engine 显式图忽略 topology；ID/version/task type仍 active |
| generic `LangGraphWorkflowEngine` 生产默认路径 | inactive | SupervisorFactory 显式注入另一 engine |
| `run_demo.py` 入口适配层 | experimental | Demo/fake defaults；共用 active mainline |
| 测试中直接构造 Agent/UseCase/Engine | experimental | 测试路径不证明生产可达 |

---

## 5. 未确认问题

以下项不得被 TASK-002、Architect 或 QA 当作已确认事实：

1. 仓库外是否有调用者仍直接调用 `SchemeWriterAgent.run` 或 `SchemeGenerationUseCase.run`：**unknown**。
2. 仓库外是否存在 HTTP/API adapter：**unknown**；当前仓库内无注册证据。
3. 当前 dirty workspace 中哪些修改最终会进入正式基线：**unknown**。
4. 全量 pytest 是否通过：**unknown**；本轮只运行 5 个定向测试。
5. real provider / real Milvus / 真实业务 ProjectInput 当前是否可成功运行：**unknown**；本轮未执行外部副作用命令。
6. TASK-002 是否被授权把起点由 `SchemeGenerationUseCase.run` 改为 `SchemeGenerationStageService.retrieve_document_evidence`：**尚未确认**。

---

## 6. 新发现但未处理的问题

### Finding F-001：TASK 起点与当前代码事实不一致

TASK-001 和 TASK-002 均假定存在 active `SchemeGenerationUseCase` 调用点；当前代码和运行测试证明该假定不成立。

处理状态：仅登记，未修改 TASK 文件。

### Finding F-002：WorkflowDefinition topology 与当前执行 topology 不一致

WorkflowDefinition 声明 Agent steps，但 current engine 使用独立显式业务图；runtime metadata 明确 topology ignored。

处理状态：仅登记，未评价、未修改。

### Finding F-003：注册 Agent 不等于执行 Agent

`ProjectInputNormalizerAgent` 被注册，但当前 production graph直接调用 `ProjectInputNormalizationUseCase.execute`。

处理状态：仅登记，未修改。

### Finding F-004：`agent_name` 结果标签可能被误读为调用证据

terminal result 的 `agent_name="SchemeWriterAgent"` 由 StageService 构造参数产生，不证明 `SchemeWriterAgent.run` 被调用。

处理状态：仅登记，未修改。

### Finding F-005：调查基线工作区不干净

基线为：

```text
branch main
commit 07dda2e80645cac67f8b5f5e30002d21c2ab01eb
workspace dirty
```

调查前统计：modified 42、added 6、deleted 1、untracked 38。

处理状态：仅登记，未清理、未提交。

---

## 7. 建议交接动作

## 7.1 Project Manager

需要作出唯一决策：

```text
是否将 TASK-002 起点修订为：
EnterpriseDocumentWorkflowEngine explicit node
-> SchemeGenerationStageService.retrieve_document_evidence
-> SchemeEvidenceService.retrieve
```

在该决策完成前，TASK-002 不应声称从 `SchemeGenerationUseCase` 开始，也不应自行选择起点。

## 7.2 RAG Engineer

只有在 Project Manager 确认边界后，才能从以下直接调用边继续：

```python
rag_result = self.evidence_service.retrieve(
    shared_state,
    project_input,
)
```

调查时必须保留以下输入事实：

- Query=`project_input.user_query`；
- `project_input` 已经过 strict/demo adapter + normalization；
- `shared_state` 为 GraphState；
- production retrieval scope 来自独立可信输入，不来自 ProjectInput；
- 上游调用者是 StageService，不是 `SchemeGenerationUseCase.run`。

## 7.3 Architect

汇总时应区分：

- construction ownership；
- runtime execution ownership；
- result label；
- compatibility facade。

不得把 `SchemeGenerationUseCase` 对象存在写成其拥有当前 production orchestration。

## 7.4 QA / Reviewer

建议独立复核：

1. `SupervisorFactory.build` 没有注册 SchemeWriterAgent；
2. `EnterpriseDocumentWorkflowEngine.build_graph` 不读取 `workflow.steps`；
3. production guard tests 在两个 legacy `run` 被 patch 为抛错时仍通过；
4. Handoff 的 RAG 边界是否与 Analysis 第 14 节一致；
5. 本任务仅新增指定 Analysis 与 Handoff，没有修改业务代码或治理文档。

---

## 8. 建议任务状态

### TASK-001

建议：`review`

理由：

- 调查步骤、证据矩阵、分类和运行核验已完成；
- 发现原任务假设与当前代码冲突；
- 未通过猜测填补冲突；
- 需要 Project Manager 和 QA 审核结论。

### TASK-002

建议：保持 `draft`，暂不进入 `discovery`。

解除条件：Project Manager 明确确认或更新其实际起点。

---

## 9. 关联文件

完整证据和逐边矩阵：

```text
docs/team/ANALYSIS/TASK-001-online-agent-workflow-chain.md
```

本 Handoff：

```text
docs/team/HANDOFFS/TASK-001-online-agent-workflow-chain-handoff.md
```
