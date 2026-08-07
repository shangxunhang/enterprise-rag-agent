# Evidence Gate Stage B — Document Catalog V1

## 1. 状态

阶段 B 已完成，目录批次为：

```text
document_full_v1
```

正式输入与输出：

```text
Manifest:
  data/parsed/document_full_v1/document_manifest.jsonl

分类种子:
  data/catalog/document_profile_seeds_v1.jsonl

正式 Profile:
  data/catalog/document_profiles_v1.jsonl

目录审计:
  data/catalog/document_profile_catalog_audit_v1.json
```

本阶段只建立文档逻辑分类和证据能力边界，不修改离线 Chunk、BM25、Milvus 或在线检索主链。

## 2. 实际语料口径

实施方案中的“当前 35 个可检索文档”是规划时口径。当前可验证的正式 Manifest 实际包含：

```text
文档总数:       31
可进入正式索引: 30
不可进入索引:   1
Gold 文档:      1
unknown:        0
```

Spark 清洗审计与该口径一致：

```text
output_document_count:     31
indexable_record_count:    21051
non_indexable_record_count: 2164
gold_record_count:          2164
```

不在 Manifest 中的旧解析残留：

```text
doc_59f7609343f4286266dddf49
```

没有进入分类目录，也不得在阶段 C 被重新带入索引。

## 3. 分类方法与审批边界

分类依据：

```text
Manifest identity + source_sha256
+ 文件名和版本标识
+ 已解析标题/结构特征
+ Manifest governance
+ DevSpace 逐文档复核
```

每一条种子都绑定 `document_id + source_sha256`。Raw 文档变化后，旧分类种子不能静默复用。

当前 `classification.reviewed=true` 表示已经完成本次工程复核，复核范围记录为：

```text
manifest_filename_and_parsed_structure
```

或：

```text
manifest_governance_filename_and_parsed_structure
```

它不是对政策效力、项目金额、决议真实性或法律适用性的业务背书。用户对阶段 B 的验收才构成当前项目的业务确认。

## 4. 分类结果

### 4.1 Document Type

| document_type | 数量 |
|---|---:|
| current_project_decision | 1 |
| current_project_requirement | 3 |
| tender_requirement | 1 |
| historical_feasibility_study | 8 |
| historical_construction_scheme | 7 |
| policy_standard | 6 |
| technical_whitepaper | 1 |
| document_template | 4 |
| unknown | 0 |
| **合计** | **31** |

### 4.2 Evidence Role

| evidence_role | 数量 |
|---|---:|
| project_fact | 4 |
| procurement_requirement | 1 |
| historical_case | 15 |
| policy_standard | 6 |
| technical_reference | 1 |
| template_style | 4 |

### 4.3 逻辑知识库

| kb_id | 数量 |
|---|---:|
| project_fact_kb | 5 |
| historical_case_kb | 15 |
| policy_standard_kb | 6 |
| technical_reference_kb | 1 |
| template_style_kb | 4 |

没有文档继续使用：

```text
kb_id = default
```

## 5. 当前项目边界

当前项目统一标识：

```text
dajian_transport
```

共 5 份材料：

| document_id | 材料 | document_type | 索引状态 |
|---|---|---|---|
| doc_41a218696b126f8270238a45 | 重大需求、应用场景、改革措施“三张清单” | current_project_requirement | indexable |
| doc_a8144d5d4e2b2478bbdfc1cd | 实施方案 0702 Gold | current_project_decision | **not indexable** |
| doc_5bd4faa071c0d2cb316c1add | 前期采购项目 | tender_requirement | indexable |
| doc_9237648465447a2b4a396140 | 三张清单 v2 | current_project_requirement | indexable |
| doc_da8cd497ed9aa16c5cd1dd16 | 可行性研究报告 0618 | current_project_requirement* | indexable |

`doc_da8...` 的原始材料形态是当前项目可研，但 Stage A V1 没有 `current_project_feasibility_study` 类型，因此暂时映射为：

```text
current_project_requirement
```

并明确记录：

```text
taxonomy_mapping = current_project_requirement_due_to_v1_taxonomy
```

这不是把可研语义改写成需求书，而是 V1 类型体系的显式近似映射。

## 6. 历史项目边界

15 份历史项目材料全部满足：

```text
project_relation = other_project
kb_id = historical_case_kb
evidence_role = historical_case
```

只允许支持：

```text
general_method
typical_architecture
reference_case
```

统一禁止支持：

```text
current_project_fact
current_project_cost
current_project_quantity
current_project_schedule
current_project_configuration
```

因此历史可研、设计说明和建设方案即使语义高度相关，也不能被阶段 C/D 当作当前项目参数来源。

## 7. 政策与技术资料

6 份政策/规划/编制规范统一进入：

```text
policy_standard_kb
```

只允许：

```text
policy_requirement
```

当前只依据文档名称与结构确认其政策角色，没有虚构精确生效日期、失效日期或发布机关。政策效力与适用范围仍须在后续政策版本治理或人工审核中确认。

`智算云底座技术方案-202603.docx` 作为唯一通用技术资料进入：

```text
technical_reference_kb
```

不得证明当前项目数量、金额、工期或配置。

## 8. 模板隔离

以下 4 份模板进入：

```text
template_style_kb
```

只允许：

```text
template_structure
style_guidance
```

模板中嵌入的案例名称仅保留为审计元数据，不能转换为事实证据：

```text
中国电信天翼网络云陕西节点
北京京仪环保国产算力
西安凤八信创云建设项目
陕投集团智算中心建设项目
```

## 9. 特殊映射

### 9.1 富平县十五五数字经济规划工作计划书

该材料是工作计划，不足以直接按正式政策处理。Stage A V1 又没有独立的 planning_work_plan 类型，因此保守映射为：

```text
historical_construction_scheme
```

并设置：

```text
authority.level = draft
taxonomy_mapping = historical_construction_scheme_due_to_v1_taxonomy
```

### 9.2 汉台 AI 算力及智慧政务窗口指导申请书

原始形态是项目申请书，V1 没有对应类型，映射为历史可研案例，并记录显式近似关系。

### 9.3 铜川政务云年 CO₂ 排放量

该材料不是完整可研，而是同一历史项目的结构化补充计算材料。它继承历史项目关系，但解析策略覆盖为：

```text
parser_strategy = structured_table
chunk_strategy = table_row_record
```

## 10. Gold 文档规则

Gold 文档：

```text
doc_a8144d5d4e2b2478bbdfc1cd
```

保留完整 Document Profile，用于评测、对照和审计，但继续保持：

```text
is_gold_reference = true
should_index = false
catalog_disposition = profiled_but_not_indexed
```

它是 31 份 Profile 中唯一不可进入正式索引的文档。

## 11. 自动校验

阶段 B 自动校验包括：

1. Manifest、Seed、Profile 一一对应。
2. 每份 Profile 与 `source_sha256` 绑定。
3. Manifest 内容变化会使 Seed 失效。
4. 不允许重复 Profile。
5. 不允许孤儿 Profile。
6. 不允许缺失 Profile。
7. `unknown=0`。
8. 31 份全部有 Evidence Role、Project Relation 和逻辑 KB。
9. 模板只能支持样式和结构。
10. 历史项目不能支持当前项目 Claim。
11. Gold 仍不进入正式索引。
12. 正式 JSONL 与生成器输出逐字一致。

## 12. 阶段 C 准入条件

阶段 C 可以读取：

```text
data/catalog/document_profiles_v1.jsonl
```

但必须继续遵守：

```text
Document Profile 优先于旧 KB 默认值
Gold / should_index=false 不得进入 Chunk 和索引
模板不得进入 factual evidence
历史项目不得被标记为 current_project
Profile 哈希不匹配时停止构建
```

阶段 B 没有执行重新切块、重建 Milvus/BM25 或索引激活，这些属于阶段 C。
