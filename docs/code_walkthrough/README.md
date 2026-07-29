# rag-agent 代码阅读手册

> 证据基线：`96ec3228cc9b523aabe9b21da01347274fbaf162`（文档编写前的 `HEAD`）。  
> 核对日期：2026-07-29。结论来自当前仓库的定义、导入、实例化、调用和测试；历史文档只作为定位线索。

## 本文范围

这套手册解释整个 `rag-agent`，重点是三条可闭合的主链：

1. [PRODUCTION] Agent/Workflow：命令行输入 → `ProjectInput` → `MainlineApplicationService` → `SupervisorAgent` → 顶层 LangGraph → Section 子图 →文档门 → 结果与持久化。
2. [REAL_PROVIDER] 在线 RAG：业务检索请求 → 自适应查询变换 → 稀疏/稠密混合检索 → Parent-Child 恢复 → 重排 → CRAG 评估/纠正 → Context Gate → `EvidenceBundleSchema`。
3. [IMPLEMENTED_ONLY] 离线索引：cleaned-text-unit JSONL → Parent/Child Chunk → Embedding → Milvus Lite/文件产物 → Manifest → 生命周期注册、验证、激活、回滚 → 在线 Resolver/Reload。

另有一条完整但隔离的 [STANDALONE] `agent_rag_v1`：四个角色、字段级 State Gateway、事件日志、artifact、checkpoint、恢复和文档闭环都已实现；默认外部适配器是 [FAKE] Scenario/Fixture，并未连接生产 Supervisor、LangGraph、Model Gateway 或 RAG。

## 当前事实结论

- [PRODUCTION] 本地主入口是 `scripts/run_demo.py:892-1161`。文件名是 Demo，但 CLI 默认选择真实 RAG 与本地 Qwen；`--fake-runtime` 才切换 fake。
- [PRODUCTION] `scripts/mainline_runtime.py:183-226` 是兼容 façade，真正的应用编排在 `backend/application/mainline_service.py:57-254`。
- [PRODUCTION] `SupervisorFactory.build()` 注入的是 `EnterpriseDocumentWorkflowEngine`，不是通用 `LangGraphWorkflowEngine`；证据在 `backend/bootstrap/supervisor_factory.py:56-175`。
- [PRODUCTION] 顶层和单章节都由 LangGraph 接管。`SectionExecutionCoordinator.execute()` 仍保留为兼容回退，但生产 Factory 构造的 Stage Service 会挂接 `SectionExecutionGraph`。
- [BROKEN] 当前生产主链在导入阶段被缺失的 `SelfRAGJudge` 阻塞；全量测试收集还暴露 `ParentChildChunkResult`、`RETRIEVAL_RESULT_TEMPLATE` 和一条循环导入。
- [STANDALONE] `agent_rag_v1` 的 10 个专项文件实跑 `84 passed`，但这证明的是 Scenario/Fixture 垂直闭环，不证明真实模型、真实检索或生产接入。

## 核心子系统与入口

| 子系统 | 事实入口 | 状态 | 详见 |
|---|---|---|---|
| 应用入口与装配 | `scripts/run_demo.py:892-1161`、`MainlineApplicationService.run()` | [PRODUCTION] [BROKEN] | [01](01_PRODUCTION_ENTRY_AND_BOOTSTRAP.md) |
| Supervisor/Workflow | `SupervisorAgent.run()`、`EnterpriseDocumentWorkflowEngine.execute()` | [PRODUCTION] [INTEGRATED] | [02](02_AGENT_WORKFLOW_MAINLINE.md) |
| Section 子图 | `SectionExecutionGraph.execute()` | [PRODUCTION] [INTEGRATED] | [03](03_SECTION_EXECUTION_SUBGRAPH.md) |
| 在线 RAG | `RAGService.retrieve()`、`RetrievalRuntime.retrieve()` | [REAL_PROVIDER] [BROKEN] | [04](04_RAG_ONLINE_RETRIEVAL.md) |
| 离线索引 | `OfflineIndexBuilder.build()`、`IndexLifecycleManager.activate()` | [IMPLEMENTED_ONLY] [BROKEN] | [05](05_RAG_OFFLINE_INDEXING.md) |
| 模型运行时 | `ModelCallBoundary.generate_response()`、`ModelGateway.generate()` | [REAL_PROVIDER] [INTEGRATED] | [06](06_MODEL_GATEWAY_AND_LLM_RUNTIME.md) |
| Multi-Agent V1 | `MultiAgentApplicationService.run()` | [STANDALONE] [FAKE] | [07](07_AGENT_RAG_V1_STANDALONE.md) |
| 数据契约 | 255 个生产代码核心 Schema/State/Result 类 | 混合状态 | [08](08_SCHEMA_AND_STATE_CATALOG.md) |
| 持久化/可观测性 | `RunWorkspace` 与两套持久化实现 | [INTEGRATED] | [09](09_ARTIFACT_EVENT_CHECKPOINT_TRACE.md) |
| 质量与评测 | 文档硬门、生成插件、RAG/Agent Eval | [BROKEN] | [10](10_QUALITY_GATE_AND_EVALUATION.md) |
| 配置/注册表 | `AppSettings`、YAML、Component Registry | [INTEGRATED] | [11](11_CONFIGURATION_AND_REGISTRY.md) |
| 测试 | `backend/tests/` 68 个测试文件 | 部分可执行 | [12](12_TEST_ARCHITECTURE_AND_ACCEPTANCE.md) |

## 推荐阅读顺序

第一次阅读建议按 `[00] → [01] → [02] → [03] → [04] → [05] → [08] → [09] → [10] → [12] → [07] → [13]`。如果只查一个符号，从 [15_QUICK_REFERENCE.md](15_QUICK_REFERENCE.md) 开始；如果要系统带读，从 [14_READING_ROADMAP.md](14_READING_ROADMAP.md) 开始。

三条核心链对应：

- Agent/Workflow：[02_AGENT_WORKFLOW_MAINLINE.md](02_AGENT_WORKFLOW_MAINLINE.md)
- 在线 RAG：[04_RAG_ONLINE_RETRIEVAL.md](04_RAG_ONLINE_RETRIEVAL.md)
- 离线索引：[05_RAG_OFFLINE_INDEXING.md](05_RAG_OFFLINE_INDEXING.md)

## 如何反向查找

- 已知文件：先在 [00](00_REPOSITORY_MAP.md) 查职责和上/下游，再到专题看调用链。
- 已知类或方法：在 [15](15_QUICK_REFERENCE.md) 查入口/输入/输出；行号均指向证据基线。
- 已知 Schema：在 [08](08_SCHEMA_AND_STATE_CATALOG.md) 按领域、Canonical/Projection/Persistence 分类。
- 已知测试：在 [12](12_TEST_ARCHITECTURE_AND_ACCEPTANCE.md) 查它能证明和不能证明什么。
- 已知一次运行产物：在 [09](09_ARTIFACT_EVENT_CHECKPOINT_TRACE.md) 按文件名反查写入者与恢复语义。

## 状态标签

| 标签 | 精确定义 |
|---|---|
| `[PRODUCTION]` | 当前生产/本地主入口的真实装配会调用 |
| `[DEMO]` | Demo、验收或本地演示入口 |
| `[STANDALONE]` | 与生产主链隔离的独立运行时 |
| `[REAL_PROVIDER]` | 真实模型、检索、Milvus 或外部服务适配器 |
| `[FAKE]` | Fake、Fixture、Scenario 或 Stub |
| `[INTEGRATED]` | 已由当前真实组合根接入 |
| `[IMPLEMENTED_ONLY]` | 有实现和/或测试，但当前生产组合根不调用 |
| `[SCHEMA_ONLY]` | 只有契约/协议，未找到执行消费者 |
| `[LEGACY]` | 历史兼容路径，仍可能被回退或测试调用 |
| `[TEST_ONLY]` | 只找到测试调用 |
| `[BROKEN]` | 当前提交下已复现无法导入、收集或执行 |

标签可叠加。例如 `[STANDALONE] [FAKE]` 表示运行时基础设施真实执行，但默认外部业务能力来自 Scenario；`[REAL_PROVIDER] [BROKEN]` 表示真实适配器代码存在，但当前导入缺陷阻止链路启动。

## 入口、调用链与 Schema 流转

总代码链：

```text
scripts/run_demo.py:main()
→ scripts/mainline_runtime.py:run_mainline()
→ backend/application/mainline_service.py:MainlineApplicationService.run()
→ backend/bootstrap/supervisor_factory.py:SupervisorFactory.build()
→ backend/agent/supervisor_agent.py:SupervisorAgent.run()
→ backend/apps/enterprise_document/workflows/top_level_business_graph.py:EnterpriseDocumentWorkflowEngine.execute()
→ backend/apps/enterprise_document/services/scheme_writer/stage_service.py:SchemeGenerationStageService.execute_sections()
→ backend/apps/enterprise_document/workflows/section_execution_graph.py:SectionExecutionGraph.execute()
→ backend/apps/enterprise_document/services/document_gate.py:evaluate_scheme_draft()
→ WorkflowEngineResult → AgentResultSchema → mainline summary
```

主要 Schema 流转是 `dict/CLI → ProjectInputSchema → TaskSchema → GraphStateSchema + LangGraphRuntimeState → SchemeWriterInput/SectionExecutionGraphPayload → EvidenceBundle/Citation → SchemeWriterOutput → WorkflowEngineResult → AgentResultSchema → summary dict`。每层转换都在专题中列出，不把 transport dict 当成 Canonical State。

## 状态修改

生产链的业务状态修改先在深拷贝 candidate 上执行，由 `GraphStateDiffer` 生成 delta、`GraphStateWriteContract` 校验允许写路径、`GraphStateApplier` 提交并增加 `graph_revision`。LangGraph 的 runtime payload 是可序列化投影；业务真相仍是 `GraphStateSchema`。Standalone 则通过 `StatePatchRequestSchema` 和按域版本的 `StateGateway.commit()` 修改 `MultiAgentSharedStateSchema`，两套状态不能混用。

## 失败、重试与恢复

- 当前最早的生产失败发生在 import：`quality/plugins.py` 导入不存在的 `SelfRAGJudge`。
- 进入运行时后，生产节点有 timeout、retryability 分类、活动 worker 保护、side-effect claim 和 SQLite LangGraph checkpoint。
- Standalone 每个字段级 patch 先追加 Domain Event，再发布 candidate state，随后保存完整 checkpoint；可从最新 checkpoint 回放连续事件。
- 失败状态、重试路径、late result 和取消语义详见 [02](02_AGENT_WORKFLOW_MAINLINE.md)、[07](07_AGENT_RAG_V1_STANDALONE.md)、[09](09_ARTIFACT_EVENT_CHECKPOINT_TRACE.md)。

## 配置来源

进程环境变量优先于仓库 `.env`，再落到代码默认值；RAG 组件由 YAML 静态规格和 active index Manifest/Pointer 决定；Standalone 主要由 `MultiAgentProjectInputSchema` 与 Scenario 决定。不要把 `.env.example` 的示例本地路径当成部署事实。

## 对应测试

2026-07-29 实跑摘要：

- V1 专项：`84 passed in 4.44s`。
- 可执行非 Provider 核心集合：`234 passed, 1 failed in 20.59s`。
- 真实 Provider：`1 skipped`，需显式环境变量和凭据。
- 全量 `--collect-only`：`491 tests collected, 30 errors`。

命令、文件级分类和 30 个错误的逐项列表在 [12](12_TEST_ARCHITECTURE_AND_ACCEPTANCE.md)。

## 尚未实现或无法确认

- 没有名为 `RealRAGTool`、`NativeWorkflowEngine`、`WorkflowExecutor` 的当前实现；文档不会用这些名称虚构调用。
- Standalone 到生产 `ModelGateway`/RAG/Supervisor/LangGraph 的连接尚未实现。
- 离线 Builder 的输入已经是 cleaned-text-unit JSONL；当前链中没有“原始 PDF 发现/解析 → Builder”的接线证据。
- 因生产与 RAG 导入失败，真实端到端启动和真实索引切换未在本次文档任务中成功执行。

## 常见误解

1. `run_demo.py` 叫 Demo，不等于只跑 Fake；默认配置覆盖为 real。
2. `LangGraphWorkflowEngine` 有大量测试，不等于它是生产注入的顶层引擎。
3. `SectionExecutionCoordinator` 仍被构造，不等于其手写 `execute()` 是生产路径；新子图复用它持有的服务。
4. RAG 有 `FakeRAGTool`，但不存在当前 `RealRAGTool` 类；真实边界是 `RAGService`。
5. `agent_rag_v1` 的 E2E 全绿，不等于已接入生产 Provider。

## 推荐下一篇

继续阅读 [00_REPOSITORY_MAP.md](00_REPOSITORY_MAP.md)，先建立目录、组合根与新旧实现的全景关系。
