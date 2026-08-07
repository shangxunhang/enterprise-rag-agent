# Stage E.2 Mandatory Identity & Evidence Lineage Closure V3

## 1. 最终结论

Stage E.2 已关闭 Writer Artifact 身份和 Evidence 血缘字段可空即通配的问题，同时完整保留 Stage E.1：

```text
最终正文 Content Hash
Claim Audit 重算
Gate-independent Claim Classification
Unclassified Substantive Claim Fail-Closed
Generated Claim Span 校验
Document Hard Gate 正式交付资格重算
JUnit 驱动验收证据
```

Stage E 最终闭环：

```text
Final Content
→ Claim Audit
→ Section Identity
→ Document Identity
→ Run Identity
→ Section Order
→ Writer Gate Hash
→ Evidence Contract Hash
→ Prompt Evidence Contract Hash
→ Document Hard Gate Recompute
→ Formal Delivery Credential
```

Stage F 尚未开始。

## 2. Schema 兼容与正式发布规则

`WriterGateBindingSchema` 继续允许以下字段为 `None`，用于读取历史 Artifact：

```text
section_order
document_id
run_id
evidence_contract_sha256
prompt_evidence_contract_sha256
```

但新增运行属性：

```text
identity_fields_present
evidence_lineage_fields_present
ordinary_generation_permitted
legacy_artifact_incomplete
```

正式 Writer 只有在以下条件全部成立时才可调用模型：

```text
generation_allowed
AND section_order/document_id/run_id 全部存在
AND evidence_contract_sha256 存在
AND prompt_evidence_contract_sha256 存在
```

因此：

```text
兼容读取 != 兼容发布
```

旧 Artifact 可以解析，但进入：

```text
writer_execution_status = not_assessed
need_human_review = true
formal_deliverable_eligible = false
generation_block_reason = legacy_writer_artifact_incomplete
```

## 3. 身份字段存在性与一致性

Document Hard Gate 分开验证：

```text
writer_binding_identity_fields_present
writer_binding_section_identity_valid
```

存在性要求：

```text
binding.section_order is not None
binding.document_id is not None
binding.run_id is not None
```

一致性要求：

```text
binding.section_id == section.section_id
binding.section_title == section.section_title
binding.section_order == section.section_order
binding.document_id == draft.document_id
binding.run_id == draft.run_id
```

正式发布路径不再使用：

```python
field is None or field == expected
```

而是严格使用：

```python
field is not None and field == expected
```

## 4. Evidence 血缘存在性与一致性

新增：

```text
writer_evidence_lineage_fields_present
writer_audit_evidence_lineage_valid
```

存在性要求：

```text
binding.evidence_contract_sha256 非空
binding.prompt_evidence_contract_sha256 非空
audit.integrity.evidence_contract_sha256 非空
audit.integrity.prompt_evidence_contract_sha256 非空
```

一致性要求：

```text
audit.integrity.evidence_contract_sha256
== binding.evidence_contract_sha256

audit.integrity.prompt_evidence_contract_sha256
== binding.prompt_evidence_contract_sha256
```

`None == None` 不再被视为血缘有效。

## 5. Failure Codes

身份缺失：

```text
writer_binding_section_order_missing
writer_binding_document_id_missing
writer_binding_run_id_missing
writer_binding_identity_fields_missing
```

Evidence 血缘缺失：

```text
writer_evidence_contract_hash_missing
writer_prompt_evidence_contract_hash_missing
writer_audit_evidence_contract_hash_missing
writer_audit_prompt_evidence_contract_hash_missing
writer_evidence_lineage_fields_missing
```

历史不完整 Artifact：

```text
legacy_writer_artifact_incomplete
```

字段存在但值不一致：

```text
writer_binding_identity_mismatch
writer_audit_identity_mismatch
writer_audit_evidence_lineage_mismatch
```

## 6. 普通 Writer Fail-Closed

`SectionExecutionCoordinator`、`SectionExecutionGraph` 和 `SectionGenerationService` 均使用：

```text
ordinary_generation_permitted
```

而不是只检查：

```text
generation_allowed
```

以下路径全部覆盖：

```text
Document Gate inheritance
Section Retrieval
ResearchAgent ToolGateway Retrieval
Self-RAG retrieve_more
Corrective Retrieval
Recovery Retrieval
Budget/Recovery Path
```

当身份或血缘不完整时：

```text
模型调用 = 0
诊断章节 = partial_success
Writer Status = not_assessed
人工复核 = true
正式交付 = false
```

没有生成假 Hash，也没有修改 Stage D Gate 语义。

## 7. Document Hard Gate 正式交付重算

正式交付资格加入：

```text
writer_binding_identity_fields_present
writer_binding_section_identity_valid
writer_evidence_lineage_fields_present
writer_audit_evidence_lineage_valid
```

完整重算至少要求：

```text
Gate 存在
Identity Fields 存在且匹配
普通生成被允许
无人工复核
Audit Schema 有效
Audit 属于当前 Section
Content Hash 匹配
Binding Hash 匹配
Evidence Lineage Fields 存在且匹配
Claim Boundary 匹配
Claim Span 匹配
Classifier Policy 有效
当前正文重审计一致
Audit passed
Required Qualifiers 当前可见
Section Status 可交付
```

任意项失败：

```text
recomputed_formal_deliverable_eligible = false
Document Hard Gate passed = false
```

上游 `formal_deliverable_eligible` 仍然只是声明，不是事实源。

## 8. 负向测试结果

### 缺少 Section Order

```text
writer_binding_identity_fields_present = false
writer_binding_section_identity_valid = false
writer_binding_section_order_missing
Document Hard Gate passed = false
```

### 缺少 Document ID

```text
writer_binding_document_id_missing
Document Hard Gate passed = false
```

### 缺少 Run ID

```text
writer_binding_run_id_missing
Document Hard Gate passed = false
```

### 缺少 Evidence Contract Hash

```text
writer_evidence_lineage_fields_present = false
writer_audit_evidence_lineage_valid = false
writer_evidence_contract_hash_missing
writer_audit_evidence_contract_hash_missing
Document Hard Gate passed = false
```

### 缺少 Prompt Evidence Contract Hash

```text
writer_prompt_evidence_contract_hash_missing
writer_audit_prompt_evidence_contract_hash_missing
Document Hard Gate passed = false
```

### 跨 Document 重放

```text
writer_binding_identity_fields_present = true
writer_binding_section_identity_valid = false
writer_binding_identity_mismatch
Document Hard Gate passed = false
```

### 跨 Run 重放

```text
writer_binding_section_identity_valid = false
Document Hard Gate passed = false
```

### 错误 Section Order

```text
writer_binding_section_identity_valid = false
Document Hard Gate passed = false
```

### 完整控制组

```text
writer_binding_identity_fields_present = true
writer_binding_section_identity_valid = true
writer_evidence_lineage_fields_present = true
writer_audit_evidence_lineage_valid = true
writer_formal_deliverable_eligible = true
Document Hard Gate passed = true
```

## 9. Runtime Acceptance V3

文件：

```text
E:\rag-agent-runtime\stage_e_final_runtime_acceptance_v3.json
E:\rag-agent-runtime\stage_e_final_runtime_capture_v3.jsonl
```

Runtime SHA-256：

```text
a057c0cbc65967676051ef0667d4230fac5d0e6afaa02e417c3d1d493147d136
```

旧 Stage E.1 Hard Failures 和新增 Stage E.2 Hard Failures 全部为 `false`。

新增项：

```text
missing_binding_section_order_not_rejected
missing_binding_document_id_not_rejected
missing_binding_run_id_not_rejected
missing_evidence_contract_hash_not_rejected
missing_prompt_evidence_contract_hash_not_rejected
cross_document_writer_binding_reuse_not_rejected
cross_run_writer_binding_reuse_not_rejected
wrong_section_order_binding_not_rejected
complete_identity_and_lineage_control_failed
```

## 10. JUnit V3

Focused：

```text
E:\rag-agent-runtime\stage_e_focused_junit_v3.xml
74 tests / 0 failures / 0 errors / 0 skipped
SHA-256: 612bd22ae037004517a3c43c662ab497af03441ee2c72db453b38632a387fb5c
```

Regression：

```text
E:\rag-agent-runtime\stage_e_regression_junit_v3.xml
537 tests / 0 failures / 0 errors / 0 skipped
SHA-256: 271d70fca5c568674cfb3c0bd9cb0f8185f929999a8e95586755e86df0c7ffbd
```

总计：

```text
611 tests
0 failures
0 errors
0 skipped
```

## 11. 可复制命令证据

Focused：

```text
scripts/stage_e_focused_commands_v3.txt
SHA-256: 5c878a25ff41a5cd53197e1606ac46395af36b230ee8e1cdf697c8a3e3edeb19
```

Regression：

```text
scripts/stage_e_regression_commands_v3.txt
SHA-256: 30ee0552da8a827ee5683584b67c2c9a115bf0769a90696047c733d1b6ef4ae2
```

Finalizer V3 将命令文件路径、Hash 和完整文本写入最终报告。

## 12. Active Index

```text
Index Version:
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e

Pointer SHA-256:
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce

Manifest SHA-256:
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
```

本阶段未修改 Pointer、Manifest、索引 Artifact 或 Milvus Collection。

## 13. 最终产物

```text
data/processed/indexes/stage_e_final_acceptance_report_v3.json
E:\rag-agent-runtime\stage_e_final_acceptance_report_v3.json
docs/EVIDENCE_GATE_STAGE_E_MANDATORY_IDENTITY_LINEAGE_V3.md
```

## 14. 阶段边界

Stage E.1 与 Stage E.2 均已完成。

Stage E 现在可以证明：

> 当前准备发布的正文是 Claim Audit 审计过的准确内容，并且该 Audit 明确属于当前 Section、Document、Run、Section Order、Writer Gate、Evidence Contract 和 Prompt Evidence Contract。任何身份或血缘字段缺失都不会被视为兼容成功，只能进入人工复核或拒绝正式交付。

Stage F 尚未开始。
