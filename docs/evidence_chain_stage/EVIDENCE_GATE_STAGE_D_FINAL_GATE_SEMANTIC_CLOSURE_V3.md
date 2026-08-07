# Stage D.1 Final Gate Semantic Closure

## 1. 最终结论

Stage D.1 已完成。Stage D 在线检索与证据门禁现在能够确定回答：

- 模型实际看到了哪些证据；
- 某条证据是否被 Context Packer 截断；
- 结构化 Fact 的 Predicate、Value、Unit、Fact Status 是否仍在 Prompt 中可见；
- Candidate 的身份、权限与 Document Profile 是否完全一致；
- 多文档结构化事实是否冲突；
- 当前 Claim 可以 grounded、qualified、blocked 还是必须 human_review；
- 最终允许或禁止生成的原因。

Stage D.1 没有重建或修改 Stage C 索引，没有修改 Active Pointer，也没有开始 Stage E Writer 改造。

当前 Active Index：

```text
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e
```

Manifest SHA-256：

```text
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
```

Pointer SHA-256：

```text
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce
```

## 2. 最终在线链路

```text
Trusted Access Scope
→ Canonical Retrieval Scope
→ Dense / BM25 同范围召回
→ RRF / Parent Backfill / Rerank
→ RetrievalCandidateV2
→ Retrieval-view Governance
→ eligible retrieval results
→ Context Packer
→ PromptEvidenceCandidateV1
→ Prompt-view Governance
→ EvidenceSetAssessment
→ GateDecision
→ Evidence Bundle
```

旧 C-RAG 仅决定是否触发 Corrective Retrieval。最终 Claim 权限只能来自：

```text
Document Profile
+ Structured Evidence
+ Prompt-visible Evidence
+ Fact Status / Authority Policy
+ Evidence Set Assessment
+ Gate Policy
```

## 3. P0-1：空 selected_results 不再回退

修复位置：

```text
backend/rag/mapping/result_mapper.py
```

现在明确区分：

```text
selected_results 字段存在且为 []
selected_results 字段不存在
```

### 3.1 字段存在且为空

`selected_results=[]` 是最终 Prompt 事实，不允许从 `retrieval_results` 回捞。

验收结果：

```text
Prompt Candidate Count   0
Eligible Count           0
Coverage Ratio           0.0
Evidence Status          absent
Delivery Mode            blocked
Allowed Claim Types      []
Generation Allowed       false
```

### 3.2 字段缺失

强门禁模式满足任一条件：

```text
access_scope_enforced=true
evidence_requirements 明确存在
```

字段缺失时：

```text
prompt_evidence_source = missing_blocked
reason_code             = prompt_evidence_contract_missing
delivery_mode           = blocked
generation_allowed      = false
```

旧兼容模式才允许：

```text
prompt_evidence_source = legacy_fallback
```

即使兼容回退成功，也会强制：

```text
delivery_mode         = qualified
human_review_required = true
```

若旧结果连 Document Profile/Candidate Contract 都不存在，则只保留旧 Evidence Bundle 投影，最终 Gate 阻断。

## 4. P0-2：Final Gate 只读取 Prompt-visible Evidence

新增：

```text
backend/rag/schema/prompt_evidence_candidate_v1.py
```

冻结结构：

```text
schema_version
candidate_id
child_chunk_id
parent_chunk_id
document_id
prompt_visible_text
prompt_visible_char_count
prompt_visible_token_count
citation_ids
context_rank
context_truncated
truncated_source_id
truncation_reason
document_profile
structured_evidence
retrieval_candidate
profile_sha256
source_sha256
fact_visible_in_prompt
```

Context Packer 现在对每条 selected result 显式写入：

```text
prompt_visible_text
context_text = prompt_visible_text
metadata.context_truncated
metadata.prompt_citation_ids
metadata.prompt_visible_char_count
metadata.prompt_visible_token_count
metadata.truncated_source_id
metadata.truncation_reason
```

Final Governance 不再通过 `parent_text/context_text/text` 优先级猜测视图，而只接受 `PromptEvidenceCandidateV1`。

### 4.1 Prompt Contract 一致性

以下情况硬失败：

```text
selected_results 非空但 context 为空
selected_results 为空但 context 非空
prompt_visible_text 不在 context_pack.context 中
Prompt Candidate 与 Retrieval Candidate 身份或 Hash 不一致
```

### 4.2 截断验收

测试 Parent 末尾包含：

```text
预算金额：250万元
```

实际 Prompt 只包含前部说明和：

```text
...[TRUNCATED]
```

结果：

```text
context_truncated       true
fact_visible_in_prompt  false
预算金额 covered        false
allowed_claim_types     []
delivery_mode           blocked
generation_allowed      false
```

检索命中的完整 Child 可保留用于语义相关性和审计，但不能用于 Topic Coverage 或 Claim Support。

## 5. P0-3：真实 Structured Evidence 在线贯穿

新增：

```text
backend/rag/schema/structured_evidence.py
```

冻结结构：

```text
StructuredEvidenceSchema
├── representation_type
├── original_strategy
├── actual_strategy
├── fact
├── table
├── policy
├── template
└── fallback
```

### 5.1 Fact

```text
record_id
fact_category
subject
predicate
value
unit
fact_status
source_span
```

### 5.2 Table

```text
table_id
table_title
header
row_index
cells
normalized_fields
inherited_cells
merged_cell_relations
calculation_record
```

真实 CO₂ 记录的 `calculation_record=true` 被按原始布尔类型稳定保留。

### 5.3 Policy

```text
policy_name
chapter_id
chapter_title
article_id
article_title
clause_id
clause_path
is_table_of_contents
```

### 5.4 Template

```text
section_path
section_title
required_fields
placeholder_fields
style_hints
is_example_content
```

### 5.5 Fallback / 普通文本

普通段落也生成稳定对象：

```text
representation_type = paragraph_text
fallback.used        = false
```

显式 fallback：

```text
fallback.used
fallback.reason
fallback.strategy
```

### 5.6 投影路径

```text
Stage C Child extra
→ Dense Hit
→ BM25 Hit
→ Retrieval Result V2
→ RetrievalCandidateV2
→ PromptEvidenceCandidateV1
→ Item Assessment
→ Evidence Set Assessment
→ Evidence Bundle
```

Dense 与 BM25 统一调用同一个 `build_structured_evidence()`，不存在分支语义漂移。

## 6. P0-4：Candidate 与 Profile 全量一致性

`RetrievalCandidateV2Schema` 现在逐项校验：

```text
document_id
tenant_id
kb_id
document_type
evidence_role
project_relation
project_id
authority_level
profile_sha256
source_sha256
allowed_claim_types
forbidden_claim_types
```

Claim Type 列表按集合语义比较，同时要求：

```text
无重复
无交集
值属于合法 Enum
与 Profile content_capabilities 完全一致
```

以下九类不一致均有独立负向测试：

```text
tenant_id mismatch
kb_id mismatch
document_type mismatch
evidence_role mismatch
project_relation mismatch
project_id mismatch
authority_level mismatch
allowed_claim_types mismatch
forbidden_claim_types mismatch
```

任意不一致：

```text
Candidate 构建失败
rejected_candidate_id 保留
candidate_contract_errors 发布
```

不得构造“标量来自 Hit、权限来自 Profile”的混合对象。

## 7. Retrieval Scope 语义收口

新增严格结构：

```text
StructuredRetrievalScopeSchema
ConfigDict(extra="forbid")
```

只接受：

```text
tenant_id
kb_ids
file_ids
doc_ids
access_scope_enforced
```

未知字段如 `kb_idz` 会硬失败。

KB/File/Doc ID 被定义为集合：

```text
strip
去重
排序
```

因此：

```text
[a, b]
[b, a]
```

解析为相同 Canonical Scope 和 Fingerprint。

授权模式仍要求：

```text
tenant_id 必须存在
至少一个 kb_id 必须存在
Dense/BM25 范围冲突必须硬失败
```

## 8. Fact Status 与 Authority 联合决策

新增统一决策：

```text
resolve_fact_claim_eligibility()
```

输入：

```text
claim_type
fact_status
authority_level
document_type
source_document_form
project_relation
```

### 8.1 决策表

| Fact Status | 当前项目 Claim 行为 | Delivery | 限定语/约束 |
|---|---|---|---|
| `unknown` | 不允许确定性 Claim | `blocked` | `fact_status_unknown` |
| `estimated` | 仅支持估算表达 | `qualified` | 根据现有估算材料；最终金额以审批或采购结果为准 |
| `proposed` | 仅支持拟议表达 | `qualified` | 拟；尚待确认 |
| `planned` | 仅支持计划表达 | `qualified` | 计划；尚待确认 |
| `confirmed` | 需 current_project + authoritative/official/confirmed Authority | `grounded` 或阻断 | 不得仅凭 status 绕过 Profile |
| `decided` | 还需正式决策型 `source_document_form` | `grounded` 或阻断 | 非正式可研/参考材料不能视为决策 |

所有状态仍受以下上位条件约束：

```text
project_relation
document_type
authority_level
source_document_form
allowed_claim_types
forbidden_claim_types
Prompt 可见性
```

`other_project` 永远不能支持 current_project Claim。

### 8.2 真实 Active Runtime：estimated

查询：

```text
大件运输许可数字化提升前期采购项目预算金额250000.00元合同包预算金额
```

限定当前项目文档后，实际 Prompt Evidence 中包含 estimated 采购预算记录。

结果：

```text
status                  sufficient
delivery_mode           qualified
selected_count          1
generation_allowed      true
human_review_required   true
required_qualifiers:
  - 根据现有估算材料
  - 最终金额以审批或采购结果为准
```

### 8.3 真实 Active Runtime：unknown

查询：

```text
大件运输许可系统升级项目预算金额和投资估算
```

命中记录的成本 Fact Status 为 `unknown`，因此不再沿用旧 Stage D 的“检索相关即充分”结论。

结果：

```text
selected_count       0
status               absent
delivery_mode        blocked
allowed_claim_types  []
generation_allowed   false
```

这是 Fact Status 正式生效后的预期变化。

## 9. 真实结构化冲突检测

冲突检测读取：

```text
candidate.structured_evidence.fact.predicate
candidate.structured_evidence.fact.value
candidate.structured_evidence.fact.unit
```

冲突要求：

```text
同一 Claim Type
同一规范化 Predicate
至少两个不同 Document
不同规范化 Value
```

同一文档重复行不构成跨文档冲突。

### 9.1 安全值规范化

支持明确货币单位转换：

```text
250 万元 == 250万元
250万元 == 2500000元
```

不会智能合并：

```text
约250万元
不超过250万元
250万元以上
```

### 9.2 冲突验收

两个不同当前项目文档：

```text
预算金额 = 250万元
预算金额 = 300万元
```

结果：

```text
Evidence Status        conflicted
Delivery Mode          human_review
Allowed Claim Types    []
Human Review Required  true
Generation Allowed     false
```

## 10. Active Index 全量审计

```text
Child Records                    10247
RetrievalCandidateV2             10247
Distinct Candidate IDs           10247
Distinct Documents                  30
Distinct Profile Hashes             30
Distinct Source Hashes              30
Candidate Build Errors               0
StructuredEvidence Records       10247
```

结构分布：

```text
Fact Evidence Records       5034
Table Evidence Records       107
Policy Evidence Records      490
Template Evidence Records    928
Fallback Evidence Records    469
Plain Text Evidence Records 3688
```

Fact Status 分布：

```text
estimated    18
unknown    5016
```

全量违规：

```text
Profile 标量不一致                   0
Candidate 构建错误                   0
Dense/BM25 Structured 不一致         0
重复 Candidate ID                    0
kb_id=default                        0
unknown / unclassified               0
Gold / non-indexable                 0
历史材料当前项目 Claim 泄漏            0
模板非模板 Claim 泄漏                  0
当前 Claim 项目关系错误                0
```

抽样覆盖：

```text
current_project fact
estimated cost
unknown fact
policy clause
table row
template section
fallback text evidence
```

## 11. Runtime / Negative Acceptance

V3 Runtime 报告包含九组场景：

```text
estimated_active_runtime
unknown_active_runtime_empty_prompt
explicit_empty_selected_results
missing_selected_results
truncated_prompt_topic
structured_fact_conflict
fact_status_policy
candidate_profile_mismatch
prompt_context_inconsistent
```

显式 hard failures：

```text
empty_selected_results_fell_back                     false
missing_selected_results_not_blocked                 false
truncated_topic_counted_as_visible                   false
structured_evidence_projection_missing               false
dense_bm25_structured_evidence_mismatch              false
candidate_profile_scalar_mismatch_not_rejected       false
real_fact_conflict_not_detected                      false
unknown_fact_status_allowed_as_grounded              false
estimated_fact_missing_qualifier                     false
prompt_context_selected_records_inconsistent         false
active_runtime_estimated_scenario_failed             false
active_runtime_unknown_scenario_failed               false
active_index_audit_failed                            false
```

## 12. Evidence Bundle 最终契约

Evidence Bundle 继续发布：

```text
extra.evidence_governance_v2
extra.gate_decision
extra.generation_allowed
```

新增：

```text
extra.prompt_evidence_contract
extra.prompt_evidence_source
```

Prompt Contract 包含：

```text
source
contract_missing
candidate_count
candidate_errors
candidates
context_sha256
```

不合格证据：

```text
不进入 Context
不获得 Citation
保留为 Dropped Evidence
具有明确 context_drop_reason
```

Legacy C-RAG 继续保存在：

```text
assessment.details.legacy_corrective_assessment
```

但不能改变：

```text
allowed_claim_types
delivery_mode
generation_allowed
```

## 13. 测试结果

Stage D 专项：

```text
59 passed
0 failed
0 error
0.76 seconds
```

最终相关回归：

```text
409 passed
0 failed
0 error
3 warnings
95.57 seconds
```

警告仅来自 Transformers Cache 和 FAISS/SWIG 弃用信息。

测试临时目录：

```text
E:\rag-agent-tmp\pytest-stage-d-focused-v3
E:\rag-agent-tmp\pytest-stage-d-final-v3
```

## 14. 主要代码变更

新增：

```text
backend/rag/schema/structured_evidence.py
backend/rag/schema/prompt_evidence_candidate_v1.py
backend/test_support/stage_d_fixtures.py

backend/tests/test_stage_d_final_gate_prompt_visibility.py
backend/tests/test_stage_d_structured_evidence_projection.py
backend/tests/test_stage_d_candidate_profile_consistency.py
backend/tests/test_stage_d_fact_status_policy.py
backend/tests/test_stage_d_negative_acceptance.py

scripts/finalize_stage_d_acceptance_v3.py
```

重写或更新：

```text
backend/rag/schema/retrieval_candidate_v2.py
backend/rag/evidence/governance_v2.py
backend/rag/application/retrieval_scope.py
backend/rag/mapping/result_mapper.py
backend/rag/context/context_packer.py
backend/rag/retriever/milvus_child_retriever.py
backend/rag/retriever/bm25_child_retriever.py
backend/rag/schema/Retrieval_Result_Schema.py
backend/rag/plugins/candidate_enrichers/plugin.py
backend/rag/schema/__init__.py

scripts/audit_stage_d_online_v2.py
scripts/run_stage_d_online_acceptance.py
```

## 15. 最终交付物

仓库：

```text
data/processed/indexes/stage_d_final_acceptance_report_v3.json
docs/EVIDENCE_GATE_STAGE_D_FINAL_GATE_SEMANTIC_CLOSURE_V3.md
```

E 盘运行证据：

```text
E:\rag-agent-runtime\stage_d_final_acceptance_report_v3.json
E:\rag-agent-runtime\stage_d_final_active_audit_v3.json
E:\rag-agent-runtime\stage_d_final_runtime_probe_v3.json
E:\rag-agent-runtime\stage_d_final_runtime_capture_v3.jsonl
```

## 16. Stage 边界

Stage D.1 已完成：

```text
Canonical Retrieval Scope 正确
Candidate 与 Profile 全量一致
真实 Structured Evidence 在线贯穿
Final Gate 只读取 Prompt-visible Evidence
空 Prompt 不回退
截断内容不计入覆盖
Fact Status 正式进入 Gate
真实结构化冲突可检测
Evidence Bundle 发布最终权威 Gate
Active Index 全量审计通过
全部负向验收通过
全部相关回归通过
```

尚未开始 Stage E：

```text
Writer 强制消费 allowed_claim_types
qualified 限定语自动注入
blocked / human_review 正文拒绝
生成后 Claim Audit
Document Hard Gate 聚合 Writer 执行结果
```

Stage E 必须只消费 Evidence Bundle 中最终权威字段：

```text
gate_decision.delivery_mode
gate_decision.allowed_claim_types
gate_decision.forbidden_claim_types
gate_decision.required_qualifiers
gate_decision.human_review_required
generation_allowed
```
