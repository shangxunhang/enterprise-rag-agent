# 四个 E2E Bug 修复验收记录

- 项目：`D:\MyCode\rag-agent`
- 验收日期：2026-08-05
- 验收分支：`checkpoint/controlled-architecture-20260728`
- 验收基线 HEAD：`2bc46b0`
- 实施方案：`docs/E2E_FOUR_BUG_REPAIR_IMPLEMENTATION_PLAN_20260805.md`
- 验收性质：代码修复验收 + 专项回归 + Stage D/E/F 回归 + 真实 RAG 探针 + 完整真实 E2E
- 最终结论：**四个生产 Bug 的最小修复验收通过；完整真实 E2E 已执行完毕，执行完整性通过，但业务质量门禁失败并生成 rejected artifact。**

---

## 1. 结论摘要

本轮四个目标 Bug 均已在代码中找到对应修复，职责边界与实施方案一致，没有发现通过放宽 Evidence Gate、Claim Audit 或 Citation Hard Gate 换取测试通过的行为。

独立复核结果：

1. 四个 Bug 相关核心测试及 CLI KB Scope 透传：`52 passed`；
2. Stage D/E/F 聚合测试：`160 passed`；
3. 实施方报告的全量结果：`1466 passed, 2 skipped, 0 failed`；
4. 验收方尝试独立复跑全量测试时，DevSpace 上游返回 502，因此全量数字未被本次验收独立复现；
5. 真实政务云预生成探针成功完成八章真实检索，八章 C-RAG 均为 `sufficient`，Evidence Governance 均产生 `qualified` Gate；
6. 完整真实 E2E 通过独立 Windows 进程执行，规避 DevSpace 单次 Bash 300 秒限制；
7. 完整 E2E Run ID 为 `acceptance_gov_cloud_e2e_20260805_1608`，运行约 `1028.9` 秒；
8. 最终 `execution_integrity=passed`、`business_quality=failed`、`validation_error=null`；
9. 八章、十次真实 RAG 调用、158 次真实模型调用、Document Hard Gate 和 rejected artifact 均已完成；
10. Citation Binding 在真实链中达到 16 个 Marker、16 个 verified occurrence、0 orphan、bijection=true；
11. 业务失败主要来自全部章节 Claim Audit 未通过、全部章节要求人工复核，以及存储章节生成了未被当前项目证据支持的 AZ1/AZ2、324.2TB、8 台 XSSD、Kunpeng 920 等确定配置；
12. 因此当前不再缺“完整 E2E”，而是已经获得了下一轮应处理的真实坏例。

本轮应当冻结为：

> **四项原生产 Bug 修复通过；完整 E2E 技术链通过；正式交付被业务 Hard Gate 正确拒绝。下一步只处理真实 E2E 暴露出的 Claim Audit、历史材料转当前项目事实和验收契约问题。**

在这些问题形成新的最小修复和回归闭环前，仍不建议恢复新的 Multi-Agent、Memory 或 Research Loop 开发。

---

## 2. 验收范围与约束

### 2.1 本次验收覆盖

- Bug 1：Evidence Governance 用户意图推断退出正式主路径；
- Bug 2：显式 ProjectInput 八章契约及可信 KB Scope；
- Bug 3：Claim Classifier 配置声明规则及 Writer Prompt 边界；
- Bug 4：Qualifier 注入后的最终 Citation Binding 生命周期；
- 相关单元测试、集成测试与 Stage D/E/F 回归；
- 真实 MilvusLite 检索探针；
- 完整真实政务云 E2E 启动与 Trace 检查。

### 2.2 本次未覆盖

- 不验收 Multi-Agent 新能力；
- 不验收 Memory、Research Loop 或其他扩展架构；
- 不把仓库已有 dirty tree 中的全部修改归因于本轮；
- 不执行 `git reset`、`git clean`、分支切换或提交；
- 不修改或重建原活动索引；
- 不以 FakeRAG 或 FakeLLM 代替真实 E2E；
- 不把预生成探针等同于完整文档 E2E。

### 2.3 无关材料处理

会话附带的 node5/HDFS 上传核验说明与本轮仓库修复无关，未作为验收依据。

---

## 3. 仓库状态核验

验收开始时执行：

```bash
git status --short
git branch --show-current
git rev-parse --short HEAD
git diff --stat
git diff --check
```

结果：

- 当前分支：`checkpoint/controlled-architecture-20260728`
- 当前 HEAD：`2bc46b0`
- 仓库存在较大的历史 dirty tree；
- 未执行 reset、clean、分支切换或提交；
- `git diff --check` 未发现新的空白错误；
- 本次验收只锁定四个 Bug 目标文件、Fixture、探针和相关测试。

---

## 4. Bug 1 验收：Evidence Governance

### 4.1 验收文件

```text
backend/rag/evidence/governance_v2.py
```

### 4.2 实际实现

`_infer_claim_policy()` 仍然保留，但 `resolve_evidence_requirements_v2()` 不再调用它。

正式路径改为：

```text
candidate.allowed_claim_types
- candidate.forbidden_claim_types
→ 按 project_relation / authority / structured evidence / fact_status 收紧
→ 汇总合格候选的 Claim 能力
```

代码中可见：

```python
supported_claims = allowed - forbidden
```

并且输出元数据明确记录：

```text
claim_resolution_mode = candidate_capability_union
requested_claim_types_ignored = true
```

### 4.3 职责边界核验

当前 Query 仍用于：

- 语义相关度；
- 章节主题覆盖；
- C-RAG 检索与纠正检索。

Query 不再用于：

- 推断用户需要 `current_project_cost`；
- 推断用户需要数量、配置或工期；
- 决定候选材料有哪些 Claim 权限。

这符合已冻结职责：

```text
C-RAG：判断是否相关、是否继续检索
Evidence Governance：判断已召回材料能证明什么
```

### 4.4 当前项目事实收紧

实现中确认：

- 非 `current_project` 证据会移除全部 `CURRENT_PROJECT_CLAIM_TYPES`；
- 被移除的 Claim 会进入 `forbidden_claim_types`；
- 当前项目事实还需要结构化事实兼容；
- `FactStatus.UNKNOWN` 不允许；
- `ESTIMATED / PROPOSED / PLANNED` 需要限定语和人工复核；
- `CONFIRMED` 需要权威材料；
- `DECIDED` 需要正式决策类来源。

### 4.5 验收判断

**通过。**

原始 Bug：

```text
“金额不得编造”
→ current_project_cost
→ 通用、政策、案例材料全部失去资格
```

已从正式主路径消除。

### 4.6 后续清理项

`_infer_claim_policy()` 和 `requested_claim_types` 目前作为兼容资产保留。它们不阻塞本轮验收，但后续可在确认无旧产物依赖后单独废弃，不应在本轮继续扩张。

---

## 5. Bug 2 验收：显式 ProjectInput

### 5.1 新增 Fixture

```text
backend/tests/fixtures/e2e/gov_cloud_framework_project_input.json
```

### 5.2 八章契约

Fixture 明确包含：

1. 项目背景
2. 建设目标
3. 总体架构
4. 计算资源建设方案
5. 存储资源建设方案
6. 网络资源建设方案
7. 安全设计
8. 实施与验收建议

上述章节同时存在于：

- `generation_requirements.required_sections`
- `generation_requirements.citation_required_sections`
- `output_schema.required_sections`
- DocumentPlan
- 章节契约测试构造的 SchemeDraft

### 5.3 Demo 默认值退出正式场景

Fixture 中明确记录：

```json
"allow_demo_defaults": false
```

正式 E2E 通过：

```text
--project-input-file backend/tests/fixtures/e2e/gov_cloud_framework_project_input.json
```

传入，不再依赖 `DEFAULT_SCHEME_SECTIONS`。

### 5.4 缺失信息

Fixture 已包含八项缺失信息：

- 项目现状
- 用户规模
- 计算资源数量
- 存储容量
- 网络带宽
- 设备品牌型号
- 投资金额
- 实施工期

### 5.5 可信 KB Scope

`run_demo.py` 已新增：

```text
--authorized-kb-id
```

并构造、透传 `RetrievalAccessScopeSchema`。

相关专项测试已独立通过。

### 5.6 验收判断

**通过。**

Planner 没有被修改为自然语言章节解析器，仍然严格复制规范 ProjectInput，职责边界正确。

---

## 6. Bug 3 验收：Claim Classifier 与 Writer Prompt

### 6.1 验收文件

```text
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
```

### 6.2 Classifier 版本

```python
CLAIM_CLASSIFIER_VERSION = "2.1.0"
```

Classifier Policy SHA 由新规则和新版本重新计算，没有沿用旧策略身份。

### 6.3 `_CONFIGURATION_PATTERN` 收窄

当前配置声明要求同时出现：

```text
当前项目主体
+
确定性配置/部署动作
```

当前模式包含：

```text
本项目 / 本次建设 / 本系统 / 项目建设 / 项目将 / 拟建设 / 计划建设
+
采用 / 配置 / 部署 / 安装 / 配备 / 选用 / 接入 / 升级
```

已删除仅凭以下对象词就判为当前项目配置的宽泛分支：

```text
服务器 / 存储 / 网络 / 数据库 / 节点 / 型号 / 规格……
```

因此普通方法性句子不会再仅因出现“存储”“网络”“服务器”而变成 `current_project_configuration`。

### 6.4 Writer Prompt 边界

`writer_gate_prompt_contract()` 已根据 Gate 中未允许的 Claim Type 动态生成明确约束：

- 未允许当前项目配置时，不得确定具体组件、产品、品牌、型号或选型；
- 未允许数量时，不得确定服务器数量、节点数、容量和并发规模；
- 未允许金额时，不得给出预算、概算、造价和金额；
- 未允许工期时，不得确定工期、上线时间和实施周期；
- 历史案例和通用材料不得改写为当前项目既定事实；
- 输入不足时只能写原则、测算维度、选择方法、典型架构、参考方案和待确认项。

该约束按 Claim 权限生成，不依赖“政务云”“安全设计”等章节名称硬编码。

### 6.5 安全边界

未发现以下规避行为：

- 扩大 `allowed_claim_types` 让 E2E 强行通过；
- 删除 fail-closed；
- 放过无法分类的实质性声明；
- 新增额外语义分类模型或新 Agent。

### 6.6 验收判断

**通过。**

---

## 7. Bug 4 验收：最终 Citation Binding

### 7.1 验收文件

```text
backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py
```

### 7.2 最终生命周期

当前流程为：

```text
生成、修复与 Self-RAG 使用 working bindings
→ reconcile citation markers
→ inject required qualifiers
→ 基于 Qualifier 注入后的正文重新 bind
→ supported_bindings
→ 再 reconcile markers
→ 基于真正最终正文再次 bind
→ supported_bindings
→ 保存 final content + final bindings
```

### 7.3 最终正文冻结

第二次最终绑定后，后续步骤只读取正文：

- semantic judge
- high-risk claim gate
- claim audit
- quality evaluation
- SchemeSectionSchema 构造

未发现再插入 Qualifier、重排段落或重新格式化章节正文的操作。

### 7.4 回归目标

双 Marker 测试达到：

```text
marker occurrences = 2
final bindings = 2
verified occurrences = 2
orphan markers = 0
orphan bindings = 0
bijection = true
```

### 7.5 验收判断

**通过。**

没有通过忽略 paragraph、marker offset 或放宽 Citation Integrity Gate 来规避问题。

---

## 8. 独立测试复核

验收使用解释器：

```text
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe
Python 3.10.20
```

未使用仓库 `.venv`。

### 8.1 四项核心与相邻回归

执行：

```bash
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe -m pytest -q \
  --basetemp=E:\rag-agent-tmp\pytest-four-bug-independent-acceptance \
  backend/tests/test_stage_d_evidence_governance_v2.py \
  backend/tests/test_project_input_e2e_contract.py \
  backend/tests/test_stage_e_writer_governance.py \
  backend/tests/test_stage_e_claim_classifier_fail_closed.py \
  backend/tests/test_citation_binding_regression.py \
  backend/tests/test_day2_architecture_closure.py::test_run_demo_forwards_trusted_retrieval_access_scope
```

结果：

```text
52 passed in 1.56s
```

### 8.2 Stage D/E/F 聚合回归

执行全部顶层：

```text
test_stage_d*.py
test_stage_e*.py
test_stage_f*.py
```

结果：

```text
160 passed in 18.66s
```

### 8.3 全量测试

实施方报告：

```text
1466 passed
2 skipped
0 failed
```

两个跳过项依赖：

- Microsoft Word COM 环境；
- 真实模型凭证。

验收方尝试独立执行：

```bash
python -m pytest -q backend/tests
```

DevSpace 调用返回上游 502，未留下 pytest 进程。因此：

- 实施方的全量测试结果可作为实施证据记录；
- 本次验收没有独立复现 1466 的完整数字；
- 已独立覆盖四项核心及 Stage D/E/F 共 212 项，均无失败。

---

## 9. 真实政务云预生成探针

### 9.1 探针文件

```text
scripts/run_gov_cloud_pre_generation_probe.py
```

### 9.2 成功产物

```text
E:\rag-agent-runtime\gov_cloud_pre_generation_probe_20260805_success.json
```

Trace：

```text
E:\rag-agent-runtime\gov_cloud_pre_generation_probe_runtime\runs\gov_cloud_pre_generation_probe_20260805_trace.jsonl
```

### 9.3 重要事实修正

实施摘要中写道：

```text
探针仍解析到项目原 active pointer，真实检索仍为 0 条
```

该描述已经不是当前事实。

成功探针显示：

- 八个章节均完成真实检索；
- 每章 `raw_retrieved_count = 5`；
- Dense 与 BM25 均有命中；
- C-RAG 均为 `sufficient`；
- 每章均产生纠正检索记录；
- 每章 Gate 均为 `qualified`；
- 每章 `generation_allowed = true`；
- `claim_resolution_mode = candidate_capability_union`；
- `requested_claim_types_ignored = true`。

### 9.4 八章检索结果

| 章节 | Raw | Selected | Citation | C-RAG | Delivery | Generation |
|---|---:|---:|---:|---|---|---|
| 项目背景 | 5 | 3 | 4 | sufficient | qualified | true |
| 建设目标 | 5 | 3 | 3 | sufficient | qualified | true |
| 总体架构 | 5 | 4 | 5 | sufficient | qualified | true |
| 计算资源建设方案 | 5 | 5 | 6 | sufficient | qualified | true |
| 存储资源建设方案 | 5 | 4 | 4 | sufficient | qualified | true |
| 网络资源建设方案 | 5 | 5 | 10 | sufficient | qualified | true |
| 安全设计 | 5 | 4 | 4 | sufficient | qualified | true |
| 实施与验收建议 | 5 | 3 | 3 | sufficient | qualified | true |

### 9.5 索引血缘

成功 Trace 明确使用：

```text
pointer_path:
D:\MyCode\rag-agent\data\processed\indexes\active_index.json

manifest_path:
E:\rag-agent-indexes\idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e\index_manifest.json

db_file:
E:\rag-agent-indexes\idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e\milvus.db

collection:
rag_child_chunks

child_chunk_count:
10247

embedding:
D:/models/huggingface/embedding/m3e-base

vector_db:
milvus_lite
```

因此：

- 原活动索引当前可以被真实检索链使用；
- Milvus `DataDirLockedError` 不是当前预生成探针的持续阻塞；
- 临时索引副本和 `RAG_PROJECT_ROOT` 覆盖不是成功探针的必要前提；
- 原索引没有被本次验收修改。

### 9.6 Bug 1 的真实链验证

例如“项目背景”章节：

允许：

```text
general_method
policy_requirement
reference_case
typical_architecture
```

禁止：

```text
current_project_configuration
current_project_cost
current_project_fact
current_project_quantity
current_project_schedule
```

这证明 Query 中的“金额不得编造”没有再把 Gate 收缩成 `current_project_cost`，候选证据能力并集模式已进入真实链。

### 9.7 探针验收判断

**通过。**

但探针只到检索、C-RAG 和 Evidence Governance，不等于完整章节生成和发布验收。

---

## 10. 完整真实政务云 E2E

### 10.1 运行方法

DevSpace 的单次 Bash 调用最多 300 秒，不能直接承载完整真实 E2E。本次采用 Windows 独立进程：

```text
DevSpace 启动 Start-Process
→ Python 进程脱离单次 Bash 生命周期持续运行
→ stdout / stderr 重定向到 E: 运行目录
→ 轮询 PID、Trace 和 e2e_report
→ 进程自然退出
```

关键点：

- 不修改生产代码；
- 不后台承诺结果，而是在本次验收中持续轮询至进程结束；
- 不删除锁文件；
- 不强杀模型或 Milvus 进程；
- 使用唯一 Run ID 和唯一输出目录；
- 运行结果通过 `e2e_report.json` 判定，而不是只看控制台退出码。

可复用 PowerShell 结构：

```powershell
$root = "E:\rag-agent-runtime\<unique_run_id>"
New-Item -ItemType Directory -Force -Path $root | Out-Null

$fixture = "D:\MyCode\rag-agent\backend\tests\fixtures\e2e\gov_cloud_framework_project_input.json"
$query = (Get-Content -Raw -Encoding UTF8 $fixture | ConvertFrom-Json).user_query

$args = @(
  "scripts\run_demo.py",
  "--user-input", $query,
  "--project-input-file", "backend\tests\fixtures\e2e\gov_cloud_framework_project_input.json",
  "--authorized-kb-id", "historical_case_kb",
  "--authorized-kb-id", "policy_standard_kb",
  "--authorized-kb-id", "project_fact_kb",
  "--authorized-kb-id", "technical_reference_kb",
  "--authorized-kb-id", "template_style_kb",
  "--run-id", "<unique_run_id>",
  "--output-root", $root,
  "--strict",
  "--e2e-report-path", ($root + "\e2e_report.json")
)

Start-Process `
  -FilePath "D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe" `
  -ArgumentList $args `
  -WorkingDirectory "D:\MyCode\rag-agent" `
  -RedirectStandardOutput ($root + "\console.stdout.log") `
  -RedirectStandardError ($root + "\console.stderr.log") `
  -PassThru
```

### 10.2 正式运行

Run ID：

```text
acceptance_gov_cloud_e2e_20260805_1608
```

输出目录：

```text
E:\rag-agent-runtime\acceptance_gov_cloud_e2e_20260805_1608
```

核心产物：

```text
E:\rag-agent-runtime\acceptance_gov_cloud_e2e_20260805_1608\e2e_report.json
E:\rag-agent-runtime\acceptance_gov_cloud_e2e_20260805_1608\acceptance_gov_cloud_e2e_20260805_1608_experiment_manifest.json
E:\rag-agent-runtime\acceptance_gov_cloud_e2e_20260805_1608\runs\acceptance_gov_cloud_e2e_20260805_1608_trace.jsonl
E:\rag-agent-runtime\acceptance_gov_cloud_e2e_20260805_1608\runs\acceptance_gov_cloud_e2e_20260805_1608_model_usage.json
E:\rag-agent-runtime\acceptance_gov_cloud_e2e_20260805_1608\runs\acceptance_gov_cloud_e2e_20260805_1608_rejected_draft.md
```

运行耗时：

```text
1028.9 seconds
约 17 分 9 秒
```

运行使用：

- 显式 ProjectInput；
- 八章固定契约；
- `allow_demo_defaults=false`；
- 五个可信 KB Scope；
- 原活动 MilvusLite 索引；
- `rag_child_chunks`；
- m3e-base embedding；
- 本地 Qwen 1.5B 检索判断；
- 本地 Qwen 3B 章节生成；
- 非 Fake runtime。

### 10.3 执行完整性

最终报告：

```text
status = failed
task_status = failed
execution_integrity = passed
business_quality = failed
business_gate_passed = false
validation_error = null
```

Artifact lifecycle：

```text
generated_draft = true
rejected_artifact = true
deliverable_final_result = false
```

运行规模：

```text
section_count = 8
rag_call_count = 10
model_call_count = 158
trace_event_count = 428
graph_node_event_count = 16
content_chars = 5522
```

这表示：

> E2E 技术链完整执行，最终失败是业务 Hard Gate 拒绝，不是运行时异常、Milvus 锁、检索为 0 或报告未落盘。

### 10.4 八章结果

| 章节 | 状态 | 正文字符 | Citation | Recovery |
|---|---|---:|---:|---:|
| 项目背景 | failed | 618 | 2 | 1 |
| 建设目标 | failed | 428 | 1 | 0 |
| 总体架构 | failed | 1223 | 3 | 0 |
| 计算资源建设方案 | failed | 615 | 3 | 0 |
| 存储资源建设方案 | failed | 854 | 2 | 0 |
| 网络资源建设方案 | failed | 551 | 1 | 0 |
| 安全设计 | failed | 560 | 2 | 0 |
| 实施与验收建议 | failed | 588 | 2 | 0 |

八章共同失败原因：

```text
生成后 Claim Audit 未通过
```

所有章节 Writer 状态均为 `qualified`，并且全部：

```text
writer_human_review_required
writer_claim_audit_failed
writer_section_status_not_deliverable
```

### 10.5 Citation Binding 真实链结果

Document Hard Gate 中以下检查全部通过：

```text
citations_present = true
citation_referential_integrity = true
citation_marker_binding_bijection = true
citation_marker_occurrences_verified = true
citation_orphan_markers_absent = true
citation_orphan_bindings_absent = true
citation_marker_binding_consistent = true
citation_required_sections_bound = true
citation_targets_valid = true
citation_grounding_verified = true
```

计数：

```text
citation_binding_count = 16
citation_marker_occurrence_count = 16
verified_binding_occurrence_count = 16
orphan markers = 0
orphan bindings = 0
bijection = true
```

结论：

> Bug 4 的 Qualifier 后最终重新绑定修复在完整真实 E2E 中通过。

### 10.6 Claim Audit 结果

各章审计违规计数：

| 章节 | Claim 数 | unclassified | forbidden | not allowed |
|---|---:|---:|---:|---:|
| 项目背景 | 5 | 6 | 3 | 2 |
| 建设目标 | 5 | 2 | 3 | 0 |
| 总体架构 | 6 | 16 | 1 | 0 |
| 计算资源建设方案 | 4 | 6 | 2 | 0 |
| 存储资源建设方案 | 8 | 5 | 8 | 0 |
| 网络资源建设方案 | 7 | 7 | 5 | 0 |
| 安全设计 | 1 | 8 | 0 | 0 |
| 实施与验收建议 | 16 | 1 | 5 | 0 |

合计：

```text
unclassified_substantive_claim = 51
forbidden_claim_type_generated = 27
claim_type_not_allowed = 2
```

这说明 Bug 3 的局部配置误判已修复，但 Claim Classifier 与真实长文本之间仍存在明显覆盖缺口；同时模型仍会生成 Gate 禁止的声明。

### 10.7 高风险当前项目事实

存储资源章节产生了未被 ProjectInput 或当前项目证据支持的确定事实，包括：

```text
AZ1 / AZ2 两个节点
每个节点 324.2TB
每个节点新增 8 台 XSSD 块存储服务器
Kunpeng 920 7260
512GB 内存
12 × 7.68TB U.2 NVMe SSD
具体 RAID、端口和硬件配置
```

Hard Gate 正确记录：

```text
mandatory_project_fact_not_supported
high_risk_claims_grounded = false
```

这表明历史材料中的具体配置仍被模型改写成当前项目确定配置。Writer Prompt 已写明边界，但仅靠 Prompt 没有阻止该行为。

### 10.8 Document Hard Gate

通过项：

```text
required_sections_present
not_truncated
tool_success
output_schema_valid
citation 全链完整
writer gate / identity / lineage 完整
qualifiers applied
workflow_complete
assembly_validation_passed
final_artifact_review_passed
document_version_decided
```

失败项：

```text
high_risk_claims_grounded = false
writer_human_review_clear = false
writer_claim_audits_passed = false
writer_section_status_deliverable = false
writer_formal_deliverable_eligible = false
section_status_valid = false
document_verification_passed = false
conflict_repairs_resolved = false
```

Hard Gate 最终拒绝原因：

1. 存在未获项目输入或已验证证据支持的高风险项目事实；
2. 文档仍包含待人工复核章节；
3. 存在未通过的章节 Claim Audit；
4. 存在不可交付状态章节；
5. 存在经 Document Hard Gate 重算后不具备正式交付资格的章节；
6. 存在失败章节；
7. `document_verification_failed`；
8. `document_conflict_repairs_unresolved`。

### 10.9 完整 E2E 验收判断

**执行完整性通过，业务交付失败。**

这次运行已经足以确认：

- 四个原 Bug 的主链修复进入真实运行；
- Bug 4 真实 Citation bijection 已通过；
- Milvus、真实检索、C-RAG、模型、装配、Hard Gate 和 rejected artifact 链均完整；
- 当前剩余问题是新的真实业务坏例，不是 E2E 没跑完。

---

## 11. 验收矩阵

| 验收项 | 结果 | 说明 |
|---|---|---|
| 仓库安全约束 | 通过 | 未 reset/clean/切分支/提交 |
| Bug 1 代码修复 | 通过 | Query 退出 Claim 能力计算 |
| Bug 1 真实检索链 | 通过 | 候选能力并集进入真实 Gate |
| Bug 2 显式 ProjectInput | 通过 | 八章、缺失信息、demo=false |
| Bug 2 KB Scope | 通过 | CLI 参数和透传测试通过 |
| Bug 3 局部配置误判修复 | 通过 | 2.1.0，普通方法句不再仅凭对象词误判 |
| Bug 3 完整业务输出 | 未通过 | 仍有 51 个 unclassified 和 27 个 forbidden 违规 |
| Bug 4 Citation 生命周期 | 通过 | 16/16 verified，0 orphan，bijection=true |
| 四项核心测试 | 通过 | 独立 52/52 |
| Stage D/E/F 回归 | 通过 | 独立 160/160 |
| 全量测试 | 实施方通过 | 1466 passed、2 skipped；验收方因 502 未独立复现 |
| 真实预生成 RAG 探针 | 通过 | 八章真实检索、C-RAG、Gate 均成功 |
| 完整真实生成 E2E | 已完成 | 1028.9 秒，428 Trace 事件 |
| E2E 执行完整性 | 通过 | execution_integrity=passed，validation_error=null |
| E2E 业务质量 | 未通过 | business_quality=failed |
| 最终文档发布 | 正确拒绝 | 生成 rejected draft，无 deliverable |
| Multi-Agent 恢复条件 | 未满足 | Claim Audit 与历史事实污染尚未闭环 |

---

## 12. 最终验收结论

### 12.1 四个原 Bug

四个原 Bug 的最小代码修复验收通过。

可以确认：

1. “金额不得编造”不再被 Evidence Governance 解析成当前项目金额需求；
2. 正式政务云场景不再使用 Demo 固定章节；
3. 普通计算、存储、网络方法句不再仅凭对象词被判为当前项目配置；
4. Writer Prompt 已明确高风险事实边界；
5. Qualifier 注入后最终 Citation Binding 会从最终正文重新构建；
6. 真实 E2E 中 Citation Marker/Binding bijection 已通过；
7. 下游 Gate 未被放宽；
8. 真实 RAG 可以在原活动索引上为八个章节返回证据和 Gate。

### 12.2 整体状态

整体状态应标记为：

```text
Original Four Bugs Accepted
Full Real E2E Completed
Execution Integrity Passed
Business Quality Failed
Rejected Artifact Persisted
```

不应再标记为：

```text
Pending Full Real E2E Closure
```

### 12.3 新的真实问题

完整 E2E 暴露了三个新的优先问题：

1. **Classifier 覆盖缺口**：真实长文本产生 51 个 `unclassified_substantive_claim`；
2. **历史材料转当前项目事实**：存储章节把历史配置直接写成当前项目的 AZ、容量、数量、品牌型号和硬件参数；
3. **验收契约语义**：Fixture 明确 `need_human_review=true`，同时所有 Gate 都是 `qualified`，因此正式交付资格天然无法通过；需要明确本场景的成功定义究竟是“产出可人工复核 rejected draft”还是“自动正式发布”。

第三项不是简单代码 Bug，而是验收目标和发布政策需要冻结。

---

## 13. 下一轮最小闭环

下一轮不再重复解决 E2E 运行方式，只处理以下三件事：

### 13.1 冻结成功定义

在测试契约中明确二选一：

```text
A. qualified + human_review_required
   允许生成可人工复核文档，但不得正式发布

B. grounded + human_review_clear
   才允许进入正式不可变发布
```

当前 Fixture 使用：

```text
need_human_review = true
```

因此不能同时把“正式发布成功”作为必达断言。

### 13.2 处理历史材料污染

历史案例包含数量、容量、品牌和型号时，必须确保它只能进入：

```text
reference_case
典型案例
参考参数
非本项目确定事实
```

不能进入：

```text
本项目配置
本项目数量
本项目容量
本项目品牌型号
```

仅增加 Prompt 文字已经被本次 E2E 证明不足。下一轮应优先审查 Prompt Evidence 渲染、历史材料标签和生成后确定性裁剪/重写边界，不新增 Agent。

### 13.3 收敛 Claim Classifier

以本次 rejected draft 为固定坏例集，逐条区分：

```text
真正无法分类的实质声明
Classifier 规则缺口
应属于 general_method / typical_architecture 的合法句子
模型生成的禁止声明
```

禁止直接把所有 unclassified 放行；应先形成真实句子级映射测试，再做最小规则修复。

---

## 14. 后续开发边界

在上述三个问题闭环前：

- 不继续扩展 Multi-Agent；
- 不继续 Memory；
- 不继续 Research Loop；
- 不重构整个 Evidence Governance；
- 不增加新的语义分类模型；
- 不增加新的 Gate；
- 不通过放宽 Claim Audit 或 Hard Gate 让 E2E 变绿。

下一轮只允许：

```text
冻结业务成功定义
提取本次真实坏例
修历史材料转当前项目事实
修 Classifier 的真实覆盖缺口
增加对应场景回归
再次运行同一 E2E
```

---

## 15. 变更与提交状态

- 本次验收更新了本记录文件；
- 完整 E2E 产物写入 `E:\rag-agent-runtime\acceptance_gov_cloud_e2e_20260805_1608`；
- 未提交代码；
- 未执行 `git reset`；
- 未执行 `git clean`；
- 未切换分支；
- 未修改原活动索引；
- 未继续开发 Multi-Agent、Memory 或 Research Loop。
