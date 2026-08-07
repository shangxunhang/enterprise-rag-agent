# Stage A 实施与验收证据：Evidence Contract Freeze

> 记录日期：2026-08-04  
> 阶段状态：**已完成并通过**  
> 在证据链中的位置：为后续文档分类、索引治理、在线证据门禁和 Writer 治理冻结统一契约。

## 1. Stage A 解决了什么问题

在 Stage A 之前，系统使用的证据状态、文档角色、生成模式和 Claim 权限缺少统一且可校验的契约。如果直接开始文档分类或在线 Gate，实现很容易出现以下问题：

- 同一个证据状态在不同模块中含义不同；
- 历史项目材料被误当成当前项目事实；
- 模板、政策、技术资料没有明确的能力边界；
- `blocked`、`qualified`、`human_review` 等模式只存在于流程判断，没有 Schema 约束；
- 为了适配数据而静默修改类型语义。

Stage A 的任务不是接入运行主链，而是先冻结后续所有阶段共同遵守的语言和边界。

## 2. 实际实施内容

### 2.1 冻结五类核心契约

| 契约 | Schema/版本 | 代码位置 |
|---|---|---|
| 文档档案 | `document_profile_v1` | `backend/rag/catalog/document_profile.py` |
| 单条证据评估 | `evidence_assessment_v2` | `backend/rag/schema/evidence_assessment_v2.py` |
| 证据集合评估 | `evidence_set_assessment_v1` | `backend/rag/schema/evidence_assessment_v2.py` |
| 最终门禁决策 | `gate_decision_v1` | `backend/apps/enterprise_document/schemas/gate_decision.py` |
| 交付模式 | Enum | `backend/apps/enterprise_document/schemas/gate_decision.py` |

### 2.2 冻结 Evidence Status

```text
sufficient
partial
absent
conflicted
not_assessed
```

集合级约束：

- `sufficient`：覆盖率为 1，无缺失 Topic、无冲突；
- `partial`：覆盖率在 0 和 1 之间，同时存在 covered 和 missing Topic；
- `absent`：覆盖率为 0，不得声明支持数量；
- `conflicted`：必须存在显式冲突；
- `not_assessed`：不得伪造覆盖、数量或冲突结果。

### 2.3 冻结 Delivery Mode

```text
grounded
qualified
framework_only
blocked
human_review
```

关键语义：

- `grounded` 只能由充分证据进入；
- `qualified` 必须携带限定语，高风险 Claim 不得仅凭 partial evidence 放行；
- `framework_only` 只允许模板结构和样式指导；
- `blocked` 不允许任何 Claim Type；
- `human_review` 必须明确 `human_review_required=true`；
- `conflicted` 只能进入 `blocked` 或 `human_review`。

### 2.4 冻结文档类型和证据角色边界

V1 文档类型：

```text
current_project_requirement
current_project_decision
current_project_inventory
historical_feasibility_study
historical_construction_scheme
policy_standard
technical_whitepaper
tender_requirement
document_template
unknown
```

硬边界：

1. 当前项目材料必须是 `current_project`；
2. 历史项目材料必须是 `other_project`；
3. 历史材料不能支持当前项目金额、数量、工期、配置和事实；
4. 模板只能支持 `template_structure` 和 `style_guidance`；
5. 未分类文档不能支持任何 Claim；
6. Authority 生效和失效日期必须形成合法区间；
7. Allowed/Forbidden Claim Set 必须去重、互斥；
8. 所有冻结契约拒绝未知字段。

### 2.5 分离相关性与可用资格

Stage A 明确：

```text
semantic_relevance != eligibility
```

一条材料可以与问题高度相关，但仍然没有资格支持当前 Claim。例如历史可研可以提供一般方法参考，但不能证明当前项目预算。

### 2.6 兼容策略

Stage A 为增量契约冻结：

- 不删除旧 `ResearchStatus`、Section State 或 Writer Contract；
- 不让新契约在本阶段直接接管在线主链；
- 后续阶段必须使用显式 Adapter；
- JSON 边界使用 `model_dump(mode="json")`；
- 任何破坏性契约变化必须升级 Schema Version。

## 3. 本阶段没有实施什么

Stage A 没有进行：

```text
真实文档分类
重新切块
索引构建
在线 Retrieval Router
Evidence Gate 主链接入
Writer 改造
Document Hard Gate
正式发布
```

这些分别属于 Stage B–F。

## 4. 验收标准

Stage A 验收要求：

1. 五类核心契约可进行稳定 JSON Round-trip；
2. Enum 和 Schema Version 冻结；
3. 文档类型、证据角色、项目关系不允许非法组合；
4. 历史材料不能获得当前项目 Claim 权限；
5. 模板不能获得事实权限；
6. `partial/absent/conflicted/not_assessed` 满足各自不变量；
7. 五种 Delivery Mode 满足严格进入条件；
8. 高风险 Claim 不得凭 partial evidence 进入确定性输出；
9. 未声明字段必须被拒绝；
10. 后续 Stage B 的分类结果必须能够通过 `DocumentProfileSchema`。

## 5. 验收结果

### 5.1 当前专项复核

执行时间：2026-08-04。

```text
backend/tests/test_stage_a_evidence_gate_contracts.py
13 passed
0 failed
0 errors
0.34s
```

复核命令：

```powershell
$env:PYTHONPATH="backend;scripts"
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe `
  -m pytest -q `
  backend/tests/test_stage_a_evidence_gate_contracts.py
```

### 5.2 后续阶段持续回归

Stage A 测试持续被 Stage C、Stage E 和 Stage F 的核心回归命令纳入。后续阶段没有通过静默修改 Stage A 契约来适配数据。

## 6. 验收证据位置

### 6.1 实施说明

```text
docs/EVIDENCE_GATE_STAGE_A_CONTRACT_FREEZE_V1.md
```

### 6.2 代码与测试

```text
backend/rag/catalog/document_profile.py
backend/rag/schema/evidence_assessment_v2.py
backend/apps/enterprise_document/schemas/gate_decision.py
backend/tests/test_stage_a_evidence_gate_contracts.py
```

### 6.3 验收记录说明

Stage A 完成时尚未建立后续 C–F 使用的统一 Finalizer/JUnit 报告体系，因此仓库中没有独立的 `stage_a_final_acceptance_report.json`。本文件明确区分：

- 原 Stage A 实施文档中冻结的验收标准；
- 2026-08-04 对当前代码的专项重新复核结果。

不将后续阶段回归记录冒充为 Stage A 当时的独立验收报告。

## 7. 已知边界

Stage A V1 类型体系没有：

```text
current_project_feasibility_study
planning_work_plan
project_application
```

因此 Stage B 对部分真实材料采用了显式近似映射，并通过 `taxonomy_mapping` 保留原始材料形态。这属于 V1 类型体系边界，不是静默改写。

## 8. 最终结论

Stage A 已完成并通过。它建立的核心能力是：

> 系统在处理任何真实文档之前，已经冻结“材料是什么、能支持什么、证据状态是什么、最终允许以何种方式生成”的统一契约。

Stage B 可以在不改变这些语义的前提下建立真实 Document Catalog。
