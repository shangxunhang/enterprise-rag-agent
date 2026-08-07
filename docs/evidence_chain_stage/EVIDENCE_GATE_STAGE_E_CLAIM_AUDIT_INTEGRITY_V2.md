# Stage E.1 Claim Audit Integrity & Fail-Closed Closure V2

## 1. 最终结论

Stage E.1 已完成以下完整性闭环：

```text
Final Gate
→ WriterGateBinding V2
→ Final Section Content
→ Independent Multi-label Claim Classification
→ Substantive Sentence Fail-Closed Detection
→ Claim Audit Integrity Hash
→ Current Artifact Revalidation
→ Document Hard Gate Recomputed Eligibility
→ JUnit-backed Acceptance Evidence
```

最终测试结果：

```text
Focused JUnit     54 tests / 0 failures / 0 errors / 0 skipped
Regression JUnit 470 tests / 0 failures / 0 errors / 0 skipped
Total            524 tests / 0 failures / 0 errors / 0 skipped
```

Runtime Acceptance V2：

```text
passed = true
hard_failure_count = 0
```

Stage F 尚未开始。

---

## 2. 本次修复范围

本次只修复：

```text
Writer Claim Audit
Writer Artifact Integrity
Independent Claim Classification
Substantive Sentence Fail-Closed Detection
Document Hard Gate Recalculation
Stage E Runtime Acceptance V2
JUnit-backed Final Acceptance V2
Architecture Regression
```

未修改：

```text
Stage D Gate 语义
data/catalog/document_profiles_v1.jsonl
Stage C Parent/Child/Vector/BM25 Artifact
Milvus Collection
active_index.json
```

未重建索引，未删除历史索引，未执行 Git commit。

---

## 3. 修改文件清单

### 3.1 生产代码

```text
backend/apps/enterprise_document/schemas/writer_governance.py
backend/apps/enterprise_document/schemas/__init__.py
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
backend/apps/enterprise_document/services/writer_artifact_integrity.py
backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/apps/enterprise_document/services/document_gate.py
backend/apps/enterprise_document/services/scheme_writer/__init__.py
```

### 3.2 测试

```text
backend/tests/stage_e_integrity_test_fixtures.py
backend/tests/test_stage_e_claim_audit_integrity.py
backend/tests/test_stage_e_claim_classifier_fail_closed.py
backend/tests/test_stage_e_cross_section_reuse.py
backend/tests/test_stage_e_document_gate_recomputation.py
backend/tests/test_stage_e_acceptance_evidence.py
backend/tests/test_stage_e_writer_governance.py
backend/tests/test_stage_e_document_hard_gate.py
backend/tests/test_scheme_writer_decomposition.py
backend/tests/test_p1_trust_semantic_payload_closure.py
```

### 3.3 Acceptance 与报告脚本

```text
scripts/run_stage_e_writer_acceptance_v2.py
scripts/finalize_stage_e_acceptance_v2.py
scripts/merge_junit_reports.py
scripts/run_stage_e_writer_acceptance.py
scripts/finalize_stage_e_acceptance_v1.py
```

旧 V1 文件名仅作为明确兼容入口，直接进入 V2 实现；人工传入测试数量的 CLI 已删除。

---

## 4. Claim Audit Integrity Schema

新增：

```text
ClaimAuditIntegritySchema
```

结构：

```yaml
schema_version: claim_audit_integrity_v1
section_id:
section_title:
content_sha256:
normalized_content_sha256:
writer_gate_binding_sha256:
evidence_contract_sha256:
prompt_evidence_contract_sha256:
classifier_id:
classifier_version:
classifier_policy_sha256:
substantive_detector_version:
```

所有 Hash 字段使用严格的小写 SHA-256 十六进制格式：

```text
^[0-9a-f]{64}$
```

`ClaimAuditResultSchema` 已升级为：

```text
claim_audit_result_v2
```

同时记录：

```text
integrity
audit_created_at
human_review_required
generated_claims
violations
allowed_claim_types
forbidden_claim_types
required_qualifiers
applied_qualifiers
checks
```

`audit_created_at` 必须包含时区。

---

## 5. Content Hash 计算时机

Claim Audit 在以下所有正文变更完成后运行：

```text
模型生成
截断恢复
压缩恢复
Validation Rewrite
Citation Repair
Grounded Regeneration
Citation Marker Reconcile
Required Qualifier Injection
最终语义复核
高风险项目事实复核
```

执行顺序：

```text
final content
→ content hash
→ independent classification
→ claim audit
→ write SchemeSection.content and ClaimAudit together
```

Audit 后没有代码再修改 `content`。

Document Hard Gate 对持久化后的当前 `section.content` 重新计算 Hash，因此即使 Audit 后通过外部路径修改正文，也会被识别为 stale Audit。

---

## 6. Content Hash 规范化规则

### 6.1 原始正文 Hash

```text
content_sha256
```

计算规则：

```text
SHA256(exact section.content UTF-8 bytes)
```

不做任何规范化。

该 Hash 是正式完整性判断的事实源。

### 6.2 规范化诊断 Hash

```text
normalized_content_sha256
```

只执行：

```text
CRLF / CR → LF
Unicode NFC
移除每一行的行尾空白
```

不执行：

```text
删除标点
删除正文空格
删除 Citation
大小写归一化
数字重写
中文数字转换
```

Document Hard Gate 同时核验两个 Hash，但原始 `content_sha256` 必须匹配。

专项测试证明：

```text
CRLF 和行尾空白变化：原始 Hash 不同
稳定规范化后：normalized Hash 相同
```

---

## 7. Audit 与 Binding、Section、Evidence 的绑定

### 7.1 WriterGateBinding V2

新增身份字段：

```text
section_order
document_id
run_id
```

生产 Coordinator 和 Production Section Graph 均写入：

```text
section_id
section_title
section_order
document_id
run_id
```

### 7.2 当前章节身份验证

Document Hard Gate 要求：

```text
binding.section_id == section.section_id
binding.section_title == section.section_title
binding.section_order == section.section_order
binding.document_id == draft.document_id
binding.run_id == draft.run_id

audit.section_id == section.section_id
audit.integrity.section_id == section.section_id
audit.integrity.section_title == section.section_title

every generated_claim.section_id == section.section_id
```

身份不一致时产生：

```text
writer_binding_identity_mismatch
writer_audit_identity_mismatch
```

### 7.3 Writer Binding Hash

```text
writer_gate_binding_sha256
= canonical SHA256(current WriterGateBinding V2 JSON)
```

Document Hard Gate 对当前 Binding 重新计算，不能使用历史 Hash。

### 7.4 Evidence 血缘

必须满足：

```text
audit.integrity.evidence_contract_sha256
== binding.evidence_contract_sha256

audit.integrity.prompt_evidence_contract_sha256
== binding.prompt_evidence_contract_sha256
```

任一不一致：

```text
writer_audit_evidence_lineage_mismatch
```

### 7.5 Claim Boundary

必须满足：

```text
audit.execution_status == binding.execution_status
audit.allowed_claim_types == gate.allowed_claim_types
audit.forbidden_claim_types == gate.forbidden_claim_types
audit.required_qualifiers == gate.required_qualifiers
```

Schema 先保证：

```text
无重复
allowed 与 forbidden 无交集
qualifier 无重复
```

边界不一致：

```text
writer_audit_claim_boundary_mismatch
```

---

## 8. Generated Claim Span 可复核

每条 Generated Claim 现在记录：

```text
section_id
sentence_index
claim_type
text
span_start
span_end
classifier_id
classifier_version
confidence
```

Document Hard Gate 要求：

```text
0 <= span_start < span_end <= len(section.content)
```

并重新读取：

```text
section.content[span_start:span_end]
```

唯一允许的差异是使用冻结规则移除 Citation Marker：

```text
[A-Za-z0-9_.:-]
```

清理后必须与 `claim.text` 完全一致。

修改任一：

```text
span_start
span_end
claim.text
claim.section_id
```

都会被拒绝。

---

## 9. Claim 分类彻底移除 Allowed Set 偏置

分类接口已冻结为：

```python
classify_sentence(
    sentence: str,
    *,
    governance_qualifiers: Iterable[str] = (),
) -> ClaimClassificationResult
```

分类输入中不存在：

```text
allowed_claim_types
forbidden_claim_types
delivery_mode
```

正确顺序：

```text
Sentence
→ Independent Multi-label Classification
→ Actual Claim Types
→ Compare with Gate allowed / forbidden sets
```

不再存在：

```text
无法识别类型
→ 从 allowed_claim_types 中选择一个类型
```

专项测试使用同一句：

```text
遵循《数据安全法》开展建设。
```

分别配置：

```text
allowed = general_method
allowed = policy_requirement
allowed = template_structure
```

三次分类结果完全相同：

```text
policy_requirement
```

---

## 10. 多标签规则

同一句可以产生多个类型。

分类器不会因为已经命中一个类型而停止。

例如数量和配置可以同时命中：

```text
本项目部署两百台服务器。
```

结果至少包含：

```text
current_project_quantity
current_project_configuration
```

Audit 对任一未获允许或禁止类型都会失败。

---

## 11. Substantive Sentence Detector

版本：

```text
1.0.0
```

以下内容可判为非实质性：

```text
Markdown 标题
纯章节名称
纯目录编号
纯 Citation Marker
待补充/TODO/TBD/N/A 等占位符
证据限定标题及限定语本身
明确的 blocked/human-review 诊断拒绝文本
```

以下内容判为实质性：

```text
命中金额、数量、时间、配置、政策、技术、采购等规则
含应、须、必须、采用、建设、部署、计划、符合、遵循等谓词
含完整中文陈述且长度达到保守阈值
```

Detector 采用保守策略：

```text
看起来是完整业务陈述
但无法确认它是纯格式文本
→ substantive = true
```

---

## 12. 未识别 Claim 的 Fail-Closed 行为

测试输入：

```text
平台具备持续演进能力。
```

该句：

```text
substantive = true
claim_types = []
```

结果：

```text
violation_code = unclassified_substantive_claim
audit.passed = false
human_review_required = true
formal_deliverable_eligible = false
```

未识别完整陈述不会再被静默忽略。

当前确定性分类器：

```text
规则命中 confidence = 1.0
实质性但未命中 confidence = 0.0 and fail closed
```

---

## 13. Classifier Identity 与策略冻结

```text
classifier_id:
deterministic_stage_e_claim_classifier

classifier_version:
2.0.0

substantive_detector_version:
1.0.0

classifier_policy_sha256:
56fa3e586aa71b25a651b406514b235346d89eeae1305e2f95703765da1c7279
```

Policy Hash 来源包括：

```text
所有关键词
所有正则表达式
多标签规则
Substantive 判定规则
未识别 fail-closed 规则
confidence threshold
分类不得依赖 Gate 的声明
```

Document Hard Gate 通过 Registry 验证：

```text
classifier_id
classifier_version
classifier_policy_sha256
```

未知版本或 Hash 不一致：

```text
writer_classifier_policy_mismatch
```

---

## 14. 中文自然语言分类测试结果

在 Gate 只允许 `general_method` 时，以下全部被识别并拒绝：

| 输入 | 检出类型 | Audit |
|---|---|---|
| 遵循《数据安全法》开展建设。 | policy_requirement | failed |
| 平台总投入二百五十万。 | current_project_cost | failed |
| 人民币二百五十万元用于平台建设。 | current_project_cost | failed |
| 项目投资为贰佰伍拾万元。 | current_project_cost | failed |
| 系统应符合 GB/T 22239。 | technical_specification | failed |
| 平台将在第三季度上线。 | current_project_schedule | failed |
| 系统应在三个月内完成交付。 | current_project_schedule | failed |
| 本项目部署两百台服务器。 | current_project_quantity | failed |
| 平台配置三十套安全设备。 | current_project_quantity | failed |
| 采购采用公开招标方式。 | procurement_requirement | failed |

Runtime V2 中对应 Hard Failure 全部为 `false`。

---

## 15. Audit 后正文篡改测试

流程：

```text
采用分阶段实施方法，形成闭环治理流程。
→ Audit passed
→ 替换为：遵循《数据安全法》开展建设。
→ 保留旧 Audit
→ Document Hard Gate
```

结果：

```text
writer_audit_content_hash_valid = false
writer_claim_audit_recomputed_valid = false
writer_claim_audit_stale
Document Hard Gate passed = false
```

普通字符追加同样被拒绝。

---

## 16. Qualified 限定语删除测试

流程：

```text
注入：根据现有估算材料
注入：最终金额以审批或采购结果为准
→ Audit passed
→ 删除第二条限定语
→ Document Hard Gate
```

结果：

```text
writer_audit_content_hash_valid = false
writer_required_qualifiers_applied = false
writer_formal_deliverable_eligible = false
Document Hard Gate passed = false
```

`applied_qualifiers` 由最终正文重新计算，不信任调用者传入值。

---

## 17. 跨章节复用测试

### 17.1 Binding 复用

```text
Binding.section_id = section_A
实际挂到 section_B
```

结果：

```text
writer_binding_section_identity_valid = false
writer_binding_identity_mismatch
```

### 17.2 Audit 复用

```text
Audit.section_id = section_A
实际挂到 section_B
```

结果：

```text
writer_audit_section_identity_valid = false
writer_audit_identity_mismatch
```

### 17.3 Generated Claim 复用

修改：

```text
generated_claim.section_id = section_other
```

结果：

```text
writer_audit_identity_mismatch
Document Hard Gate passed = false
```

---

## 18. Document Hard Gate 重新计算

Document Hard Gate 不再直接信任：

```text
section.extra.formal_deliverable_eligible
claim_audit.passed
```

它重新计算：

```text
Binding Schema valid
AND Binding identity valid
AND generation_allowed
AND delivery mode not blocked/human_review
AND no pending human review
AND Audit Schema valid
AND Audit identity valid
AND exact content Hash valid
AND normalized diagnostic Hash valid
AND Binding Hash valid
AND Evidence lineage valid
AND Claim boundary valid
AND Generated Claim spans valid
AND Classifier Policy valid
AND deterministic re-audit equals stored Audit
AND stored Audit passed
AND required qualifiers currently visible and recorded
AND section status deliverable
= recomputed_formal_deliverable_eligible
```

新增检查：

```text
writer_binding_section_identity_valid
writer_audit_section_identity_valid
writer_audit_content_hash_valid
writer_audit_binding_hash_valid
writer_audit_evidence_lineage_valid
writer_audit_claim_boundary_valid
writer_audit_claim_spans_valid
writer_classifier_policy_valid
writer_claim_audit_recomputed_valid
writer_formal_eligibility_recomputed
```

---

## 19. formal_deliverable_eligible 不再是事实源

### 19.1 伪造 true

流程：

```text
正文已篡改
旧 Audit 仍在
手工 formal_deliverable_eligible = true
```

结果：

```text
upstream_formal_deliverable_eligible = true
recomputed_formal_deliverable_eligible = false
Document Hard Gate passed = false
```

### 19.2 伪造 false

流程：

```text
当前 Artifact 完整且可交付
手工 formal_deliverable_eligible = false
```

结果：

```text
writer_formal_eligibility_mismatch
Document Hard Gate passed = false
```

上游字段保留用于一致性审计，但不能决定最终事实。

### 19.3 伪造 claim_audit.passed

删除 Generated Claims 并手工保持：

```text
claim_audit.passed = true
```

Document Hard Gate 会对当前正文重新执行确定性 Audit。

结果：

```text
writer_claim_audit_recomputed_valid = false
writer_claim_audit_recomputation_mismatch
```

---

## 20. 隐藏动态委托层删除

文件：

```text
backend/apps/enterprise_document/services/scheme_writer/__init__.py
```

当前只包含包说明与空 `__all__`。

不存在：

```text
__getattr__
动态代理
隐藏转发
魔术导出
```

所有调用方从具体模块导入：

```text
apps.enterprise_document.services.scheme_writer.use_case
```

测试：

```text
test_hidden_runtime_delegation_layer_is_deleted
```

结果：

```text
passed
```

---

## 21. 生产 Writer Gate 强制性

现有生产上层主线继续保持：

```text
Gate 缺失
→ 不调用模型
→ diagnostic-only / hard failure
```

所有生产 `generate_section()` 调用仍显式传入：

```text
writer_gate_binding
```

Coordinator 和 Production Section Graph 在：

```text
Document Gate inheritance
Section Retrieval
Research Tool Retrieval
Self-RAG Retrieve More
Corrective Retrieval
Recovery
```

之后都会刷新并携带当前章节身份。

Stage D Gate 语义未修改。

---

## 22. Runtime Acceptance V2

路径：

```text
E:\rag-agent-runtime\stage_e_final_runtime_acceptance_v2.json
```

SHA-256：

```text
35b877226aa483c6c0a6a1052d5e73abf413208bcd9deea03714fae9534eccd8
```

Capture：

```text
E:\rag-agent-runtime\stage_e_final_runtime_capture_v2.jsonl
```

场景包括：

```text
Grounded Method 正常交付
Audit 后政策正文篡改
直接政策越权声明
中文金额
技术标准编号
自然语言工期
中文数量
采购要求
未分类实质性声明
跨章节 Binding 复用
跨章节 Audit 复用
Qualified 限定语删除
Active Unknown/Blocked
Active Pointer/Manifest 不变
```

---

## 23. Runtime Hard Failures

以下全部为 `false`：

```text
stale_claim_audit_content_mutation_not_rejected
qualified_content_qualifier_removal_not_rejected
cross_section_writer_binding_reuse_not_rejected
cross_section_claim_audit_reuse_not_rejected
writer_gate_binding_hash_mismatch_not_rejected
evidence_contract_hash_mismatch_not_rejected
prompt_evidence_contract_hash_mismatch_not_rejected
claim_audit_allowed_boundary_mismatch_not_rejected
claim_audit_forbidden_boundary_mismatch_not_rejected
claim_audit_qualifier_boundary_mismatch_not_rejected
generated_claim_span_mismatch_not_rejected
generated_claim_section_mismatch_not_rejected
unclassified_substantive_claim_not_blocked
allowed_set_biased_classification_detected
policy_synonym_claim_bypassed
technical_standard_claim_bypassed
chinese_money_claim_bypassed
natural_language_schedule_claim_bypassed
chinese_quantity_claim_bypassed
procurement_claim_bypassed
formal_deliverable_boolean_trusted_without_recompute
hidden_runtime_delegation_layer_present
active_unknown_blocked_not_rejected
grounded_method_not_deliverable
active_pointer_or_manifest_changed
```

---

## 24. JUnit Acceptance Evidence

### 24.1 Focused

路径：

```text
E:\rag-agent-runtime\stage_e_focused_junit_v2.xml
```

SHA-256：

```text
c9bcdc20ee58c3b3b94acd526e0544db0e0337f686f5215e49d3d23aa0c77b51
```

结果：

```text
54 tests
0 failures
0 errors
0 skipped
```

覆盖：

```text
Writer Governance
Document Hard Gate
Claim Audit Integrity
Fail-Closed Classifier
Cross-section Reuse
Document Gate Recomputation
Acceptance Evidence
Hidden Runtime Delegation
```

### 24.2 Regression

路径：

```text
E:\rag-agent-runtime\stage_e_regression_junit_v2.xml
```

SHA-256：

```text
b994b8f28fb163ffa79d85c97e158310b9b90cb38c0112a2eee8fb65880c27b4
```

结果：

```text
470 tests
0 failures
0 errors
0 skipped
```

组成：

```text
Stage A-D / RAG / Section / Document Core 345
Agent-RAG V1                                80
Top-level / Section / Recovery Mainline     45
```

独立 pytest 进程的 JUnit 使用：

```text
scripts/merge_junit_reports.py
```

合并，并保留每个 source_junit 路径。

---

## 25. Acceptance Finalizer V2

路径：

```text
scripts/finalize_stage_e_acceptance_v2.py
```

输入：

```text
--runtime-probe
--focused-junit
--regression-junit
```

不再接受：

```text
--focused-tests-passed
--regression-tests-passed
--focused-tests-duration
--regression-duration
```

Finalizer 从 JUnit 解析：

```text
tests
failures
errors
skipped
time
testcase IDs
test files
source JUnit files
```

并记录：

```text
JUnit path
JUnit SHA-256
pytest command
Python executable/version
Git HEAD
Git dirty status
Git status SHA-256
Git diff SHA-256
Active Index hashes
```

若任一 JUnit：

```text
failures > 0
errors > 0
tests == 0
```

Finalizer 拒绝生成 `passed=true`。

---

## 26. Final Acceptance Report V2

仓库：

```text
data/processed/indexes/stage_e_final_acceptance_report_v2.json
```

Runtime 副本：

```text
E:\rag-agent-runtime\stage_e_final_acceptance_report_v2.json
```

两份内容必须字节一致。

最终 SHA-256：

```text
a48d2720fb3f941f1c04fe5544fb1eedd002148907e09df75e44b2c4c7ba3558
```

---

## 27. Python 与工作区证据

Python：

```text
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe
3.10.20
```

Git HEAD：

```text
2bc46b02296b7bf060a9c434453366b41a843271
```

工作区：

```text
dirty = true
```

这是预期状态；仓库原本存在大量未提交改动，本次未清理、重置、覆盖或提交其他人的修改。

---

## 28. compileall 与 git diff check

执行：

```text
python -m compileall -q backend scripts
git diff --check
```

结果：

```text
compileall passed
git diff --check passed
```

Git 仅输出既有 CRLF/LF 转换提示，没有空白错误，命令退出码为 0。

---

## 29. Active Index 完整性

Index Version：

```text
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e
```

Active Pointer SHA-256：

```text
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce
```

Manifest SHA-256：

```text
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
```

Runtime 前后全部一致。

---

## 30. 遗留问题

当前无阻止 Stage E 完成的正确性遗留问题。

非阻断事项：

```text
TRANSFORMERS_CACHE 弃用警告
SWIG 类型弃用警告
本地 PyTorch 未启用 Flash Attention 的性能提示
```

后续可选增强，不属于 Stage E.1：

```text
模型辅助 Claim Classifier
人工复核处理 UI
Classifier Policy Registry 外部配置
更多行业中文表达词表
```

这些事项不影响当前 fail-closed 完整性保证。

---

## 31. Stage E 最终完成定义

当前系统已能证明：

> 准备正式发布的每一个字符，正是 Claim Audit 当时审计过的内容；该 Audit 属于当前章节、当前 Writer Gate 和当前 Evidence Contract；所有实质性声明均由不依赖 Gate 权限的分类器识别，无法分类的声明被 fail-closed 阻断；正式交付资格由 Document Hard Gate 基于当前 Artifact 重新计算，而不是沿用历史布尔字段。

Stage E.1 已完成。

Stage F 尚未开始。
