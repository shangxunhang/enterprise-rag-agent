# 15 一页速查表

## 本文范围

本文用于从概念快速反查当前文件、类、方法、Schema、下游和状态标签。它是导航摘要，不替代 01–13 篇的调用、失败和证据分析；行号基于本手册生成时的提交 `96ec3228cc9b523aabe9b21da01347274fbaf162`。

## 当前事实结论

- 当前本地真实入口是 [DEMO] 命名的 `scripts/run_demo.py`，默认并不启用 Fake runtime。
- 生产 workflow 是 `EnterpriseDocumentWorkflowEngine` 顶层 LangGraph，内部调用 `SectionExecutionGraph`；通用 `LangGraphWorkflowEngine` 是可执行回退/测试实现。
- 在线 RAG 的真实边界是 `RAGService`，不是仓库中不存在的 `RealRAGTool` 类。
- 离线 builder 从 cleaned-text-unit JSONL 起步，而且 build 不自动 activate。
- V1 是 [STANDALONE]，默认外部能力为 Scenario/Fixture/Fake，未接生产 Supervisor/RAG/ModelGateway。
- 当前全量 pytest collection 为 [BROKEN]：491 tests collected，30 collection errors；详见 12 篇。

## 核心入口速查

| 项目 | 文件 | 类/主要方法 | 输入 | 输出 | 下游 | 状态标签 |
|---|---|---|---|---|---|---|
| 生产本地入口 | `scripts/run_demo.py` | `main()`、`build_project_input()` | CLI 参数、settings | `ProjectInputSchema`、退出码/摘要 | `mainline_runtime.run_mainline()` | [DEMO] [PRODUCTION] |
| Fake Demo 分支 | `scripts/run_demo.py` | `main()` 的 `--fake-runtime` 分支 | CLI 参数 | Fake 运行结果 | Fake Agent/Provider | [DEMO] [FAKE] |
| Mainline 薄入口 | `scripts/mainline_runtime.py` | `run_mainline()` | `ProjectInputSchema`、runtime options | application result | `MainlineApplicationService.run()` | [PRODUCTION] |
| Application Service | `backend/application/mainline_service.py` | `MainlineApplicationService.run()` | `ProjectInputSchema` | `MainlineRunResultSchema`/summary | TaskFactory、SupervisorFactory、Supervisor | [PRODUCTION] |
| Composition Root | `backend/bootstrap/supervisor_factory.py` | `SupervisorFactory.build()` `56-181` | settings、task/artifact/trace dependencies | `SupervisorAgent` | 专用 workflow、RAG、model、checkpointer | [PRODUCTION] |
| Supervisor | `backend/agent/supervisor_agent.py` | `SupervisorAgent.run()` `1044-1274` | `TaskSchema` | `AgentResultSchema` | workflow router/engine、lifecycle、trace | [PRODUCTION] |
| 通用 Workflow Engine | `backend/agent/runtime/langgraph_workflow_engine.py` | `LangGraphWorkflowEngine.execute()` `192-307` | `WorkflowDefinitionSchema`、`GraphStateSchema` | `WorkflowEngineResultSchema` | registry Agent nodes | [IMPLEMENTED_ONLY] [TEST_ONLY] |
| 顶层 Graph | `backend/apps/enterprise_document/workflows/top_level_business_graph.py` | `EnterpriseDocumentWorkflowEngine.build_graph()` `1309-1458`、`execute()` `1644-1678` | workflow、`GraphStateSchema` | `WorkflowEngineResultSchema` | Stage Service、Section Graph、checkpointer | [PRODUCTION] [INTEGRATED] |
| 顶层 Graph State | `backend/agent/runtime/graph_state.py` | `GraphStateSchema` | `SharedStateSchema` + revisions | canonical business state | differ/write-contract/applier、codec | [PRODUCTION] |
| LangGraph State 投影 | `backend/agent/runtime/langgraph_state.py` | `LangGraphRuntimeState` | serialized graph state/node payloads | LangGraph channel state | checkpointer/codec | [PRODUCTION] [INTEGRATED] |
| Scheme Writer Legacy | `backend/apps/enterprise_document/agents/scheme_writer_agent.py` | `SchemeWriterAgent.run()` `173-...` | `SharedStateSchema` | `AgentResultSchema` | 分解后的 SchemeWriter services | [LEGACY] |
| Scheme Stage Service | `backend/apps/enterprise_document/services/scheme_writer/stage_service.py` | `plan()`、`retrieve_document_evidence()`、`execute_sections()`、`assemble_document()`、`evaluate_document_gate()`、`persist_output()` | graph business state/typed stage inputs | candidate state/output schemas | RAG、Section Graph、Model、Artifact | [PRODUCTION] [INTEGRATED] |
| Section Graph | `backend/apps/enterprise_document/workflows/section_execution_graph.py` | `SectionExecutionGraph.build_graph()` `1019-1069`、`execute()` `1071-1120` | `SectionExecutionGraphPayloadSchema` | `SectionExecutionResult`/state | evidence、generation、quality、corrective services | [PRODUCTION] [INTEGRATED] |
| Section Coordinator | `backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py` | `execute()` `201-...` | section/context/budget | Section 结果 | 所持子服务 | [LEGACY] [INTEGRATED] |
| ProjectInput | `backend/apps/enterprise_document/schemas/project_input_schema.py` | `ProjectInputSchema` | 项目事实、模板、路径/选项 | 校验后的项目输入 | TaskFactory、Scheme planning | [PRODUCTION] |
| Task | `backend/schemas/task.py`、Task factory/manager | `TaskSchema`、`TaskFactory.build()` | ProjectInput + identity | Task aggregate | Supervisor、event persistence | [PRODUCTION] |

## RAG、模型与配置速查

| 项目 | 文件 | 类/主要方法 | 输入 | 输出 | 下游 | 状态标签 |
|---|---|---|---|---|---|---|
| RAG Port | `backend/contracts/rag.py` | `RAGServicePort.retrieve()` | `RAGToolInputSchema` | `RAGToolOutputSchema` | SchemeWriter/Section services | [PRODUCTION] |
| RAG 装配 | `backend/bootstrap/rag_service_factory.py` | `RAGServiceFactory` | settings、registry、index resolver、model gateway | `RAGService` | online retrieval components | [PRODUCTION] |
| Online Retrieval | `backend/rag/services/rag_service.py` | `RAGService.retrieve()` | query、profile、filters、context budget | retrieval/evidence/tool output | query transform、hybrid、parent fill、rerank、CRAG、context gate | [PRODUCTION] [REAL_PROVIDER] |
| Query Router | `backend/rag/query/` | adaptive profile router / transformer plugins | query、intent、config | retrieval plan/transformed query | retrievers | [PRODUCTION] [INTEGRATED] |
| Hybrid Retrieval | `backend/rag/retrieval/` | dense/BM25/parent-child/RRF components | query + index handle | child/parent candidates | enrichment/reranker | [PRODUCTION] [REAL_PROVIDER] |
| Milvus | `backend/rag/` 的 Milvus adapter/index modules | search/write/load methods | vectors、metadata、collection | hits/index handle | online retrieval/offline builder | [REAL_PROVIDER] |
| Reranker | `backend/rag/` 的 reranker plugin modules | rerank | query + candidates | scored candidates | context/evidence building | [PRODUCTION]；Provider 可用性有条件 |
| Context Manager | `backend/context_manager/`、`backend/rag/context/` | `ContextManager`/packer methods | evidence、token budget | bounded context | writer/model | [PRODUCTION] [INTEGRATED] |
| Evidence Contract | `backend/rag/evidence/`、`backend/rag/schema/`、`backend/schemas/` | Evidence/Citation schemas | retrieval candidates/lineage | evidence bundle/contract | writer、gate、trace | [PRODUCTION] |
| RAG Quality | `backend/rag/judge/`、`backend/rag/plugins/correction_gates/`、`backend/rag/plugins/corrective_query_planners/` | judge/corrective components | query、evidence、scores | assessment/repair plan | corrective retrieval/context gate | [PRODUCTION]；部分导入 [BROKEN] |
| Offline Builder | `backend/rag/offline/builder.py` | `OfflineIndexBuilder` | cleaned-text-unit JSONL、build config | staged index/version/manifest | embedding、Milvus writer、verification | [REAL_PROVIDER] [IMPLEMENTED_ONLY] |
| Chunker | `backend/rag/chunker/FixedSizeChunker.py`、`backend/rag/chunker/fixed_chunker.py`、`backend/rag/chunker/fixed_size_core.py` | chunk methods | normalized text units | parent/child chunks | embedding/index writer | [INTEGRATED]；一项测试 [BROKEN] |
| Index Lifecycle | `backend/rag/offline/lifecycle.py` | register/activate/rollback | verified version、registry paths | active pointer/lifecycle record | online resolver/reload | [INTEGRATED] |
| Index Resolver | `backend/rag/` resolver modules | resolve/reload | active pointer/index config | live retrieval engine/index handle | RAGService | [PRODUCTION] |
| Model Gateway | `backend/model_gateway/` 与 adapter modules | gateway `generate()`/route | prompt/request、route options | text/structured output + usage | SchemeWriter、RAG judges/transforms | [PRODUCTION] [REAL_PROVIDER] |
| 本地 Qwen Adapter | `backend/model_gateway/` provider modules | load/generate | 本地模型路径、prompt | provider response | ModelGateway | [REAL_PROVIDER]；配置条件 |
| DeepSeek Adapter | `backend/model_gateway/` provider modules | API generate | API key/model/base URL | provider response | ModelGateway | [REAL_PROVIDER]；配置条件 |
| Settings | `backend/core/config.py`、`backend/rag/config/`、`backend/apps/enterprise_document/config/` | settings/load/validate | env、YAML/JSON、默认值 | runtime settings | Factories/registries | [PRODUCTION] |
| Component Registry | `backend/rag/registry/` 或对应 registry modules | register/resolve/default registrations | component name/config | plugin instance | RAG Factory/Pipeline | [PRODUCTION] [INTEGRATED] |

`[REAL_PROVIDER]` 表示代码会调用真实本地模型、Milvus 或外部 API 边界，不保证当前机器已经具备模型、索引或凭据。精确模块和配置键见 04、05、06、11 篇。

## Artifact、Event、Checkpoint、Trace 与质量速查

| 项目 | 文件 | 类/主要方法 | 输入 | 输出 | 下游 | 状态标签 |
|---|---|---|---|---|---|---|
| 生产 Artifact | `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`、`backend/application/run_workspace.py` | `persist_deliverable()`、`persist_rejected()`、workspace path/write methods | payload、identity、metadata | business output/workspace files | task/result、final output | [PRODUCTION] |
| Task Domain Event | task manager/event store modules | append/replay | task state transition | sequenced JSONL event | task aggregate recovery | [PRODUCTION] |
| LangGraph Checkpoint | `backend/agent/runtime/official_checkpoint_saver.py` 与 Factory | saver/compile injection | runtime state、thread/ns | SQLite checkpoint/history | execute/resume/inspect | [PRODUCTION] |
| Side-effect Ledger | agent runtime side-effect modules | claim/commit/release | execution identity/input hash | idempotency record | stage runner | [PRODUCTION] |
| Trace V1/V2 | `backend/observability/`、trace services/schemas | start/finish/append span | run/agent/tool/model/evidence metadata | trace events/spans | diagnostics/evaluation | [PRODUCTION] |
| Section Gate | Section quality/evidence services | evaluate | section text/evidence/citations | action/pass/failure | rewrite/research-more/finalize | [PRODUCTION] |
| Document Hard Gate | Stage/document gate service | evaluate_document_gate | assembled draft + evidence/citations | deliver/reject/review/technical route | artifact route | [PRODUCTION] |
| Offline Evaluation | `backend/eval/` | eval/strategy runner | dataset、retrieval/generated outputs | metric/result report | CI/manual analysis | [TEST_ONLY] / offline |
| 测试入口 | `backend/tests/` | pytest discovery | fixtures、env | pass/fail/skip/collection errors | 开发验收 | [TEST_ONLY] [BROKEN] |

## Standalone V1 速查

| 项目 | 文件 | 类/主要方法 | 输入 | 输出 | 下游 | 状态标签 |
|---|---|---|---|---|---|---|
| V1 CLI | `scripts/run_agent_rag_v1_standalone.py` | `main()` | scenario/输出路径 | Standalone run summary | scenario runtime | [STANDALONE] [DEMO] |
| V1 Application | `backend/apps/enterprise_document/runtime/agent_rag_v1/application_service.py` | `MultiAgentApplicationService.run()` | `MultiAgentProjectInputSchema` | `StandaloneRunResultSchema` | RuntimeFactory/orchestrator | [STANDALONE] |
| V1 Factory | `backend/apps/enterprise_document/runtime/agent_rag_v1/runtime_factory.py` | `RuntimeFactory.create_session()` | runtime options | session/components | 四 Agent、gateways、stores | [STANDALONE] [FAKE] |
| V1 Orchestrator | `backend/apps/enterprise_document/runtime/agent_rag_v1/workflow_orchestrator.py` | `WorkflowOrchestrator.run()` | `MultiAgentSharedStateSchema`/session | closed run state/result | Planner/Research/Writer/Verifier | [STANDALONE] |
| Planner | `backend/apps/enterprise_document/agents/agent_rag_v1/planner.py` | `PlannerAgent.run()` | `PlannerInvocationInputSchema` | `PlannerResultPayloadSchema` | state patch/DAG | [STANDALONE] [FAKE] |
| Research | `backend/apps/enterprise_document/agents/agent_rag_v1/research.py` | `ResearchAgent.run()` | `ResearchInvocationInputSchema` | `ResearchResultPayloadSchema` | ToolGateway/Writer | [STANDALONE] [FAKE] |
| Writer | `backend/apps/enterprise_document/agents/agent_rag_v1/writer.py` | `WriterAgent.run()` | `WriterInvocationInputSchema` | `WriterResultPayloadSchema` | Verifier/artifacts | [STANDALONE] [FAKE] |
| Verifier | `backend/apps/enterprise_document/agents/agent_rag_v1/verifier.py` | `VerifierAgent.run()` | `VerifierInvocationInputSchema` | `VerifierResultPayloadSchema` | accept/rewrite/research/replan | [STANDALONE] [FAKE] |
| V1 State | `backend/apps/enterprise_document/schemas/agent_rag_v1/state.py` | `MultiAgentSharedStateSchema` | initialized domains | canonical V1 state | StateGateway/checkpoint | [STANDALONE] |
| State Gateway | `backend/apps/enterprise_document/services/agent_rag_v1/state_gateway.py` | patch/commit methods | expected version、authorized patch | new state/event/checkpoint | orchestrator/recovery | [STANDALONE] |
| Tool Gateway | `backend/apps/enterprise_document/services/agent_rag_v1/tool_gateway.py` | execute | authorized `ToolCallSchema` | idempotent tool result | ResearchAgent | [STANDALONE] [FAKE] |
| V1 Artifact | `backend/apps/enterprise_document/services/agent_rag_v1/artifact_store.py` | `FileArtifactStore.put()/get()` | immutable bytes/payload | hash-addressed ref | state/result/recovery | [STANDALONE] |
| V1 Event | `backend/apps/enterprise_document/services/agent_rag_v1/event_log.py` | append/replay | `DomainEventSchema` | sequenced JSONL | recovery/audit | [STANDALONE] |
| V1 Checkpoint | `backend/apps/enterprise_document/services/agent_rag_v1/checkpoint_store.py` | save/load | full state + event sequence/hash | checkpoint envelope | resume/recovery | [STANDALONE] |
| V1 Recovery | `backend/apps/enterprise_document/services/agent_rag_v1/event_replay_recovery.py` 等 | recover/replay/route | checkpoint + events + running invocations | recovered state/action | orchestrator resume | [STANDALONE] |

## 入口

按用途直接选择：

```text
运行/排查生产主链
→ scripts/run_demo.py:main()
→ MainlineApplicationService.run()
→ SupervisorFactory.build()

排查某个章节
→ SchemeGenerationStageService.execute_sections()
→ SectionExecutionGraph.execute()

排查 query/evidence
→ RAGService.retrieve()

排查索引版本
→ OfflineIndexBuilder build
→ IndexLifecycleManager register/activate/rollback

排查 V1
→ MultiAgentApplicationService.run()
→ WorkflowOrchestrator.run()
```

## 完整调用链

```text
[PRODUCTION]
scripts/run_demo.py:main()
→ scripts/mainline_runtime.py:run_mainline()
→ backend/application/mainline_service.py:MainlineApplicationService.run()
→ backend/bootstrap/supervisor_factory.py:SupervisorFactory.build()
→ backend/agent/supervisor_agent.py:SupervisorAgent.run()
→ top_level_business_graph.py:EnterpriseDocumentWorkflowEngine.execute()
→ stage_service.py:SchemeGenerationStageService.execute_sections()
→ section_execution_graph.py:SectionExecutionGraph.execute()
→ rag_service.py:RAGService.retrieve()
→ model gateway adapter:generate()
→ stage_service.py:assemble_document()/evaluate_document_gate()/persist_output()
→ WorkflowEngineResultSchema
→ AgentResultSchema
```

## Schema 流转

| 链 | 最小 Schema 序列 |
|---|---|
| 生产 | `ProjectInputSchema → TaskSchema → GraphStateSchema → LangGraphRuntimeState → SchemeGenerationGraphState → SectionExecutionGraphPayloadSchema → SchemeWriterOutputSchema → WorkflowEngineResultSchema → AgentResultSchema` |
| 在线 RAG | `RAGToolInputSchema → query/retrieval request schemas → child/parent candidates → Evidence/Citation/Context schemas → RAGToolOutputSchema` |
| 离线 RAG | `cleaned text unit → parent/child chunk schemas → embedding records → manifest/index version/lifecycle records` |
| V1 | `MultiAgentProjectInputSchema → MultiAgentSharedStateSchema → role Invocation → role ResultPayload + StatePatch → DomainEvent + Checkpoint → StandaloneRunResultSchema` |

## 状态修改

- 顶层生产图：candidate state 经 diff/write contract/applier 后提交 `graph_revision`。
- Section 图：节点返回 state update，章节 namespace 单独 checkpoint，父图只汇总已收敛 Section 结果。
- 在线 RAG：主要返回 Evidence/Context，不拥有生产 canonical Graph State。
- 索引生命周期：build 产生版本，activate 才原子更新 active pointer。
- V1：初始化后通过 StateGateway 版本化 patch，并记录 Event/Checkpoint。

## 失败、重试、恢复

| 场景 | 快速定位 |
|---|---|
| 顶层节点 timeout/retry/cancel | `top_level_business_graph.py:_run_stage()` |
| 通用 engine step retry | `langgraph_workflow_engine.py:_execute_step()` |
| Section research-more/rewrite/partial | `section_execution_graph.py` 的 gate/route/finalize |
| RAG corrective retrieval | `backend/rag/judge/`、`backend/rag/plugins/correction_gates/`、`backend/rag/plugins/corrective_query_planners/` 与 `RAGService.retrieve()` |
| 模型 Provider fallback | ModelGateway route/provider registry |
| 索引 reload/rollback | `backend/rag/offline/lifecycle.py` 与在线 resolver |
| 生产 resume/HITL | `EnterpriseDocumentWorkflowEngine.resume()/inspect()` + checkpointer |
| V1 crash recovery | event replay/checkpoint/running invocation recovery services |
| 当前测试收集失败 | `12_TEST_ARCHITECTURE_AND_ACCEPTANCE.md` 的四类阻塞表 |

## 配置来源

```text
backend/core/config.py + backend/rag/config/ + backend/apps/enterprise_document/config/
→ backend/bootstrap/ Factory
→ component/plugin registry
→ runtime constructor
→ method branch
```

最易混淆的配置事实：

- `--fake-runtime` 是 Demo 显式开关，不应由文件名推断。
- 模型 route/provider 的“已配置”不等于 Provider 当前可用。
- 离线 builder 输出版本不等于已经 activate。
- V1 Factory 默认 Scenario/Fixture/Fake，除非调用者显式注入别的 adapter。

## 对应测试

- 生产/图：`test_phase1_langgraph_serializable_state.py`、`test_phase2_top_level_business_graph.py`、`test_phase3_section_execution_subgraph.py`、`test_mainline_closure_acceptance.py`。
- 在线 RAG：`test_langchain_rag_integration.py`、`test_rag_evidence_pipeline.py`、config-driven RAG 系列。
- 离线 RAG：`test_parent_child_generation_runtime.py`、`test_step_11_2_offline_index_verification.py`、`test_step_11_3_index_lifecycle.py`。
- Model/Trace/Context：`test_langchain_model_gateway_integration.py`、`test_model_gateway_lifecycle.py`、`test_step_13_trace_v2.py`、`test_step_14_context_manager.py`。
- V1：10 个 `test_agent_rag_v1_*.py`，本次 84 passed。
- 当前完整执行状态统一以 12 篇为准，不能从本速查表推断全绿。

## 尚未实现

- `NativeWorkflowEngine`、`WorkflowExecutor`、可调用的 `RealRAGTool` 当前不存在。
- V1 没有接入生产 Supervisor、LangGraph、RAGService 或 ModelGateway。
- Offline builder 没有直接 PDF discovery/parser 入口，也不自动 activate。
- 当前全量测试无法无错误收集；真实 Provider 验收本次跳过。

## 常见误解

1. `[PRODUCTION]` 表示进入当前装配链，不表示已经通过本次真实 Provider E2E。
2. `[REAL_PROVIDER]` 表示真实边界代码，不表示本机资源/凭据一定可用。
3. `[INTEGRATED]` 的子服务可能仍由 [LEGACY] facade 复用；标签描述入口角色，不是文件质量评价。
4. LangGraph runtime state、Section state 和 canonical `GraphStateSchema` 不是三个并列 source of truth。
5. V1 的真实文件持久化不能抵消其默认 reasoner/retrieval 是 Fake 的事实。

## 推荐下一篇

首次阅读回到 [README.md](README.md) 并按 [14_READING_ROADMAP.md](14_READING_ROADMAP.md) 展开；定位到具体子系统后，使用本表跳转到 01–13 对应专题。
