# 现有门禁链三项 Bug 修复实施结果

**项目路径：** `D:\MyCode\rag-agent`  
**实施日期：** 2026-08-05  
**基线提交：** `c04e98e80186d2e79810b9ebcd69a990475fbb91`  
**当前分支：** `checkpoint/controlled-architecture-20260728`  
**提交状态：** 未提交

---

## 一、结论

本轮三项既有门禁 Bug 已完成最小修复：

1. C-RAG 不再把“不能证明当前项目事实”当成“不相关”；`sufficient=false` 只继续驱动 Corrective Retrieval，不再无条件删除 surviving evidence。
2. 空或 blocked Recovery 不再覆盖已经获准普通生成的初次证据；有效 Recovery 仍可替换初次结果。
3. Writer 的 Evidence Block、正常 Citation Catalog 和修复 Prompt Citation Catalog 统一使用 `prompt_visible_text`；内部 `CitationSchema.quote_text` 继续保留原始证据，不再作为 Writer Prompt 的回退文本。

门禁结构、Schema、DeliveryMode、Claim Audit、Document Hard Gate 和 Release Gate 均未放宽或扩展。

测试结果：

- 本轮新增回归测试：`8 passed`
- 最终专项回归：`205 passed`
- Stage D/E/F：`161 passed`
- 分片覆盖全部 129 个测试文件：`1483 passed, 2 skipped, 0 failed`
- 目标文件 `git diff --check`：通过，仅有仓库既有 CRLF/LF 提示

真实检索和 Prompt 安全投影探针已完成。完整八章节真实 E2E 已进入真实 Writer/Citation Repair，但被一个新的独立 Context Budget Bug 阻断，因此本轮没有正式发布文档。

---

## 二、工作区保护

实施前已检查当前分支、HEAD 和 dirty tree。

本轮未执行：

- `git reset`
- `git clean`
- 分支切换
- `git checkout .`
- 批量格式化
- 提交代码
- 回滚既有未提交修改

`section_execution_coordinator.py`、`run_demo.py`、`test_day2_architecture_closure.py` 和 `fake_rag_tool.py` 在本轮开始前已经存在未提交修改。本轮先读取当前内容和 diff，再对目标逻辑做精确替换，没有覆盖原有工作。

---

# 三、Bug 1：C-RAG 门禁语义

## 3.1 修改

文件：

```text
backend/rag/judge/crag_judge.py
```

现有 C-RAG LLM Prompt 已明确：

```text
C-RAG 只判断片段是否与当前问题或章节主题相关。
不要判断片段能否证明当前项目事实。
不是当前项目事实，不等于与当前章节无关。
历史案例、技术参考和模板材料可支持通用方法、典型架构、参考经验或章节组织。
只有与当前主题没有实际可用关系时才判为 irrelevant。
```

没有修改 `confidence_threshold`，没有新增状态，也没有修改 C-RAG 的集合评分公式。

现有最终过滤逻辑本身已经只删除 `irrelevant`，因此本轮没有扩大过滤或保留范围，只修正模型判断职责。

## 3.2 回归覆盖

新增测试覆盖：

- 1 relevant + 1 partial + 4 irrelevant，集合 `sufficient=false`：只删除 4 个 irrelevant。
- partial-only 且 `sufficient=false`：历史参考证据继续保留。
- C-RAG Prompt 不再要求判断当前项目事实权限。

## 3.3 真实探针结果

真实政务云探针的“实施与验收建议”章节：

```text
Corrective Retrieval 最终 C-RAG：
sufficient=false
confidence=0.431
relevant=1
partial=4
irrelevant=0
```

这些证据没有因 `sufficient=false` 被整体删除，而是继续进入 Evidence Governance：

```text
Governance selected=3
dropped=2
delivery_mode=qualified
generation_allowed=true
```

允许能力：

```text
general_method
procurement_requirement
reference_case
style_guidance
template_structure
typical_architecture
```

禁止能力继续包括：

```text
current_project_configuration
current_project_cost
current_project_fact
current_project_quantity
current_project_schedule
```

这证明 `sufficient=false` 已恢复为“检索完整性不足”，而不是“surviving evidence 不可生成”。

---

# 四、Bug 2：Recovery 退化覆盖

## 4.1 修改

文件：

```text
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
```

修复同时覆盖：

- Self-RAG retrieve-more Recovery
- Citation/Section Corrective Retrieval Recovery

替换条件现在是：

```text
recovery context 非空
且 recovery Writer Gate 存在
且 recovery_writer_gate.ordinary_generation_permitted=true
→ 使用 Recovery
```

当初次结果允许普通生成，而 Recovery 为空或 blocked 时：

```text
保留初次 context
保留初次 chunks
保留初次 citations
保留初次 assessment
保留初次 writer gate
不使用 blocked Recovery 重新生成
```

新增诊断元数据：

```text
self_rag_recovery_rejected
corrective_recovery_accepted
corrective_recovery_rejected
corrective_retained_scope
```

没有新增 Recovery 合并器、Selector、Bundle 或评分机制。

## 4.2 回归覆盖

三种边界均已通过：

1. 初次 permitted，Recovery 为空/blocked：保留初次证据和正文。
2. Recovery permitted：替换初次结果并重新生成。
3. 初次 blocked：继续原 blocked 路径，不绕过 Writer Gate。

完整真实 E2E 在进入 Section Coordinator 的 Citation Corrective Recovery 之前，被新的 Context Budget Bug 阻断，因此本项的真实退化场景本轮未自然触发；生产逻辑由确定性回归测试覆盖。

---

# 五、Bug 3：Citation Catalog 安全投影旁路

## 5.1 修改

文件：

```text
backend/rag/evidence/citation_sources.py
backend/rag/mapping/evidence_mapper.py
backend/apps/enterprise_document/services/scheme_writer/prompt_service.py
backend/context_manager/policies.py
backend/tools/fake_rag_tool.py
```

新增内部辅助函数：

```python
prompt_visible_citation_text(citation)
```

行为：

```text
优先读取 citation.extra.prompt_visible_text
或 prompt_evidence_candidate_v1.prompt_visible_text
找不到安全投影时返回空文本
绝不回退到 CitationSchema.quote_text
```

原始 `CitationSchema.quote_text` 仍保留，用于：

- Citation Binding
- Grounding 验证
- Claim Audit
- 原始证据追踪
- 人工审计

Writer 可见路径统一为：

```text
Evidence Block → prompt_visible_text
正常 Citation Catalog → prompt_visible_text
Citation Repair Catalog → prompt_visible_text
Grounded Regeneration Catalog → prompt_visible_text
紧凑 Context Catalog → prompt_visible_text
```

## 5.2 真实 Prompt 泄漏探针

报告：

```text
E:\rag-agent-runtime\gov_cloud_gate_three_bug_prompt_probe_20260805.json
```

### 存储资源建设方案

原始 Citation 内部文本命中：

```text
324.2TB
```

Writer 可见 Evidence Block + 两种 Citation Catalog 命中：

```text
[]
```

结果：

```text
writer_catalog_uses_safe_projection=true
writer_visible_token_hits=[]
```

### 安全设计

原始 Citation 内部文本命中：

```text
HTTPS
AES
手机号验证码
指纹识别
面部识别
```

Writer 可见 Evidence Block + 两种 Citation Catalog 命中：

```text
[]
```

结果：

```text
writer_catalog_uses_safe_projection=true
writer_visible_token_hits=[]
```

这证明原始引用仍可用于内部审计，但不能再通过 Citation Catalog 绕过 Evidence Governance 投影进入 Writer Prompt。

---

# 六、四章节真实探针

使用显式 ProjectInput，`allow_demo_defaults=false`，授权知识库：

```text
historical_case_kb
policy_standard_kb
project_fact_kb
technical_reference_kb
template_style_kb
```

| 章节 | 初始/最终选择 | Corrective Retrieval | Delivery Mode | Writer 可生成 | Prompt 泄漏 |
|---|---:|---:|---|---|---|
| 建设目标 | 5 / 5 | 1 | qualified | 是 | 0 |
| 总体架构 | 5 / 5 | 0 | qualified | 是 | 0 |
| 存储资源建设方案 | 5 / 5 | 1 | qualified | 是 | 原始命中 `324.2TB`，Writer 命中 0 |
| 安全设计 | 5 / 5 | 0 | qualified | 是 | 原始命中 5 项具体技术枚举，Writer 命中 0 |

所有章节均保持：

```text
current_project_configuration/cost/fact/quantity/schedule 被禁止
历史和参考证据只获得 reference/general/typical 等能力
requested_claim_types_ignored=true
claim_resolution_mode=candidate_capability_union
```

---

# 七、完整真实 E2E

## 7.1 运行信息

```text
run_id=gov_cloud_gate_three_bug_full_e2e_20260805_2300
runtime_root=E:\rag-agent-runtime\gov_cloud_gate_three_bug_full_e2e
report=E:\rag-agent-runtime\gov_cloud_gate_three_bug_full_e2e_report_20260805.json
trace=E:\rag-agent-runtime\gov_cloud_gate_three_bug_full_e2e\runs\gov_cloud_gate_three_bug_full_e2e_20260805_2300_trace.jsonl
```

正式入口使用：

```text
--project-input-file
--rag-project-root
--authorized-kb-id
allow_demo_defaults=false
```

八个章节由显式 ProjectInput 进入 Planner，DocumentPlan 数量和顺序均正确。

## 7.2 已执行链路

E2E 已真实执行：

```text
ProjectInput
DocumentPlan
Document-level Retrieval
Section Retrieval
C-RAG
Corrective Retrieval
Evidence Governance
Writer Gate
Section Generation
Citation Binding
Citation Repair
```

第一章初始 Writer Prompt：

```text
used_context_chars=4060
max_context_chars=6000
```

Citation Repair Prompt：

```text
used_context_chars=4063
max_context_chars=6000
```

## 7.3 最终状态

```text
status=failed
formal_release=false
```

失败原因：

```text
SCHEME_GENERATION_STAGE_FAILED
ValidationError: rendered context exceeds max_context_chars
failed_node=execute_sections
```

失败发生在：

```text
项目背景
→ Citation Repair 未形成受支持绑定
→ 准备 Grounded Regeneration
→ LLMContextPackageSchema 在模型调用前拒绝超预算 Prompt
```

因此：

- Claim Audit、Document Hard Gate、Release Gate 没有在本次真实 E2E 中走到最终文档阶段。
- 本次没有正式发布文档。
- 不能声称 `unsupported_current_project_facts=0` 已由完整最终正文验证。

---

# 八、新发现的独立 Bug

## Grounded Regeneration Context Budget 溢出

文件位置：

```text
backend/apps/enterprise_document/services/scheme_writer/grounding_repair_service.py
```

当前 Prompt 同时拼接：

```text
完整 ProjectInput JSON
完整治理后 Citation Catalog
未通过校验的原始正文
修复指令
```

随后整体进入仅 6000 字符的 `LLMContextPackageSchema`，但该路径没有在 Prompt 构造前做预算化裁剪，导致模型调用前 ValidationError。

本轮没有直接修复，原因：

- 它不属于三项冻结门禁 Bug。
- 修复需要明确 Grounded Regeneration 对 ProjectInput、原文和 Catalog 的预算优先级。
- 直接扩大 `max_context_chars` 或绕过 Context Gate 会破坏现有 fail-closed 约束。

建议作为独立最小任务处理：

```text
Grounded Regeneration Prompt 预算化投影
```

不得通过放宽 Context Gate 处理。

---

# 九、测试明细

## 9.1 红测试

首轮新增测试结果：

```text
4 failed, 1 passed
```

失败分别对应：

- C-RAG Prompt 职责混淆
- blocked/empty Recovery 覆盖
- 正常 Citation Catalog 原文旁路
- 紧凑/修复 Citation Catalog 原文旁路

随后扩展到 8 个边界测试并全部转绿。

## 9.2 专项

```text
本轮三项回归：8 passed
C-RAG / Context / Prompt / Repair / Citation：50 passed
Stage D/E/F：161 passed
最终合并专项：205 passed
```

## 9.3 全量

由于 DevSpace 长任务通道多次 502，且 C 盘临时目录曾只剩约 339 MB，全量测试按排序后的 129 个测试文件分片执行。

最终：

```text
1483 passed
2 skipped
0 failed
```

跳过项：

- Microsoft Word COM 条件测试
- 真实 Provider 凭证条件测试

临时目录切换到 D 盘后，因 LangGraph SQLite 句柄与 pytest `--basetemp` 清理冲突，慢测试进一步改为单文件独立进程执行；所有文件最终均有明确通过结果。

---

# 十、附加验收入口

为在不修改原活动索引的情况下处理 Windows MilvusLite 锁冲突，本轮增加两个验收脚本：

```text
scripts/prepare_isolated_active_index.py
scripts/run_gov_cloud_pre_generation_probe.py
```

前者只复制现有索引并生成隔离 Manifest/Pointer；不会修改原索引或原 active pointer。后者支持显式 `--rag-project-root` 并输出 Prompt 泄漏扫描结果。

`scripts/run_demo.py` 同步支持：

```text
--rag-project-root
--authorized-kb-id
```

当提供 `--project-input-file` 且没有显式 `--user-input` 时，CLI 直接使用文件内规范 `user_query`，避免终端编码或转录导致 ProjectInput 一致性失败。

---

# 十一、范围核验

本轮未新增：

- DeliveryMode
- C-RAG 状态
- Citation 字段
- Schema 版本
- Evidence Bundle
- Recovery 合并器
- 门禁服务
- Agent
- Memory
- Research Loop
- 新 LLM 调用

未修改：

- 索引构建逻辑
- Reranker
- Claim Audit 规则
- Document Hard Gate 规则
- Release Gate 规则

本轮未提交代码。
