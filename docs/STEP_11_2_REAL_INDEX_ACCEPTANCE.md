# Step 11.2：真实数据 + m3e-base + Milvus Lite

## 阶段边界

本阶段只负责：

```text
cleaned_text_units.jsonl
→ Parent/Child Chunk
→ m3e-base Embedding
→ Milvus Lite
→ IndexManifest
→ 构建后完整性验证
→ 检索自检报告
```

本阶段**不更新** `active_index.json`。索引激活、在线刷新和回滚属于 Step 11.3。

## 输入数据最低要求

每行一个 JSON 对象，至少应包含：

```json
{
  "unit_id": "doc_001_unit_001",
  "doc_id": "doc_001",
  "text": "正文内容"
}
```

建议同时保留 `title`、`section`、`page_start`、`page_end`、`source_uri`、`cleaning_version` 等字段，供引用追溯使用。

## 安装缺失依赖

在现有 Conda 环境中执行：

```powershell
pip install -r requirements-step-11-2.txt
```

不要让 `pip` 覆盖当前可用的 CUDA PyTorch；如果依赖解析准备更换 PyTorch，应停止安装并单独安装 `pymilvus`、`sentence-transformers`。

## 配置检查

默认配置：

```text
backend/rag/index_profiles/m3e_milvus_lite_real_v1.yaml
```

确认以下路径与本机一致：

```text
source.path
chunker.params.tokenizer_model_name
embedding.model_name
embedding.device
```

源数据发生变化时必须更新 `dataset_version`，否则相同 `index_version` 下会触发源数据哈希冲突。

## 正式验收命令

在项目根目录执行：

```powershell
$env:PYTHONPATH="D:\MyCode\rag-agent\backend;D:\MyCode\rag-agent\scripts"

D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe `
  scripts\run_step_11_2_acceptance.py `
  --index-config backend\rag\index_profiles\m3e_milvus_lite_real_v1.yaml
```

也可以不修改 YAML，直接覆盖路径：

```powershell
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe `
  scripts\run_step_11_2_acceptance.py `
  --index-config backend\rag\index_profiles\m3e_milvus_lite_real_v1.yaml `
  --source-path D:\your-data\cleaned_text_units.jsonl `
  --embedding-model D:\models\huggingface\embedding\m3e-base `
  --device cuda `
  --dataset-version cleaned_text_units_20260717_v1
```

## 通过标准

命令退出码必须为 `0`，报告中的状态必须为：

```json
{
  "status": "success"
}
```

核心检查全部为 `passed`：

```text
manifest_exists
manifest_schema
required_artifacts_declared
artifact_files_exist
artifact_sha256
artifact_record_counts
vector_matrix_shape
vector_values_finite
vectors_l2_normalized
chunk_ids_unique
child_parent_references
child_index_lineage
milvus_collection_exists
milvus_entity_count
milvus_self_retrieval
```

报告默认写入：

```text
<data/processed/indexes>/<index_version>/step_11_2_acceptance_report.json
```

## 已有索引只做复验

```powershell
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe `
  scripts\run_step_11_2_acceptance.py `
  --index-config backend\rag\index_profiles\m3e_milvus_lite_real_v1.yaml `
  --skip-build `
  --manifest-path data\processed\indexes\<index_version>\index_manifest.json
```

## 失败处理

- `embedding model path not found`：模型路径错误。
- `embedding dimension mismatch`：配置维度不是模型实际维度；m3e-base应与配置保持一致。
- `index version collision`：同一 `dataset_version` 下源文件内容发生变化；确认数据后提高 `dataset_version`。
- `milvus_entity_count failed`：Milvus实体数和Child Chunk数不一致，不得进入Step 11.3。
- `milvus_self_retrieval failed`：向量文件和Milvus记录可能错位，不得激活该索引。
