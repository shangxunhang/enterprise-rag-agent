# 12 测试架构与验收现状

## 本文范围

本文以 2026-07-29 的实际收集和执行结果为准，覆盖 `backend/tests/` 下全部 68 个 `test_*.py` 文件，区分单元、契约、组件集成、主链验收、Standalone E2E、恢复、真实 Provider、离线索引、在线检索、架构守卫、安全/持久化、评测和兼容性测试。测试名称只能说明意图；“可证明什么”以下面的实际依赖和本次执行结果为边界。

## 当前事实结论

- [TEST_ONLY] `agent_rag_v1` 专项集合本次为 **84 passed**；它证明 Standalone 协议、四 Agent 编排、文件基础设施、恢复与闭环在 Fake/Fixture/Scenario 环境工作，不证明生产 Supervisor、LangGraph、RAG 或真实模型已接入。
- [TEST_ONLY] 一组避开已知导入阻塞的核心非 Provider 测试本次为 **234 passed, 1 failed**。唯一失败是固定分块器调用 `build_chunk(doc=...)`，而目标函数不接受 `doc` 关键字。
- [BROKEN] 全量 `pytest --collect-only` 收集到 **491 tests**，同时出现 **30 个 collection errors**：17 个直接报 `SelfRAGJudge`，11 个直接报 `ParentChildChunkResult`，1 个报 `RETRIEVAL_RESULT_TEMPLATE`，1 个报循环导入。
- [REAL_PROVIDER] 真实 Provider 验收文件可单独收集 1 项；实际执行为 **1 skipped**，因为未设置 `REAL_PROVIDER_ACCEPTANCE=1` 及模型凭据。
- [BROKEN] 全量收集的 30 个错误不是所有潜在受影响文件的闭包：部分文件单独或以不同导入顺序收集时也会暴露 `SelfRAGJudge` 导入错误。因此不能把“491 已收集”解释为“其余文件全部可稳定收集”。

## 入口

```text
python -m pytest
→ pytest.ini:pythonpath/testpaths/addopts
→ pytest 对 backend/tests/test_*.py 执行模块导入
→ 各测试函数 / fixture
→ backend 下的应用、Bootstrap、Agent、RAG、模型、持久化与评测对象
```

`pytest.ini:1-13` 把 `backend`、`scripts` 加入 `pythonpath`，将 `backend/tests` 设为 `testpaths`，并声明五类 marker。仓库没有自有 conftest 模块；测试层级由文件自己的导入、fixture、marker 和断言决定。

## 完整调用链

### Standalone 专项

```text
backend/tests/test_agent_rag_v1_standalone_e2e.py
→ backend/apps/enterprise_document/runtime/agent_rag_v1/runtime_factory.py:RuntimeFactory.create_session()
→ backend/apps/enterprise_document/runtime/agent_rag_v1/application_service.py:MultiAgentApplicationService.run()
→ backend/apps/enterprise_document/runtime/agent_rag_v1/workflow_orchestrator.py:WorkflowOrchestrator.run()
→ PlannerAgent / ResearchAgent / WriterAgent / VerifierAgent
→ StateGateway + ArtifactStore + EventLog + CheckpointStore
→ MultiAgentRunResult
```

该链使用确定性 Scenario/Fake 端口，验证的是 [STANDALONE] 运行时，不是 [PRODUCTION] 主链。

### 核心非 Provider 集合

```text
pytest 参数中的 11 个测试文件
→ LangChainModelGatewayAdapter / LangChainRAGToolAdapter
→ Mainline / Graph state / Trace V2 / ContextManager / Provider protocol
→ 断言结构、生命周期、序列化和副作用闭环
```

这是按“能在当前导入状态下收集”的证据集合，并非完整生产验收套件。失败链为：

```text
backend/tests/test_decoupled_architecture.py:test_fixed_chunkers_share_algorithm_but_preserve_legacy_metadata()
→ backend/rag/chunker/FixedSizeChunker.py:FixedSizeChunker.chunk_document()
→ backend/rag/chunker/fixed_size_core.py:chunk_fixed_size()
→ backend/rag/schema/Chunk_Schema.py:build_chunk(doc=...)
→ TypeError: build_chunk() got an unexpected keyword argument 'doc'
```

### 全量收集失败链

```text
pytest collector
→ import test module
→ import backend.rag / backend.agent / backend.eval 相关模块
→ 缺失导出或循环导入
→ 测试函数尚未执行，collection error
```

## 全部测试文件目录

层级是对实际断言范围的简写；“真实”只表示真实仓库组件，不等于真实外部 Provider。

| 文件 | 测试层级 | 被测链路 | Fake / 真实组件 | 可证明什么 | 不能证明什么 |
|---|---|---|---|---|---|
| `stage1/test_advisory_policies.py` | policy unit | 建议策略 | 真实策略 + fixture | 策略决策契约 | [BROKEN] 当前全量不可收集；不证明主链 |
| `stage1/test_contracts.py` | contract | Stage 1 契约 | 真实 Schema | 契约形状与导入边界 | [BROKEN] 当前循环导入；不证明执行 |
| `stage1/test_generation_recovery.py` | recovery component | 生成恢复 | 真实组件 + fake | 恢复分支行为 | 不证明进程级崩溃恢复 |
| `stage1/test_workflow_integrity.py` | architecture guard | 工作流完整性 | 真实模块 | 静态/组件约束 | 不证明端到端产物质量 |
| `test_adaptive_profile_router.py` | unit | Adaptive 路由 | 真实 router + fixture | profile 选择规则 | [BROKEN] 当前全量不可收集 |
| `test_adversarial_closure_followup.py` | acceptance | 对抗闭环 | 真实组件 + fake | 已编码闭环反例 | [BROKEN] 当前全量不可收集 |
| `test_agent_rag_v1_agents.py` | unit/component | V1 四 Agent | Scenario/Fake | Agent 输入输出和职责 | 不证明真实模型/RAG |
| `test_agent_rag_v1_contracts.py` | contract | V1 Schema/Ports | 真实 Schema | 契约校验与不变量 | 不证明编排执行 |
| `test_agent_rag_v1_document_closure.py` | component | V1 文档闭环 | Scenario/Fake + 真存储 | 文档收敛条件 | 不证明生产文档 Gate |
| `test_agent_rag_v1_infrastructure.py` | component | V1 基础设施 | 真实文件实现 | Artifact/Event/Checkpoint I/O | 不证明跨进程并发 |
| `test_agent_rag_v1_recovery.py` | recovery unit | V1 恢复 | Fake + 真文件组件 | 恢复决策和幂等 | 不证明操作系统故障 |
| `test_agent_rag_v1_recovery_e2e.py` | recovery E2E | V1 中断恢复 | Scenario/Fake + 真文件组件 | 独立运行时恢复闭环 | 不证明生产 LangGraph resume |
| `test_agent_rag_v1_runtime_contracts.py` | contract | V1 runtime ports | Fake/Fixture | Adapter 边界 | 不证明 Provider 可用 |
| `test_agent_rag_v1_standalone_e2e.py` | standalone E2E | V1 全链 | Scenario/Fake + 真文件组件 | V1 可独立运行 | 不证明已接生产 |
| `test_agent_rag_v1_workflow.py` | component | V1 orchestrator | Scenario/Fake | 状态迁移、路由、预算 | 不证明 LangGraph |
| `test_agent_rag_v1_writer_claims.py` | unit/component | V1 claim/citation | Scenario/Fake | Writer claim 契约 | 不证明真实检索忠实度 |
| `test_architecture_hardening.py` | architecture guard | 总体架构 | 真实模块 | 已编码架构约束 | [BROKEN] 当前全量不可收集 |
| `test_citation_binding_regression.py` | regression | Citation 绑定 | 真实组件 + fixture | 引用绑定回归 | 不证明来源事实正确 |
| `test_controlled_architecture_completion.py` | acceptance | 受控架构闭环 | 真实组件 + fake | 指定闭环不变量 | [BROKEN] 当前全量不可收集 |
| `test_day2_architecture_closure.py` | acceptance | Day-2 架构闭环 | 真实组件 + fake | 兼容与闭环约束 | [BROKEN] 当前全量不可收集 |
| `test_decoupled_architecture.py` | architecture/component | 解耦与分块 | 真实组件 | 端口边界；当前还暴露分块参数缺陷 | 不证明完整 RAG；本次 1 项失败 |
| `test_document_assembler.py` | component | 文档装配 | 真实 assembler + fixture | 章节合并规则 | 导入顺序下可受 `SelfRAGJudge` 阻塞 |
| `test_grounded_generation_budget.py` | component | grounded 生成预算 | 真实策略 + fake | 预算耗尽语义 | 不证明模型 token 计费 |
| `test_langchain_model_gateway_integration.py` | component integration | LangChain 模型适配 | fake provider + 真 adapter | Gateway 映射和失败语义 | 不证明真实模型 |
| `test_langchain_rag_integration.py` | component integration | LangChain RAG 适配 | fake retriever + 真 adapter | Tool 契约映射 | 不证明 Milvus |
| `test_mainline_closure_acceptance.py` | mainline acceptance | 生产闭环 | 真实编排 + fake 边界 | 主链收敛契约 | [BROKEN] 当前全量不可收集 |
| `test_model_gateway_lifecycle.py` | component | 模型网关生命周期 | fake provider + 真 gateway | 关闭、复用、错误状态 | 不证明远端可达 |
| `test_model_gateway_multi_model_runtime.py` | component | 多模型路由 | fake provider | route/fallback 语义 | [BROKEN] `ParentChildChunkResult` |
| `test_model_usage_artifact.py` | component | Model usage artifact | fake model + 真 artifact | usage 记录链 | [BROKEN] `SelfRAGJudge` |
| `test_p1_high_risk_fact_and_citation_integrity.py` | security/quality | 高风险事实/引用 | 真实 Gate + fixture | hard gate 规则 | 导入顺序下可受 `SelfRAGJudge` 阻塞 |
| `test_p1_security_durability_identity_closure.py` | security/durability | 身份、耐久性 | 真实组件 + fake | 安全/持久化不变量 | [BROKEN] `ParentChildChunkResult` |
| `test_p1_trust_semantic_payload_closure.py` | quality/security | 语义 payload | 真实组件 + fake | trust/semantic 契约 | [BROKEN] `SelfRAGJudge` |
| `test_parent_child_generation_runtime.py` | component | Parent-child 生成 | 真实 runtime + fixture | 父子块生成行为 | 不证明离线完整建库 |
| `test_phase1_langgraph_serializable_state.py` | architecture guard | LangGraph State | 真实 state | State 可序列化 | 不证明 checkpoint 后端耐久性 |
| `test_phase2_runtime_safety_closure.py` | acceptance | runtime 安全 | 真实组件 + fake | 超时/取消等约束 | [BROKEN] `SelfRAGJudge` |
| `test_phase2_top_level_business_graph.py` | component/acceptance | 顶层业务图 | 真图 + fake 节点依赖 | 节点与路由 | [BROKEN] `SelfRAGJudge` |
| `test_phase3_section_execution_subgraph.py` | component/acceptance | Section 子图 | 真图 + fake 依赖 | 章节拓扑与路由 | [BROKEN] `SelfRAGJudge` |
| `test_phase4_durable_resume_hitl.py` | recovery E2E | Resume/HITL | 真 checkpoint + fake 边界 | 持久恢复/HITL 契约 | [BROKEN] `SelfRAGJudge` |
| `test_phase5_langchain_takeover_and_legacy_retirement.py` | architecture guard | LangChain 接管/Legacy | 真实模块 | 接管与兼容边界 | [BROKEN] `SelfRAGJudge` |
| `test_provider_protocol_hardening.py` | contract/component | Provider protocol | fake providers | 协议失败与资源释放 | 不证明真实 Provider |
| `test_rag_config_driven_context_packer.py` | unit/component | Context packer | 真实 packer + fixture | 配置驱动 token/证据选择 | [BROKEN] `ParentChildChunkResult` |
| `test_rag_config_driven_quality_plugins.py` | unit/component | RAG quality plugins | 真实插件 + fixture | 配置注册和 judge 选择 | [BROKEN] `SelfRAGJudge` |
| `test_rag_config_driven_query_transformers.py` | unit/component | Query transform | 真实插件 + fake model | transform 配置和结果 | [BROKEN] `ParentChildChunkResult` |
| `test_rag_config_driven_repair_strategy.py` | unit/component | Corrective repair | 真实策略 + fixture | 修复策略路由 | [BROKEN] `SelfRAGJudge` |
| `test_rag_config_driven_reranker.py` | unit/component | Reranker | fake scorer + 真插件 | 排序和注册 | [BROKEN] `ParentChildChunkResult` |
| `test_rag_config_driven_retrieval_stack.py` | component | Retrieval stack | fake backends + 真装配 | 配置到检索组件 | [BROKEN] `RETRIEVAL_RESULT_TEMPLATE` |
| `test_rag_corrective_retrieval_loop.py` | component | C-RAG loop | fake retriever/judge | 纠正检索分支 | [BROKEN] `ParentChildChunkResult` |
| `test_rag_evidence_pipeline.py` | component | Evidence pipeline | fake retrieval + 真处理 | evidence 转换与约束 | 不证明外部索引 |
| `test_rag_model_gateway_runtime.py` | component | RAG 模型网关 | fake provider + 真 adapter | RAG 调模路由 | [BROKEN] `ParentChildChunkResult` |
| `test_rag_offline_chunker_index_version.py` | offline index | chunk/version | 真离线组件 + fixture | 分块和版本元数据 | [BROKEN] `ParentChildChunkResult` |
| `test_rag_plugin_architecture_closure.py` | architecture guard | RAG 插件闭环 | 真实 registry + fake plugins | 插件边界与装配 | [BROKEN] `ParentChildChunkResult` |
| `test_rag_strategy_eval_runner.py` | evaluation | RAG 策略评测 | fixture dataset + 真 runner | 指标聚合与策略比较 | [BROKEN] `ParentChildChunkResult` |
| `test_real_provider_acceptance.py` | real provider | 真模型最小验收 | [REAL_PROVIDER] | 凭据存在时可验证 Provider | 本次跳过，不能证明当前可用 |
| `test_root_cause_closure.py` | acceptance/regression | 根因闭环 | 真实组件 + fake | 一组历史根因不变量 | 不等同全量健康 |
| `test_scheme_writer_decomposition.py` | unit/component | SchemeWriter 分解 | 真实服务 + fake 边界 | 服务分解和职责 | 不证明完整主链 |
| `test_scheme_writer_secondary_service_boundaries.py` | architecture guard | 次级服务边界 | 真实模块 | 边界依赖规则 | 不证明运行质量 |
| `test_section_aware_evidence_retrieval.py` | component | 章节感知检索 | fake retrieval + 真服务 | section query/evidence 映射 | [BROKEN] `SelfRAGJudge` |
| `test_section_execution_contracts.py` | contract | Section 执行 | 真实 Schema | 输入输出/状态契约 | 不证明图执行 |
| `test_section_generation_subservices.py` | unit/component | 章节生成子服务 | 真服务 + fake 模型/RAG | 子服务协作规则 | 不证明 Provider |
| `test_side_effect_state_machine_closure.py` | component | 副作用状态机 | 真实状态机 + fixture | claim/finalize 状态闭环 | 不证明外部系统幂等 |
| `test_stage1_acceptance.py` | mainline acceptance | Stage 1 总验收 | 真实组件 + fake 边界 | 大范围业务不变量 | 不证明真实 Provider |
| `test_step_11_2_offline_index_verification.py` | offline index | 索引校验 | 真 verifier + fixture | manifest/索引验证 | [BROKEN] `ParentChildChunkResult` |
| `test_step_11_3_index_lifecycle.py` | offline index/recovery | 索引生命周期 | 真 lifecycle + 临时目录 | register/activate/rollback/reload | 不证明真实 Milvus 并发 |
| `test_step_12_evidence_contract.py` | contract | Evidence 契约 | 真实 Schema + fixture | evidence/citation 约束 | 导入顺序下可受 `SelfRAGJudge` 阻塞 |
| `test_step_13_trace_v2.py` | component | Trace V2 | 真 trace + fixture | span/lineage/脱敏字段 | 不证明集中式追踪后端 |
| `test_step_14_context_manager.py` | unit/component | ContextManager | 真 manager + fixture | token 压缩与顺序 | 不证明模型 tokenizer 完全一致 |
| `test_step_15_graph_state_workflow_engine.py` | component | Graph State/Engine | 真实 engine + fake services | reducer、路由、失败状态 | 不证明生产 Composition Root |
| `test_workflow_budget_exhaustion_semantics.py` | component | 预算耗尽 | 真状态/策略 + fake | `retryable_failed` 等语义 | 导入顺序下可受 `SelfRAGJudge` 阻塞 |

## Schema 流转

测试通常从 fixture 构造生产 Schema，而不是维护一套测试专用业务对象：

```text
pytest fixture / factory
→ ProjectInput / TaskSchema / SectionExecutionState / RetrievalRequest
→ 被测服务返回 Result / State Delta / ArtifactRef
→ Pydantic/字段/事件/持久化断言
```

V1 专项则使用 `backend/apps/enterprise_document/schemas/agent_rag_v1/` 与 `backend/apps/enterprise_document/runtime/agent_rag_v1/contracts.py` 中的 Standalone-only Schema。两套 Schema 在测试里也没有自动互转，不能因字段同名而视为共享 Source of Truth。

## 状态修改

- 单元测试一般只观察返回值和内存状态，不落正式运行目录。
- 需要文件基础设施的测试使用 pytest 临时目录，写入 Artifact、JSONL Event Log、Checkpoint 或索引生命周期文件。
- 图测试检查节点返回 State Delta、reducer 后状态和路由；这些测试不代表每一次中间 mutation 都已进入生产 Checkpointer。
- V1 E2E 会真实写临时文件，但 Provider 与业务内容仍是 Scenario/Fake。

## 失败、重试、恢复

### 本次 30 个全量收集错误

| 直接阻塞类型 | 数量 | 受阻文件 |
|---|---:|---|
| 缺少 `SelfRAGJudge` 导出/定义 | 17 | `stage1/test_advisory_policies.py`、`test_adaptive_profile_router.py`、`test_adversarial_closure_followup.py`、`test_architecture_hardening.py`、`test_controlled_architecture_completion.py`、`test_day2_architecture_closure.py`、`test_mainline_closure_acceptance.py`、`test_model_usage_artifact.py`、`test_p1_trust_semantic_payload_closure.py`、`test_phase2_runtime_safety_closure.py`、`test_phase2_top_level_business_graph.py`、`test_phase3_section_execution_subgraph.py`、`test_phase4_durable_resume_hitl.py`、`test_phase5_langchain_takeover_and_legacy_retirement.py`、`test_rag_config_driven_quality_plugins.py`、`test_rag_config_driven_repair_strategy.py`、`test_section_aware_evidence_retrieval.py` |
| 缺少 `ParentChildChunkResult` 导出/定义 | 11 | `test_model_gateway_multi_model_runtime.py`、`test_p1_security_durability_identity_closure.py`、`test_rag_config_driven_context_packer.py`、`test_rag_config_driven_query_transformers.py`、`test_rag_config_driven_reranker.py`、`test_rag_corrective_retrieval_loop.py`、`test_rag_model_gateway_runtime.py`、`test_rag_offline_chunker_index_version.py`、`test_rag_plugin_architecture_closure.py`、`test_rag_strategy_eval_runner.py`、`test_step_11_2_offline_index_verification.py` |
| 缺少 `RETRIEVAL_RESULT_TEMPLATE` | 1 | `test_rag_config_driven_retrieval_stack.py` |
| 循环导入 | 1 | `stage1/test_contracts.py` |

收集失败发生在测试运行前，不适用 pytest retry。恢复方式只能是修复导入边界后重新收集；本文不修改业务或测试代码。

## 配置来源与实际执行记录

执行环境：Windows PowerShell，仓库虚拟环境 `.\.venv\Scripts\python.exe`，日期 **2026-07-29**。`-p no:cacheprovider` 避免写 pytest cache；`--basetemp` 固定在仓库可写目录，避免系统临时目录权限噪声。

| 范围 | 命令 | 结果 |
|---|---|---|
| V1 专项 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.tmp\pytest-codewalk-v1-20260729 backend/tests/test_agent_rag_v1_agents.py backend/tests/test_agent_rag_v1_contracts.py backend/tests/test_agent_rag_v1_document_closure.py backend/tests/test_agent_rag_v1_infrastructure.py backend/tests/test_agent_rag_v1_recovery.py backend/tests/test_agent_rag_v1_recovery_e2e.py backend/tests/test_agent_rag_v1_runtime_contracts.py backend/tests/test_agent_rag_v1_standalone_e2e.py backend/tests/test_agent_rag_v1_workflow.py backend/tests/test_agent_rag_v1_writer_claims.py` | **84 passed in 4.44s** |
| 全量收集 | `.\.venv\Scripts\python.exe -m pytest --collect-only -q -p no:cacheprovider` | **491 tests collected, 30 errors in 2.31s** |
| 可收集核心非 Provider | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.tmp\pytest-codewalk-core-safe-20260729c backend/tests/test_decoupled_architecture.py backend/tests/test_langchain_model_gateway_integration.py backend/tests/test_langchain_rag_integration.py backend/tests/test_model_gateway_lifecycle.py backend/tests/test_parent_child_generation_runtime.py backend/tests/test_phase1_langgraph_serializable_state.py backend/tests/test_provider_protocol_hardening.py backend/tests/test_side_effect_state_machine_closure.py backend/tests/test_step_13_trace_v2.py backend/tests/test_step_14_context_manager.py backend/tests/test_step_15_graph_state_workflow_engine.py` | **234 passed, 1 failed in 20.59s** |
| 真实 Provider 单独收集 | `.\.venv\Scripts\python.exe -m pytest --collect-only -q -p no:cacheprovider backend/tests/test_real_provider_acceptance.py` | **1 test collected** |
| 真实 Provider执行 | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.tmp\pytest-codewalk-provider-20260729 backend/tests/test_real_provider_acceptance.py` | **1 skipped in 0.68s** |

第一次 V1 执行未指定 `--basetemp` 时为 **56 passed, 28 errors**，错误来自 pytest 清理/扫描系统临时目录的 `PermissionError`；这是测试环境权限噪声，不计入产品失败。固定仓库内临时目录后的 84 passed 才是本文采用结果。

真实 Provider 的跳过条件位于 `backend/tests/test_real_provider_acceptance.py:12-20`：需要显式 `REAL_PROVIDER_ACCEPTANCE=1`，并需要测试读取的模型凭据/配置。未满足条件时，skip 是预期保护，不是通过。

## 对应测试与三条主链的证据边界

| 主链 | 最接近的测试证据 | 当前边界 |
|---|---|---|
| Agent / Workflow 生产主链 | `test_mainline_closure_acceptance.py`、Phase 2–5、`test_stage1_acceptance.py` | 多个主链验收文件当前被 `SelfRAGJudge` 阻塞；安全子集不等于整链验收 |
| RAG 在线检索链 | `test_langchain_rag_integration.py`、`test_rag_evidence_pipeline.py`、config-driven 系列 | 部分 adapter 测试通过，但大量插件/检索栈文件当前无法收集，且无本次真实 Milvus 验收 |
| RAG 离线索引链 | `test_parent_child_generation_runtime.py`、`test_step_11_3_index_lifecycle.py`、chunk/version/verification | lifecycle 有独立测试；部分离线测试被 `ParentChildChunkResult` 阻塞；固定分块测试存在 1 个运行失败 |
| V1 Standalone | 10 个 `test_agent_rag_v1_*.py` | 84 passed；仍是 Scenario/Fake 业务边界 |

## 尚未实现或本次未证明

- [BROKEN] 本文没有修复四类导入错误或固定分块参数错误，因为任务限定只写文档。
- [REAL_PROVIDER] 没有启用真实模型验收，故不能确认本机 Qwen/DeepSeek 调用成功、费用、延迟或结构化输出稳定性。
- 未运行需要外部 Milvus、网络或生产数据的端到端验收。
- 当前没有一条在本次运行中同时穿过 CLI、生产 Composition Root、顶层图、Section 图、真实 RAG、真实模型和最终持久化的绿色 E2E 证据。

## 常见误解

1. “491 tests collected”不是 491 passed；它与 30 个收集错误同时出现。
2. “84 passed”只属于 V1 专项，不代表全仓库。
3. Fake Provider 测试真实 adapter，只能证明协议映射，不能证明外部 Provider 可用。
4. 单独文件可收集不保证全量收集；当前导入顺序会改变部分错误首先暴露的位置。
5. collection error 不会进入测试函数，因此不能用失败断言数来替代。
6. `skip` 不等于 pass；真实 Provider 本次没有被执行。

## 推荐下一篇

继续阅读 [13_CURRENT_ARCHITECTURE_RELATIONSHIPS.md](13_CURRENT_ARCHITECTURE_RELATIONSHIPS.md)，把这些测试所覆盖的多套 Workflow、Shared State 和 Supervisor 概念放回同一张当前架构图中。
