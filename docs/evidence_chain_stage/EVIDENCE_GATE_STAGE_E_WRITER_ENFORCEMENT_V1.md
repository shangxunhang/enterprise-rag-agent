# Stage E Writer Enforcement and Document Publication Gate V1

## 1. 验收结论

Stage E 已完成以下闭环：

```text
Stage D Final Gate
→ WriterGateBinding
→ Writer Prompt Constraint
→ Delivery Mode Enforcement
→ Required Qualifier Injection
→ Post-generation Claim Audit
→ Section Writer Status
→ Document Hard Gate Aggregation
→ Formal Deliverable Decision
```

最终状态：

```text
Stage E focused tests        16 passed
Final relevant regression  479 passed
Failures                      0
Errors                        0
Warnings                      3
Runtime acceptance       passed
Active Index             unchanged
Stage F                  not started
```

Active Index：

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

本阶段没有重建索引，没有修改 Active Pointer，没有删除历史索引。

---

## 2. Stage E 解决的问题

Stage D 已能回答：

```text
哪些证据可用
哪些 Claim Type 可写
哪些 Claim Type 禁写
是否需要限定表达
是否需要人工复核
是否允许进入生成
```

但 Stage D 结束时，Writer 仍存在以下风险：

1. Writer 没有强制消费最终 GateDecision。
2. Legacy Evidence Assessment 仍可能参与生成分支判断。
3. `qualified` 只在 Gate 中存在，没有保证限定语进入最终正文。
4. `blocked` 或 `human_review` 可能仍调用模型。
5. 模型可能生成 Gate 未允许的 Claim。
6. 文档级 Hard Gate 不知道每个章节的 Writer 执行状态。
7. 安全拒绝说明可能被误当作正式章节正文。

Stage E 将 Gate 从“检索治理结果”升级为“生成和发布阶段的强制执行协议”。

---

## 3. 权威 Gate 来源

Writer 只接受 Canonical Evidence Bundle 中的：

```text
evidence.extra.gate_decision
evidence.extra.generation_allowed
evidence.extra.prompt_evidence_source
evidence.extra.prompt_evidence_contract
```

当 `evidence` Contract 存在时，顶层：

```text
normalized.extra.gate_decision
normalized.extra.generation_allowed
```

不能覆盖 Canonical Evidence Contract。

只有在完全不存在 Evidence Contract 的兼容边界，才允许读取顶层旧字段；生产强门禁路径缺失 Gate 时默认阻断。

该规则由以下函数实现：

```text
extract_writer_gate_binding()
```

---

## 4. WriterGateBinding V1

新增：

```text
backend/apps/enterprise_document/schemas/writer_governance.py
```

核心对象：

```text
WriterGateBindingSchema
```

字段包括：

```text
section_id
section_title
gate_decision
generation_allowed
evidence_contract_sha256
prompt_evidence_source
prompt_evidence_contract_sha256
metadata
```

契约校验：

1. `generation_allowed` 必须与 `delivery_mode` 一致。
2. `blocked` 不允许存在 `allowed_claim_types`。
3. `human_review` 必须满足 `human_review_required=true`。
4. Binding 必须与当前章节 ID 和章节标题一致。
5. Evidence Contract 和 Prompt Contract 可建立稳定 Hash 血缘。

Writer 执行状态枚举：

```text
grounded
qualified
framework_only
blocked
human_review
not_assessed
not_applicable
failed
```

---

## 5. Delivery Mode 执行语义

### 5.1 Grounded

```text
model generation       allowed
qualifier injection    not required
claim audit            required
formal delivery        only after all audits/gates pass
```

Writer 只能生成 `allowed_claim_types` 中的声明。

### 5.2 Qualified

```text
model generation       allowed
qualifier injection    exact required_qualifiers
claim audit            required
human review           follows GateDecision
formal delivery        requires review requirement cleared
```

限定语采用确定性注入，不依赖模型自觉遵守。

例如：

```text
根据现有估算材料
最终金额以审批或采购结果为准
```

必须同时：

```text
出现在最终 content
记录在 required_qualifiers_applied
通过 ClaimAuditResult.applied_qualifiers 核验
```

### 5.3 Framework Only

只允许：

```text
template_structure
style_guidance
```

当前项目事实、金额、数量、进度、配置等 Claim 会在生成后审计中被拒绝。

### 5.4 Blocked

```text
model call             forbidden
ordinary section prose forbidden
diagnostic output      allowed
formal delivery        forbidden
```

生成结果为诊断性章节：

```text
status                     partial_success
writer_execution_status    blocked
generation_blocked         true
formal_deliverable_eligible false
```

### 5.5 Human Review

```text
model call             forbidden
ordinary section prose forbidden
diagnostic output      allowed
need_human_review      true
formal delivery        forbidden
```

---

## 6. Writer Prompt 强约束

进入模型调用前，Writer Prompt 追加：

```text
【最终证据门禁约束｜不得覆盖】
delivery_mode
 generation_allowed
allowed_claim_types
forbidden_claim_types
required_qualifiers
human_review_required
```

模型 Prompt 约束只是第一层。

即使模型忽略 Prompt，后续 Claim Audit 仍会阻断越权声明。

---

## 7. Qualified 限定语注入

实现函数：

```text
inject_required_qualifiers()
```

规则：

1. 仅 `qualified` 模式执行。
2. 已存在的限定语不重复注入。
3. 缺失限定语以确定性前缀写入最终正文。
4. 注入结果记录到 `required_qualifiers_applied`。
5. Document Hard Gate 再次核验正文可见性和 Audit 记录。

治理限定语不参与业务 Claim 分类，避免把“最终金额以审批结果为准”误判成新的业务金额 Claim。

---

## 8. Post-generation Claim Audit

新增：

```text
GeneratedClaimSchema
ClaimAuditViolationSchema
ClaimAuditResultSchema
WriterClaimAuditor
```

生成后审计输入：

```text
final section content
WriterGateBinding
required qualifiers actually applied
```

当前确定性 Claim 类型覆盖：

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

每个 Claim 记录：

```text
claim_id
section_id
sentence_index
claim_type
text
span_start
span_end
classifier
confidence
metadata
```

审计检查：

```text
claim type in allowed set
claim type not in forbidden set
all required qualifiers visible
all required qualifiers recorded
```

典型违规码：

```text
forbidden_claim_type_generated
claim_type_not_allowed
required_qualifier_missing
writer_generation_blocked
writer_human_review_required
```

模型生成越权 Claim 时：

```text
section.status = failed
error_code = SECTION_CLAIM_AUDIT_FAILED
formal_deliverable_eligible = false
need_human_review = true
```

---

## 9. 两条章节执行主链均已接入

### 9.1 Legacy Coordinator 路径

```text
SectionExecutionCoordinator
```

已覆盖：

```text
document gate inheritance
section retrieval
self-RAG retrieve-more
corrective retrieval
budget/recovery path
```

### 9.2 Production Section LangGraph

```text
SectionExecutionGraph
```

已覆盖：

```text
prepare section
initial section retrieval
ResearchAgent ToolGateway retrieval
retrieve_more
corrective retrieval
evaluate_evidence_gate
generate_section
finalize section result
```

所有生产 `generate_section()` 调用都携带 `writer_gate_binding`。

Legacy：

```text
assessment.status == insufficient
```

不再决定 Writer 是否执行。

最终决定仅来自：

```text
writer_gate is present
writer_gate.generation_allowed
writer_gate.delivery_mode
```

非引用必需章节不重复触发 Section RAG，而是继承 Document Final Gate；引用必需章节执行独立检索并刷新 Gate。

---

## 10. Section Evidence Bundle 新字段

新增：

```text
writer_gate_binding
generation_allowed
```

Section Result Extra 新增：

```text
writer_gate_binding
writer_execution_status
required_qualifiers_applied
claim_audit
formal_deliverable_eligible
need_human_review
generation_blocked
generation_block_reason
```

这使章节的生成权限、执行方式和发布资格可审计，而不是从正文或旧状态推断。

---

## 11. Document Hard Gate 新增检查

Document Hard Gate 新增：

```text
writer_gate_bound
writer_execution_status_consistent
writer_generation_allowed
writer_claim_audits_passed
writer_required_qualifiers_applied
writer_human_review_clear
writer_formal_deliverable_eligible
```

元数据记录：

```text
writer_status_by_section
writer_gate_missing_section_ids
writer_gate_invalid_section_ids
writer_status_mismatch_section_ids
writer_blocked_section_ids
writer_human_review_section_ids
writer_claim_audit_missing_section_ids
writer_claim_audit_invalid_section_ids
writer_claim_audit_failed_section_ids
writer_qualifier_failure_section_ids
writer_formal_ineligible_section_ids
```

诊断性安全拒绝章节可以证明：

```text
evidence_boundary_respected = true
```

但仍会因为：

```text
writer_generation_allowed = false
writer_formal_deliverable_eligible = false
```

被正式发布门禁拒绝。

因此“安全拒绝”与“正式交付”被明确分离。

---

## 12. Document Assembler 兼容 Stage E 状态

Document Assembler 现在识别：

```text
evidence_insufficient
writer_* generation block reasons
writer_execution_status = blocked
writer_execution_status = human_review
writer_execution_status = not_assessed
```

这些状态被记录为显式阻断，而不是误判为系统绕过证据边界。

它们仍不会满足正式交付条件。

---

## 13. Legacy Assessment 不再夺权

Stage E 增加主链负向测试：

```text
Legacy Assessment = insufficient
Final Writer Gate = grounded
```

结果：

```text
Writer Gate remains authoritative
Writer executes
Verifier executes
generate_section executes
```

另一个真实阻断测试：

```text
Final Writer Gate = blocked
```

结果：

```text
Writer does not execute
Verifier does not execute
model generation node does not execute
rejected artifact only
formal deliverable absent
```

---

## 14. 真实 Active Index Runtime 验收

运行脚本：

```text
scripts/run_stage_e_writer_acceptance.py
```

### 14.1 Estimated Cost

真实 Stage D Gate：

```text
delivery_mode         qualified
generation_allowed    true
allowed_claim_types   current_project_cost
human_review_required true
```

限定语：

```text
根据现有估算材料
最终金额以审批或采购结果为准
```

结果：

```text
qualifiers injected        true
claim audit passed         true
section writer status      qualified
document formal delivery   blocked pending human review
```

### 14.2 Unknown Cost

真实 Stage D Gate：

```text
delivery_mode         blocked
generation_allowed    false
```

结果：

```text
normal model generation    skipped
section status             partial_success
writer status              blocked
evidence boundary respected true
formal delivery            rejected
```

### 14.3 Grounded Method

```text
claim audit       passed
document hard gate passed
formal delivery   allowed
```

### 14.4 Forbidden Cost Claim

Gate 只允许 `general_method`，模型正文包含项目金额。

结果：

```text
claim type current_project_cost detected
forbidden_claim_type_generated
claim audit failed
document hard gate failed
formal delivery rejected
```

### 14.5 Canonical Gate Override

Evidence Contract 存在但不含 Gate，顶层伪造 grounded Gate。

结果：

```text
binding rejected
```

### 14.6 Immutable Index

Runtime 前后：

```text
pointer hash  unchanged
manifest hash unchanged
index version unchanged
```

---

## 15. Runtime Hard Failures

全部为 `false`：

```text
estimated_qualified_writer_failed
unknown_blocked_writer_failed
grounded_writer_not_deliverable
forbidden_claim_not_rejected
canonical_gate_override_accepted
active_pointer_or_manifest_changed
```

---

## 16. 测试结果

### 16.1 Stage E 专项

```text
16 passed
0 failed
0 errors
```

### 16.2 最终相关回归

```text
479 passed
0 failed
0 errors
3 warnings
```

构成：

```text
Stage A-E / RAG / Section / Document core  354
Agent-RAG V1                                 80
Top-level / Section / Document mainline      45
Total                                       479
```

Windows 下 LangGraph/SQLite 测试使用独立 pytest 进程和独立 E 盘 basetemp，避免打开句柄导致的清理噪声。

警告仅来自：

```text
TRANSFORMERS_CACHE deprecation
SWIG deprecation
```

Runtime 另显示本地 PyTorch 未启用 Flash Attention，该提示不影响正确性验收。

---

## 17. 主要新增和修改文件

新增：

```text
backend/apps/enterprise_document/schemas/writer_governance.py
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
backend/tests/test_stage_e_writer_governance.py
backend/tests/test_stage_e_document_hard_gate.py
scripts/run_stage_e_writer_acceptance.py
scripts/finalize_stage_e_acceptance_v1.py
```

核心修改：

```text
backend/apps/enterprise_document/services/scheme_writer/section_generation_service.py
backend/apps/enterprise_document/services/scheme_writer/section_execution_coordinator.py
backend/apps/enterprise_document/workflows/section_execution_graph.py
backend/apps/enterprise_document/services/document_gate.py
backend/apps/enterprise_document/services/scheme_writer/document_assembler.py
backend/apps/enterprise_document/services/scheme_writer/stage_service.py
backend/apps/enterprise_document/schemas/section_execution_graph.py
backend/apps/enterprise_document/schemas/scheme_writer/section_execution.py
backend/apps/enterprise_document/schemas/scheme_writer/evidence.py
backend/apps/enterprise_document/services/scheme_writer/__init__.py
backend/tools/fake_rag_tool.py
```

---

## 18. 验收产物

仓库：

```text
data/processed/indexes/stage_e_writer_acceptance_report_v1.json
docs/EVIDENCE_GATE_STAGE_E_WRITER_ENFORCEMENT_V1.md
```

Runtime：

```text
E:\rag-agent-runtime\stage_e_writer_runtime_acceptance_v1.json
E:\rag-agent-runtime\stage_e_writer_runtime_capture_v1.jsonl
E:\rag-agent-runtime\stage_e_writer_acceptance_report_v1.json
```

---

## 19. 阶段边界

Stage E 已完成：

```text
Final Gate → Writer binding
Writer mode enforcement
qualified qualifier injection
post-generation Claim Audit
section writer status publication
Document Hard Gate aggregation
blocked/human-review diagnostic-only path
```

Stage F 未开始：

```text
downstream release workflow
human review resolution workflow/UI
model-assisted claim classifier expansion
```

本次未执行 Git commit。
