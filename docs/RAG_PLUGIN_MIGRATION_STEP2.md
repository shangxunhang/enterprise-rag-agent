# RAG 插件化迁移 Step 2：QueryTransformer

## 目标

将查询扩展从 `retrieval_strategy` 字符串分支迁移为外部配置驱动插件链：

```text
YAML Profile
→ ComponentConfig 校验
→ Registry 按 name/version 构建
→ QueryTransformChain 按声明顺序执行
→ Retrieval Pipeline 只消费统一 QueryExpansionResult
→ Trace 记录配置哈希和插件实现
```

## 已实现插件

```text
identity@v1
multi_query@v1
hyde@v1
```

组合能力：

```text
identity
multi_query
hyde
multi_query → hyde
```

## 配置示例

### Hybrid baseline

```yaml
query_transformers:
  - name: identity
    version: v1
    enabled: true
    params: {}
```

### RAG-Fusion

```yaml
query_transformers:
  - name: multi_query
    version: v1
    enabled: true
    params:
      num_rewrites: 3
      use_llm: true
      fallback_to_deterministic: true
```

### RAG-Fusion + HyDE

```yaml
query_transformers:
  - name: multi_query
    version: v1
    params:
      num_rewrites: 3
  - name: hyde
    version: v1
```

## 重要行为变化

查询扩展不再由以下参数决定：

```text
--retrieval-strategy rag_fusion
--retrieval-strategy hyde
--enable-hyde
--num-rewrites
```

这些旧参数当前只保留在兼容接口和 Trace 中。查询扩展的真实来源是：

```text
--rag-pipeline-config backend/rag/profiles/<profile>.yaml
```

运行示例：

```powershell
python scripts/run_demo.py `
  --rag-pipeline-config backend\rag\profiles\rag_fusion_v1.yaml
```

## 当前仍未插件化

```text
Retriever
Fusion
CandidateEnricher / ParentResolver
Reranker
EvidenceGrader / CRAG
Adaptive Router
GenerationChecker / Self-RAG
RepairStrategy
```

## 验收标准

1. `ParentChildRetrievalPipeline` 不再调用 `QueryExpander.expand(strategy=...)`。
2. Profile 中至少存在一个启用的 QueryTransformer。
3. 插件按 YAML 顺序执行。
4. 新增 QueryTransformer 只需实现 Port、注册、配置和测试。
5. Trace 中记录 `pipeline_components.query_transformers`。
6. 同一 Pipeline 代码可通过不同 Profile 产生不同 Query Set。
