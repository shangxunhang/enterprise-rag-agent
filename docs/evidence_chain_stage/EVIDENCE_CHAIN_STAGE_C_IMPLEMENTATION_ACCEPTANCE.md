# Stage C 实施与验收证据：Profile-aware Offline Index

> 记录日期：2026-08-04  
> 阶段状态：**已完成并通过**  
> 在证据链中的位置：把 Stage B 的 Document Profile 贯穿到清洗单元、Parent/Child Chunk、向量、BM25 和 Milvus，形成可追溯、可过滤、可回滚的离线证据资产。

## 1. Stage C 解决了什么问题

Stage B 已经知道每份文档是什么、属于哪个项目、能支持哪些 Claim，但旧离线索引仍可能：

- 只保留文本，不保留完整 Document Profile；
- 使用统一固定切块，破坏政策条款、表格行、事实记录和模板章节结构；
- 将 Gold 或旧残留重新带入索引；
- Dense、BM25、Parent Store 和 Milvus 使用不同身份；
- Profile 标量过滤字段和嵌套 Profile 出现平行语义；
- 构建完成后无法证明输入、配置、Artifact 和激活 Pointer 的一致性。

Stage C 的目标是建立一个受 Document Profile 强约束的、不可变且可复核的离线索引版本。

## 2. 最终离线链路

```text
DocumentProfileSchema
→ Profiled CleanedTextUnit
→ ParentChunk
→ ChildChunk
→ VectorIndexRecord
→ BM25 Record
→ Milvus extra_json
```

唯一 Profile 事实源：

```text
extra.document_profile
```

用于 Metadata Filter 的标量字段全部从该嵌套对象派生，不允许维护另一套独立 Profile 语义。

## 3. 实际实施内容

### 3.1 Profile 全链路投影

每个正式记录保留：

```text
document_id
source_sha256
document_type
evidence_role
kb_id
tenant_id
project_relation
project_id
authority
content_capabilities
parsing
classification
source_document_form
taxonomy_mapping
should_index
is_gold_reference
profile_sha256
catalog_sha256
```

未知值显式保留为 `null`，避免离线投影时丢失字段语义。

Milvus 同时冗余以下过滤字段：

```text
kb_id
tenant_id
document_type
evidence_role
project_relation
project_id
authority_level
```

所有标量都必须与 `extra.document_profile` 一致。

### 3.2 类型感知 Parent/Child Chunk

实现五种正式策略：

```text
heading_parent_paragraph_child
policy_article_clause
table_row_record
fact_record
template_section
```

对应实现：

```text
backend/rag/plugins/chunkers/heading_parent_paragraph_child.py
backend/rag/plugins/chunkers/policy_article_clause.py
backend/rag/plugins/chunkers/table_row_record.py
backend/rag/plugins/chunkers/project_fact_record.py
backend/rag/plugins/chunkers/template_section.py
```

关键语义：

- 普通方案：标题路径作为 Parent，段落/列表组作为 Child；
- 政策材料：章/条/款结构化，目录项不作为正式证据；
- 表格：一个逻辑行一个 Child，保留表头、合并单元格和计算记录；
- 项目事实：提取 subject/predicate/value/unit/fact_status；
- 模板：保留章节结构和占位符，但不赋予事实能力。

### 3.3 Fallback 显式治理

每个 Child 都记录：

```text
strategy
representation_type
parser_version
chunker_version
structure_path
fallback_used
fallback_reason
fallback_strategy
original_strategy
actual_strategy
```

最终 fallback：

```text
fallback 文档数              3
fallback Chunk 数          469
unsupported_fact_structure 392
no_policy_clause_structure  77
未披露 fallback               0
```

无法可靠结构化的正文保留为文本证据，不伪造事实值。

### 3.4 Fact Status 保守投影

最终 Fact Status：

```text
unknown   5016
estimated   18
proposed     0
planned      0
confirmed    0
decided      0
```

只有具备明确预算/概算措辞的记录进入 `estimated`。无法确定的材料保持 `unknown`，不通过启发式补全成为确认事实。

### 3.5 Gold、残留和 KB 边界

```text
Document Profile / Manifest  31
正式索引文档                  30
Gold 文档                      1
Gold Chunk                     0
旧残留 Chunk                   0
unknown 文档                   0
缺失 Profile                   0
孤儿 Profile                   0
source_sha256 不一致           0
kb_id=default Chunk            0
```

Gold：

```text
doc_a8144d5d4e2b2478bbdfc1cd
should_index = false
```

旧残留：

```text
doc_59f7609343f4286266dddf49
```

没有进入任何正式离线 Artifact。

### 3.6 不可变索引与生命周期

最终 Active Index：

```text
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e
```

控制面 Pointer：

```text
data/processed/indexes/active_index.json
```

完成：

```text
激活最终索引
回滚到历史完整版本
验证历史版本可加载
恢复最终索引
```

历史完整索引未删除；未发布且没有 Manifest 的失败构建目录经确认后清理。

## 4. Artifact 结果

| Artifact | 数量 | SHA-256 |
|---|---:|---|
| Profiled CleanedTextUnit | 21051 | `90a4d35d6207e1962ea01c35e70134f08a351c11c2fc753ae4f98680d1dcb527` |
| ParentChunk | 4213 | `a5718c0107212f15a5149731f3f2b6e923b9e3fccc4b6904269d7e1e624ca750` |
| ChildChunk | 10247 | `4e57b775dac075522e3f9115a0be2125ccd8a8f0b8e51f62a42f568207901e86` |
| Child Vector | 10247 × 768 | `f0b4689a36b1655557f4c35feb319c527be43bf6823b123bb1d85c48cbcb17f0` |
| VectorIndexRecord | 10247 | `545311fb5ec1deed396b8dd7661b23874edc4c55ebe16004f63b1e0349dc19fb` |
| BM25 Record | 10247 | `60953ac8e7ebadfa8793502d5401c2147c7d53c3b7263f85b564c166b87ef87f` |
| Milvus Row | 10247 | semantic `9fbb3bef38e202f7fa0ecf3b11e3e2b051c59d95aa314c34a5ffae35c54e1a6e` |

一致性：

```text
Vector Count = Child Count = 10247
BM25 Count   = Child Count = 10247
Milvus Rows  = Child Count = 10247
Embedding Dimension        = 768
Finite / L2 Normalized     = passed
```

## 5. 验收标准

Stage C 必须证明：

1. 只有 Manifest + Profile 中允许索引的 30 份文档进入正式资产；
2. Gold 和旧残留 Chunk 为 0；
3. 每一层都保留完整 Profile、Source Hash 和 Profile Hash；
4. Parent/Child/Vector/BM25/Milvus 身份一一对应；
5. 不存在孤儿、重复 ID 或 Profile Hash 漂移；
6. 五种类型感知策略实际生效；
7. Fallback 全部显式披露；
8. 历史、模板、政策 Claim 边界没有泄漏；
9. Dense/BM25/Milvus 数量一致；
10. Active Pointer 可以激活、回滚和恢复；
11. 相同输入和配置可复现同一语义结果。

## 6. 验收结果

### 6.1 离线全量审计

```text
Profiled Unit 缺失 Profile      0
Parent 缺失 Profile             0
Child 缺失 Profile              0
Vector/BM25/Milvus 缺失 Profile 0
重复 Parent/Child/Vector/BM25 ID 0
孤儿 Child/Vector/BM25           0
Profile 标量镜像不一致           0
历史材料当前项目 Claim 泄漏       0
模板事实 Claim 泄漏              0
政策非政策 Claim 泄漏            0
```

### 6.2 Self Retrieval

对 5 个 KB 和 5 个切块策略执行 exact-vector Top-5 自检：

```text
样本数       10
Top-5 命中率 1.0
来源文档     passed
Profile      passed
Parent 回填  passed
```

### 6.3 测试与静态检查

```text
103 passed
0 failed
0 errors

python compileall  passed
git diff --check  passed
Active Resolver   passed
```

测试覆盖 Stage A、Stage B、Stage C Builder、结构化 Chunker、Profile Lineage、索引版本、Milvus 兼容、离线验证和生命周期。

## 7. 最终索引身份

```text
Index Version:
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e

Manifest SHA-256:
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02

Active Pointer SHA-256:
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce

Config Hash:
b95c8188a6d35ee9eb4a0b9442ba1a9442e6e9ace469c738c0ffb3d5ef57bda0

Source Hash:
29a6435a5f151bdc8e47c591ac3a59d48ece2fbe6a2196f097e3c1f0c2cd60b8

Reproducibility Hash:
d74c31437316acaa9332b86b2e4eeaa4933a888e14d98ba9acb2a16eb27a99ae
```

## 8. 验收证据位置

```text
docs/EVIDENCE_GATE_STAGE_C_PROFILE_AWARE_INDEX_V1.md
docs/EVIDENCE_GATE_STAGE_C_FINAL_V2.md

data/processed/indexes/stage_c_final_acceptance_report_v2.json
SHA-256: 1cb157c8a52d7015ddd407df355843e486a390e23e0a8f7c67df2f92b83835fb

E:\rag-agent-runtime\stage_c_final_acceptance_report_v2.json
E:\rag-agent-runtime\stage_c_final_v2_verification.json
E:\rag-agent-runtime\stage_c_final_v2_lifecycle.json
E:\rag-agent-runtime\stage_c_final_v2_r4_build.stdout.log
E:\rag-agent-runtime\stage_c_final_v2_r4_build.stderr.log
```

## 9. 本阶段没有实施什么

Stage C 没有修改在线：

```text
Retrieval Scope
Candidate Governance
Evidence Assessment
Gate Policy
Writer
Document Hard Gate
正式发布
```

这些属于 Stage D–F。

## 10. 最终结论

Stage C 已完成并通过。它能够证明：

> 进入在线检索的每一个 Chunk 和索引记录，都能追溯到唯一原始文档、Source Hash、Document Profile、切块策略和不可变索引版本；Gold、历史材料、模板和未分类材料不会通过离线环节获得错误证据能力。

Stage D 可以在不重建索引的前提下，对这些候选证据实施在线范围、Prompt 可见性和 Gate 决策。
