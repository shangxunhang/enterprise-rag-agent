# RAG 可插拔改造：Step 1（配置驱动 ContextPacker）

## 本阶段目标

建立一条完整、可运行的插件闭环：

```text
YAML/JSON Profile
→ Pydantic 严格校验
→ 版本化 ComponentRegistry
→ 按配置构建具体插件
→ 注入现有 ParentChildRAGEngine
→ Trace 记录配置路径、配置哈希和组件元数据
```

本阶段只迁移 `ContextPacker`，不修改检索、融合、Rerank、CRAG 等算法行为。

## 新增组件

- `backend/rag/config/pipeline_config.py`
  - `ComponentConfig`
  - `OnlineRAGPipelineConfig`
  - `PipelineConfigLoader`
  - 稳定 SHA-256 配置哈希
- `backend/rag/registry/component_registry.py`
  - `(category, name, version)` 唯一定位插件
  - 重复注册和未知组件启动失败
- `backend/rag/registry/default_registrations.py`
  - 内置插件注册入口
- `backend/rag/plugins/context_packers/`
  - `DefaultContextPacker`
  - `LostInMiddleContextPacker`
- `backend/rag/profiles/`
  - `hybrid_v1.yaml`
  - `hybrid_default_context_v1.yaml`

## 切换方式

### 默认：Lost-in-the-Middle

```powershell
python scripts/run_demo.py `
  --rag-pipeline-config backend/rag/profiles/hybrid_v1.yaml
```

### 默认顺序 ContextPacker

```powershell
python scripts/run_demo.py `
  --rag-pipeline-config backend/rag/profiles/hybrid_default_context_v1.yaml
```

也可以使用环境变量：

```powershell
$env:RAG_PIPELINE_CONFIG_FILE="backend/rag/profiles/hybrid_v1.yaml"
python scripts/run_demo.py
```

## 当前验收结果

```text
新增插件测试：5 passed
不依赖缺失真实数据的全量回归：83 passed
RAG evidence 其余测试：7 passed，1 deselected
```

被排除的测试依赖分享包中未包含的：

```text
data/processed/parent_child_chunks/child_chunks.jsonl
```

## 当前边界

完成配置驱动的插槽：

```text
ContextPacker
```

尚未迁移，仍由旧代码组合：

```text
QueryTransformer
Retriever
Fusion
CandidateEnricher / ParentResolver
Reranker
EvidenceGrader
Adaptive Router
GenerationChecker
RepairStrategy
```

## 下一步

Step 2 迁移 `QueryTransformer`：

```text
IdentityQueryTransformer
MultiQueryTransformer
HyDEQueryTransformer
QueryTransformChain
```

目标是删除 `QueryExpander` 中根据 `retrieval_strategy` 判断 RAG-Fusion / HyDE 的职责，但通过 Legacy Profile Adapter 保持现有 CLI 策略名称兼容。
