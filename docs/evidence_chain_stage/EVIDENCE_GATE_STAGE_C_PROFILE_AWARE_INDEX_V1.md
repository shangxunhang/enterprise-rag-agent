# Evidence Gate Stage C：Profile-Aware Index V1

## 1. 状态

- 阶段：C
- 状态：已完成并激活
- 激活时间：2026-08-03T09:52:37.062362+00:00
- Active Index：`idx_type_aware_parent_child_v1_m3e_base_local_m3e_base_profiled_v1_a96b7662d310`
- 上一版本：`idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_1c8f74d5143b`
- 验收报告：`data/processed/indexes/stage_c_profile_aware_index_acceptance_report.json`

Stage C 的目标不是简单重建向量，而是把 Stage B 冻结的 `DocumentProfileSchema` 变成离线索引的强制治理边界。任何未分类文档、源哈希变化、Gold 错误入库或 Profile 冲突，都必须在 Chunk 和 Embedding 之前失败。

## 2. 正式输入

### 2.1 Cleaned Unit

```text
data/raw/jsonl/cleaned_text_unit_v1_batch_20260803.jsonl
```

实际输入：

- 31 份文档；
- 23215 条 Cleaned Unit；
- 其中 1 份 Gold 文档在 Profile Catalog 中声明 `should_index=false`。

### 2.2 Document Profile Catalog

```text
data/catalog/document_profiles_v1.jsonl
```

Catalog 属性：

- 31/31 文档完整覆盖；
- 所有 Profile 已人工复核；
- 每条 Profile 绑定 `document_id + source_sha256`；
- `unknown=0`；
- Profile Catalog SHA-256：`40e45955585c1f4323d98994c949ff052f69ed50e6bf9160e7905dcf3e70c5e1`。

## 3. Profile 合并规则

实现入口：

```text
backend/rag/catalog/document_profile_projection.py
```

`DocumentProfile` 对以下字段拥有最高优先级：

- `tenant_id`；
- `kb_id`；
- `file_id`；
- `source_name`；
- `source_uri`；
- `source_format`；
- `title`；
- `document_type`；
- `evidence_role`；
- `project_relation`；
- `authority_level`；
- `allowed_claim_types`；
- `forbidden_claim_types`；
- `parser_strategy`；
- `chunk_strategy`；
- `classification_*`；
- `should_index`；
- `is_gold_reference`。

完整边界进入：

```text
CleanedTextUnit.extra.document_profile
  -> ParentChunk.extra.document_profile
  -> ChildChunk.extra.document_profile
  -> VectorIndexRecord / Milvus extra_json
```

禁止行为：

1. Profile 缺失时回退 `kb_id=default`；
2. Cleaned Unit 的旧 KB 字段覆盖 Profile；
3. 源 SHA-256 不一致时继续构建；
4. Gold 文档因生产者旧标志错误而进入索引；
5. 未分类文档进入 Chunker；
6. 同一文档在不同 Unit 中声明不同 Profile 或不同切块策略。

## 4. 类型感知切块

新增插件：

```text
type_aware_parent_child@v1
```

实现位置：

```text
backend/rag/plugins/chunkers/plugin.py
backend/rag/registry/default_registrations.py
```

路由字段：

```text
extra.document_profile.chunk_strategy
```

### 4.1 路由与参数

| Profile Chunk Strategy | Delegate | Parent Size | Parent Overlap | Child Size | Child Overlap |
|---|---|---:|---:|---:|---:|
| `heading_parent_paragraph_child` | Heading | 1500 | 120 | 480 | 48 |
| `policy_article_clause` | Heading | 1200 | 80 | 400 | 40 |
| `fact_record` | Recursive | 900 | 40 | 300 | 30 |
| `table_row_record` | Recursive | 700 | 20 | 260 | 20 |
| `template_section` | Heading | 1600 | 80 | 450 | 40 |
| `fixed_token_fallback` | Recursive | 1200 | 100 | 400 | 40 |

所有 Child Chunk 额外记录：

```text
extra.type_aware_chunking.selected_profile_strategy
extra.type_aware_chunking.delegate
extra.type_aware_chunking.parameters
```

### 4.2 实际文档路由

```text
fact_record                       2
heading_parent_paragraph_child   15
policy_article_clause             6
table_row_record                  3
template_section                  4
```

最终产物：

```text
Parent Chunk：5085
Child Chunk： 8981
文档：         30
```

Gold 文档：

```text
doc_a8144d5d4e2b2478bbdfc1cd
```

未进入任何 Parent、Child、Vector 或 Milvus 记录。

## 5. 离线 Builder 改造

修改：

```text
backend/rag/offline/config.py
backend/rag/offline/builder.py
```

新增 Source 配置：

```yaml
document_profiles_path: data/catalog/document_profiles_v1.jsonl
require_document_profiles: true
```

构建身份不再只依赖 Cleaned Unit，复合源哈希由以下两项共同决定：

```text
cleaned_source_sha256
document_profiles_sha256
```

因此，即使 Cleaned Unit 未变化，只要 Profile 分类、权限边界或 Claim 能力变化，也会改变构建输入哈希并阻止错误复用旧索引。

Manifest 新增来源事实：

- `input_record_count=23215`；
- `record_count=21051`；
- `profile_document_count=31`；
- `profiled_source_document_count=31`；
- Cleaned Source SHA-256；
- Profile Catalog SHA-256；
- Profile Catalog 路径；
- 类型感知路由计数。

## 6. 新索引

构建配置：

```text
backend/rag/index_profiles/type_aware_m3e_milvus_lite_stage_c_v1.yaml
```

索引属性：

```text
Index Version: idx_type_aware_parent_child_v1_m3e_base_local_m3e_base_profiled_v1_a96b7662d310
Dataset:       cleaned_text_units_document_profiled_v1_r2
Embedding:     m3e-base
Device:        CUDA
Batch Size:    8
Dimension:     768
Metric:        COSINE
Backend:       Milvus Lite
Collection:    rag_child_chunks
```

### 6.1 存储位置

构建初期，D 盘仅剩 0.14 GB。Milvus Lite 在写入第 3072 条记录时明确失败：

```text
[Errno 28] No space left on device
```

E 盘当时剩余 141.07 GB，因此最终不可变索引写入：

```text
E:\rag-agent-indexes\idx_type_aware_parent_child_v1_m3e_base_local_m3e_base_profiled_v1_a96b7662d310
```

Active Pointer 仍位于：

```text
D:\MyCode\rag-agent\data\processed\indexes\active_index.json
```

Pointer 使用绝对 Manifest 路径，已通过跨盘解析与真实 Runtime 加载验证。

## 7. 验证结果

### 7.1 Catalog 与 Profile

```text
正式文档：                 31
索引文档：                 30
Gold 排除：                 1
未分类文档：                0
源哈希不一致：              0
缺失 Profile 的 Child：     0
Gold Child：                0
乱码 source_name：          0
```

知识库 Child 分布：

```text
historical_case_kb       5131
project_fact_kb          1687
policy_standard_kb        849
template_style_kb        1150
technical_reference_kb    164
```

### 7.2 Artifact 与 Milvus

```text
Parent Chunk：             5085
Child Chunk：              8981
Vector Record：            8981
Vector Matrix：             8981 × 768
Milvus Row：               8981
重复 Parent ID：              0
重复 Child ID：               0
孤儿 Parent 引用：             0
非有限向量：                  0
Self Retrieval：             3 / 3
Self Retrieval Hit Rate：    1.0
Failed Checks：              0
```

### 7.3 在线 Runtime

真实查询：

```text
大件运输许可系统升级项目建设内容
```

过滤条件：

```text
kb_id == "project_fact_kb"
```

结果：

```text
Dense Hits：          10
BM25 Hits：           10
Fused Candidates：    17
P2 Results：           5
Citations：            4
Packed Context：    5807 chars
Capture：           success
```

实际链路包含：

```text
Dense -> BM25 -> RRF / Query Fusion -> Parent Backfill -> Reranker -> Citation
```

返回文档均属于当前项目事实知识库：

```text
doc_5bd4faa071c0d2cb316c1add
doc_da8cd497ed9aa16c5cd1dd16
```

## 8. 激活与回滚

当前 Active：

```text
idx_type_aware_parent_child_v1_m3e_base_local_m3e_base_profiled_v1_a96b7662d310
```

回滚目标仍完整保留：

```text
idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_1c8f74d5143b
```

旧版本在 Registry 中状态为 `verified`，新版本状态为 `active`。激活使用 Lifecycle Manager 完成，验证失败时会恢复旧 Pointer；本次切换成功。

## 9. 测试

新增：

```text
backend/tests/test_stage_c_profile_aware_indexing.py
```

覆盖：

- 31 -> 30 文档投影；
- Gold 排除；
- Profile 覆盖旧 KB；
- Claim Boundary 透传；
- Source Hash mismatch；
- 严格 Catalog 配置；
- 类型感知路由；
- 缺失 Profile 硬失败；
- Profile Catalog 变化影响复合 Source Hash；
- 未分类文档硬失败；
- Builder 不自动激活。

结果：

```text
Stage C：10 passed
A+B+C 及离线索引、Lifecycle 相关回归：98 passed
```

## 10. Stage D 接入边界

Stage D 不应重新推断文档角色，而应直接消费：

```text
ChildChunk.extra.document_profile
```

建议接入顺序：

1. Retrieval Candidate 读取 `document_type/evidence_role/project_relation`；
2. Evidence Assessor 读取 `allowed_claim_types/forbidden_claim_types`；
3. Evidence Set Assessment 按主题与 Claim Type 聚合；
4. Gate 根据 Stage A 冻结规则输出 Delivery Mode；
5. Writer 只消费 Gate 允许的 Claim 与限定语。

Stage C 已完成的是“数据和索引层门禁”；它尚不等于在线 Evidence Assessor、Gate 和 Writer 已接管这些字段。
