# Stage E 实施与验收证据：Writer Enforcement & Claim Audit Integrity

> 记录日期：2026-08-04  
> 阶段状态：**Stage E.1 与 Stage E.2 均已完成并通过**  
> 在证据链中的位置：强制 Writer 执行 Stage D Gate，并证明最终正文、Claim Audit、Section/Document/Run 身份以及 Evidence 血缘完全一致。

## 1. Stage E 解决了什么问题

Stage D 已经能够回答哪些证据可用、哪些 Claim 可写、是否需要限定语或人工复核，但仍然存在“Gate 有结论，Writer 不一定真正遵守”的风险：

- Writer 可能不消费最终 GateDecision；
- 旧 Assessment 可能重新决定是否生成；
- `qualified` 限定语可能只存在于 Gate，没有进入正文；
- `blocked/human_review` 仍可能调用模型；
- 模型可能生成 Gate 未允许的 Claim；
- Claim Audit 可能对应旧正文或其他章节；
- 上游可以伪造 `claim_audit.passed` 或 `formal_deliverable_eligible`；
- Audit 的 Section、Document、Run、Evidence Hash 缺失时可能被 `None == None` 错误放行。

Stage E 将 Stage D 的“证据治理决策”升级为 Writer 和 Document Hard Gate 必须执行的正式生成协议。

## 2. Stage E 最终链路

```text
Stage D Final Gate
→ WriterGateBinding
→ Writer Prompt Constraint
→ Delivery Mode Enforcement
→ Required Qualifier Injection
→ Final Section Content
→ Independent Multi-label Claim Classification
→ Substantive Sentence Fail-Closed Detection
→ Claim Audit Integrity Hash
→ Section/Document/Run/Order Identity Validation
→ Evidence/Prompt Evidence Lineage Validation
→ Current Artifact Revalidation
→ Document Hard Gate Recomputed Eligibility
→ Formal Delivery Credential
```

## 3. Stage E V1：Writer 强制执行

### 3.1 Writer 只接受 Canonical Gate

Writer 权威输入：

```text
evidence.extra.gate_decision
evidence.extra.generation_allowed
evidence.extra.prompt_evidence_source
evidence.extra.prompt_evidence_contract
```

Evidence Contract 存在时，顶层旧字段不能覆盖 Canonical Gate。生产强门禁路径缺失 Gate 时默认阻断。

### 3.2 WriterGateBinding

核心 Schema：

```text
backend/apps/enterprise_document/schemas/writer_governance.py
WriterGateBindingSchema
```

最终绑定内容包括：

```text
section_id
section_title
section_order
document_id
run_id
gate_decision
generation_allowed
evidence_contract_sha256
prompt_evidence_source
prompt_evidence_contract_sha256
metadata
```

### 3.3 Delivery Mode 执行

#### grounded

```text
允许模型生成
不强制限定语
必须执行 Claim Audit
通过 Document Hard Gate 后才可交付
```

#### qualified

```text
允许模型生成
确定性注入全部 required_qualifiers
必须执行 Claim Audit
有人工复核要求时不得正式交付
```

#### framework_only

只允许：

```text
template_structure
style_guidance
```

#### blocked

```text
模型调用禁止
普通正文禁止
只允许诊断性输出
正式交付禁止
```

#### human_review

```text
模型调用禁止
普通正文禁止
need_human_review = true
正式交付禁止
```

### 3.4 Qualified 限定语确定性注入

实现：

```text
inject_required_qualifiers()
```

限定语必须同时：

```text
出现在最终 content
记录在 required_qualifiers_applied
记录在 ClaimAudit.applied_qualifiers
通过 Document Hard Gate 当前正文重验
```

模型不能通过忽略 Prompt 跳过限定表达。

### 3.5 两条章节主链接入

覆盖：

```text
SectionExecutionCoordinator
SectionExecutionGraph
SectionGenerationService
```

覆盖正常检索、Self-RAG、Corrective Retrieval、恢复和预算路径。所有生产生成调用都携带 WriterGateBinding。

旧：

```text
assessment.status == insufficient
```

不再决定 Writer 是否执行。

## 4. Stage E V1：生成后 Claim Audit

新增：

```text
GeneratedClaimSchema
ClaimAuditViolationSchema
ClaimAuditResultSchema
WriterClaimAuditor
```

生成后审计输入：

```text
最终 Section Content
WriterGateBinding
实际可见限定语
```

Claim 类型覆盖：

```text
current_project_fact
current_project_cost
current_project_quantity
current_project_schedule
current_project_configuration
policy_requirement
technical_specification
procurement_requirement
template_structure
style_guidance
reference_case
typical_architecture
general_method
```

Audit 检查：

```text
Claim Type 在 allowed set
Claim Type 不在 forbidden set
Required Qualifiers 当前可见
Required Qualifiers 已记录
```

越权时：

```text
section.status = failed
error_code = SECTION_CLAIM_AUDIT_FAILED
formal_deliverable_eligible = false
need_human_review = true
```

## 5. Stage E.1：正文与 Audit 完整性闭环

### 5.1 ClaimAuditIntegritySchema

记录：

```text
section_id
section_title
content_sha256
normalized_content_sha256
writer_gate_binding_sha256
evidence_contract_sha256
prompt_evidence_contract_sha256
classifier_id
classifier_version
classifier_policy_sha256
substantive_detector_version
```

所有 Hash 使用严格小写 SHA-256。

### 5.2 Audit 发生在所有正文修改之后

顺序：

```text
模型生成
→ 截断/压缩恢复
→ Validation Rewrite
→ Citation Repair
→ Grounded Regeneration
→ Citation Marker Reconcile
→ Required Qualifier Injection
→ 最终语义和高风险事实复核
→ 最终 Content Hash
→ Claim Classification/Audit
→ Content 与 Audit 同时落入 Section Artifact
```

Audit 完成后，生产代码不得再次修改正文。

### 5.3 原始 Hash 与诊断 Hash

```text
content_sha256
= SHA256(exact section.content UTF-8 bytes)
```

`normalized_content_sha256` 只进行：

```text
CRLF/CR → LF
Unicode NFC
移除行尾空白
```

正式完整性必须以原始 Hash 匹配为准。

### 5.4 Generated Claim Span

每条 Claim 记录：

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

Document Hard Gate 重新读取当前正文 Span，并只允许冻结规则移除 Citation Marker 后与 Claim Text 一致。

### 5.5 Claim 分类独立于 Gate

正确顺序：

```text
Sentence
→ Independent Multi-label Classification
→ Actual Claim Types
→ 与 Gate allowed/forbidden 比较
```

分类器输入不包含：

```text
allowed_claim_types
forbidden_claim_types
delivery_mode
```

不存在“无法识别后从 Allowed Set 中挑一个类型”的偏置。

### 5.6 未识别实质性陈述 Fail-Closed

实质性句子如果：

```text
claim_types = []
```

则产生：

```text
unclassified_substantive_claim
audit.passed = false
human_review_required = true
formal_deliverable_eligible = false
```

未知完整业务陈述不会静默放行。

### 5.7 Classifier 策略冻结

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

Document Hard Gate 会校验 Classifier ID、Version 和 Policy Hash。

## 6. Stage E.1：Document Hard Gate 重新计算

Document Hard Gate 不再直接信任：

```text
section.extra.formal_deliverable_eligible
claim_audit.passed
```

它重新计算：

```text
Binding Schema/Identity
Generation Allowed
Delivery Mode
Human Review
Audit Schema/Identity
Exact Content Hash
Normalized Hash
Binding Hash
Evidence Lineage
Claim Boundary
Generated Claim Span
Classifier Policy
Deterministic Re-audit
Stored Audit Passed
Required Qualifiers 当前可见
Section Status
```

最终得到：

```text
recomputed_formal_deliverable_eligible
```

伪造 `true`、伪造 `false` 或只修改 `claim_audit.passed` 都会被一致性检查拒绝。

## 7. Stage E.2：身份字段和 Evidence 血缘必填

Stage E.1 最初为了兼容历史 Artifact，以下字段可为 `None`：

```text
section_order
document_id
run_id
evidence_contract_sha256
prompt_evidence_contract_sha256
```

如果正式路径把 `None` 视为通配，会出现跨 Document/Run 或 `None == None` 血缘错误放行。

### 7.1 兼容读取不等于兼容发布

Schema 继续允许历史 Artifact 解析，但正式 Writer 需要：

```text
generation_allowed
AND section_order/document_id/run_id 全部存在
AND evidence_contract_sha256 存在
AND prompt_evidence_contract_sha256 存在
```

新增运行属性：

```text
identity_fields_present
evidence_lineage_fields_present
ordinary_generation_permitted
legacy_artifact_incomplete
```

历史不完整 Artifact 进入：

```text
writer_execution_status = not_assessed
need_human_review = true
formal_deliverable_eligible = false
generation_block_reason = legacy_writer_artifact_incomplete
模型调用 = 0
```

### 7.2 身份存在性与一致性分离

存在性：

```text
writer_binding_identity_fields_present
```

一致性：

```text
writer_binding_section_identity_valid
```

严格要求：

```text
binding.section_id == section.section_id
binding.section_title == section.section_title
binding.section_order == section.section_order
binding.document_id == draft.document_id
binding.run_id == draft.run_id
```

不再使用：

```python
field is None or field == expected
```

### 7.3 Evidence 血缘存在性与一致性分离

```text
writer_evidence_lineage_fields_present
writer_audit_evidence_lineage_valid
```

Binding 和 Audit 的两个 Hash 都必须存在并相等：

```text
evidence_contract_sha256
prompt_evidence_contract_sha256
```

`None == None` 不再表示血缘有效。

### 7.4 主要 Failure Codes

```text
writer_binding_section_order_missing
writer_binding_document_id_missing
writer_binding_run_id_missing
writer_binding_identity_fields_missing
writer_evidence_contract_hash_missing
writer_prompt_evidence_contract_hash_missing
writer_audit_evidence_contract_hash_missing
writer_audit_prompt_evidence_contract_hash_missing
writer_evidence_lineage_fields_missing
legacy_writer_artifact_incomplete
writer_binding_identity_mismatch
writer_audit_identity_mismatch
writer_audit_evidence_lineage_mismatch
```

## 8. 关键负向验收结果

全部被 Document Hard Gate 拒绝：

```text
Audit 后正文替换或字符追加
Qualified 限定语删除
跨 Section Binding 复用
跨 Section Audit/Generated Claim 复用
缺少 section_order
缺少 document_id
缺少 run_id
缺少 Evidence Contract Hash
缺少 Prompt Evidence Contract Hash
跨 Document Binding 重放
跨 Run Binding 重放
错误 Section Order
未知 Classifier Policy
伪造 formal_deliverable_eligible
伪造 claim_audit.passed
未分类实质性 Claim
中文金额/数量/工期/政策/标准/采购绕过
```

完整合法控制组：

```text
Identity Fields Present      true
Binding Identity Valid       true
Evidence Lineage Present     true
Evidence Lineage Valid       true
Claim Re-audit Valid         true
Formal Deliverable Eligible  true
Document Hard Gate Passed    true
```

## 9. 验收标准

Stage E 最终必须证明：

1. Writer 只消费 Stage D Canonical Gate；
2. Blocked/Human Review 不调用模型；
3. Qualified 限定语确定性进入最终正文；
4. 生成后 Claim 分类独立于 Gate；
5. 未识别实质性 Claim fail-closed；
6. Audit 绑定最终正文 Exact Hash；
7. Claim Span 可回到当前正文复核；
8. Binding/Audit 属于当前 Section、Document、Run 和 Order；
9. Evidence 与 Prompt Evidence Hash 必须存在并匹配；
10. Document Hard Gate 对当前 Artifact 重新计算交付资格；
11. 上游布尔值和历史 Audit 不能直接决定发布；
12. 全部负向场景阻断，合法控制组放行。

## 10. 最终验收结果

### 10.1 Runtime Acceptance V3

```text
passed = true
Hard Failures = 0 / 34
```

Runtime：

```text
E:\rag-agent-runtime\stage_e_final_runtime_acceptance_v3.json
SHA-256:
a057c0cbc65967676051ef0667d4230fac5d0e6afaa02e417c3d1d493147d136
```

新增 9 项身份和血缘 Hard Failure 全部为 `false`：

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

### 10.2 JUnit

Focused：

```text
74 tests
0 failures
0 errors
0 skipped

E:\rag-agent-runtime\stage_e_focused_junit_v3.xml
SHA-256:
612bd22ae037004517a3c43c662ab497af03441ee2c72db453b38632a387fb5c
```

Regression：

```text
537 tests
0 failures
0 errors
0 skipped

E:\rag-agent-runtime\stage_e_regression_junit_v3.xml
SHA-256:
271d70fca5c568674cfb3c0bd9cb0f8185f929999a8e95586755e86df0c7ffbd
```

总计：

```text
611 tests
0 failures
0 errors
0 skipped
```

### 10.3 可复制命令

```text
scripts/stage_e_focused_commands_v3.txt
SHA-256:
5c878a25ff41a5cd53197e1606ac46395af36b230ee8e1cdf697c8a3e3edeb19

scripts/stage_e_regression_commands_v3.txt
SHA-256:
30ee0552da8a827ee5683584b67c2c9a115bf0769a90696047c733d1b6ef4ae2
```

### 10.4 静态和索引检查

```text
compileall       passed
git diff --check passed
Active Index     unchanged
```

## 11. 主要代码位置

```text
backend/apps/enterprise_document/schemas/writer_governance.py
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
backend/apps/enterprise_document/services/writer_artifact_integrity.py
backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/apps/enterprise_document/services/document_gate.py
backend/apps/enterprise_document/services/scheme_writer/document_assembler.py
```

## 12. 验收证据位置

实施说明：

```text
docs/EVIDENCE_GATE_STAGE_E_WRITER_ENFORCEMENT_V1.md
docs/EVIDENCE_GATE_STAGE_E_CLAIM_AUDIT_INTEGRITY_V2.md
docs/EVIDENCE_GATE_STAGE_E_MANDATORY_IDENTITY_LINEAGE_V3.md
```

最终报告：

```text
data/processed/indexes/stage_e_final_acceptance_report_v3.json
SHA-256:
4e0c3f3b4067e8c7a17e1820e76f5bff27d7028c0abca17efdadb5538a7b2f7d

E:\rag-agent-runtime\stage_e_final_acceptance_report_v3.json
```

Runtime Capture：

```text
E:\rag-agent-runtime\stage_e_final_runtime_capture_v3.jsonl
```

## 13. Active Index

```text
Index Version:
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e

Pointer SHA-256:
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce

Manifest SHA-256:
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
```

Stage E 没有重建索引、修改 Pointer、修改 Stage D Gate 语义或删除历史索引。

## 14. 本阶段没有实施什么

Stage E 只决定当前文档是否具备正式交付资格，没有负责：

```text
不可变版本存储
发布幂等性
发布并发互斥
发布原子 Commit
发布后版本回读
```

这些属于 Stage F。

## 15. 最终结论

Stage E 已完成并通过。它能够证明：

> 当前准备正式交付的正文，正是 Claim Audit 审计过的内容；该 Audit 明确属于当前 Section、Section Order、Document、Run、Writer Gate、Evidence Contract 和 Prompt Evidence Contract。任何正文篡改、越权 Claim、限定语删除、身份缺失、跨对象重放或血缘缺失都会被 Document Hard Gate 拒绝。

Stage F 的输入只能是 Stage E 当前重新计算后通过的 Formal Delivery Credential。
