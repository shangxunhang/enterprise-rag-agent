# TASK-002 Online RAG Chain — Handoff

## 1. Handoff Metadata

- Task ID: `TASK-002`
- Title: Online RAG Chain
- Owner Role: RAG Engineer
- Investigation Output: `docs/team/ANALYSIS/TASK-002-online-rag-chain.md`
- Baseline Branch: `main`
- Baseline Commit: `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`
- Workspace State: dirty；结论对应 HEAD + 调查时未提交内容
- Suggested Task Status: `review`

---

## 2. 已确认事实

### 2.1 真实 RAG 起点

TASK-001 已证明 `SchemeGenerationUseCase.run()` 当前生产主链不可达。本任务没有使用该虚假起点。

当前真实边界：

```text
backend/apps/enterprise_document/workflows/top_level_business_graph.py
EnterpriseDocumentWorkflowEngine.build_graph
  -> retrieve_document_evidence node
  -> EnterpriseDocumentWorkflowEngine._run_stage
  -> SchemeGenerationStageService.retrieve_document_evidence
  -> SchemeEvidenceService.retrieve
```

上游：`plan_document`。

成功下游：`initialize_generation -> execute_sections`。

失败下游：`technical_failure -> finalize_workflow_failure`。

分类：

- graph node / stage method：active；
- `SchemeGenerationUseCase.run`：legacy / unreachable。

### 2.2 canonical RAG port 与 runtime

已确认调用链：

```text
SchemeEvidenceService.retrieve
  -> LangChainRAGServiceFacade.retrieve/invoke
  -> authoritative RAG Runnable
  -> ObservedRAGService.retrieve
  -> RAGService.retrieve
  -> RAGRequestMapper.map
  -> RetrievalRuntime.retrieve
  -> ParentChildRAGEngine.run
```

LangChain facade 只承担 Runnable 接口，不拥有检索算法。

`RuntimeOptions.resolve` 的代码默认 `USE_REAL_RAG_TOOL=true`；当前调查 shell 未设置该变量。实际部署进程是否覆盖该变量为 unknown。

### 2.3 Query 输入与 trusted scope

Document-level original query：

```text
ProjectInput.user_query
```

`SchemeEvidenceService.retrieve` 生成 `RAGToolInputSchema`，包含：

- task/run/agent identity；
- query；
- requested KB/doc/file narrowing；
- trusted `RetrievalAccessScopeSchema`；
- citation requirement；
- max context chars/items；
- model context requirements；
- retrieval scope/trace/document context metadata。

`RAGRequestMapper.map` fail-closed：

- production real-RAG 缺 access scope 时失败；
- tenant conflict 失败；
- KB 无授权交集失败；
- doc/file 越权失败；
- caller 只能缩小 trusted scope。

输出包括：

- Milvus `filter_expr`；
- BM25 `keyword_scope`；
- planner/runtime context。

### 2.4 Query transform

每次 request 只选择一种：

```text
identity | multi_query | hyde
```

不是三者串行。

- identity：1 个 retrieval query；
- multi-query：original + 最多 3 rewrites，最多 4 个；
- HyDE：original + 1 hypothetical document，最多 2 个。

正式方案/报告类任务通常由 planner 选择 multi-query。LLM 一般异常可 deterministic fallback；budget exceeded / cancellation 继续抛出。

### 2.5 当前配置选择的 retrieval stack

`backend/rag/config/static_retrieval_v1.yaml`：

```text
per retrieval query:
  Milvus dense child top-10
  + BM25 child top-10
  -> child RRF(k=60)
  -> parent dedup/backfill top-5

if query count > 1:
  -> parent/query RRF(k=60, top-5)

then:
  -> BGE parent cross-encoder rerank top-5
  -> CRAG evidence assessment
  -> conditional corrective retrieval
  -> context packing max 5
```

每 query 数量上界：

```text
dense <= 10
keyword <= 10
child RRF union <= 20
parent enriched <= 5
reranked <= 5
```

### 2.6 Corrective retrieval

当前 policy 默认 correction budget = 1。

触发条件：evidence insufficient 且 budget 剩余。

每轮最多 2 个 corrective queries，每条 query 重复完整 dense/BM25/RRF/parent-backfill 流程；默认把原 reranked candidates 合并，随后 query fusion、rerank、reassess。

它是证据不足纠错，不是技术异常通用 retry。

### 2.7 Context 与 citation

`ContextGate` 在 default 与 lost-in-middle packer 之间选择：

```text
estimated tokens > evidence budget * 0.65
  -> lost-in-middle
else
  -> default
```

两者最多选 5 items，并记录 dropped/truncated evidence。

Citation grounding 来自 matched child span；context 默认使用 parent text，parent 缺失时回退 child text。

### 2.8 Evidence 创建与状态写入

canonical Evidence 创建点：

```text
backend/rag/mapping/result_mapper.py
RAGResultMapper.map
  -> EvidenceMapper.chunks/citations
  -> RAGEvidenceContractBuilder.build
```

主要字段：

- query / rewritten_queries；
- selected + dropped evidence items；
- citations；
- context projection；
- lineage；
- assessment；
- correction trace；
- budget usage；
- runtime trace；
- task/run/status/error/retrieval trace id。

Stage 后续执行：

```text
SchemeEvidenceService.extract_rag_output
  -> RAGEvidenceContractReader.projections
  -> DocumentCitationRegistry.remap_bundle
  -> SharedStateWriter.set_evidence_context
```

写入：

- `state.context_bundle.evidence.*`；
- payload `document_rag_context`；
- `document_retrieved_chunks`；
- `document_citations`；
- `document_rag_output`；
- assessment/availability/tool call IDs；
- aggregate `all_*` fields。

Generation 消费边界：`execute_sections` 把 document context/chunks/citations/assessment 传给 section execution。

### 2.9 空 Evidence 与失败 Evidence 不同

已确认状态语义：

```text
retrieval execution success
!= evidence available
!= evidence sufficient
```

- 成功但空 Evidence：contract 标记无 selected evidence，assessment 可 insufficient，Stage 不因空结果自动停止；
- failed Evidence bundle：Stage 添加 error、抛 `SchemeGenerationStageFailure`，graph 进入 technical failure。

### 2.10 当前 active index

`data/processed/indexes/active_index.json` 当前指向：

```text
idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_869c349c871f
```

manifest 已确认：

- Milvus Lite；
- collection `rag_child_chunks`；
- COSINE；
- m3e-base；
- 768 dimensions；
- dataset `cleaned_text_unit_all_20260717_v1`；
- 5 docs / 69 parents / 258 children。

在线 runtime 在 manifest mode 下读取该 manifest 指定的 parent JSONL、child JSONL 和 Milvus DB，而不是旧默认路径。

### 2.11 active index scope metadata 缺失

全量字段计数已确认：

| Artifact | records | tenant_id | kb_id | file_id | doc_id |
|---|---:|---:|---:|---:|---:|
| active `child_chunks.jsonl` | 258 | 0 | 0 | 0 | 258 |
| active `vector_index_records.jsonl` | 258 | 0 | 0 | 0 | 258 |

直接后果：

- BM25 在 production trusted tenant/KB scope 下按现有代码不会命中；
- active Milvus DB 实体行为未确认。

### 2.12 实现分类

**active / active conditional**

- LangGraph document retrieval node；
- Stage/EvidenceService；
- LangChain facade/Runnable；
- ObservedRAGService/RAGService；
- request mapper/runtime/engine/pipeline；
- identity/multi-query/HyDE；
- dense/BM25、child RRF、parent enrichment、conditional query RRF；
- BGE rerank；
- CRAG assessment；
- correction gate/planner；
- context gate/default/lost-in-middle；
- result mapper/Evidence contract/citation registry/state writer。

**legacy / inactive**

- `SchemeGenerationUseCase.run`；
- `HybridParentChildRetriever`；
- old `ParentChildRetriever`；
- `LegacyEvidenceAdapter` for canonical mainline。

**experimental / conditional**

- `FakeRAGService`；
- flat `MilvusRetriever` eval path；
- no-op reranker / no-op assessor test/baseline path。

**unknown**

- 当前部署进程是否覆盖 real/fake RAG；
- current active DB 在 trusted filter 下的 dense behavior；
- current deployment model/package/device readiness。

---

## 3. 未确认问题

1. Active Milvus DB 实体 schema 是否实际包含 tenant/kb/file 字段。
2. Milvus 对当前 scope filter 返回空还是字段错误。
3. 实际部署环境是否安装 `pymilvus` 和 real-RAG optional dependencies。
4. 实际部署进程是否使用默认 real-RAG。
5. Active dataset 与 `authorized_kb_ids` 的业务映射是什么。
6. 当前 embedding/BGE/quality LLM 能否在部署设备完成真实运行。
7. 空/insufficient document Evidence 最终如何被 document quality gate 处置；TASK-002 未展开。

---

## 4. 新发现但未处理的问题

### F-002-01 TASK 起点偏差

TASK 文本以 `SchemeGenerationUseCase` 为起点，但当前真实主链直接调用 StageService。

建议对象：AI Project Manager / Workflow Coordinator。

### F-002-02 Active index 缺 trusted-scope metadata

Active child/vector records 无 tenant/kb/file；online mapper 强制 tenant+KB scope。

建议对象：TASK-003 Offline Data / Index Engineer、TASK-005 Architect、TASK-006 QA。

### F-002-03 Manifest notes 与 pointer 冲突

Manifest notes 声称不激活，但 active pointer 已指向它。

建议对象：TASK-003。

### F-002-04 当前 DevSpace `.venv` 缺 `pymilvus`

真实 Milvus scope test 无法执行。不能推断部署环境同样缺包。

建议对象：TASK-006 QA / Runtime owner。

### F-002-05 一个 security test 文件存在 collection-time circular import

`test_p1_security_durability_identity_closure.py` 无法收集其中 3 个 RAG scope tests。

建议对象：TASK-006 QA / 业务模块 owner。

### F-002-06 Evidence `budget_usage.queries_executed` 可能低计

当前写法为：

```text
1 + len(rewritten_queries)
```

未覆盖 HyDE hypothetical query 和 corrective queries。

建议对象：TASK-005 Architect / TASK-006 QA。

### F-002-07 Evidence lineage 的 reranker identity 是占位值

当前 canonical trace 写：

```text
reranker_model = runtime-config
reranker_version = unknown
```

建议对象：TASK-005 Architect。

### F-002-08 Production real-data end-to-end 仍未验证

代码和受控测试已确认，不等于 active index + trusted scope + real models 已成功运行。

建议对象：TASK-006 QA。

以上 Finding 均未修复。

---

## 5. 对 TASK-004 Algorithm Lead 的交接

请只基于以下已确认的真实执行点继续判断算法归属：

| Algorithm candidate | 实际代码位置 | 执行条件 | 当前状态 |
|---|---|---|---|
| Adaptive retrieval planning | `rag/planning/retrieval_planner.py::AdaptiveRetrievalPlanner.plan` | 每次 request | active |
| Multi-query | `rag/plugins/query_transformers/plugin.py::MultiQueryTransformer` | planner mode=multi_query | active conditional |
| HyDE | 同文件 `HyDEQueryTransformer` | planner mode=hyde | active conditional |
| Dense retrieval | `MilvusDenseChildRetrieverPlugin` | 每个 retrieval query | active configured |
| BM25 | `BM25ChildRetrieverPlugin` | 每个 retrieval query | active configured |
| RRF source fusion | `ChildRRFFusionPlugin` | 每个 retrieval query | active |
| Parent-child enrichment | `ParentChildCandidateEnricher` | source fusion 后 | active |
| RAG-Fusion-style parent query fusion | `ParentRRFFusionPlugin` | query count > 1 | active conditional |
| Cross-encoder rerank | `BGEParentCrossEncoderRerankerPlugin` | fusion/enrichment 后 | active configured |
| CRAG-style assessment | `CRAGEvidenceAssessorPlugin` | initial/final rerank 后 | active |
| Corrective retrieval | gate + planner + pipeline correction loop | insufficient + budget | active conditional |
| Lost-in-middle packing | `ContextGate` + packer | estimated tokens > 65% budget | active conditional |

TASK-002 没有评价这些算法是否应该保留、替换或移动。

---

## 6. 对 TASK-005 Architect 的交接

### Evidence owner facts

- public canonical schema：`backend/schemas/rag.py::EvidenceBundleSchema`；
- construction owner：`RAGResultMapper` + `RAGEvidenceContractBuilder`；
- retrieval-to-schema mapper：`EvidenceMapper`；
- projection reader：`RAGEvidenceContractReader`；
- document citation identity writer：`DocumentCitationRegistry`；
- shared state writer：`SharedStateWriter.set_evidence_context`；
- primary consumer：Scheme document/section generation stages。

### 需要继续确认的 ownership 问题

- tenant/kb/file metadata 由哪个离线 contract owner保证；
- `budget_usage` 的真实计数定义；
- reranker model/version lineage 的 writer；
- full Evidence contract 与 `document_rag_output`/context projections 的长期唯一事实源边界；
- 空 evidence / insufficient evidence 的跨层状态责任。

---

## 7. 对 TASK-003 Offline Data / Index Engineer 的交接

在线读取要求已确认：

- dense 与 BM25 都需要 child records；
- parent backfill 需要 parent records；
- trusted scope 需要 tenant/kb，optional file/doc；
- citation 需要 child ID、parent ID、doc、text、title/section/page；
- lineage 需要 dataset/index/embedding/version；
- resolver 依赖 active pointer、manifest 和 hash。

当前 active artifacts 仅确认 doc_id，缺 tenant/kb/file。请沿离线真实构建链确认这些字段：

- 原始来源；
- schema owner；
- chunker 是否保留；
- vector record 是否保留；
- Milvus entity schema 是否写入；
- activation validation 是否检查。

TASK-002 未对离线实现作推断。

---

## 8. 对 TASK-006 QA 的交接

建议使用隔离工作区、临时 output/capture 路径和完整 real-RAG 环境，验证：

1. active pointer/manifest hash 解析；
2. Milvus DB collection/schema；
3. trusted tenant/KB/file/doc filter；
4. BM25 与 dense 各自命中数量；
5. identity/multi-query/HyDE 分支；
6. child RRF/parent backfill/query RRF/rerank 数量；
7. sufficient 与 insufficient + one corrective round；
8. empty success 与 failed bundle 的不同 graph route；
9. context drop/truncate/lost-in-middle；
10. Evidence selected/dropped/citation/lineage/assessment/correction/budget fields；
11. shared state 与 payload projection 一致；
12. capture deferred commit 行为。

本轮运行证据：

```text
10 targeted RAG chain/contract tests passed
6 trusted-scope/narrowing tests passed
1 real Milvus scope test failed to start: pymilvus missing
1 security test module failed collection: circular import
```

---

## 9. 建议交接对象

- AI Project Manager / Workflow Coordinator：接受 TASK 起点偏差，决定是否更新任务表述；
- TASK-003 Offline Data / Index Engineer：active index scope metadata 与 manifest；
- TASK-004 Algorithm Lead：算法位置、条件和 ownership；
- TASK-005 Architect：Evidence/lineage/budget/scope contract ownership；
- TASK-006 QA：真实 active index + trusted scope + real model 端到端验证；
- Runtime / dependency owner：当前验证环境缺 `pymilvus`。

---

## 10. 建议任务状态

建议：`review`。

依据：

- TASK-002 范围内的代码调用链、数据流、状态、异常、Evidence 创建和 Generation 消费边界已完成；
- active/inactive/legacy/experimental/unknown 已分类；
- 已提供定向运行证据；
- production real-data 端到端仍由 TASK-003 / TASK-006 验证；
- 所有新问题只记录，未修改或声称修复。
