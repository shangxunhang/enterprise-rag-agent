# RAG-Agent 四个 E2E 阻断问题修复实施方案

- 日期：2026-08-05
- 项目：`D:\MyCode\rag-agent`
- 当前分支：`checkpoint/controlled-architecture-20260728`
- 实施性质：已知问题修复与 E2E 诊断基线恢复
- 当前优先级：高于继续开发 Multi-Agent

---

## 1. 实施目标

本轮只修复第一次政务云真实 E2E 暴露出的四个已知问题，使下一次 E2E 能够真实反映：

1. C-RAG 是否召回了与章节相关的材料；
2. Document Profile 与 Evidence Governance 是否正确限制证据权限；
3. Writer 是否能在证据许可范围内稳定生成；
4. Citation Binding 是否与最终正文严格一致；
5. 剩余失败究竟来自语料缺口、小模型能力还是新的实现问题。

本轮不继续新增 Multi-Agent、Memory、Research Loop 或跨章节复用能力。

---

## 2. 已冻结的职责边界

主链职责固定如下：

```text
ProjectInput
→ 明确章节结构、项目事实、缺失信息和输出要求

C-RAG
→ 判断召回结果是否与当前章节相关
→ 判断是否覆盖章节问题
→ 决定是否改写 Query、继续检索或停止检索

Evidence Governance
→ 不解析用户意图
→ 不判断用户“想不想要金额、数量、品牌或配置”
→ 只检查候选材料自身的证据身份、权限和限制
→ 汇总当前可用证据能够支持的 Claim Types

Writer Gate
→ 将 Evidence Governance 的最终证据能力投影给 Writer

Writer
→ 只能在 Gate 允许范围内生成

Claim Audit
→ 检查最终正文是否生成了未允许或明确禁止的声明

Citation Hard Gate
→ 检查最终正文中的 Marker 与 Citation Binding 是否一一对应
```

### 2.1 Bug 1 已选定方案

采用“候选证据能力并集模式”。

Evidence Governance 不再依赖 `requested_claim_types` 过滤候选证据，也不再通过 `_infer_claim_policy()` 从完整 Query 推断 Claim 需求。

核心规则：

```text
章节问题决定检索什么                 ← C-RAG
召回材料决定能够写什么               ← Evidence Governance
最终正文是否超出允许能力             ← Claim Audit
```

---

## 3. 范围控制

### 3.1 本轮必须完成

1. Bug 1：移除 Evidence Governance 的用户意图推断职责；
2. Bug 2：正式 E2E 使用显式 Canonical ProjectInput；
3. Bug 3：对齐 Gate、Writer Prompt 与 Deterministic Claim Classifier；
4. Bug 4：Qualifier 注入后基于最终正文重新生成 Citation Binding；
5. 为四个问题建立回归测试；
6. 运行 Stage D/E/F 相关测试和全量回归；
7. 使用同一个政务云场景重新运行真实 E2E。

### 3.2 本轮明确不做

- 不新增 Claim Intent Contract；
- 不做否定句或用户意图语义解析器；
- 不新增 LLM Claim Classifier；
- 不增加额外模型调用；
- 不建立章节名称到 Claim Type 的硬编码映射表；
- 不开发自然语言章节提取器；
- 不新增 Agent；
- 不新增 Memory；
- 不继续 Multi-Agent 功能开发；
- 不建立新的大型测试平台；
- 不通过放宽 Claim Audit、Evidence Gate 或 Citation Hard Gate 让 E2E 强行通过。

---

## 4. 实施前安全要求

当前工作区存在较多未提交修改。实施过程中必须遵守：

1. 禁止执行 `git reset --hard`；
2. 禁止执行 `git clean`；
3. 禁止切换分支；
4. 禁止批量覆盖与本轮无关的文件；
5. 禁止使用“提交所有改动”作为本轮收尾方式；
6. 每个 Bug 独立建立测试、独立验证；
7. 发现现有实现与本文不一致时，先记录差异，不得自行扩大架构。

第一次真实 E2E 基线：

```text
run_id:
manual_gov_cloud_e2e_20260805_1200

root:
E:\rag-agent-runtime\manual_gov_cloud_e2e_20260805_1200

report:
E:\rag-agent-runtime\manual_gov_cloud_e2e_20260805_1200\e2e_report.json

trace:
E:\rag-agent-runtime\manual_gov_cloud_e2e_20260805_1200\runs\manual_gov_cloud_e2e_20260805_1200_trace.jsonl

raw interactions:
E:\rag-agent-runtime\manual_gov_cloud_e2e_20260805_1200\captures\raw_interactions\manual_gov_cloud_e2e_20260805_1200_raw_interactions.jsonl
```

---

# 5. Bug 1：Evidence Governance 错误承担用户意图推断

## 5.1 当前问题

文件：

```text
backend/rag/evidence/governance_v2.py
```

当前正式路径在缺少显式 `requested_claim_types` 时调用：

```python
_infer_claim_policy(
    section_title + section_type + query
)
```

因此完整 Query 中的“金额不得编造”被识别成：

```text
requested_claim_types = current_project_cost
```

后续 `_item_assessment()` 使用：

```python
supported_claims = requested & allowed - forbidden
```

导致只能支持通用方法、政策、模板或历史案例的候选材料全部失去资格。

## 5.2 修复原则

Evidence Governance 只根据候选材料能力进行资格判断：

```python
supported_claims = candidate.allowed_claim_types - candidate.forbidden_claim_types
```

之后再根据以下约束收紧：

- `project_relation`；
- `authority_level`；
- `document_type`；
- `evidence_role`；
- `structured_evidence`；
- `fact_status`；
- Prompt 中结构化事实是否真实可见；
- 与当前章节主题的相关度。

Query 仍可用于计算相关度和主题覆盖，但不得用于推断 Claim 权限。

## 5.3 具体修改

### A. `EvidenceRequirementsV2Schema`

保留 `requested_claim_types` 字段作为兼容字段，但正式主链不再消费。

修改要求：

```python
requested_claim_types: list[ClaimType] = Field(default_factory=list)
```

删除“`requested_claim_types` 不得为空”的校验。

在 metadata 中记录：

```json
{
  "claim_resolution_mode": "candidate_capability_union",
  "requested_claim_types_ignored": true
}
```

第一轮不要直接删除字段，以免历史产物和旧测试全部同时失效。字段清理应作为后续独立任务。

### B. `resolve_evidence_requirements_v2()`

正式路径不再调用 `_infer_claim_policy()`。

该函数只负责生成：

- `section_id`；
- `section_type`；
- `required_topics`；
- `coverage_strategy`；
- `allow_framework_only`；
- 诊断 metadata。

默认策略：

```text
coverage_strategy = topic_union
claim_risk_level = low
resolution = candidate_capability_union
```

`_infer_claim_policy()` 暂时保留，但只能标记为 legacy/demo compatibility，不得由正式 E2E 和生产主链调用。

### C. `_item_assessment()`

当前：

```python
allowed = set(candidate.allowed_claim_types)
forbidden = set(candidate.forbidden_claim_types)
requested = set(requirements.requested_claim_types)
supported_claims = requested & allowed - forbidden
```

改为：

```python
allowed = set(candidate.allowed_claim_types)
forbidden = set(candidate.forbidden_claim_types)
supported_claims = allowed - forbidden
```

然后继续执行现有的：

- 非当前项目材料删除 `CURRENT_PROJECT_CLAIM_TYPES`；
- 结构化事实不兼容时删除对应 Claim；
- Fact Status 不合格时删除对应 Claim；
- 主题无关时判为不合格。

被删除的 Claim 可以记录进 assessment 的 `forbidden_claim_types` 或 reason codes，但不得再次从用户 Query 构造禁止集合。

资格条件保持：

```python
eligibility = (
    bool(supported_claims)
    and relevance is not EvidenceRelevance.IRRELEVANT
)
```

### D. `_set_assessment()`

主题覆盖仍由：

```text
required_topics + relevance + supported_topics
```

判断。

删除或弃用 metadata 中将 `requested_claim_types` 作为核心诊断依据的逻辑，替换为：

```json
{
  "claim_resolution_mode": "candidate_capability_union"
}
```

### E. `_gate()`

当前：

```python
requested = set(requirements.requested_claim_types)
allowed = supported & requested
forbidden = requested - allowed
```

改为：

```python
allowed = union(
    assessment.supported_claim_types
    for assessment in eligible_assessments
)
```

`forbidden_claim_types` 只汇总候选 Profile 明确禁止、关系校验删除或结构化事实校验删除的能力。

注意：Claim Audit 本身已经会拒绝任何“不在 allowed 中”的 Claim，因此不需要人为构造“所有未允许类型”的巨大 forbidden 集合。

Gate 决策继续遵守：

- 证据冲突：`HUMAN_REVIEW`；
- 无合格证据：`BLOCKED`；
- 历史或参考材料：`QUALIFIED`；
- 当前项目高权威材料：允许相应当前项目 Claim；
- Prompt 证据契约缺失：`BLOCKED`；
- legacy fallback：`QUALIFIED + human review`。

### F. 风险等级

风险等级不得再由 Query 关键词产生，应由最终 `allowed_claim_types` 推导：

```text
包含 CURRENT_PROJECT_* 或 PROCUREMENT_REQUIREMENT
→ HIGH

包含 POLICY_REQUIREMENT 或 TECHNICAL_SPECIFICATION
→ MEDIUM

仅包含 GENERAL_METHOD / TYPICAL_ARCHITECTURE /
REFERENCE_CASE / TEMPLATE_STRUCTURE / STYLE_GUIDANCE
→ LOW
```

## 5.4 Bug 1 回归测试

扩展：

```text
backend/tests/test_stage_d_evidence_governance_v2.py
```

至少包含：

### 测试 A：Query 不改变证据能力

对同一组候选材料分别使用：

```text
生成项目建设方案
金额不得编造
请给出预算金额
品牌型号不得虚构
```

断言最终 `allowed_claim_types` 完全一致。

### 测试 B：历史材料不得支持当前项目事实

候选 Profile 即使声明 `current_project_cost`，只要：

```text
project_relation = other_project
```

治理后必须删除 `current_project_cost`，但保留 `reference_case` 或 `typical_architecture`。

### 测试 C：当前项目正式材料可以支持高风险事实

当候选具备：

```text
project_relation = current_project
authority = confirmed
structured fact compatible
fact_status eligible
```

治理后可以授权对应的 `current_project_cost`、`current_project_quantity` 或其他当前项目事实。

### 测试 D：无合格候选仍然 Blocked

方案一不等于“只要召回就允许生成”。

当候选全部主题无关、Profile 合同错误或结构化事实不可用时，Gate 仍必须 `BLOCKED`。

---

# 6. Bug 2：正式 E2E 使用了 Demo 固定章节

## 6.1 当前问题

文件：

```text
backend/application/project_input_factory.py
scripts/run_demo.py
```

`run_demo.py` 在未提供 `--project-input-file` 时：

```python
allow_demo_defaults = True
```

随后使用：

```text
DEFAULT_SCHEME_SECTIONS
```

因此 Planner 按错误输入正确地产生了 Demo 八章。

## 6.2 修复原则

- 不修改 Planner；
- 不修改 `DocumentPlanningService`；
- 不开发自然语言章节提取器；
- 正式 E2E 必须显式提供 Canonical ProjectInput；
- Demo 默认章节只允许用于 Smoke Test。

## 6.3 具体实施

新增政务云 E2E Fixture：

```text
backend/tests/fixtures/e2e/gov_cloud_framework_project_input.json
```

必须明确包含以下章节，名称和顺序不得变化：

```text
1. 项目背景
2. 建设目标
3. 总体架构
4. 计算资源建设方案
5. 存储资源建设方案
6. 网络资源建设方案
7. 安全设计
8. 实施与验收建议
```

同时在 `generation_requirements.required_sections` 和 `output_schema.required_sections` 中声明相同列表。

`missing_information` 至少包含：

```text
项目现状
用户规模
计算资源数量
存储容量
网络带宽
设备品牌型号
投资金额
实施工期
```

正式 E2E 命令必须使用：

```powershell
python scripts/run_demo.py `
  --user-input "<与第一次 E2E 相同的政务云请求>" `
  --project-input-file "backend/tests/fixtures/e2e/gov_cloud_framework_project_input.json" `
  --strict `
  --e2e-report-path "<新的报告路径>"
```

此时必须满足：

```python
project_input is not None
allow_demo_defaults is False
```

第一轮不要求修改 `run_demo.py` 增加新的 CLI 参数。现有 `--project-input-file` 已足够。防误用通过测试和固定 E2E 命令解决。

## 6.4 Bug 2 回归测试

新增或扩展主链契约测试，断言以下四处完全一致：

```text
ProjectInput.generation_requirements.required_sections
ProjectInput.output_schema.required_sections
DocumentPlan.sections
最终 SchemeDraft.sections
```

必须精确断言：

```text
章节数 = 8
顺序完全一致
不得出现“技术方案”
不得出现“资源配置”
不得自动追加“待补充事项”
```

---

# 7. Bug 3：Gate、Writer Prompt 与 Claim Classifier 语义不一致

## 7.1 当前问题

文件：

```text
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
```

当前 `_CONFIGURATION_PATTERN` 存在过宽分支：

```python
(?:型号|规格|配置|部署|节点|服务器|存储|网络|数据库|...)
```

仅出现“服务器、存储、网络”等领域名词，就可能被判断为：

```text
current_project_configuration
```

同时 Writer Prompt 仅向小模型暴露抽象枚举：

```text
allowed_claim_types
forbidden_claim_types
```

缺少可执行的语言边界。

## 7.2 修复原则

本轮只完成：

1. 收窄确定性高风险配置规则；
2. 将 Gate 中现有权限翻译成通用写作边界；
3. 保持 Claim Audit fail-closed；
4. 不新增语义分类模型；
5. 不根据具体章节名写死 Claim 规则；
6. 不扩大 Gate 权限来强行通过。

## 7.3 收窄 `_CONFIGURATION_PATTERN`

删除无条件领域名词分支。

改为只有同时出现“当前项目主体 + 确定性配置动作”时，才判为 `current_project_configuration`。

建议规则：

```python
_CONFIGURATION_PATTERN = re.compile(
    r"(?:本项目|本次建设|本系统|项目建设|项目将|拟建设|拟采用)"
    r"(?:[^。；\n]{0,48})"
    r"(?:采用|配置|部署|安装|配备|选用|接入|升级)"
)
```

应判为 `current_project_configuration`：

```text
本项目采用 Hadoop 集群。
本次建设部署三台服务器。
本系统配置对象存储。
```

不得仅因领域词判为 `current_project_configuration`：

```text
计算资源规模应结合业务并发量和性能目标进行测算。
存储方案应考虑容量增长和备份周期。
网络规划应结合业务区域和安全域进行设计。
```

具体品牌、具体组件建议即使使用“建议、可采用”，在没有证据许可时也不能自动通过；无法稳定归类时继续按 fail-closed 处理。

## 7.4 加强 `writer_gate_prompt_contract()`

在现有最终证据门禁 Prompt 中，根据 Gate 缺失的权限动态添加通用限制。

至少包含：

```text
未允许 current_project_configuration：
不得把具体产品、品牌、型号、组件或技术选型写成本项目确定配置。

未允许 current_project_quantity：
不得确定服务器数量、节点数量、容量、并发规模等项目数值。

未允许 current_project_cost：
不得给出项目预算、概算、费用、造价和金额结论。

未允许 current_project_schedule：
不得确定项目工期、上线时间、交付日期和实施周期。

历史案例和通用参考不得改写为当前项目已经确定的事实。
缺少项目特定信息时，应写测算维度、选择原则或待确认项。
```

这些限制由 Gate 中已有 Claim 权限决定，不得按“政务云、计算资源、安全设计”等章节名称硬编码。

## 7.5 分类器版本

修改任何 Claim Pattern 后，必须升级：

```python
CLAIM_CLASSIFIER_VERSION
```

例如：

```python
CLAIM_CLASSIFIER_VERSION = "2.1.0"
```

并重新生成：

```text
CLAIM_CLASSIFIER_POLICY_SHA256
SUPPORTED_CLASSIFIER_POLICIES
```

不得修改规则但继续声明旧版本号。

## 7.6 Bug 3 回归测试

扩展：

```text
backend/tests/test_stage_e_writer_governance.py
```

建议使用参数化测试。

### 不得误判为当前项目配置

```text
计算资源规模应结合并发量和性能目标进行测算。
存储方案应考虑容量增长和备份周期。
网络规划应结合业务区域和安全域进行设计。
典型政务云可采用分层架构。
```

### 必须识别并在无权限时拒绝

```text
本项目采用 Hadoop 集群。
本次建设部署三台服务器。
本项目预算为 500 万元。
本项目计划于 2026 年 10 月上线。
```

### 具体选型仍不得越权

```text
建议使用阿里云 OSS。
建议部署 Hadoop 和 Spark。
```

当 Gate 未授权对应 Claim 时，上述句子必须被拒绝或进入未分类实质声明的 fail-closed 路径，不能为了通过测试扩大 `allowed_claim_types`。

---

# 8. Bug 4：Qualifier 注入后 Citation Binding 未重建

## 8.1 当前问题

文件：

```text
backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py
```

当前顺序：

```text
正文生成与引用修复
→ 创建 Citation Binding
→ reconcile Marker
→ inject_required_qualifiers
→ 正文段落号和字符位置变化
→ 保存旧 Binding
```

导致：

```text
paragraph_id
claim_id
marker_index
marker_start
marker_end
```

与最终正文不一致。

## 8.2 修复原则

Citation Binding 必须针对最终不可再修改的章节正文生成。

生成过程中的 Binding 可以作为 `working_bindings` 供 Self-RAG、Citation Repair 或 Grounded Regeneration 使用，但保存到 `SchemeSectionSchema` 的必须是 `final_bindings`。

## 8.3 正确顺序

```text
模型生成
→ 截断恢复、压缩、局部修复
→ Citation 插入与 Grounding 校验
→ Self-RAG / Repair Strategy
→ reconcile Marker
→ inject_required_qualifiers
→ 基于新正文重新 bind_citations
→ supported_bindings
→ reconcile Marker
→ 基于清理后的正文再次 bind_citations
→ supported_bindings
→ Claim Audit
→ High Risk Gate
→ Section 冻结
```

## 8.4 具体修改

当前大致逻辑：

```python
content = self.citation_service.reconcile_citation_markers(content, bindings)

content, applied_qualifiers = inject_required_qualifiers(
    content,
    writer_gate_binding,
)

citation_ok = (not citation_required) or bool(bindings)
```

改为：

```python
content = self.citation_service.reconcile_citation_markers(
    content,
    bindings,
)

content, applied_qualifiers = inject_required_qualifiers(
    content,
    writer_gate_binding,
)

final_bindings = self.citation_service.supported_bindings(
    self.citation_service.bind_citations(
        document_id=document_id,
        section_id=section_id,
        content=content,
        citations=citations,
    ),
    citations,
)

content = self.citation_service.reconcile_citation_markers(
    content,
    final_bindings,
)

final_bindings = self.citation_service.supported_bindings(
    self.citation_service.bind_citations(
        document_id=document_id,
        section_id=section_id,
        content=content,
        citations=citations,
    ),
    citations,
)

bindings = final_bindings
citation_ok = (not citation_required) or bool(bindings)
```

两次重新绑定的原因：

1. Qualifier 改变段落号和字符位置；
2. 第一次 `reconcile_citation_markers()` 可能删除无效 Marker；
3. 删除 Marker 后正文位置可能再次变化；
4. 第二次 Binding 才是最终持久化身份。

## 8.5 最终冻结要求

第二次 Final Binding 完成后：

- 不得再向 `section.content` 插入文字；
- 不得重新排列段落；
- 不得重新格式化章节内部正文；
- Claim Audit、High Risk Gate 和 Document Assembly 只能读取。

如后续步骤确实需要改写正文，必须再次执行完整 Final Binding 流程。

## 8.6 Bug 4 回归测试

扩展：

```text
backend/tests/test_citation_binding_regression.py
```

构造两条带引用正文，随后注入 Qualified 前缀。

最终断言：

```text
marker_occurrence_count = final_binding_count
verified_occurrence_binding_count = marker_occurrence_count
orphan_marker_count = 0
orphan_binding_count = 0
bijection = true
```

并精确比较最终正文重新解析值：

```text
target_paragraph_id
target_claim_id
target_marker_index
target_marker_start
target_marker_end
claim_text
citation_id
```

所有字段必须来自最终 `section.content`。

---

# 9. 实施顺序

## 阶段 A：建立红测试

先增加四组回归测试，不修改生产实现。

预期：当前代码上测试失败，且失败原因分别对应四个已知问题。

建议文件：

```text
backend/tests/test_stage_d_evidence_governance_v2.py
backend/tests/test_project_input_e2e_contract.py
backend/tests/test_stage_e_writer_governance.py
backend/tests/test_citation_binding_regression.py
```

完成标准：

```text
四个红测试能够独立证明四个问题
测试不是依赖第一次 E2E 文件路径才能运行
测试失败信息可定位到具体契约
```

## 阶段 B：修 Bug 1

修改：

```text
backend/rag/evidence/governance_v2.py
```

完成后运行 Stage D 专项测试。

验收：

```text
Query 内容不再改变 Evidence Claim 能力
“金额不得编造”不再触发 current_project_cost 筛选
历史材料自动失去 CURRENT_PROJECT_* 权限
当前项目正式材料仍可支持对应高风险事实
```

## 阶段 C：处理 Bug 2

新增：

```text
backend/tests/fixtures/e2e/gov_cloud_framework_project_input.json
```

完成章节贯穿测试。

随后运行轻量探针，只检查：

```text
ProjectInput
DocumentPlan
逐章节 Retrieval Query
C-RAG 结果
Evidence Gate
```

此时不必立即生成完整文档。

验收：

```text
八个章节名称、数量和顺序完全正确
计算、存储、网络不再合并
正式运行没有进入 Demo Adapter
```

## 阶段 D：修 Bug 3

修改：

```text
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
```

完成：

```text
收窄 _CONFIGURATION_PATTERN
增强 writer_gate_prompt_contract
升级 classifier version
更新 classifier policy hash
运行 Stage E 专项测试
```

验收：

```text
通用规划方法不再被误判为 current_project_configuration
当前项目确定配置仍被识别
无证据品牌、型号、数量和金额仍无法通过
```

## 阶段 E：修 Bug 4

修改：

```text
backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py
```

优先复用现有：

```text
bind_citations()
supported_bindings()
reconcile_citation_markers()
```

本轮不强制新增 Citation Finalizer 抽象。

验收：

```text
Qualifier 注入后 Marker 与 Binding 仍一一对应
所有最终位置字段来自最终正文
Document Hard Gate 不再产生这五条 orphan
```

## 阶段 F：回归与第二次 E2E

执行顺序：

```text
四组专项测试
→ Stage D/E/F 相关测试
→ 全量回归
→ 同一政务云真实 E2E
```

---

# 10. 第二次真实 E2E 检查项

## 10.1 输入层

```text
八个章节是否正确
ProjectInput 是否明确 missing_information
是否完全绕过 Demo 默认章节
```

## 10.2 C-RAG 层

逐章检查：

```text
原始 Dense/BM25 Hit
Rerank 结果
相关性判断
是否发生 corrective retrieval
停止检索原因
```

重点章节：

```text
总体架构
计算资源建设方案
存储资源建设方案
网络资源建设方案
安全设计
实施与验收建议
```

## 10.3 Evidence Governance 层

检查：

```text
allowed_claim_types 是否来自候选证据能力
Query 中“金额、数量、品牌”是否不再改变证据能力
历史案例是否被限制为 reference_case / typical_architecture
当前项目高风险事实是否仅由当前项目正式材料支持
```

## 10.4 Writer 层

检查：

```text
是否生成通用方法、建设原则、测算维度和典型架构
缺失项目事实是否标记为待确认
是否编造品牌、型号、数量、金额、工期
Claim Audit 的失败是否可解释
```

## 10.5 Citation 层

检查：

```text
Marker count
Binding count
Verified occurrence count
Orphan Marker count
Orphan Binding count
Bijection
```

预期：

```text
orphan_marker_count = 0
orphan_binding_count = 0
bijection = true
```

## 10.6 文档层

检查：

```text
所有必需章节存在
Document Hard Gate 结果可解释
Stage F 仅在满足发布条件时正式发布
```

---

# 11. 第二次 E2E 后的判断规则

## 结果 A：大部分章节正常生成

说明第一次 E2E 的主要阻断确实来自这四个实现问题。

下一步才评估是否恢复 Multi-Agent。

## 结果 B：输入、Gate 和 Citation 正常，但原始检索无相关材料

此时才确认真实语料缺口，并按章节补充：

```text
计算资源
存储资源
网络资源
安全规范
实施验收
```

不得泛化为“整个知识库不够”。

## 结果 C：原始召回相关，但 Evidence Governance 后仍大量为零

继续检查：

```text
Document Profile
project_relation
authority_level
structured evidence
fact_status
Prompt evidence projection
```

此时修 Evidence Governance 数据合同，不得立即扩建 Agent。

## 结果 D：Gate 合理，但 Claim Audit 仍大量失败

检查：

```text
小模型是否理解 Prompt 边界
Classifier 是否仍有确定性误判
是否存在具体选型或项目事实越权
```

先根据真实 badcase 调整，不预先引入新的语义分类系统。

---

# 12. 恢复 Multi-Agent 的最低条件

以下条件全部满足后，才恢复 Multi-Agent 开发：

```text
1. Query 不再影响 Evidence Claim 能力；
2. 用户指定章节 100% 保持；
3. 通用方法不再被误判为当前项目配置；
4. 无证据品牌、型号、数量、金额仍被拒绝；
5. Citation Marker 与 Binding 一一对应；
6. Stage D/E/F 相关测试通过；
7. 全量回归无新增失败；
8. 同一个政务云场景至少连续三次表现一致；
9. 剩余失败能够明确归因于数据、模型或新的独立 Bug。
```

---

# 13. 新窗口执行指令

新窗口开始后，先执行以下动作：

1. 打开项目：`D:\MyCode\rag-agent`；
2. 阅读本文档；
3. 检查当前分支和 dirty tree，不做 reset/clean；
4. 先建立阶段 A 的四组红测试；
5. 按 Bug 1 → Bug 2 → Bug 3 → Bug 4 顺序实施；
6. 每完成一个 Bug，运行对应专项测试；
7. 四项完成后运行 Stage D/E/F 和全量回归；
8. 使用显式政务云 ProjectInput 重跑真实 E2E；
9. 输出第二次 E2E 的输入、检索、Gate、Claim Audit、Citation 和 Hard Gate 对比报告；
10. 未完成第二次 E2E 诊断前，不继续 Multi-Agent。

本轮最终目标不是让所有章节强行通过，而是让失败原因真实、稳定、可诊断。
