# Stage B 实施与验收证据：Document Catalog

> 记录日期：2026-08-04  
> 阶段状态：**业务分类与目录审计已完成并通过**  
> 在证据链中的位置：把真实文档映射到 Stage A 冻结的文档类型、证据角色、项目关系、知识库和 Claim 能力边界。

## 1. Stage B 解决了什么问题

Stage A 只冻结了契约，但尚未回答真实语料中的每一份文档究竟是什么、属于哪个项目、能支持哪些 Claim、是否允许进入正式索引。

如果没有 Stage B，后续索引和检索只能依赖文件名或默认 `kb_id`，可能产生：

- 历史项目材料混入当前项目事实；
- 模板内容被当作事实；
- Gold 对照文档进入正式索引；
- 文档源文件变化后继续复用旧分类；
- 未分类材料以 `default` KB 身份进入 Stage C。

## 2. 实际实施内容

### 2.1 建立 Manifest 绑定的 Document Catalog

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

每条分类种子绑定：

```text
document_id + source_sha256
```

Raw 文档变化后，旧种子不能静默复用。

### 2.2 冻结真实语料口径

最终口径：

```text
Manifest 文档数       31
正式 Profile 数       31
可进入正式索引        30
不可进入正式索引       1
Gold 文档              1
unknown                0
```

旧解析残留：

```text
doc_59f7609343f4286266dddf49
```

不在 Manifest 中，因此没有进入 Catalog，也不得在 Stage C 被重新带入。

### 2.3 文档类型分布

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

### 2.4 Evidence Role 与逻辑知识库

| evidence_role | 数量 |
|---|---:|
| project_fact | 4 |
| procurement_requirement | 1 |
| historical_case | 15 |
| policy_standard | 6 |
| technical_reference | 1 |
| template_style | 4 |

| kb_id | 数量 |
|---|---:|
| project_fact_kb | 5 |
| historical_case_kb | 15 |
| policy_standard_kb | 6 |
| technical_reference_kb | 1 |
| template_style_kb | 4 |

不存在：

```text
kb_id = default
```

### 2.5 当前项目、历史项目和模板隔离

当前项目统一标识：

```text
dajian_transport
```

当前项目共 5 份材料，其中 1 份 Gold 不进入索引。

15 份历史项目材料全部满足：

```text
project_relation = other_project
kb_id = historical_case_kb
evidence_role = historical_case
```

历史材料只允许：

```text
general_method
typical_architecture
reference_case
```

统一禁止：

```text
current_project_fact
current_project_cost
current_project_quantity
current_project_schedule
current_project_configuration
```

4 份模板只允许：

```text
template_structure
style_guidance
```

模板中的示例项目名称、金额和数量只保留为审计元数据，不获得事实能力。

### 2.6 Gold 文档治理

Gold 文档：

```text
doc_a8144d5d4e2b2478bbdfc1cd
```

规则：

```text
is_gold_reference = true
should_index = false
catalog_disposition = profiled_but_not_indexed
```

它保留完整 Profile 用于评测和审计，但不能进入正式 Chunk 与索引。

### 2.7 显式近似映射

Stage A V1 没有覆盖所有真实材料形态，因此 Stage B 使用显式 `taxonomy_mapping`，而不是静默改写：

- 当前项目可研 0618 暂映射为 `current_project_requirement`，保留 `source_document_form=feasibility_study`；
- 工作计划保守映射为历史建设方案并设置 `authority.level=draft`；
- 项目申请书映射为历史可研案例；
- CO₂ 计算材料继承历史项目关系，并指定 `structured_table/table_row_record`。

## 3. 本阶段没有实施什么

Stage B 没有：

```text
重新切块
生成 Embedding
重建 BM25/Milvus
激活索引
修改在线 Retrieval
执行 Evidence Assessment
修改 Writer
```

这些属于 Stage C 以后。

## 4. 验收标准

Stage B 自动校验要求：

1. Manifest、Seed、Profile 一一对应；
2. 每份 Profile 与 `source_sha256` 绑定；
3. Source Hash 变化后旧 Seed 失效；
4. 无重复、孤儿或缺失 Profile；
5. `unknown=0`；
6. 31 份文档都有 Evidence Role、Project Relation、KB 和 Claim 能力；
7. 模板只能支持样式和结构；
8. 历史项目不能支持当前项目 Claim；
9. Gold 继续保持 `should_index=false`；
10. 正式 JSONL 与生成器输出一致。

## 5. 验收结果

### 5.1 Catalog Audit

`data/catalog/document_profile_catalog_audit_v1.json` 当前记录：

```text
passed                         true
manifest_document_count       31
profile_document_count        31
reviewed_document_count       31
indexable_document_count      30
non_indexable_document_count   1
unknown_document_count         0
missing_profile_document_ids   []
orphan_profile_document_ids    []
duplicate_profile_document_ids []
source_hash_mismatch_ids        []
```

### 5.2 2026-08-04 联合复核

A+B 联合执行结果：

```text
25 passed
0 failed
0 errors
0.40s
```

其中测试文件定义：

```text
Stage A 13 个测试
Stage B 12 个测试
```

### 5.3 当前测试隔离问题

当前仓库中，单独执行：

```text
backend/tests/test_stage_b_document_catalog.py
```

会在测试收集阶段受到后续 Stage F Release Schema 导入链的循环导入影响：

```text
ImportError: cannot import name 'PublishedReleaseSchema'
from partially initialized module
'apps.enterprise_document.schemas.release_publication'
```

但按 A→B 顺序联合运行时 25 项全部通过，Catalog Audit 也为 `passed=true`。

本问题的判断：

- **不是 Stage B 数据分类、Profile 内容或 Catalog Audit 失败**；
- 是后续代码加入后形成的模块导入顺序/测试隔离债务；
- 不在本次证据文档任务中修改代码；
- 必须进入完成 Stage F 后的代码审计清单。

因此 Stage B 的业务成果仍为通过，但当前仓库的“B 测试可独立运行”不应被虚假标记为通过。

## 6. Artifact Hash

```text
document_manifest.jsonl
0572ea116b64403bd223c10dfd84b53b473b8837e4336c77c151bb8c6a5ddcd3

document_profile_seeds_v1.jsonl
fb9ddd73524dba201f2bc927f649b5e43499ef58a3c91420d3f3826dfeca8b6d

document_profiles_v1.jsonl
40e45955585c1f4323d98994c949ff052f69ed50e6bf9160e7905dcf3e70c5e1

document_profile_catalog_audit_v1.json
dfd4363eee87d4cd8d776d76ad0893f5ce297d4d8b02f4b950c4fbd511d27526
```

## 7. 验收证据位置

```text
docs/EVIDENCE_GATE_STAGE_B_DOCUMENT_CATALOG_V1.md
scripts/build_document_profiles_v1.py
backend/tests/test_stage_b_document_catalog.py

data/parsed/document_full_v1/document_manifest.jsonl
data/catalog/document_profile_seeds_v1.jsonl
data/catalog/document_profiles_v1.jsonl
data/catalog/document_profile_catalog_audit_v1.json
```

Stage B 完成时尚未使用后续阶段统一的 Finalizer/JUnit 报告体系，本文件将原始 Catalog Audit、当前 Artifact Hash 和当前复核状态统一落盘。

## 8. 最终结论

Stage B 已建立真实 Document Catalog，并通过目录审计。它能够证明：

> 当前 31 份真实材料中的每一份都有明确身份、源文件 Hash、项目关系、证据角色、知识库位置和 Claim 能力边界；Gold、历史材料和模板不会以默认身份进入正式索引。

进入 Stage C 时必须以 `document_profiles_v1.jsonl` 为唯一分类事实源。当前单独运行 Stage B 测试的循环导入问题留待证据链完成后的代码审计修复。
