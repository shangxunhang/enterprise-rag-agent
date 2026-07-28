# TASK-002 Online RAG Chain — As-Is 调查结果

## 0. 调查结论

**当前真实在线 RAG 主链的业务触发点不是 `SchemeGenerationUseCase.run()`，而是显式 LangGraph 节点 `retrieve_document_evidence` 直接调用 `SchemeGenerationStageService.retrieve_document_evidence()`。**

TASK-001 已确认 `SchemeGenerationUseCase.run()` 在当前生产主链不可达，因此本任务没有虚构该调用边，而是从 TASK-001 唯一确认的真实边界继续向下调查：

```text
EnterpriseDocumentWorkflowEngine.build_graph
  -> node: retrieve_document_evidence
  -> EnterpriseDocumentWorkflowEngine._run_stage
  -> SchemeGenerationStageService.retrieve_document_evidence
  -> SchemeEvidenceService.retrieve
  -> LangChainRAGServiceFacade.retrieve / invoke
  -> authoritative RAG Runnable
  -> ObservedRAGService.retrieve
  -> RAGService.retrieve
  -> RAGRequestMapper.map
  -> RetrievalRuntime.retrieve
  -> ParentChildRAGEngine.run
  -> ParentChildRetrievalPipeline.run
       -> AdaptiveRetrievalPlanner.plan
       -> QueryTransformSelector.transform
       -> dense child retrieval + BM25 child retrieval
       -> child-level RRF
       -> parent grouping / parent backfill
       -> optional multi-query parent-level RRF
       -> parent cross-encoder rerank
       -> CRAG-style evidence assessment
       -> bounded corrective retrieval when evidence is insufficient
  -> ContextGate.pack
  -> RAGResultMapper.map
  -> RAGEvidenceContractBuilder.build
  -> EvidenceBundleSchema
  -> SchemeEvidenceService.extract_rag_output
  -> DocumentCitationRegistry.remap_bundle
  -> SharedStateWriter.set_evidence_context
  -> SchemeGenerationGraphPayload.document_*
  -> initialize_generation
  -> execute_sections / section generation
```

当前静态检索配置选择的在线拓扑为：

```text
mutually-exclusive query transform
  = identity | multi_query | hyde

per transformed query:
  Milvus dense child top-10
  + in-memory BM25 child top-10
  -> child RRF(k=60)
  -> parent dedup/backfill top-5

if transformed query count > 1:
  -> parent/query RRF(k=60, top-5)

then:
  -> BGE parent cross-encoder rerank top-5
  -> CRAG evidence assessment
  -> at most 1 corrective round by default
       -> at most 2 corrective queries
       -> repeat full retrieval stack
       -> merge previous candidates
       -> rerank and reassess
  -> context gate/default-or-lost-in-middle packing, max 5 evidence items
  -> canonical Evidence contract
```

### 0.1 重要限制

代码主链和受控测试已确认，但**当前 active index 尚不能被判定为已在 production trusted-scope 条件下端到端可用**：

- active index 的 258 条 child records 和 258 条 vector index records 均没有 `tenant_id`、`kb_id`、`file_id`；
- production real-RAG 入口默认要求 `RetrievalAccessScopeSchema`，并同时向 dense 与 BM25 下发 tenant/KB 约束；
- BM25 代码在该数据上按 tenant/KB 精确过滤后必然无命中；
- 当前 DevSpace `.venv` 缺少 `pymilvus`，真实 Milvus Lite scope probe 无法运行，因此 dense 侧究竟返回空结果还是因字段缺失报错，标记为 **unknown**。

这不是“问题已修复”的结论，也不是设计评价；只是当前仓库事实和验证边界。

---

## 1. 调查范围与停止边界

### 1.1 已调查

- TASK-001 确认的真实 RAG 调用边界；
- Query、任务身份、授权 scope、文档过滤、上下文预算如何形成 `RAGToolInputSchema`；
- LangChain facade 与 authoritative RAG runtime 的真实关系；
- Query 规划、identity / multi-query / HyDE 的选择和输出；
- Dense、BM25、child RRF、parent backfill、query RRF、rerank；
- Evidence assessment 和 bounded corrective retrieval；
- context packing、citation 生成、Evidence contract 创建；
- Evidence 写入共享状态和交给 Generation 的边界；
- 当前 active index pointer、manifest 和在线读取字段；
- 相关实现的 active / inactive / legacy / experimental / unknown 判定；
- 定向测试提供的运行证据及其失败边界。

### 1.2 未调查 / 非目标

按照 TASK-002 范围，本轮没有：

- 修改任何业务代码；
- 修改 `ARCHITECTURE.md` 或 `CONTRACTS.md`；
- 评价或重构当前 RAG 设计；
- 调查离线索引构建算法内部实现；
- 调查章节 Prompt、生成模型、文档质量门禁的内部算法；
- 新增算法、Retriever、Reranker、Agent 或配置；
- 用当前真实索引执行会写入共享运行目录的完整生产生成任务。

离线产物字段与在线过滤条件之间的事实差异只作为 Finding 交给 TASK-003 / TASK-005，不在本任务处理。

---

## 2. 调查基线

| 项目 | 调查结果 | 证据 |
|---|---|---|
| Workspace | `D:\MyCode\rag-agent` | DevSpace checkout workspace |
| Branch | `main` | `git branch --show-current` |
| Commit | `07dda2e80645cac67f8b5f5e30002d21c2ab01eb` | `git rev-parse HEAD` |
| Exact tag | 无 | `git describe --tags --exact-match HEAD` 返回 `NO_EXACT_TAG` |
| 工作区 | dirty | `git status --porcelain=v1` |
| 调查前状态计数 | unstaged deleted 1、unstaged modified 37、untracked 38、staged added 1、staged+modified 5 | `git status --porcelain=v1 | awk ...` |
| `USE_REAL_RAG_TOOL` 当前 shell 环境 | 未设置 | 环境检查 |
| `RAG_PROJECT_ROOT` 当前 shell 环境 | 未设置 | 环境检查 |
| 静态检索配置环境覆盖 | 均未设置 | 环境检查 |
| 代码默认 real-RAG | `true` | `backend/bootstrap/runtime_options.py::RuntimeOptions.resolve` |
| 完整测试基线 | unknown，本轮未运行全量测试 | 本轮只运行 TASK-002 定向测试 |

当前结论对应：

```text
HEAD 07dda2e + 调查时工作区未提交内容
```

后续核验必须重新确认工作区内容，不能只用 commit 号代替当前基线。

---

## 3. 已读取的事实源

### 3.1 团队文档

- `docs/team/PROJECT.md`
- `docs/team/ORGANIZATION.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- `docs/team/STATUS.md`
- `docs/team/TASKS/TASK-002-online-rag-chain.md`
- `docs/team/HANDOFFS/TASK-001-online-agent-workflow-chain-handoff.md`

### 3.2 主要代码证据

- `backend/apps/enterprise_document/workflows/top_level_business_graph.py`
- `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`
- `backend/apps/enterprise_document/services/scheme_writer/evidence_service.py`
- `backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py`
- `backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py`
- `backend/agent/runtime/state_access.py`
- `backend/bootstrap/supervisor_factory.py`
- `backend/bootstrap/rag_service_factory.py`
- `backend/bootstrap/runtime_options.py`
- `backend/application/mainline_service.py`
- `backend/rag/integrations/langchain/runtime_facade.py`
- `backend/rag/integrations/langchain/runnable.py`
- `backend/rag/services/rag_service.py`
- `backend/rag/mapping/request_mapper.py`
- `backend/rag/runtime/retrieval_runtime.py`
- `backend/rag/runtime/parent_child_runtime_factory.py`
- `backend/rag/runtime/resource_pool.py`
- `backend/rag/rag_engine/parent_child_rag_engine.py`
- `backend/rag/application/parent_child_retrieval.py`
- `backend/rag/planning/retrieval_planner.py`
- `backend/rag/query/query_transform_selector.py`
- `backend/rag/query/query_expander.py`
- `backend/rag/plugins/query_transformers/plugin.py`
- `backend/rag/plugins/retrievers/plugin.py`
- `backend/rag/plugins/fusions/plugin.py`
- `backend/rag/plugins/candidate_enrichers/plugin.py`
- `backend/rag/plugins/rerankers/plugin.py`
- `backend/rag/plugins/evidence_assessors/plugin.py`
- `backend/rag/plugins/correction_gates/plugin.py`
- `backend/rag/plugins/corrective_query_planners/plugin.py`
- `backend/rag/context/context_gate.py`
- `backend/rag/context/context_packer.py`
- `backend/rag/retriever/milvus_child_retriever.py`
- `backend/rag/retriever/bm25_child_retriever.py`
- `backend/rag/ranker/rrf_fusion.py`
- `backend/rag/retrieval/query_fusion.py`
- `backend/rag/reranker/parent_child_reranker.py`
- `backend/rag/schema/Retrieval_Result_Schema.py`
- `backend/rag/mapping/result_mapper.py`
- `backend/rag/mapping/evidence_mapper.py`
- `backend/rag/evidence/contract.py`
- `backend/rag/data_capture/rag_run_capture.py`
- `backend/schemas/rag.py`

### 3.3 运行配置与数据证据

- `backend/rag/config/static_retrieval_v1.yaml`
- `backend/rag/config/intent_policy_v1.yaml`
- `backend/rag/config/retrieval_gate_policy_v1.yaml`
- `data/processed/indexes/active_index.json`
- `data/processed/indexes/idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f/index_manifest.json`
- 同目录 `child_chunks.jsonl`
- 同目录 `vector_index_records.jsonl`

---

## 4. TASK 起点偏差与真实边界

### 4.1 TASK 文本要求的起点

TASK-002 要求从 TASK-001 确认的 `SchemeGenerationUseCase` 实际调用点继续调查。

### 4.2 TASK-001 的已确认事实

TASK-001 已证明：

- `SchemeGenerationUseCase.__init__`：active construction；
- `SchemeGenerationUseCase.run`：legacy / 当前生产主链不可达；
- 当前显式 LangGraph 直接注册 `SchemeGenerationStageService` 的 stage methods。

因此本任务不存在合法的：

```text
SchemeGenerationUseCase.run -> online RAG
```

### 4.3 本任务采用的真实起点

```text
backend/apps/enterprise_document/workflows/top_level_business_graph.py
EnterpriseDocumentWorkflowEngine.build_graph
  add_node("retrieve_document_evidence", ...)
  -> self.stage_service.retrieve_document_evidence
```

图中的上游和下游为：

```text
plan_document
  -> retrieve_document_evidence
  -> initialize_generation
  -> execute_sections
```

任何 stage error 会令 `_route_after_stage` 选择 `technical_failure`，进入 `finalize_workflow_failure`。

**判定：真实起点 active；TASK 原始 `SchemeGenerationUseCase.run` 起点 legacy / 不可达。**

---

## 5. 权威调用边证据表

下表对每条主要结论同时给出用户要求的路径、类、函数、上游、下游、输入、输出、状态变化和异常行为。

| ID | 分类 | 文件路径 | 类 / 函数 | 上游调用者 | 下游调用者 | 输入 | 输出 | 状态变化 | 异常行为 |
|---|---|---|---|---|---|---|---|---|---|
| E-01 | active | `backend/apps/enterprise_document/workflows/top_level_business_graph.py` | `EnterpriseDocumentWorkflowEngine.build_graph` | composition root 构造 engine 后执行 graph | `_run_stage(..., stage=SchemeGenerationStageService.retrieve_document_evidence)` | `SchemeGenerationGraphState` | LangGraph node result | graph node 从 `plan_document` 路由到 retrieval，成功后到 `initialize_generation` | stage error 路由 `technical_failure -> finalize_workflow_failure` |
| E-02 | active | 同上 | `EnterpriseDocumentWorkflowEngine._run_stage` | LangGraph node lambda | stage callable | graph state、workflow、node name、payload | 经写权限校验的 graph delta / payload | 写入 node result、stage status、错误或 terminal route | timeout/cancel/no-result/contract violation/stage failure 被规范化为 `ErrorSchema` |
| E-03 | active | `backend/apps/enterprise_document/services/scheme_writer/stage_service.py` | `SchemeGenerationStageService.retrieve_document_evidence` | E-02 | `SchemeEvidenceService.retrieve` | `SharedStateSchema`、payload 中 `ProjectInput` | 更新后的 `SchemeGenerationGraphPayloadSchema` | 激活 document RAG budget；写 shared evidence；写 `document_*`、`all_*` 字段 | `rag_result is None` 或 `success=False` 时添加 error 并抛 `SchemeGenerationStageFailure` |
| E-04 | active | `backend/apps/enterprise_document/services/scheme_writer/evidence_service.py` | `SchemeEvidenceService.retrieve` | E-03 | `LangChainRAGServiceFacade.retrieve` | shared state、ProjectInput、可选 query/scope/section | `EvidenceBundleSchema` | 不直接写 GraphState；构造 request 与 retrieval trace id | tenant 与 trusted scope 不一致时 `ValueError`；RAG port 未配置时返回 `None` |
| E-05 | active | `backend/rag/integrations/langchain/runtime_facade.py` | `LangChainRAGServiceFacade.retrieve/invoke` | E-04 | Runnable `.invoke` | `RAGToolInputSchema` | `EvidenceBundleSchema` | invocation count 增加；不改变业务 payload | Runnable/schema/下游异常向上传递 |
| E-06 | active | `backend/rag/integrations/langchain/runnable.py` | `build_rag_runnable` 内部 `_invoke` | E-05 | wrapped `RAGServicePort.retrieve` | schema 或可校验 dict | canonical `EvidenceBundleSchema` | 无算法状态；LangChain 只是调用接口 | 输入无法校验或下游返回非 canonical schema 时失败 |
| E-07 | active | `backend/rag/services/rag_service.py` | `ObservedRAGService.retrieve` | E-06 | `RAGService.retrieve` | `RAGToolInputSchema` | `EvidenceBundleSchema` | trace 记录 `rag_started/rag_finished`、selected/citation count | 下游异常不在此吞掉 |
| E-08 | active | 同上 | `RAGService.retrieve` | E-07 | `RAGRequestMapper.map -> RetrievalRuntime.retrieve -> RAGResultMapper.map` | canonical request | 成功或失败 `EvidenceBundleSchema` | 成功构建 Evidence；一般异常转换为 failed bundle | `ModelCallBudgetExceeded`、`WorkflowExecutionCancelled` 直接抛出；其他异常转换为 `RAG_RETRIEVAL_FAILED` |
| E-09 | active | `backend/rag/mapping/request_mapper.py` | `RAGRequestMapper.map` | E-08 | runtime payload | request query、scope、KB/doc/file narrowing、context requirements | `RAGInvocation` | 生成 Milvus `filter_expr` 和 BM25 `keyword_scope`；不写共享状态 | 空 query、缺 trusted scope、tenant 冲突、KB 无授权交集、doc/file 越权均 `ValueError` |
| E-10 | active | `backend/rag/runtime/retrieval_runtime.py` | `RetrievalRuntime.retrieve` | E-08 | `ParentChildRAGEngine.run` | dict payload | raw RAG result dict | 首次惰性初始化 runtime；附加 offline index/static spec/policy lineage | active pointer/manifest/资源初始化或 engine 异常向 E-08 返回 |
| E-11 | active | `backend/rag/runtime/parent_child_runtime_factory.py` | `ParentChildRuntimeFactory.build` | E-10 initialize | registry/resource pool/components/engine | resolved runtime config | `ParentChildRAGEngine` | 解析 active manifest；实例化静态配置选中的插件 | 缺组件、未知组件、manifest/hash/backend/资源错误直接失败 |
| E-12 | active | `backend/rag/rag_engine/parent_child_rag_engine.py` | `ParentChildRAGEngine.run` | E-10 | `ParentChildRetrievalPipeline.run`、`ContextGate.pack`、evaluator、capture | query、filter、keyword scope、metadata | raw result dict | 创建 `rag_run_id`；捕获模型调用；形成 run record；capture 延迟提交 | 空 query `ValueError`；pipeline/packer/evaluator/capture 异常向 E-08 传播 |
| E-13 | active | `backend/rag/application/parent_child_retrieval.py` | `ParentChildRetrievalPipeline.run` | E-12 | planner、selector、retrievers、fusion、enricher、reranker、assessment/correction | query、filter/scope/context | `RetrievalStageResult` | query expansion metadata、candidate ranks/scores、assessment/correction trace | 组件异常向 E-12 传播；纠错 planner 无 query 时停止纠错并记录原因 |
| E-14 | active | 同上 | `_retrieve_one_query` | E-13 初始或纠错 loop | 所有 configured retrievers -> source fusion -> candidate enricher | 一个 retrieval query + trusted filters | parent-level `CandidateSet` | dense/keyword source sets -> fused child set -> enriched parent set | 任一 retriever/fusion/enricher 异常终止该轮并向上传播 |
| E-15 | active | `backend/rag/plugins/retrievers/plugin.py` | `MilvusDenseChildRetrieverPlugin.retrieve` | E-14 | `MilvusChildRetriever.search` | `RetrievalRequest.query/filter_expr` | dense child `CandidateSet`，最多 10 | 只生成候选和 metadata | DB/collection/embedding/pymilvus/search 错误向上传播 |
| E-16 | active | 同上 | `BM25ChildRetrieverPlugin.retrieve` | E-14 | `BM25ChildRetriever.search` | query + tenant/kb/file/doc scope | keyword child `CandidateSet`，最多 10 | 只生成候选和 metadata | 空 query `ValueError`；加载空索引或路径错误在初始化阶段失败 |
| E-17 | active | `backend/rag/plugins/fusions/plugin.py`、`backend/rag/ranker/rrf_fusion.py` | `ChildRRFFusionPlugin.fuse` / `rrf_fuse` | E-14 | candidate enricher | dense/keyword child sets | child-level hybrid candidates | 按 child id 去重；写 source ranks/scores、RRF contribution、fusion rank | `rrf_k < 1` 失败；空 source set 返回空候选 |
| E-18 | active | `backend/rag/plugins/candidate_enrichers/plugin.py` | `ParentChildCandidateEnricher.enrich` | E-14 after E-17 | `ParentChunkStore.get`、`build_retrieval_result_v2` | fused child candidates | parent-level retrieval results，最多 5 | 按 parent 分组、收集 matched children、回填 parent；写 `parent_found` | parent 缺失不抛错，`build_retrieval_result_v2` 回退 child text |
| E-19 | active conditional | `backend/rag/plugins/fusions/plugin.py`、`backend/rag/retrieval/query_fusion.py` | `ParentRRFFusionPlugin.fuse` / `MultiQueryFusion.fuse` | E-13 `_fuse_query_sets` | reranker | 多个 query 的 parent candidate sets | parent-level query-fused top-5 | 仅 query set 数量 > 1 时应用；按 parent/context key 聚合 | 空输入返回空；单 query 不执行 query fusion |
| E-20 | active | `backend/rag/plugins/rerankers/plugin.py`、`backend/rag/reranker/parent_child_reranker.py` | `BGEParentCrossEncoderRerankerPlugin.rerank` / `ParentChildReranker.rerank` | E-13 | evidence assessor | original query + parent results | reranked top-5 | 写 `rerank_score`、pre-rerank rank/score、新 rank | 模型加载/推理异常无 no-op fallback，向 E-08 汇总为 failed Evidence |
| E-21 | active | `backend/rag/plugins/evidence_assessors/plugin.py` | `CRAGEvidenceAssessorPlugin.assess` | E-13 after rerank and after correction | correction gate | query + reranked results | `EvidenceAssessment` | always executed；写 sufficiency/confidence/reasons/verification mode | LLM 一般异常可 deterministic fallback；budget/cancel 继续抛出 |
| E-22 | active conditional | `backend/rag/plugins/correction_gates/plugin.py` | `EvidenceSufficiencyCorrectionGate.decide` | E-13 `_run_correction` | corrective query planner | assessment、budget、completed rounds | `CorrectionGateDecision` | evidence insufficient 且 budget 剩余时打开 | 不抛业务异常；budget 耗尽时关闭并记录原因 |
| E-23 | active conditional | `backend/rag/plugins/corrective_query_planners/plugin.py` | `SectionGapCorrectiveQueryPlanner.plan` | E-22 required=true | E-14 重复完整检索 | original query、assessment、document context | 最多 2 个 corrective queries | 默认合并上一轮 reranked candidates；记录 method/raw output | LLM 一般异常 fallback；budget/cancel 抛出 |
| E-24 | active conditional | `backend/rag/context/context_gate.py`、`context_packer.py` | `ContextGate.pack` / `ContextPacker.pack` | E-12 after retrieval | result mapper | final reranked results + context requirements | `ContextPack` | default 或 lost-in-middle；dedup/drop/truncate；生成 citation markers | context 超预算且 packer 最终仍超限时 `RuntimeError` |
| E-25 | active | `backend/rag/mapping/result_mapper.py` | `RAGResultMapper.map` | E-08 | EvidenceMapper + contract builder | runtime raw result | canonical `EvidenceBundleSchema` | selected/dropped candidates、assessment、trace、budget usage 写入 contract | raw_result success=false 时 `RuntimeError`，由 E-08 转 failed bundle |
| E-26 | active | `backend/rag/evidence/contract.py` | `RAGEvidenceContractBuilder.build` | E-25 | schema validator / reader | selected/dropped chunks、citations、trace、context pack | `RAGEvidenceContractSchema` / `EvidenceBundleSchema` | 规范化 citation 为 C1..；evidence 为 E1..；context 为 projection；写 lineage | context 声明超字符预算或 contract 引用不一致时 validation error |
| E-27 | active | `backend/apps/enterprise_document/services/scheme_writer/evidence_service.py` | `SchemeEvidenceService.extract_rag_output` | E-03 after retrieval | contract reader | `EvidenceBundleSchema` | context/chunks/citations/normalized dict | 把 contract 存入 context.extra 并计算 contract hash/lineage projection | 非 canonical ToolResult 才进入 LegacyEvidenceAdapter |
| E-28 | active | `backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py` | `DocumentCitationRegistry.remap_bundle` | E-03 | state writer/payload | context、chunks、citations、contract | 文档级稳定 citation IDs | 重写 context marker、contract items/citations、hash 和 scope metadata | remapped contract 校验失败时异常向 stage 传播 |
| E-29 | active | `backend/agent/runtime/state_access.py` | `SharedStateWriter.set_evidence_context` | E-03 | generation stages/readers | query、contract、context、chunks、citations、assessment | `state.context_bundle.evidence` | 这是在线 RAG 首次写共享 GraphState 的位置 | 无本地异常恢复；数据结构错误向 stage 传播 |
| E-30 | active consumer boundary | `backend/apps/enterprise_document/services/scheme_writer/stage_service.py` | `execute_sections` | `initialize_generation` | section execution graph/coordinator | payload `document_rag_context/chunks/citations/assessment` | section execution request | 注册 document citations，作为章节默认 evidence；章节可另行检索 | 缺 `document_rag_context` 时 `_require` 失败，进入 technical failure |

---

## 6. Query 与检索输入形成过程

## 6.1 原始 Query

来源：

```text
ProjectInput.user_query
```

`SchemeGenerationStageService.retrieve_document_evidence` 不改写原文，直接调用：

```python
self.evidence_service.retrieve(shared_state, project_input)
```

`SchemeEvidenceService.retrieve` 的 `resolved_query` 优先级：

```text
显式 query 参数
  > project_input.user_query
```

本次 document-level 调用未传显式 query，因此实际值为 `ProjectInput.user_query`。

## 6.2 任务与运行身份

`SchemeEvidenceService.retrieve` 从 `SharedStateSchema` 和 `ProjectInput` 形成：

- `task_id = shared_state.task.task_id`
- `run_id = shared_state.task.run_id`
- `agent_name = SchemeEvidenceService.agent_name`
- `task_type = project_input.task_type`
- `retrieval_trace_id = rag_{run_id}_{scope/section suffix}`
- `retrieval_scope = document`

这些字段进入 `RAGToolInputSchema.extra` 和 runtime planner metadata。

## 6.3 trusted retrieval scope 与 caller narrowing

授权边界来自组合根注入的：

```text
RetrievalAccessScopeSchema
  tenant_id
  authorized_kb_ids
  allowed_file_ids
  allowed_doc_ids
```

caller 只能收窄：

- `ProjectInput.source_materials[*].doc_id`
- `ProjectInput.source_materials[*].file_id`
- source material metadata 中 `kb_id/kb_ids`
- `shared_state.task["kb_ids"]`

`RAGRequestMapper.map` 规则：

1. production real-RAG 没有 access scope 时 fail closed；
2. caller tenant 与 trusted tenant 冲突时失败；
3. requested KB 必须与 authorized KB 有交集，否则失败；
4. requested doc/file 只能从 allowed doc/file 中取子集，否则失败；
5. 生成 dense Milvus filter 和 BM25 keyword scope。

典型 dense filter：

```text
tenant_id == "<trusted tenant>"
and kb_id in ["<authorized or narrowed kb>"]
[and file_id in [...]]
[and doc_id in [...]]
```

BM25 receives the same tenant/KB/file/doc values as structured fields.

## 6.4 上下文预算

`SchemeEvidenceService.retrieve` 根据 ModelGateway capability plan 与 generation requirements 形成：

- `model_context_window`
- `prompt_reserved_tokens`
- `section_token_budget`，默认 4096
- `max_evidence_items`，默认 5
- `max_context_chars`

stage 另外激活 document-level workflow budget，默认：

- max retrieval rounds: 4
- max rewrite rounds: 2
- max total LLM calls: 16
- max total tokens: 32000
- exhaustion requires human review: true

注意：retrieval planner 的默认 correction budget 来自独立 policy，当前为 1；document workflow budget 是更外层总预算，两者不是同一个字段。

---

## 7. Query 规划、改写、HyDE 与数量变化

## 7.1 每次请求只选择一种 Query transform

`AdaptiveRetrievalPlanner.plan` 与 `QueryTransformSelector.transform` 明确互斥执行：

```text
identity OR multi_query OR hyde
```

不是三种算法串行全部执行。

### 7.1.1 multi-query 条件

以下任一条件命中时选择 multi-query：

- 去空白后 query 长度 <= 16；
- 包含歧义词；
- 包含多方面/比较词；
- query 或 task_type 命中正式文档词：`方案`、`报告`、`规划`、`设计文档`、`scheme_generation`、`report_generation`。

当前方案生成任务通常会因 task type / formal signal 进入 multi-query。

### 7.1.2 HyDE 条件

在未先命中 multi-query 条件时，抽象机制类词语如 `原理`、`机制`、`本质`、`为什么`、`架构` 等选择 HyDE。

### 7.1.3 identity 条件

足够具体且不命中以上信号时，保持原 query。

### 7.1.4 overrides 可达性

planner 支持 `retrieval_plan_overrides`，但当前 document-level `SchemeEvidenceService.retrieve` 构造的固定 `extra_metadata` 没有从 `ProjectInput.generation_requirements.extra` 透传该字段。因此：

- capability 本身存在并可由其他直接 RAG caller 使用；
- 在本任务确认的 document-level主链中，没有发现 caller 注入该 override 的真实边。

当前主链状态判定：**inactive for this caller / active capability elsewhere**。

## 7.2 Query 数量

| mode | 输入 | 新增 | 最大 retrieval query 数 | 失败处理 |
|---|---|---|---:|---|
| identity | original query | 0 | 1 | 空 query 失败 |
| multi_query | original query | 最多 3 条 rewrite | 4 | LLM 一般异常或无有效输出时 deterministic fallback；budget/cancel 抛出 |
| HyDE | original query | 1 个 hypothetical document | 2 | LLM 一般异常或空输出时 deterministic fallback；budget/cancel 抛出 |

Multi-query deterministic fallback 以背景/范围、概念/流程、实施/风险等模板产生查询；HyDE fallback 产生一段企业资料风格的假想文本。

本链没有独立的“复杂问题 decomposition 子图”；当前可确认的 Query 扩展只有上述 mutually-exclusive transform 和证据不足后的 corrective queries。

---

## 8. 召回、融合、父块回填、重排与纠错

## 8.1 每个 retrieval query 的固定步骤

```text
RetrievalRequest
  -> MilvusDenseChildRetrieverPlugin.retrieve(top_k=10)
  -> BM25ChildRetrieverPlugin.retrieve(top_k=10)
  -> ChildRRFFusionPlugin.fuse(rrf_k=60, no immediate top-k cutoff)
  -> ParentChildCandidateEnricher.enrich(dedup_parent=true, top_k=5)
```

### 数量上界

每个 query：

```text
dense <= 10
keyword <= 10
child union after RRF <= 20
parent grouped/deduped result <= 5
```

实际数量可能因：

- retriever 返回不足；
- dense/keyword child 重复；
- 多个 child 属于同一 parent；
- scope/filter；
- BM25 score 必须 > 0；
- parent 缺失；

而减少。

## 8.2 Dense backend

`MilvusChildRetriever.search`：

```text
query
  -> embedding model or hash embedding
  -> MilvusClient.search(
       collection_name,
       data=[vector],
       anns_field="vector",
       limit=10,
       search_params={metric_type},
       output_fields,
       optional filter
     )
  -> normalized dense child hits
```

职责只到 child retrieval，不做 BM25、RRF、parent 回填、rerank。

## 8.3 BM25 backend

`BM25ChildRetriever`：

- 从 active manifest 的 `child_chunks.jsonl` 构建内存索引；
- 拉丁/代码 token 采用正则词；
- 中文采用单字 + overlapping 2-gram；
- 先按 trusted tenant/kb/file/doc 精确过滤，再计算 BM25；
- 只保留 score > 0 的记录。

## 8.4 child-level RRF

RRF key：`child_chunk_id` 或 `chunk_id`。

公式：

```text
fusion_score = sum(1 / (60 + source_rank))
```

不会直接把 Milvus score 与 BM25 score 相加。输出保留：

- retrieval sources；
- source ranks；
- source scores；
- RRF contributions；
- dense/keyword convenience fields。

## 8.5 parent dedup/backfill

`ParentChildCandidateEnricher`：

1. 按 `parent_chunk_id` 分组；
2. 每个 parent 选 fusion score 最高 child 为代表；
3. 收集该 parent 下所有 matched child IDs、文本与来源；
4. `ParentChunkStore.get(parent_id)` 回填 parent；
5. 构造 `retrieval_result_v2`；
6. 截断 parent top-5。

父块缺失时不抛异常：

```text
context_text = parent_text if parent exists else child_text
metadata.parent_found = false
```

## 8.6 multi-query parent-level RRF

只有 retrieval query 数量 > 1 才执行。

- key 优先 parent/context chunk id；
- RRF k=60；
- 输出 top-5；
- identity 单 query 路径跳过该步骤。

## 8.7 rerank

当前静态配置选择：

```text
BGEParentCrossEncoderRerankerPlugin
  text_field = parent_text
  top_k = 5
```

输入 pair：

```text
[original user query, parent_text]
```

注意：reranker 始终使用 original query，不使用当前 transformed query 作为最终 pair query。

模型加载或推理异常没有自动切到 no-op reranker；异常由 `RAGService.retrieve` 汇总为 failed Evidence bundle。

## 8.8 evidence assessment

`CRAGEvidenceAssessorPlugin.assess` 每次初始 rerank 后必执行，纠错后再次执行。

当前阈值：

- max judge chunks: 8
- confidence threshold: 0.55
- minimum relevant chunks: 1
- LLM enabled by static spec；ModelGateway 不可用或一般调用失败时允许 deterministic fallback。

assessment 将区分：

- `llm_verified`
- `deterministic_primary`
- `degraded_deterministic_fallback`
- `unavailable`

## 8.9 corrective retrieval

当前 default correction budget = 1。

触发条件：

```text
assessment.sufficient == false
AND completed_rounds < correction_budget
```

一次纠错最多产生 2 个 corrective queries。每条 query 重新执行完整的：

```text
dense + BM25 -> child RRF -> parent backfill
```

然后默认把上一轮 reranked candidates 一并加入 query fusion，再：

```text
query fusion top-5 -> rerank top-5 -> reassess
```

如果 planner 返回空 query，停止纠错并在 trace 中记录原因。默认只允许 1 轮，不存在无界循环。

---

## 9. Context packing 与 citation

## 9.1 ContextGate

从最多 5 个 final results 估算 token 数。

```text
threshold = evidence_token_budget * 0.65
```

- estimated tokens <= threshold：default packer，保持 rerank order；
- estimated tokens > threshold：lost-in-middle packer。

两个 packer 都是当前 static spec 已配置且可达的 active conditional 实现。

## 9.2 lost-in-middle ordering

例如：

```text
[1, 2, 3, 4, 5, 6]
  -> [1, 3, 5, 6, 4, 2]
```

最高相关证据放开头，第二高放结尾。

## 9.3 drop / truncate

packer 会因以下原因把结果放入 `dropped_results`：

- `max_items`
- `duplicate_parent`
- `empty_text`
- `token_budget_exhausted`
- `token_budget_overflow`

只有第一个 item 可以在仍有最低预算时被截断，截断 item ID 进入 `truncated_item_ids`。最终 token 数仍超预算会抛 `RuntimeError`。

## 9.4 citation 来源

Citation 以 matched child evidence 为 grounding span，即：

- `child_chunk_id`
- child `quote_text`
- parent ID
- doc/title/section/page
- score/rerank score

同一 parent 的多个 matched children 可生成多个 citation。Contract builder 统一规范为 `C1...Cn`，DocumentCitationRegistry 再按整个文档稳定去重和 remap。

---

## 10. 当前 active index 与在线读取契约

## 10.1 active pointer

`data/processed/indexes/active_index.json`：

```text
index_version = idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f
manifest_sha256 = bb986761...
```

## 10.2 manifest 已确认字段

| 字段 | 当前值 |
|---|---|
| backend | `milvus_lite` |
| collection | `rag_child_chunks` |
| metric | `COSINE` |
| embedding model | `D:\models\huggingface\embedding\m3e-base` |
| embedding version | `local_m3e_base_v1` |
| embedding dim | 768 |
| embedding device | cuda |
| dataset version | `cleaned_text_unit_all_20260717_v1` |
| document count | 5 |
| parent count | 69 |
| child count | 258 |
| parent artifact | manifest 指定 `parent_chunks.jsonl` |
| child/BM25 artifact | manifest 指定 `child_chunks.jsonl` |
| dense DB | manifest 指定 `milvus.db` |

`RetrievalRuntime` 在 manifest mode 下使用这些路径覆盖旧默认 `data/processed/parent_child_chunks` 与 `vector_store` 路径。

## 10.3 在线读取字段

Dense output fields 请求包含：

- child/parent ids；
- tenant/kb/file/doc ids；
- text/title/section/page；
- chunk/index/embedding/version/is_active metadata。

BM25 从 child JSONL 读取相同业务标识并按 scope 过滤。Parent store 从 parent JSONL 按 parent ID 回填。

## 10.4 已确认的 scope metadata 缺口

对 active artifact 做全量字段计数：

| Artifact | records | `tenant_id` | `kb_id` | `file_id` | `doc_id` |
|---|---:|---:|---:|---:|---:|
| `child_chunks.jsonl` | 258 | 0 | 0 | 0 | 258 |
| `vector_index_records.jsonl` | 258 | 0 | 0 | 0 | 258 |

因此：

- BM25 在 production trusted tenant/KB scope 下不会命中这些记录；这是由 `BM25ChildRetriever.search` 的精确过滤代码直接确定的事实；
- Milvus DB 实体是否也缺相同字段，没有在本轮打开真实 DB 验证；
- 当前 `.venv` 缺 `pymilvus`，真实 scope probe 未执行；dense 行为保持 unknown。

## 10.5 manifest notes 冲突

manifest notes 写：

```text
It intentionally does not activate active_index.
```

但当前 `active_index.json` 已明确指向该 manifest。说明 notes 不能作为当前激活状态的事实源；当前 pointer 才是在线 resolver 使用的事实。

此项只记录 Finding，不修改 manifest。

---

## 11. Evidence 创建点、字段来源与消费边界

## 11.1 Evidence 唯一 canonical 创建点

```text
backend/rag/mapping/result_mapper.py
RAGResultMapper.map
  -> EvidenceMapper.chunks
  -> EvidenceMapper.citations
  -> RAGEvidenceContractBuilder.build
  -> bundle.model_copy(update=...)
```

StageService 不是 Evidence 的创建者；它只读取、remap、投影并写入业务状态。

## 11.2 核心字段 lineage

| Evidence 字段 | 创建 / 写入函数 | 上游来源 | 下游读取者 | 生命周期 / 状态变化 |
|---|---|---|---|---|
| `query` | `RAGEvidenceContractBuilder.build` | `RAGInvocation.query` | SchemeEvidenceService、Generation trace | 原始 user query，不随 rewrite 改写 |
| `rewritten_queries` | `RAGResultMapper.map` | QueryExpansionResult | trace、budget usage、diagnostics | multi-query 才通常非空；HyDE 不进入该列表 |
| `items` | contract builder `_item` | selected + dropped RetrievedChunk | contract reader / quality / audit | 分配 E1...；保留 selected/dropped disposition |
| `selected_evidence_ids` | contract builder | context pack selected | contract reader / Stage | 只代表进入 context，不等于语义充分 |
| `dropped_evidence_ids` | contract builder | context pack dropped /未选候选 | audit / assessment count | 保留 drop reason |
| `citations` | EvidenceMapper + contract normalize | matched child spans | DocumentCitationRegistry、Generation | 初始内部 ID -> C1.. -> 文档级稳定 remap |
| `context` | contract builder `_render_context` | ContextPack rendered text | Stage / section generation | deterministic projection，不是 source of truth |
| `lineage` | contract builder `_lineage` | runtime offline index/static spec trace | Stage context.extra / audit | index/dataset/embedding/spec lineage |
| `assessment` | result mapper `_assessment_from_quality` | final CRAG assessment | Stage evidence flags / Generation | `not_assessed/sufficient/insufficient`；presence 不自动等于 sufficient |
| `correction_trace` | result mapper | evidence_quality correction rounds | Stage normalized output / audit | 每轮 query execution/fusion/rerank/assessment |
| `budget_usage` | result mapper | rewritten queries + correction rounds | Stage / audit | 当前为 retrieval rounds、queries executed、rerank calls |
| `trace` | result mapper | runtime metadata/context pack | lineage / diagnostics | 包含 retrieval plan、index、counts、latency |
| `retrieval_trace_id` | result mapper | request.extra | Stage payload tool call IDs | document / section 调用唯一标识 |
| `status/error` | RAGService | success path or caught exception | StageService | failed bundle 会触发 StageFailure |

## 11.3 Stage 投影与写入

`SchemeEvidenceService.extract_rag_output` 对 canonical bundle 使用：

```text
RAGEvidenceContractReader.projections
  -> RAGContextSchema
  -> selected RetrievedChunkSchema list
  -> CitationSchema list
```

随后把完整 contract 写入：

```text
rag_context.extra.evidence_contract
rag_context.extra.evidence_contract_sha256
rag_context.extra.lineage
```

`DocumentCitationRegistry.remap_bundle` 再更新 citation IDs 和 context marker。

`SharedStateWriter.set_evidence_context` 写入：

```text
state.context_bundle.evidence.query
state.context_bundle.evidence.contract
state.context_bundle.evidence.context_text
state.context_bundle.evidence.retrieved_chunks
state.context_bundle.evidence.citations
state.context_bundle.evidence.used_doc_ids
state.context_bundle.evidence.evidence_available
state.context_bundle.evidence.assessment_status
state.context_bundle.evidence.evidence_sufficient
```

同时 Stage payload 写入：

```text
document_rag_context
document_retrieved_chunks
document_citations
document_rag_output
document_evidence_assessment
document_evidence_available
document_assessment_status
document_tool_call_ids
section_retrieval_enabled
corrective_retrieval_enabled
all_retrieved_chunks
all_citations
all_rag_outputs
```

## 11.4 Generation 消费边界

成功后 graph 路由：

```text
retrieve_document_evidence
  -> initialize_generation
  -> execute_sections
```

`execute_sections` 要求 `payload.document_rag_context` 非空，并把 document context/chunks/citations/assessment 组装成 section execution request。章节内部是否追加 section-aware retrieval 属于后续阶段；document Evidence 是默认 evidence 基线。

本任务停止在该消费边界，不展开 Prompt 和生成算法。

---

## 12. 状态、空结果、失败、fallback 与重试语义

## 12.1 成功且有 Evidence

- RAGService 返回 `status=SUCCESS`；
- contract selected evidence 非空；
- assessment 可为 sufficient 或 insufficient；
- Stage 写共享 evidence 和 payload，进入 generation。

## 12.2 成功但空 Evidence

代码没有把“空结果”自动转换为 tool failure：

- context pack 可为空；
- contract builder 添加 `NO_SELECTED_EVIDENCE`；
- assessment.evidence_available=false；
- CRAG assessment 通常会得到 insufficient；
- 只要 `EvidenceBundle.status` 仍为 SUCCESS，Stage 不在此节点停止生成。

因此：

```text
retrieval execution success != evidence available != evidence sufficient
```

这三个状态在当前 contract 中是分离的。

## 12.3 访问范围 / runtime / retriever 失败

一般异常路径：

```text
RAGRequestMapper / RetrievalRuntime / Retriever / Reranker / Mapper exception
  -> RAGService.retrieve catches
  -> EvidenceBundle(status=FAILED, error=RAG_RETRIEVAL_FAILED)
  -> SchemeGenerationStageService sees success=false
  -> add_error
  -> raise SchemeGenerationStageFailure
  -> graph route technical_failure
```

retryable 由 failure classification 和 `RetryDecisionPolicy` 决定；RAG pipeline 自身不会对所有技术异常隐式重复执行。

## 12.4 LLM fallback

以下组件允许 deterministic fallback：

- multi-query rewrite；
- HyDE；
- CRAG evidence assessment；
- corrective query planner。

但 `ModelCallBudgetExceeded` 和 `WorkflowExecutionCancelled` 明确不 fallback，继续向上抛。

## 12.5 correction 不是技术异常 retry

Corrective retrieval 只在 evidence assessment 判断“证据不足”时触发；它不是网络/模型/DB 技术异常的通用 retry。

## 12.6 capture side effect

`ParentChildRAGEngine.run` 在成功完成后构建 run record，并调用 `RagRunCapture.capture`：

- 有 execution control 时，注册 deferred external commit，要求父 workflow 成功；
- 无 execution control 时，直接写 capture JSONL，但标记 training/eval ineligible。

因此 capture 是 active side effect，但不改变 Evidence 语义。

---

## 13. 实现分类矩阵

| 实现 / 路径 | 判定 | 是否进入当前真实主链 | 证据 |
|---|---|---|---|
| `EnterpriseDocumentWorkflowEngine.build_graph -> retrieve_document_evidence` | active | 是 | 显式 LangGraph node 和 edge |
| `SchemeGenerationStageService.retrieve_document_evidence` | active | 是 | graph 直接注册 |
| `SchemeEvidenceService.retrieve` | active | 是 | stage direct call |
| `LangChainRAGServiceFacade` + authoritative Runnable | active | 是 | SupervisorFactory 组合根安装；Phase5 test 验证 |
| `ObservedRAGService` | active | 是 | RAGServiceFactory 始终包裹 inner service |
| `RAGService` | active production default | 默认是；具体进程状态 unknown | `USE_REAL_RAG_TOOL` 代码默认 true；当前 shell 未设置；未观察运行中进程 |
| `FakeRAGService` | experimental / conditional | production default 否；demo/test 可显式启用 | RAGServiceFactory conditional；多个 tests/demo 设置 false |
| `ParentChildRAGEngine` / `ParentChildRetrievalPipeline` | active | real-RAG 是 | RetrievalRuntime composition |
| identity transformer | active conditional | planner 选择 identity 时 | static spec enabled + selector |
| multi-query transformer / `QueryExpander` rewrite | active conditional | formal/short/ambiguous query 时 | planner + static spec；受控测试执行 4 queries |
| HyDE transformer / `QueryExpander` HyDE | active conditional | abstract query 时 | planner + static spec |
| Milvus dense child retriever | active configured，runtime viability partially unknown | real-RAG 配置中是 | static spec；当前 `.venv` 无 pymilvus，active DB scope 未验证 |
| BM25 child retriever | active configured | real-RAG 配置中是；当前 active data 在 trusted scope 下无命中 | static spec + exact filter + artifact field count |
| child RRF / parent enricher / parent query RRF | active；query RRF conditional | 是 | static spec + pipeline code + tests |
| BGE parent cross-encoder reranker | active configured，current environment model execution unknown | real-RAG 配置中是 | static spec；未运行真实模型 |
| CRAG assessor | active | 是，always executed | pipeline code + static spec |
| correction gate / planner | active conditional | evidence insufficient 时 | policy budget=1 + pipeline code + tests |
| default / lost-in-middle packer | active conditional | ContextGate 二选一 | static spec + gate code |
| `RAGResultMapper` / Evidence contract builder | active | 是 | RAGService success path |
| `DocumentCitationRegistry` | active | 是 | Stage direct call |
| `LegacyEvidenceAdapter` | legacy compatibility | canonical mainline 否 | 仅 `extract_rag_output` 遇到非 EvidenceBundle 时 fallback |
| `HybridParentChildRetriever` | legacy / inactive | 否 | 生产代码无调用；测试明确主链不构造 legacy hybrid |
| `ParentChildRetriever` old wrapper | legacy / inactive | 否 | 生产调用搜索无真实 caller |
| flat `MilvusRetriever` | experimental / eval-only | 否 | 只被 `backend/eval/rag/engine/run_eval_milvus.py` 引用 |
| `NoOpParentRerankerPlugin` | experimental / inactive for current spec | 否 | registry 注册但 static spec 选择 BGE |
| `NoOpEvidenceAssessorPlugin` | experimental / inactive for current spec | 否 | registry 注册但 static spec 选择 CRAG；测试使用 |
| `SchemeGenerationUseCase.run` | legacy / unreachable | 否 | TASK-001 runtime evidence |
| planner request override in current document caller | inactive for this caller | 否 | planner 支持，但 EvidenceService 未透传 override |

---

## 14. 运行证据

## 14.1 通过的定向测试

命令覆盖：

- configured multi-query full stack；
- corrective retrieval gate/planner/reassessment；
- Evidence contract source-of-truth；
- index/model/pipeline lineage；
- Scheme 层信任 canonical contract；
- shared state 存储 contract 且不把 presence 当 sufficiency；
- LangChain facade 接管 production mainline。

结果：

```text
10 passed in 7.98s
```

另运行 trusted-scope / narrowing 测试：

- tenant conflict；
- caller tenant conflict；
- authorized KB narrowing；
- unauthorized KB fail closed；
- doc/file 只允许收窄。

结果：

```text
6 passed
1 failed
```

失败项不是断言 scope 逻辑错误，而是真实 Milvus helper 子进程导入失败：

```text
ModuleNotFoundError: No module named 'pymilvus'
```

## 14.2 不能作为 production 端到端证据的原因

上述测试主要使用 fake resource pool、temp data 或 controlled components，能够证明：

- 调用顺序；
- schema；
-数量变化；
- gate/correction 行为；
- facade 主链；
- scope mapping 规则。

它们不能证明：

- 当前 active Milvus DB 可以被当前环境打开；
- 当前 active data 能通过 trusted tenant/KB filter；
- 当前 BGE 与 embedding 模型能在实际设备完成加载和推理；
- 当前真实业务 Query 会得到非空且充分的 Evidence。

## 14.3 额外测试 Finding

`backend/tests/test_p1_security_durability_identity_closure.py` 在 collection 阶段出现既有循环导入：

```text
document_gate
  -> scheme_writer package __init__
  -> use_case
  -> stage_service
  -> document_gate (partially initialized)
```

因此该文件中的 3 个 RAG scope tests 本轮无法收集。只记录 Finding，不修改代码。

---

## 15. 新发现但未处理的问题（Findings）

以下均未修复，也不代表本任务提出重构方案。

### F-002-01：TASK 起点与真实主链不一致

- TASK 文本期待 `SchemeGenerationUseCase.run`；
- TASK-001 已证明其不可达；
- 本任务从唯一真实 stage boundary 继续。

建议交接：AI Project Manager / Workflow Coordinator。

### F-002-02：active index 缺 trusted-scope metadata

- 258/258 child records 无 tenant/kb/file；
- 258/258 vector index records无 tenant/kb/file；
- online mapper 要求 tenant+KB filter；
- BM25 trusted-scope 下必然无命中；
- dense exact behavior 未确认。

建议交接：TASK-003 Offline Data / Index Engineer、TASK-005 Architect、TASK-006 QA。

### F-002-03：active manifest notes 与 active pointer 冲突

- notes 说“不激活”；
- current pointer 已激活。

建议交接：TASK-003。

### F-002-04：当前 DevSpace `.venv` 缺 `pymilvus`

- real Milvus scope test 无法执行；
- 不能据此判断部署环境一定缺包，只能判断本次验证环境缺包。

建议交接：TASK-006 QA / Runtime owner。

### F-002-05：一个 RAG security test 文件存在 collection-time circular import

- 影响本轮 3 个 scope tests 收集；
- 未改代码。

建议交接：TASK-006 QA / 相应业务模块 owner。

### F-002-06：`budget_usage.queries_executed` 不覆盖全部真实 query 执行

`RAGResultMapper.map` 当前计算：

```text
1 + len(rewritten_queries)
```

因此至少不包含：

- HyDE hypothetical query；
- corrective queries。

该字段可能低于 pipeline 实际执行 query 数。只记录字段 lineage 事实。

建议交接：TASK-005 Architect / TASK-006 QA。

### F-002-07：Evidence lineage 的 reranker model/version 使用占位值

`RAGResultMapper` 写入：

```text
reranker_model = "runtime-config"
reranker_version = "unknown"
```

而真实 component execution metadata 中存在 model name/device 等信息。当前 canonical lineage 未直接投影真实 reranker identity。

建议交接：TASK-005 Architect。

### F-002-08：当前真实模型和 active DB 的端到端执行仍 unknown

原因：

- 不在共享工作区运行会产生 capture/任务状态写入的完整业务流程；
- 当前 `.venv` 缺 pymilvus；
- active data scope 字段缺失；
- 未运行真实 embedding/BGE/LLM。

建议交接：TASK-006 QA 在隔离环境执行。

---

## 16. 未确认问题

1. active Milvus DB 实体 schema 是否包含 tenant/kb/file 字段，或 builder 是否在插入时以其他方式补充；当前 JSONL/vector records 没有这些字段。
2. Milvus 对当前 trusted filter 是返回空结果还是字段解析错误。
3. 实际部署进程是否使用默认 real-RAG，还是显式设置 `USE_REAL_RAG_TOOL=false`。
4. 实际部署环境的 `pymilvus`、embedding model、reranker model、CUDA 资源是否完整。
5. 当前真实业务 `RetrievalAccessScopeSchema.authorized_kb_ids` 与 active dataset 的业务标识如何对应。
6. 空/insufficient document Evidence 在后续 document quality gate 的最终处置，本任务未展开。

---

## 17. 对后续任务的事实输入

### TASK-003 Offline Data / Index Engineer

在线主链明确依赖：

- child JSONL：BM25 source；
- parent JSONL：parent backfill；
- Milvus child collection：dense source；
- child records 的 tenant/kb/file/doc；
- parent/child IDs、text、title、section、page；
- embedding/index/version metadata；
- active pointer + manifest hash。

当前 active artifact 缺 tenant/kb/file，需由 TASK-003 仅调查离线产生点，不由本任务推断。

### TASK-004 Algorithm Lead

当前真实算法落点与执行条件：

- Adaptive planner：每请求选择 identity / multi-query / HyDE；
- multi-query：最多 3 rewrites；
- HyDE：最多 1 hypothetical document；
- dense + BM25 child retrieval；
- child RRF；
- parent dedup/backfill；
- optional parent query RRF；
- BGE parent rerank；
- CRAG assessment；
- evidence-insufficient corrective retrieval，默认 1 轮、最多 2 queries；
- context gate / lost-in-middle。

本文只证明位置和条件，不评价算法优劣。

### TASK-005 Architect

canonical Evidence 创建者：

```text
RAGResultMapper + RAGEvidenceContractBuilder
```

主要 writer / reader：

```text
writer:
  EvidenceMapper
  RAGEvidenceContractBuilder
  RAGResultMapper
  DocumentCitationRegistry
  SharedStateWriter

reader:
  RAGEvidenceContractReader
  SchemeGenerationStageService
  execute_sections / section execution
```

需关注 F-002-06、F-002-07 和 active scope field ownership。

### TASK-006 QA

建议在隔离副本 / 临时 output root 中验证：

- 安装 real-RAG extra 后打开 active Milvus DB；
- trusted tenant/KB filter 对 active data 的结果；
- real embedding + BGE rerank；
- identity / multi-query / HyDE 三分支；
- sufficient / insufficient + one corrective round；
- empty result 仍 success 的业务行为；
- failed bundle -> stage technical failure；
- Evidence fields/counts/citations/lineage 与 capture 一致。

---

## 18. 建议任务状态

建议 TASK-002 状态：**review**。

理由：

- 代码级真实主链、输入、阶段、Evidence 创建和 Generation 消费边界已调查完成；
- active/inactive/legacy/experimental/unknown 已分类；
- 有受控运行证据；
- production active index 的 trusted-scope 兼容性和真实模型运行仍需 TASK-003 / TASK-006 独立验证；
- TASK 起点偏差需 PM 接受或修订任务描述。
