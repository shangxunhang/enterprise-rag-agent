# 1.5B Writer Correctness E2E 结果记录（2026-08-07）

## 1. 目的

本轮目标不是提升生成质量，而是在不放宽任何 Gate、不修改 timeout、不关闭 RAG/Claim Audit/Citation/Document Hard Gate 的前提下，将章节生成与修复模型从本地 Qwen2.5-3B 临时切换为 Qwen2.5-1.5B，消除 16GB 单卡环境的 3B CPU offload 性能干扰，并验证真实政务云 8 章节主链是否能够完整执行到文档级门禁与产物持久化。

记录时间：2026-08-07（Asia/Singapore / UTC+08:00）

## 2. 仓库状态

- Project: `D:\MyCode\rag-agent`
- Branch: `checkpoint/controlled-architecture-20260728`
- HEAD: `c04e98e80186d2e79810b9ebcd69a990475fbb91`
- 当前工作区为历史大 dirty tree；本轮不执行 reset/clean/checkout/branch switch，不提交代码。
- 本轮结束核验时 `git status --porcelain=v1 | wc -l = 235`。

本轮目标修改：

1. `backend/bootstrap/model_factory.py`
   - `ModelRole.SECTION_GENERATION` 候选顺序调整为 `local_1_5b -> local_3b -> deepseek`。
   - `ModelRole.REPAIR` 候选顺序调整为 `local_1_5b -> local_3b -> deepseek`。
   - Retrieval Judge、Query Rewrite、Corrective Planner 等 RAG 路由保持原逻辑。

2. `backend/apps/enterprise_document/services/scheme_writer/model_service.py`
   - 真实 1.5B E2E 暴露 `retry_truncated_section()` 的 passthrough Prompt 超过 `max_context_chars=6000`。
   - 修复只作用于截断重试 Prompt：压缩 ProjectInput、Citation Catalog，并按剩余字符预算裁剪 RAG context。
   - 不修改 Gate、不增大 context budget、不修改 timeout。

3. `backend/tests/stage1/test_generation_recovery.py`
   - 新增 `test_truncated_retry_prompt_stays_within_context_budget`。
   - 使用超大 ProjectInput.extra 与超大 RAG context 验证截断重试 Prompt 不超过 6000 chars。

## 3. 回归测试

执行：

```text
python -m pytest \
  backend/tests/stage1/test_generation_recovery.py \
  backend/tests/test_existing_gate_three_bug_regression.py \
  backend/tests/test_model_gateway_multi_model_runtime.py \
  backend/tests/test_model_gateway_lifecycle.py -q
```

结果：

```text
39 passed in 1.20s
```

同时 `git diff --check` 对本轮目标文件通过。

## 4. 第一次 1.5B E2E：发现截断重试 Context Budget Bug

Run ID：

```text
gov_cloud_1_5b_writer_e2e_20260807_2121
```

Runtime root：

```text
E:\rag-agent-runtime\gov_cloud_1_5b_writer_e2e_20260807_2121
```

固定输入：

```text
backend/tests/fixtures/e2e/gov_cloud_framework_project_input.json
```

Authorized KB scope：

```text
historical_case_kb
policy_standard_kb
project_fact_kb
technical_reference_kb
template_style_kb
```

关键证据：

- `model_usage`: 43 次模型调用全部为 `local_qwen2_5_1_5b`，3B 调用数为 0。
- 初始 `scheme_section_generation / scheme_citation_repair / scheme_grounded_regeneration` 均约 3.4~3.6 秒。
- Section 1 corrective generation 达到 `finish_reason=length` 后进入截断恢复。
- `retry_truncated_section()` 构造完整 ProjectInput + evidence context + Citation Catalog，触发：

```text
LLMContextPackageSchema
Value error, rendered context exceeds max_context_chars
```

- `execute_sections` 约 72.2 秒即因 `SCHEME_GENERATION_STAGE_FAILED` 结束；不再是原 1000 秒 timeout。

该运行证明：1.5B 已消除 3B CPU offload 的主要性能干扰，同时暴露独立 correctness bug。

## 5. 截断重试 Context Budget 修复

修复原则与此前 Grounded Regeneration budget 修复保持一致：

- 不提高 `max_context_chars=6000`；
- 不直接塞完整 ProjectInput；
- 仅保留当前章节恢复需要的项目事实；
- Citation Catalog 使用 prompt-visible text，并限制单条与总目录体积；
- Prompt 固定部分构造后，将剩余 budget 分配给 reduced RAG context；
- 保持 retry max output token、Gate 和恢复职责不变。

修复后 targeted regression：39 passed。

## 6. 第二次真实 1.5B E2E

Run ID：

```text
gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130
```

Runtime root：

```text
E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130
```

关键文件：

```text
E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130\e2e_report.json
E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130\console.stdout.log
E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130\console.stderr.log
E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130\runs\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130_trace.jsonl
E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130\runs\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130_model_usage.json
E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130\runs\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130_rejected_draft.md
```

### 6.1 模型路由

最终 `model_usage`：

```text
logical_calls = 163
provider_attempts = 163
local_qwen2_5_1_5b = 163
local_qwen2_5_3b = 0
prompt_tokens = 157314
completion_tokens = 19271
total_tokens = 176585
model_latency_ms = 378940
failures = 0
```

本轮所有真实模型调用均命中 1.5B。

### 6.2 截断重试修复真实验收

Section 1 corrective generation：

```text
scheme_section_generation
finish_reason=length
latency≈18.479s
```

随后：

```text
scheme_section_retry
finish_reason=stop
latency≈2.579s
```

未再触发 `rendered context exceeds max_context_chars`，并成功继续进入 Section 2。

### 6.3 8 章节执行完整性

8/8 章节均实际开始并结束：

| 章节 | 最终章节状态 | chars | citations | evidence_scope |
|---|---|---:|---:|---|
| 项目背景 | failed | 263 | 1 | recovery |
| 建设目标 | failed | 1105 | 2 | section |
| 总体架构 | failed | 906 | 2 | section |
| 计算资源建设方案 | failed | 1471 | 3 | section |
| 存储资源建设方案 | failed | 1029 | 3 | section |
| 网络资源建设方案 | failed | 1291 | 2 | section |
| 安全设计 | failed | 871 | 0 | recovery |
| 实施与验收建议 | failed | 1216 | 1 | section |

`execute_sections` Trace：

```text
started_at = 2026-08-07T13:30:07.508235+00:00
finished_at = 2026-08-07T13:41:13.204949+00:00
latency_ms = 665696
status = success
```

因此原先 3B 运行中的 `WORKFLOW_NODE_TIMEOUT`（1000s）在本次 1.5B diagnostic E2E 中不再出现。

### 6.4 文档级主链

以下业务节点均实际执行：

```text
execute_sections                 success
document_verify                  success
repair_document_conflicts        success
incremental_document_reverify    success
assemble_document                success
validate_document_assembly       success
final_artifact_review            success
decide_document_version          success
evaluate_document_gate           failed (DOCUMENT_HARD_GATE_FAILED)
build_generation_report          success
persist_rejected                 success
finalize_workflow_result         failed (business gate result)
```

最终 workflow/run 均自然结束，无 shutdown traceback。

整个 workflow：

```text
latency_ms = 932200
status = failed
error_code = DOCUMENT_HARD_GATE_FAILED
```

## 7. 最终 E2E 判定

`e2e_report.json`：

```text
execution_integrity = passed
business_quality = failed
validation_error = null
business_gate_failure = true
```

Artifact lifecycle：

```text
generated_draft = true
rejected_artifact = true
deliverable_final_result = false
```

这意味着：

> 真实 1.5B + 真实 RAG 的技术全链路已经完整跑到文档级门禁并生成/持久化 rejected artifact；当前失败属于业务 Gate 正确性，不再是 timeout、GPU offload 或 Context Package 技术中断。

## 8. Document Hard Gate 当前阻断

最终 `DOCUMENT_HARD_GATE_FAILED`：

```text
必需引用章节缺少有效引用：安全设计
引用指向不存在的证据或章节
文档仍包含待人工复核章节
存在未通过的章节 Claim Audit
存在不可交付状态的章节
存在经 Document Hard Gate 重算后不具备正式交付资格的章节
存在失败章节
```

Hard Gate checks 中已经通过：

```text
required_sections_present = true
not_truncated = true
tool_success = true
output_schema_valid = true
citations_present = true
citation_referential_integrity = true
citation_marker_binding_bijection = true
citation_marker_occurrences_verified = true
citation_orphan_markers_absent = true
citation_orphan_bindings_absent = true
citation_marker_binding_consistent = true
citation_grounding_verified = true
high_risk_claims_grounded = true
writer_gate_bound = true
writer_audit_schema/identity/hash/lineage checks = true
writer_required_qualifiers_applied = true
evidence_boundary_respected = true
workflow_complete = true
document_verification_passed = true
conflict_repairs_resolved = true
assembly_validation_passed = true
final_artifact_review_passed = true
document_version_decided = true
```

仍失败的关键 checks：

```text
citation_required_sections_bound = false
citation_targets_valid = false
writer_human_review_clear = false
writer_claim_audits_passed = false
writer_section_status_deliverable = false
writer_formal_deliverable_eligible = false
section_status_valid = false
```

Metadata：

```text
citation_binding_count = 14
citation_marker_occurrence_count = 14
verified_binding_occurrence_count = 14
missing_citation_sections = ["安全设计"]
```

所有 8 个章节均出现在：

```text
writer_human_review_section_ids
writer_claim_audit_failed_section_ids
writer_formal_ineligible_section_ids
```

因此下一轮 correctness 主问题不是 1.5B 性能，而是 Claim Audit / Writer human-review / Citation target 与 section deliverability。

## 9. 当前冻结结论

1. 3B CPU offload/本地 16GB 显存是性能问题，暂不在 correctness 阶段继续治理。
2. 1.5B Writer/Repair 路由已经证明可让 8 章节进入完整主链，`execute_sections=665.7s success`。
3. 截断重试 context-budget Bug 已修复并通过真实 E2E + 39 targeted tests。
4. 不降低任何 Gate 的情况下，主链已完整到 Document Hard Gate 和 rejected artifact persistence。
5. 当前全链路尚未“正式成功交付”：Document Hard Gate 未通过，`deliverable_final_result=false`。
6. 下一轮应按 claim/citation 证据逐章节定位，优先：
   - `安全设计` 0 citation / citation_bound；
   - 全 8 章 Claim Audit failed + human review required；
   - `citation_targets_valid=false` 的具体 binding/section mismatch；
   - 为什么建设目标/实施与验收建议局部 evaluator 已 partial_success，但最终仍被 Writer/Claim Audit 判为 failed。
7. 在这些 correctness 问题闭环前，不修改 timeout、不放宽 Hard Gate、不恢复新的 Multi-Agent/Memory/Research Loop 开发。
