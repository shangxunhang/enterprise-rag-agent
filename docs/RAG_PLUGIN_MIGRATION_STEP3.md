# RAG 插件化迁移 Step 3：Retriever、Fusion 与 Parent-Child Enricher

## 1. 本阶段目标

将原来封装在 `HybridParentChildRetriever` 中的职责拆为独立、配置驱动的插件：

```text
Milvus Dense Child Retriever
+ BM25 Child Retriever
→ Child RRF Fusion
→ Parent-Child Candidate Enricher
→ Query RRF Fusion（仅多查询时执行）
→ 现有 Reranker
```

主 `ParentChildRetrievalPipeline` 不再：

- 构造 `HybridParentChildRetriever`；
- 直接调用 `rrf_fuse`；
- 直接构造 `MultiQueryFusion`；
- 通过 Dense/BM25 字符串分支决定实现。

## 2. 为什么增加两个 Fusion 插槽

系统中存在两种不同维度的融合：

1. `fusion`：同一个 Query 下融合 Dense 与 BM25 的 Child 命中；
2. `query_fusion`：融合 Original Query、Multi Query、HyDE 等多个 Query 的 Parent 级结果。

二者虽然都使用 RRF，但输入粒度和调用位置不同，因此配置中必须分别声明，不能继续混成一个隐式步骤。

## 3. 配置 Schema

本阶段为破坏性升级，配置 Schema 从：

```text
online_rag_pipeline_config_v1
```

升级为：

```text
online_rag_pipeline_config_v2
```

核心配置示例：

```yaml
retrievers:
  - name: milvus_dense_child
    version: v1
    enabled: true
    params:
      top_k: 10

  - name: bm25_child
    version: v1
    enabled: true
    params:
      top_k: 10

fusion:
  name: rrf_child
  version: v1
  enabled: true
  params:
    rrf_k: 60

query_fusion:
  name: rrf_parent
  version: v1
  enabled: true
  params:
    rrf_k: 60
    top_k: 5

candidate_enricher:
  name: parent_child
  version: v1
  enabled: true
  params:
    top_k: 5
    context_granularity: parent
    dedup_parent: true
```

这些参数由配置文件驱动。旧的 `dense_top_k`、`keyword_top_k`、`candidate_top_k`、`rrf_k` 请求字段暂时保留用于兼容，但在已经迁移的检索栈中不会覆盖 Profile 配置，并会在 Trace 中标记：

```text
ignored_by_configured_stack: true
```

## 4. 新增稳定数据契约

新增：

```text
RetrievalRequest
CandidateSet
```

插件之间不再直接传递无语义的多个 `list[dict]`。候选内部暂时继续使用现有字典结构，以保证 Citation、Rerank 和上下文构建行为不变；外层通过 `CandidateSet` 统一来源、查询和元数据。

## 5. Parent-Child 行为保留

`ParentChildCandidateEnricher` 保留原有关键语义：

- Child 命中；
- 按 `parent_chunk_id` 去重；
- Parent Backfill；
- `child_text` 与 `parent_text` 分离；
- `matched_child_chunk_ids`；
- `matched_child_chunks`；
- `source_ranks`；
- `source_scores`；
- `rrf_contributions`；
- Dense/BM25 命中数量；
- Embedding、Index、Vector DB 元数据。

并通过新旧实现对比测试验证核心字段一致。

## 6. 资源生命周期

新增 `ParentChildResourcePool`：

- Dense Retriever、BM25 索引和 Parent Store 按需加载；
- 未选择的插件不会提前加载对应重资源；
- 每个 RAG Runtime 只维护一份资源实例；
- 插件通过 `build_context['resource_pool']` 获取资源。

## 7. Trace

`pipeline_components` 现在记录：

```text
query_transformers
retrievers
fusion
query_fusion
candidate_enricher
context_packer
```

每个组件包含：

```text
category
name
version
implementation
enabled
params
```

每个 Query 的执行 Trace 还包括：

```text
source_candidate_counts
source_metadata
fused_child_count
enriched_parent_count
query_fusion_execution
```

## 8. 测试

专项测试：

```powershell
python -m pytest `
  backend\tests\test_rag_config_driven_context_packer.py `
  backend\tests\test_rag_config_driven_query_transformers.py `
  backend\tests\test_rag_config_driven_retrieval_stack.py `
  -q
```

预期：

```text
23 passed
```

完整测试在不携带真实项目数据的分享包中：

```text
108 passed, 1 deselected
```

被排除的测试依赖：

```text
data/processed/parent_child_chunks/child_chunks.jsonl
```

在用户真实项目目录中应运行完整测试，不需要排除。

## 9. 本阶段验收标准

- 修改 YAML 可替换 Dense/BM25 Retriever；
- 修改 YAML 可替换 Child Fusion；
- 修改 YAML 可替换 Query Fusion；
- 修改 YAML 可替换 Parent-Child Enricher；
- 新增 Retriever 不修改主 Pipeline；
- 主 Pipeline 不直接构造 Hybrid Retriever 或 RRF；
- Parent-Child 与 Citation 所需证据字段保持不变；
- 插件名称、版本、参数和执行数量进入 Trace；
- 原有 Agent、Citation、HardGate 流程未在本阶段改动。
