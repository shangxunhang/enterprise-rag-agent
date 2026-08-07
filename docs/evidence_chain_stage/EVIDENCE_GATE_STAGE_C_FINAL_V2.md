# Evidence Gate Stage C Final V2

## 1. 结论

Stage C 最终收口已经完成。最终系统已实现：

```text
DocumentProfileSchema
→ Profiled CleanedTextUnit
→ ParentChunk
→ ChildChunk
→ VectorIndexRecord
→ BM25 Record
→ Milvus extra_json
```

完整、嵌套且显式保留空值的 `extra.document_profile` 是唯一事实源。用于 Metadata Filter 的标量字段均从该对象派生，不存在平行 Profile 语义。

最终 Active Index：

```text
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e
```

实际路径：

```text
E:\rag-agent-indexes\idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e
```

控制面 Pointer：

```text
D:\MyCode\rag-agent\data\processed\indexes\active_index.json
```

最终 Hash：

```text
Manifest SHA-256       18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
Active Pointer SHA-256 0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce
Config Hash             b95c8188a6d35ee9eb4a0b9442ba1a9442e6e9ace469c738c0ffb3d5ef57bda0
Source Hash             29a6435a5f151bdc8e47c591ac3a59d48ece2fbe6a2196f097e3c1f0c2cd60b8
Reproducibility Hash    d74c31437316acaa9332b86b2e4eeaa4933a888e14d98ba9acb2a16eb27a99ae
```

## 2. 文档口径

```text
Document Profile / Manifest 文档数  31
正式索引文档数                     30
Gold 文档数                          1
Gold Chunk                            0
旧残留 Chunk                          0
unknown 文档                          0
缺失 Profile                          0
孤儿 Profile                          0
source_sha256 不一致                  0
kb_id=default Chunk                   0
```

Gold：

```text
doc_a8144d5d4e2b2478bbdfc1cd
is_gold_reference = true
should_index = false
```

旧残留：

```text
doc_59f7609343f4286266dddf49
```

没有进入 Profiled Cleaned Unit、Chunk、Vector、BM25 或 Milvus。

## 3. 完整 Document Profile 投影

每个正式记录的 canonical Profile 至少包含：

```yaml
schema_version:
document_id:
source_sha256:
document_type:
evidence_role:
kb_id:
tenant_id:
project_relation:
project_id:
authority:
  level:
  issuer:
  version:
  effective_date:
  expiration_date:
content_capabilities:
  allowed_claim_types:
  forbidden_claim_types:
parsing:
  parser_strategy:
  chunk_strategy:
  fallback_chunk_strategy:
classification:
  method:
  confidence:
  reviewed:
  reviewed_by:
  reviewed_at:
  reason_codes:
source_document_form:
taxonomy_mapping:
should_index:
is_gold_reference:
profile_sha256:
catalog_sha256:
```

未知值显式保留为 `null`，不会因 `exclude_none` 在离线投影中丢失。

### 3.1 0618 当前项目可研边界

`doc_da8cd497ed9aa16c5cd1dd16` 保留：

```text
source_document_form = feasibility_study
authority.level      = reference
authority.version    = 0618
taxonomy_mapping     = current_project_requirement_due_to_v1_taxonomy
```

因此后续 Evidence/Gate 可以区分拟定值、估算值、计划值与最终确认事实。

### 3.2 标量过滤字段

Milvus 同时冗余：

```text
kb_id
tenant_id
document_type
evidence_role
project_relation
project_id
authority_level
```

这些字段全部由 `extra.document_profile` 派生。全量 Milvus 审计结果：

```text
missing document_profile       0
missing type_aware_chunking    0
profile_sha256 mismatch        0
scalar mirror mismatch         0
```

## 4. Artifact 及 Hash

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
```

向量：

```text
dimension     = 768
finite        = true
L2 normalized = passed
```

## 5. 真正的类型感知切块

### 5.1 总体分布

| Strategy | 文档 | Parent | Child | fallback 文档 | fallback Chunk |
|---|---:|---:|---:|---:|---:|
| `heading_parent_paragraph_child` | 15 | 2768 | 3688 | 0 | 0 |
| `policy_article_clause` | 6 | 141 | 490 | 1 | 77 |
| `table_row_record` | 3 | 7 | 107 | 0 | 0 |
| `fact_record` | 2 | 447 | 5034 | 2 | 392 |
| `template_section` | 4 | 850 | 928 | 0 | 0 |

### 5.2 Heading Parent / Paragraph Child

独立实现：

```text
backend/rag/plugins/chunkers/heading_parent_paragraph_child.py
```

边界：

```text
文档 → 标题路径 Parent → 段落/列表组 Child
```

保留：

```text
heading_path
heading_level
section_title
source_page_start
source_page_end
source_unit_ids
```

抽样：

```text
child_chunk_id  doc_04da71abf394414281038785_parent_000002_child_0001
heading_path    ["一、 项目背景和必要性"]
heading_level   3
representation paragraph
fallback        false
```

Parent 不跨标题路径拼接。

### 5.3 Policy Article / Clause

独立实现：

```text
backend/rag/plugins/chunkers/policy_article_clause.py
```

抽样：

```text
child_chunk_id doc_2cdec2800cd37b2235191a53_parent_000005_child_0001
chapter_id     chapter_1
chapter_title  第1章 项目概述
article_id     article_1.1
article_title  1.1 项目名称
clause_id      clause_0007
representation policy_clause
TOC            false
fallback       false
```

目录项被过滤，不进入正式向量证据。一个非 fallback Child 不跨无关条款。无可靠章/条结构的 1 份政策文档显式降级：

```text
fallback_reason = no_policy_clause_structure
fallback_chunks = 77
```

### 5.4 Table Row Record

独立实现：

```text
backend/rag/plugins/chunkers/table_row_record.py
```

Word Parser 的结构化 `rows / column_count / merged_cells` JSON 被纳入复合 Source Hash，并在 Profiled Unit 固化资产 URI 与 SHA-256。

规则：

```text
一个逻辑行 → 一个 Child
表头与行绑定
合并单元格继承关系显式记录
cells 与 normalized_fields 同时保留
不推测不存在的值
```

普通表格抽样：

```text
child_chunk_id  doc_41a218696b126f8270238a45_parent_000001_child_0001
row_index       3
representation  table_row
fallback        false
asset_sha256    b4d7a454181a0a0ad6c02401c1ff447e822881445ee27078a0d58c369870c958
```

铜川 CO₂ 抽样：

```json
{
  "device_count": "38",
  "total_power_kw": "14.2",
  "emission_factor_kg_co2_per_kwh": "0.5857",
  "annual_electricity_kwh": "124392",
  "annual_co2_tons": "72.86"
}
```

该记录由原文明示字段确定性生成，没有补全或推测。

### 5.5 Project Fact Record

独立实现：

```text
backend/rag/plugins/chunkers/project_fact_record.py
```

确定性支持：

```text
键值对
编号需求
项目决议
采购条目
配置清单
结构化表格事实
```

抽样：

```text
child_chunk_id doc_5bd4faa071c0d2cb316c1add_parent_000003_child_0004
predicate      预算金额
value          250000.00元
unit           元
fact_status    estimated
fallback       false
```

当前语料最终状态分布：

```text
unknown   5016
estimated   18
proposed     0
planned      0
confirmed    0
decided      0
```

这不是能力缺失，而是保守证据边界：当前索引中只有 18 条记录具有足够明确的预算/概算措辞。以下内容经过专项回归，保持 `unknown`：

```text
项目建设单位（盖章）
编制日期
质量管理计划
用户已经确认告知书
由成交供应商支付费用
```

无法可靠结构化的正文不会伪造 `value`：

```text
representation_type = text_evidence
fallback_used        = true
fallback_reason      = unsupported_fact_structure
value                = null
fact_status          = unknown
```

### 5.6 Template Section

独立实现：

```text
backend/rag/plugins/chunkers/template_section.py
```

抽样：

```text
child_chunk_id   doc_24a730ad03ea740d395043ce_parent_000002_child_0001
section_title    建设方案
placeholder      XXXXXXXXX
representation   template_section
fallback         false
```

模板只能位于：

```text
kb_id         = template_style_kb
evidence_role = template_style
```

允许 Claim 仅为：

```text
template_structure
style_guidance
```

示例项目名、金额和数量保留为模板示例属性，不获得项目事实能力。

## 6. Fallback 治理

```text
fallback 文档数             3
fallback Chunk 数          469
unsupported_fact_structure 392
no_policy_clause_structure  77
未披露 fallback              0
缺失 fallback contract       0
非法结构化 representation    0
```

每个 Child 的 `extra.type_aware_chunking` 均包含：

```yaml
strategy:
representation_type:
parser_version:
chunker_version:
structure_path:
fallback_used:
fallback_reason:
fallback_strategy:
original_strategy:
actual_strategy:
```

不存在 Profile 声明一种策略、实际偷偷使用另一种策略的情况。

## 7. 知识库边界

| KB | Child 数量 |
|---|---:|
| `historical_case_kb` | 3533 |
| `policy_standard_kb` | 490 |
| `project_fact_kb` | 5140 |
| `technical_reference_kb` | 156 |
| `template_style_kb` | 928 |

治理校验：

```text
15 份历史项目仍为 other_project
历史项目当前项目 Claim 违规 0
模板事实 Claim 违规          0
政策非政策 Claim 违规        0
unknown/unclassified Chunk   0
```

## 8. Profile 与身份血缘

全量检查：

```text
Profiled Unit 缺失 Profile      0
Parent 缺失 Profile             0
Child 缺失 Profile              0
VectorIndexRecord 缺失 Profile  0
BM25 缺失 Profile               0
Milvus 缺失 Profile             0
Authority contract 缺失         0
source_document_form 缺失       0
taxonomy_mapping 字段缺失        0
profile_sha256 缺失             0
source_sha256 缺失              0
```

身份检查：

```text
重复 Parent ID              0
重复 Child ID               0
重复 Vector ID              0
重复 BM25 ID                0
孤儿 Child                  0
孤儿 Vector                 0
孤儿 BM25                   0
Parent/Child/Vector/BM25 的 profile_sha256 不一致 0
Milvus profile_sha256 不一致                      0
```

## 9. Self Retrieval

执行了 10 个 exact-vector Top-5 自检索样本：

```text
project_fact_kb
historical_case_kb
policy_standard_kb
technical_reference_kb
template_style_kb
heading_parent_paragraph_child
policy_article_clause
table_row_record
fact_record
template_section
```

结果：

```text
样本数                         10
Top-5 命中率                  1.0
正确 source document          passed
Profile metadata              passed
Parent Backfill identity      passed
```

这只是离线索引自检，没有修改 Stage D 在线 Retrieval。

## 10. 生命周期

最终生命周期操作：

```text
激活最终索引  idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e
回滚验证目标  idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_e9be2642ce78
恢复最终索引  idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e
```

实际加载：

```text
回滚目标 Milvus Rows 10247
最终索引 Milvus Rows 10247
```

最终 Pointer 声明 Manifest Hash 与实际文件完全一致。

必须保留的旧版本仍存在：

```text
idx_type_aware_parent_child_v1_m3e_base_local_m3e_base_profiled_v1_a96b7662d310
idx_fixed_parent_child_v1_m3e_base_local_m3e_base_v1_1c8f74d5143b
```

构建过程中形成的、具有完整 Manifest 的中间 V2 版本也按不可变索引原则保留，未删除。两个没有 Index Manifest 的私有失败构建目录在确认未发布后被清理。

## 11. Windows Milvus Lite 兼容

当前安装的 Milvus Lite `Manifest.save()` 使用：

```python
os.rename(tmp_path, target_path)
```

POSIX 会替换目标文件，Windows 在目标已存在时抛出 `WinError 183`。项目兼容层保持原有：

```text
临时文件
json dump
flush/fsync
.prev 备份
版本递增
目录 fsync
```

仅将最终提交原语改为：

```python
os.replace(tmp_path, target_path)
```

E 盘多批次关闭重开测试已通过，并加入隔离子进程回归测试。

## 12. 测试与命令

最终相关回归：

```text
103 passed
0 failed
0 error
```

运行范围：

```text
Stage A 契约
Stage B Catalog
Stage C Profile-aware Builder
Stage C Structured Chunkers
Stage C Profile Lineage
Offline Builder / Chunker / Index Version
Milvus Child Record Compatibility
Offline Index Verification
Index Lifecycle
Lifecycle Contract Compatibility
```

测试临时目录：

```text
E:\rag-agent-tmp\pytest-final-regression
```

核心命令：

```text
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe -m pytest -q \
  --basetemp=E:/rag-agent-tmp/pytest-final-regression \
  backend/tests/test_stage_a_evidence_gate_contracts.py \
  backend/tests/test_stage_b_document_catalog.py \
  backend/tests/test_stage_c_profile_aware_indexing.py \
  backend/tests/test_stage_c_structured_chunkers.py \
  backend/tests/test_stage_c_profile_lineage.py \
  backend/tests/test_rag_offline_chunker_index_version.py \
  backend/tests/test_milvus_child_chunk_record_compat.py \
  backend/tests/test_step_11_2_offline_index_verification.py \
  backend/tests/test_step_11_3_index_lifecycle.py \
  backend/tests/test_index_lifecycle_contract_compatibility.py
```

其他检查：

```text
Python compileall  passed
git diff --check  passed
Active Resolver    passed
```

只有 Transformers/FAISS-SWIG 弃用警告，没有失败。

## 13. 可复核文件

仓库内：

```text
data/processed/indexes/stage_c_final_acceptance_report_v2.json
docs/EVIDENCE_GATE_STAGE_C_FINAL_V2.md
```

E 盘完整运行证据：

```text
E:\rag-agent-runtime\stage_c_final_acceptance_report_v2.json
E:\rag-agent-runtime\stage_c_final_v2_verification.json
E:\rag-agent-runtime\stage_c_final_v2_lifecycle.json
E:\rag-agent-runtime\stage_c_final_v2_r4_build.stdout.log
E:\rag-agent-runtime\stage_c_final_v2_r4_build.stderr.log
```

## 14. 遗留问题与范围声明

一次只读在线 Hybrid Runtime 烟测能够从版本 Pointer 成功加载 Parent/Milvus，并返回结果和 Citation；但暴露出现有 Keyword 分支没有统一应用 `filter_expr`，可能在 `project_fact_kb` 查询中混入模板结果。该问题属于 Stage D 的 Retrieval Router / BM25 在线过滤契约，本阶段按范围要求没有修改。

现有 `capture_result` 也不是简单布尔成功字段，本阶段没有更改 Stage D Capture 合同。

本次没有修改或提前实现：

```text
Retrieval Candidate V2
Dense extra_json 在线解析
Retrieval Router
Evidence Assessment V2 主链
Gate Policy Resolver
ResearchAgent
Writer
Document Hard Gate
RAGAS 二开
Stage E / Stage F
```

没有执行 Git commit，也没有清理仓库其他历史未提交改动。
