# 14 代码阅读路线

## 本文范围

本文把 550 个 Python 文件的阅读压缩为八轮依赖顺序。每轮先读入口与契约，再沿真实调用进入实现和测试；“可以暂时跳过”只表示当前轮不需要展开，不代表文件无用或可删除。

## 当前事实结论

- 最有效的起点是 `scripts/run_demo.py:main()` 与 `backend/bootstrap/supervisor_factory.py:SupervisorFactory.build()`，因为名字为 Demo 的脚本仍是当前本地真实主入口，而 Factory 决定生产实际使用哪套 engine。
- 生产 Agent、在线 RAG、离线索引是三条需要分别阅读再汇合的链；`agent_rag_v1` 应最后对照阅读，避免误认为已接生产。
- Schema 阅读必须区分 canonical business state、LangGraph transport/persistence projection、Section payload 和 V1 Standalone state。
- 测试文件可提供意图与不变量线索，但当前全量 collection 有 30 个错误；阅读顺序不能建立在“测试全绿”的假设上。

## 入口

```text
docs/code_walkthrough/README.md
→ 00_REPOSITORY_MAP.md
→ 本文八轮路线
→ 15_QUICK_REFERENCE.md 反向定位
→ 具体源码与测试
```

如果只处理线上入口问题，从第一轮开始；如果只处理检索问题，可先读第一轮的 Composition Root，再跳第四、第五轮；如果只维护 V1，仍应先完成第一轮和第六轮的状态边界。

## 完整阅读调用链

```mermaid
flowchart LR
  R1["第一轮<br/>入口与装配"] --> R2["第二轮<br/>生产 Agent 主链"]
  R2 --> R3["第三轮<br/>Section 子图"]
  R3 --> R4["第四轮<br/>在线 RAG"]
  R4 --> R5["第五轮<br/>离线索引"]
  R5 --> R6["第六轮<br/>Schema/持久化"]
  R6 --> R7["第七轮<br/>质量/测试"]
  R7 --> R8["第八轮<br/>V1 新旧对比"]
  R8 --> Q["15 速查表<br/>日常反向定位"]
```

这不是运行时调用图，而是阅读依赖图；运行时调用方向见 01–07 篇。

## 第一轮：建立全局结构

目标：知道程序从哪里进、依赖在哪里装配、结果从哪里出。

| 顺序 | 文件 | 方法/位置 | 阅读问题 | 预计关联文件 | 可以暂时跳过 |
|---:|---|---|---|---|---|
| 1 | `docs/code_walkthrough/00_REPOSITORY_MAP.md` | 全文 | 顶层目录和三条主链在哪里？ | 本手册全部专题 | 255 项 Schema 表 |
| 2 | `scripts/run_demo.py` | `main()`、参数解析、`build_project_input()` | Demo 默认是否使用 Fake？CLI 如何形成输入？ | `scripts/mainline_runtime.py` | 诊断/验收脚本 |
| 3 | `scripts/mainline_runtime.py` | `run_mainline()` | 脚本薄封装到哪个 application service？ | `backend/application/mainline_service.py` | 旧 step runner |
| 4 | `backend/application/mainline_service.py` | `MainlineApplicationService.run()` | Task 如何创建、Supervisor 如何调用、结果如何返回？ | task manager、factory | 具体 Section 规则 |
| 5 | `backend/bootstrap/supervisor_factory.py` | `SupervisorFactory.build()` `56-181` | 实际注入哪个 WorkflowEngine、RAG、Model、Trace、Checkpointer？ | bootstrap 目录 | V1 RuntimeFactory |
| 6 | `docs/code_walkthrough/01_PRODUCTION_ENTRY_AND_BOOTSTRAP.md` | 全文 | 对照源码确认 composition root | 02、11 篇 | 离线 eval |

本轮完成标志：能够写出并解释下面的真实链，而不是只说“CLI → Agent”。

```text
scripts/run_demo.py:main()
→ scripts/mainline_runtime.py:run_mainline()
→ backend/application/mainline_service.py:MainlineApplicationService.run()
→ backend/bootstrap/supervisor_factory.py:SupervisorFactory.build()
→ backend/agent/supervisor_agent.py:SupervisorAgent.run()
→ EnterpriseDocumentWorkflowEngine.execute()
```

## 第二轮：生产 Agent 主链

目标：理解 Supervisor、顶层 LangGraph、canonical state、candidate/delta/commit/revision。

| 顺序 | 文件 | 方法/位置 | 阅读问题 | 预计关联文件 | 可以暂时跳过 |
|---:|---|---|---|---|---|
| 1 | `backend/agent/supervisor_agent.py` | `__init__()` `48-91`、`run()` `1044-1274` | workflow 如何选择；何时返回 `retryable_failed`？ | router、lifecycle、trace | stream/resume 细节 |
| 2 | `backend/apps/enterprise_document/workflows/top_level_business_graph.py` | `build_graph()` `1309-1458` | 节点、edge、conditional edge 和 checkpointer 如何接线？ | stage service | 单节点实现细节 |
| 3 | 同上 | `_run_stage()` `974-1141` | timeout、retry、late worker、side effect 如何处理？ | execution runner、write contract | Human-in-the-loop 深入 |
| 4 | 同上 | `_validate_stage()` `621-666` | candidate 如何变成允许提交的 delta？ | differ/applier | RAG 算法 |
| 5 | `backend/agent/runtime/graph_state.py` | `GraphStateSchema` | canonical state 增加了什么 revision 信息？ | `shared_state_schema.py` | V1 state |
| 6 | `backend/agent/runtime/langgraph_state.py` | `LangGraphRuntimeState` | 为什么它是投影而不是第二个 business state？ | codec、checkpointer | 离线索引 |
| 7 | `docs/code_walkthrough/02_AGENT_WORKFLOW_MAINLINE.md` | 全文 | 把节点表、序列图和失败图与源码互证 | 03、09 篇 | 评测 runner |

建议在纸面上跟踪一次 `normalize → plan → retrieve → initialize → sections → assemble → gate → persist → finalize` 的字段变化；不要先钻入 1,700 行 Stage Service。

## 第三轮：单章节链

目标：理解章节级检索、证据门、生成、引用、质量、修订和部分收敛。

| 顺序 | 文件 | 方法/位置 | 阅读问题 | 预计关联文件 | 可以暂时跳过 |
|---:|---|---|---|---|---|
| 1 | `backend/apps/enterprise_document/workflows/section_execution_graph.py` | `build_graph()` `1019-1069` | 子图有多少节点和条件边？ | section schema | 顶层恢复细节 |
| 2 | 同上 | `_prepare_section()`、`_retrieve_section_evidence()`、`_evaluate_evidence_gate()` | Section 输入和首次检索如何形成？ | evidence service | offline builder |
| 3 | 同上 | `_generate_section()`、`_evaluate_generation()` | Writer 输出如何被 gate？ | generation/quality services | Provider loader |
| 4 | 同上 | `_retrieve_recovery()`、`_corrective_retrieval()`、`_finalize_section()` | research-more/rewrite/partial/fail 如何收敛？ | corrective service | 全局 schema 表 |
| 5 | `backend/apps/enterprise_document/services/scheme_writer/stage_service.py` | `execute_sections()` | 父图怎样选择 ready sections、汇总结果？ | dependency scheduler | V1 DAG |
| 6 | `backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py` | `execute()` `201-...` | 手写调度入口与子图共享了哪些服务？ | subservices | Legacy Agent 外层 |
| 7 | `docs/code_walkthrough/03_SECTION_EXECUTION_SUBGRAPH.md` | 全文 | 用典型 Section 序列核对状态、预算、Trace、Citation | 04、10 篇 | 索引激活 |

## 第四轮：RAG 在线链

目标：从业务 query 一直追到父块 Evidence 和压缩 Context，并辨明 Self-RAG/C-RAG/Adaptive-RAG 的真实角色。

| 顺序 | 文件 | 方法/位置 | 阅读问题 | 预计关联文件 | 可以暂时跳过 |
|---:|---|---|---|---|---|
| 1 | `backend/contracts/rag.py` | `RAGServicePort` | 业务层只允许看到什么输入输出？ | RAG schemas | Milvus 写入 |
| 2 | `backend/bootstrap/rag_service_factory.py` | `RAGServiceFactory` | 组件如何从配置/registry 注入？ | component registry | V1 ToolGateway |
| 3 | `backend/rag/services/rag_service.py` | `RAGService.retrieve()` | 真实在线顺序是什么？ | query/retrieval/quality 子包 | Offline parser |
| 4 | `backend/rag/retrieval/` | dense、BM25、hybrid、parent-child 实现 | child 结果如何融合，parent 如何补全？ | index resolver | LLM provider 细节 |
| 5 | `backend/rag/query/` 与插件 registry | transform/router | 为什么一次计划只选择一个 transform？ | RAG settings | Agent graph retry |
| 6 | `backend/rag/judge/`、`backend/rag/plugins/correction_gates/`、`backend/rag/plugins/corrective_query_planners/` | judge/corrective | RAG judge、C-RAG 何时判断和修复；缺失的 Self-RAG 导入影响什么？ | evidence contract | 文档 assembler |
| 7 | `backend/rag/context/` | context packer/manager | token/证据顺序如何受控？ | citation lineage | V1 closure |
| 8 | `docs/code_walkthrough/04_RAG_ONLINE_RETRIEVAL.md` | 全文 | 沿 Sequence Diagram 复核调用方向 | 05、06 篇 | eval 历史数据 |

本轮须牢记当前 [BROKEN] 导入：`SelfRAGJudge`、`ParentChildChunkResult`、`RETRIEVAL_RESULT_TEMPLATE` 会阻塞多组测试；静态接线不等于当前全链可成功导入执行。

## 第五轮：RAG 离线链

目标：理解当前 builder 的真实起点、parent/child、manifest/version、activate/reload/rollback。

| 顺序 | 文件 | 方法/位置 | 阅读问题 | 预计关联文件 | 可以暂时跳过 |
|---:|---|---|---|---|---|
| 1 | `backend/rag/offline/builder.py` | `OfflineIndexBuilder`/build 入口 | 输入为什么是 cleaned-text-unit JSONL，而不是 PDF？ | offline contracts | 在线 corrective |
| 2 | `backend/rag/chunker/FixedSizeChunker.py`、`backend/rag/chunker/fixed_chunker.py`、`backend/rag/chunker/fixed_size_core.py` | chunk 方法 | parent/child ID、metadata、lineage 如何产生？ | schemas | Agent gate |
| 3 | `backend/rag/offline/` embedding/index writer | build/write | embedding 与 Milvus collection 如何绑定版本？ | provider/config | V1 agents |
| 4 | `backend/rag/offline/manifest.py`、`verification.py` | manifest/verify | fingerprint 和 drift 检测验证什么？ | lifecycle | Prompt 模板 |
| 5 | `backend/rag/offline/lifecycle.py` | register/activate/rollback | active pointer 如何原子切换、失败如何归档？ | index resolver | Legacy Agent |
| 6 | 在线 resolver/reload 实现 | resolve/reload | 新索引怎样被在线 engine 发现；失败为何保留旧 engine？ | RAG factory | 质量指标 |
| 7 | `docs/code_walkthrough/05_RAG_OFFLINE_INDEXING.md` | 全文 | 对照三张图理解 build 与 activate 是两个动作 | 11 篇 | 255 项 Schema 表 |

不要从“文件发现 → PDF parser”模板反推代码：当前 `OfflineIndexBuilder` 没有直接发现 PDF，也没有把 parser 接入 builder；上游必须先生成 cleaned-text-unit JSONL。

## 第六轮：Schema 和持久化

目标：理解状态、Payload、Artifact、Event、Checkpoint 与 Trace 的边界。

| 顺序 | 文件 | 方法/位置 | 阅读问题 | 预计关联文件 | 可以暂时跳过 |
|---:|---|---|---|---|---|
| 1 | `docs/code_walkthrough/08_SCHEMA_AND_STATE_CATALOG.md` | 领域总表和 class diagram | 同名 Schema 哪个 canonical、哪个 projection？ | 各 schema 包 | Test-only 长尾先跳过 |
| 2 | `backend/agent/runtime/shared_state_schema.py`、`graph_state.py` | State 定义 | production state 的 domain 边界是什么？ | differ/applier | V1 writer pipeline |
| 3 | `backend/agent/runtime/langgraph_state.py`、codec | encode/decode | persistence projection 如何承载 business state？ | checkpointer | Offline eval |
| 4 | Artifact/Task/Event 实现 | put/append/replay | 哈希、序号、原子写入和幂等在哪里？ | mainline service | Reranker 算法 |
| 5 | `backend/apps/enterprise_document/schemas/agent_rag_v1/state.py` | `MultiAgentSharedStateSchema` | V1 state 为什么不能与 production state 混用？ | StateGateway | Legacy tests |
| 6 | V1 artifact/event/checkpoint services | save/append/load | full checkpoint 与 event replay 怎样协作？ | recovery services | Model prompts |
| 7 | `docs/code_walkthrough/09_ARTIFACT_EVENT_CHECKPOINT_TRACE.md` | 全文 | 四条链哪些共享实现，哪些完全独立？ | 07、13 篇 | Config 长表 |

## 第七轮：质量与测试

目标：知道系统如何声明、检查和证明正确，并理解当前证据缺口。

| 顺序 | 文件 | 方法/位置 | 阅读问题 | 预计关联文件 | 可以暂时跳过 |
|---:|---|---|---|---|---|
| 1 | `docs/code_walkthrough/10_QUALITY_GATE_AND_EVALUATION.md` | Gate 表 | 哪些是 hard runtime gate，哪些只是 advisory/offline eval？ | section/document gate | V1 recovery |
| 2 | RAG quality 与 SchemeWriter quality 服务 | judge/evaluate | deterministic 与 model judge 如何组合？ | ModelGateway | lifecycle lock |
| 3 | `backend/eval/` | runner/metrics | precision、recall proxy、faithfulness proxy 等在代码中如何算？ | datasets/config | CLI 格式化 |
| 4 | `docs/code_walkthrough/12_TEST_ARCHITECTURE_AND_ACCEPTANCE.md` | 结果表和 68 文件表 | 哪些结论实际跑过；哪些只静态可见？ | `backend/tests/` | 先不通读每个断言 |
| 5 | 失败的四类导入点 | import/export | 收集为何在运行前停止？ | RAG package exports | 非相关 docs |
| 6 | 可收集核心测试 | 失败断言链 | 固定分块 `doc` 参数问题发生在哪里？ | offline chunker | Provider 真调用 |

先看测试模块的 imports 与 fixtures，再看测试名；避免把 Fake adapter 测试写成真实 Provider E2E。

## 第八轮：新 agent_rag_v1

目标：在已理解生产边界后，阅读 V1 的四 Agent、状态事务和恢复，并逐项比较而非混合。

| 顺序 | 文件 | 方法/位置 | 阅读问题 | 预计关联文件 | 可以暂时跳过 |
|---:|---|---|---|---|---|
| 1 | `scripts/run_agent_rag_v1_standalone.py`、`scenario_runtime.py` | main/run_scenario | 独立入口怎样进入 RuntimeFactory？ | contracts | 生产 CLI 参数 |
| 2 | `runtime/agent_rag_v1/runtime_factory.py` | `RuntimeFactory.create_session()` | 哪些基础设施真实写文件，哪些 Provider 是 Fake？ | standalone adapters | Milvus lifecycle |
| 3 | `runtime/agent_rag_v1/application_service.py` | `run()`、`_initialize_state()` | immutable input artifacts 和 first checkpoint 如何创建？ | supervisor state adapter | 顶层 LangGraph internals |
| 4 | `runtime/agent_rag_v1/workflow_orchestrator.py` | `run()`、section DAG、closure | 四 Agent 如何被编排，何时 research-more/rewrite/replan？ | router/scheduler | Legacy SchemeWriter |
| 5 | `agents/agent_rag_v1/` | 各 `run()` | 强输入、强输出、授权工具和 patch target 如何限制角色？ | ports | 在线 query transformer |
| 6 | `services/agent_rag_v1/state_gateway.py` | mutation methods | 版本、event、checkpoint 的提交顺序是什么？ | recovery services | production reducer |
| 7 | `docs/code_walkthrough/07_AGENT_RAG_V1_STANDALONE.md` | 全文 | 逐项核对 [FAKE]、[STANDALONE]、[IMPLEMENTED_ONLY] | 13 篇 | 旧阶段验收脚本 |
| 8 | `docs/code_walkthrough/13_CURRENT_ARCHITECTURE_RELATIONSHIPS.md` | 全文 | V1 与生产的连接点为什么仍是“尚未实现”？ | 15 篇 | 全量 Schema 明细 |

## Schema 流转检查法

每读一条链，用同一张记录表，避免只追方法名：

| 问题 | 应记录内容 |
|---|---|
| 输入是谁创建的？ | Schema 名、创建方法、默认值来源 |
| 谁拥有 canonical state？ | business state 类型，不把 transport projection 当 source of truth |
| 方法返回什么？ | 完整对象、State Delta、ArtifactRef 还是 Event |
| 谁能修改？ | write contract / StateGateway domain / reducer |
| 如何持久化？ | JSON/JSONL/SQLite/Milvus，hash/sequence/revision |
| 下游消费谁？ | 实际 import/实例化/调用位置 |
| 失败保留什么？ | candidate 是否丢弃，是否留 execution record/event/checkpoint |

## 状态修改

阅读时按四种 mutation 上色最容易发现边界：

1. 生产 canonical mutation：candidate → diff → write-contract → apply → revision。
2. LangGraph runtime update：节点返回的序列化 state/payload/execution record。
3. 外部副作用：Artifact、Task event、side-effect ledger、Trace、index active pointer。
4. V1 transaction：StateGateway patch → Domain Event → checkpoint。

不能因为它们都写 JSON 或都有 `revision` 字段就认为是同一事务。

## 失败、重试、恢复

- 第一到第三轮要分别追踪顶层 stage retry、Section corrective/rewrite、document hard gate；它们不是同一个 retry counter。
- 第四、第五轮区分 corrective retrieval、Provider fallback、index reload rollback；三者解决不同层级的问题。
- 第六轮区分 LangGraph checkpointer、Task event replay 和 V1 full checkpoint/event replay。
- 第七轮先确认测试是否完成 collection，再解释断言结果。
- 第八轮确认 V1 的 late/stale result 与生产 late worker ledger 只是相似能力，不是共享实现。

## 配置来源

阅读任何默认值时，按下面顺序验证：

```text
Schema / Settings 定义
→ environment/YAML/JSON loader
→ validation
→ Factory/Registry
→ 构造参数
→ 运行时分支
```

如果只看到配置字段而没有消费调用，应标记 [SCHEMA_ONLY] 或 [IMPLEMENTED_ONLY]；如果脚本默认 `--fake-runtime` 为 false，应以参数默认值和分支代码为准，而不是以 `run_demo.py` 文件名推断。

## 对应测试

每轮至少关联一组测试：

| 阅读轮次 | 首选测试 |
|---|---|
| 1–2 | `test_mainline_closure_acceptance.py`、`test_phase2_top_level_business_graph.py`、`test_phase1_langgraph_serializable_state.py` |
| 3 | `test_phase3_section_execution_subgraph.py`、`test_section_execution_contracts.py` |
| 4 | `test_langchain_rag_integration.py`、`test_rag_evidence_pipeline.py`、config-driven RAG 系列 |
| 5 | `test_parent_child_generation_runtime.py`、`test_step_11_2_offline_index_verification.py`、`test_step_11_3_index_lifecycle.py` |
| 6 | `test_side_effect_state_machine_closure.py`、`test_step_13_trace_v2.py`、V1 infrastructure/recovery |
| 7 | `test_stage1_acceptance.py`、评测测试、真实 Provider 验收 |
| 8 | 10 个 `test_agent_rag_v1_*.py` |

当前可执行状态以 12 篇 2026-07-29 的实跑结果为准；测试名只是导航。

## 尚未实现或证据不足时如何阅读

- 找不到 `NativeWorkflowEngine`、`WorkflowExecutor`、`RealRAGTool` 定义时停止追踪，并记录“不存在当前实现”，不要用相似类补位。
- `OfflineIndexBuilder` 没有 PDF discovery/parser 调用时，应把 builder 起点写成 cleaned-text-unit JSONL。
- V1 ports 没有生产 adapter 时，只读其协议和 Scenario adapter，不画到生产 RAG/Model 的实线。
- 真实 Provider 本次测试被跳过；相关可用性结论必须保持条件式。

## 常见误解

1. 从 `README` 或历史 Phase 测试开始，容易把目标状态当当前调用。
2. 先读 1,700 行 Stage Service 而不读 Factory，会看不出哪些入口真的被装配。
3. 只看类名会把 `SupervisorStateAdapter`、`RealRAGTool` 字符串和真正 runtime 混在一起。
4. 只看 happy path 会漏掉 candidate 不提交、late worker、防重 ledger 和 rollback。
5. 逐个读 255 个 Schema 而不先分 canonical/projection/transport，会失去业务边界。

## 推荐下一篇

把 [15_QUICK_REFERENCE.md](15_QUICK_REFERENCE.md) 作为日常入口；若尚未完成第一轮，则返回 [01_PRODUCTION_ENTRY_AND_BOOTSTRAP.md](01_PRODUCTION_ENTRY_AND_BOOTSTRAP.md) 从真实 Composition Root 开始。
