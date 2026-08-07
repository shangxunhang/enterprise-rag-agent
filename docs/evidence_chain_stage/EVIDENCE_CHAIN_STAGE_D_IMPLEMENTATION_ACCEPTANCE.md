# Stage D 实施与验收证据：Online Evidence Governance

> 记录日期：2026-08-04  
> 阶段状态：**已完成并通过**  
> 在证据链中的位置：对 Stage C 的候选证据执行在线访问范围、身份一致性、Prompt 可见性、事实状态、冲突和最终 GateDecision 治理。

## 1. Stage D 解决了什么问题

Stage C 已经建立了带完整 Profile 的离线索引，但“被检索到”不等于“有资格支持当前生成”。在线链路仍需回答：

- Dense 和 BM25 是否使用同一授权范围；
- Candidate 是否与 Stage C 的 Document Profile 完全一致；
- 模型实际看到的是否仍是完整证据；
- 被 Context Packer 截断的事实能否继续计算覆盖；
- `estimated/unknown/confirmed/decided` 等事实状态如何影响 Claim；
- 多份结构化材料是否冲突；
- 最终应该 `grounded`、`qualified`、`blocked` 还是 `human_review`。

Stage D 将相关性检索结果转化为具有明确 Claim 权限的 Evidence Bundle。

## 2. 最终在线链路

```text
Trusted Access Scope
→ Canonical Retrieval Scope
→ Dense / BM25 同范围召回
→ RRF / Parent Backfill / Rerank
→ RetrievalCandidateV2
→ Retrieval-view Governance
→ Eligible Retrieval Results
→ Context Packer
→ PromptEvidenceCandidateV1
→ Prompt-view Governance
→ EvidenceSetAssessment
→ GateDecision
→ Evidence Bundle
```

最终 Claim 权限只能来自：

```text
Document Profile
+ Structured Evidence
+ Prompt-visible Evidence
+ Fact Status / Authority Policy
+ Evidence Set Assessment
+ Gate Policy
```

旧 C-RAG 只允许决定是否触发 Corrective Retrieval，不能改变最终 Claim 权限。

## 3. 实际实施内容

### 3.1 Canonical Retrieval Scope

新增严格结构：

```text
StructuredRetrievalScopeSchema
```

只允许：

```text
tenant_id
kb_ids
file_ids
doc_ids
access_scope_enforced
```

未知字段硬失败。KB/File/Doc ID 使用集合语义进行 strip、去重和排序，因此顺序变化不会改变 Scope Fingerprint。

授权模式要求：

```text
tenant_id 必须存在
至少一个 kb_id 必须存在
Dense/BM25 范围冲突必须失败
```

### 3.2 RetrievalCandidateV2 与 Profile 强一致

Candidate 逐项核验：

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

任何不一致都会拒绝 Candidate，并保留：

```text
rejected_candidate_id
candidate_contract_errors
```

禁止构造“标量来自 Hit、权限来自另一份 Profile”的混合对象。

### 3.3 Structured Evidence 在线贯穿

统一结构：

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

在线贯穿路径：

```text
Stage C Child.extra
→ Dense/BM25 Hit
→ Retrieval Result V2
→ RetrievalCandidateV2
→ PromptEvidenceCandidateV1
→ Item Assessment
→ Evidence Set Assessment
→ Evidence Bundle
```

Dense 和 BM25 使用同一个投影函数，避免分支语义漂移。

### 3.4 Final Gate 只读取 Prompt-visible Evidence

新增：

```text
backend/rag/schema/prompt_evidence_candidate_v1.py
```

每条 Prompt Candidate 显式记录：

```text
prompt_visible_text
prompt_visible_char_count
prompt_visible_token_count
context_rank
context_truncated
truncated_source_id
truncation_reason
fact_visible_in_prompt
retrieval_candidate
structured_evidence
profile_sha256
source_sha256
```

Final Gate 不再从 Parent/Child 原文猜测模型看到了什么，只接受 Context Packer 产出的 Prompt-visible Contract。

以下情况硬失败：

```text
selected_results 非空但 context 为空
selected_results 为空但 context 非空
prompt_visible_text 不在 context 中
Prompt Candidate 与 Retrieval Candidate 身份或 Hash 不一致
```

### 3.5 空 selected_results 不回退

```text
selected_results = []
```

是最终 Prompt 事实，不允许从早期 `retrieval_results` 回捞。

结果必须是：

```text
Prompt Candidate Count 0
Evidence Status        absent
Delivery Mode          blocked
Allowed Claim Types    []
Generation Allowed     false
```

强门禁模式下字段缺失也必须阻断。旧兼容回退即使成功，也只能进入 `qualified + human_review`。

### 3.6 截断证据不获得 Claim 权限

当完整 Parent 末尾存在金额，但 Prompt 只保留前部并显示 `[TRUNCATED]` 时：

```text
context_truncated      true
fact_visible_in_prompt false
金额 Topic covered     false
generation_allowed     false
```

检索命中可以保留用于审计，但不能用于 Claim Support。

### 3.7 Fact Status 与 Authority 联合决策

统一策略输入：

```text
claim_type
fact_status
authority_level
document_type
source_document_form
project_relation
```

核心行为：

| Fact Status | 行为 |
|---|---|
| unknown | 禁止确定性 Claim，默认 blocked |
| estimated | 只允许估算表达，必须 qualified |
| proposed | 必须使用“拟/尚待确认” |
| planned | 必须使用“计划/尚待确认” |
| confirmed | 仍需当前项目和正式 Authority |
| decided | 还需正式决策型材料形态 |

`other_project` 永远不能支持 current-project Claim。

真实 estimated 采购预算场景：

```text
delivery_mode         qualified
generation_allowed    true
human_review_required true
required_qualifiers:
  根据现有估算材料
  最终金额以审批或采购结果为准
```

真实 unknown 预算场景：

```text
selected_count       0
status               absent
delivery_mode        blocked
generation_allowed   false
```

### 3.8 结构化冲突检测

冲突要求：

```text
同一 Claim Type
同一规范化 Predicate
至少两个不同 Document
不同规范化 Value
```

明确单位可以安全换算，例如：

```text
250万元 == 2500000元
```

带“约、不超过、以上”等范围语义的值不做智能合并。

两个当前项目文档分别声明预算 250 万元和 300 万元时：

```text
Evidence Status        conflicted
Delivery Mode          human_review
Allowed Claim Types    []
Generation Allowed     false
```

### 3.9 Evidence Bundle 成为最终 Gate 载体

发布：

```text
extra.evidence_governance_v2
extra.gate_decision
extra.generation_allowed
extra.prompt_evidence_contract
extra.prompt_evidence_source
```

不合格证据：

- 不进入 Context；
- 不获得 Citation；
- 作为 Dropped Evidence 保留；
- 记录明确 `context_drop_reason`。

## 4. Active Index 全量审计结果

```text
Child Records                    10247
RetrievalCandidateV2             10247
Distinct Candidate IDs           10247
Distinct Documents                  30
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

全量违规：

```text
Profile 标量不一致               0
Dense/BM25 Structured 不一致     0
重复 Candidate ID                0
kb_id=default                    0
Gold/non-indexable               0
历史材料当前项目 Claim 泄漏       0
模板非模板 Claim 泄漏             0
当前 Claim 项目关系错误           0
```

## 5. 验收标准

Stage D 必须证明：

1. Dense/BM25 使用同一 Canonical Scope；
2. Candidate 身份和 Claim 能力与 Document Profile 全量一致；
3. Final Gate 只读取 Prompt 实际可见内容；
4. 空 Prompt 不从早期检索结果回退；
5. 截断内容不计入覆盖和 Claim Support；
6. Structured Fact/Table/Policy/Template/Fallback 在线贯穿；
7. Fact Status 与 Authority 联合决定 Delivery Mode；
8. 多文档结构化事实冲突可检测；
9. Evidence Bundle 发布唯一权威 Gate；
10. 全量 Active Index Candidate 构建无错误；
11. 所有负向场景 fail-closed。

## 6. 验收结果

### 6.1 Runtime / Negative Acceptance

Runtime 包含：

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

13 项 Hard Failure 全部为 `false`，包括：

```text
empty_selected_results_fell_back
missing_selected_results_not_blocked
truncated_topic_counted_as_visible
structured_evidence_projection_missing
dense_bm25_structured_evidence_mismatch
candidate_profile_scalar_mismatch_not_rejected
real_fact_conflict_not_detected
unknown_fact_status_allowed_as_grounded
estimated_fact_missing_qualifier
prompt_context_selected_records_inconsistent
active_runtime_estimated_scenario_failed
active_runtime_unknown_scenario_failed
active_index_audit_failed
```

### 6.2 测试

```text
Stage D Focused  59 passed / 0 failed / 0 errors
相关 Regression 409 passed / 0 failed / 0 errors
```

警告仅来自 Transformers Cache 和 FAISS/SWIG 弃用。

### 6.3 索引未变化

```text
Index Version:
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e

Manifest SHA-256:
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02

Pointer SHA-256:
0e77cc066d3dfc47875e7991e22d41828bc5ad531cb2cbb134e6096c6e6772ce
```

Stage D 没有重建或修改 Stage C 索引。

## 7. 主要代码位置

```text
backend/rag/schema/retrieval_candidate_v2.py
backend/rag/schema/structured_evidence.py
backend/rag/schema/prompt_evidence_candidate_v1.py
backend/rag/application/retrieval_scope.py
backend/rag/evidence/governance_v2.py
backend/rag/mapping/result_mapper.py
backend/rag/context/context_packer.py
backend/rag/retriever/milvus_child_retriever.py
backend/rag/retriever/bm25_child_retriever.py
```

## 8. 验收证据位置

```text
docs/EVIDENCE_GATE_STAGE_D_ONLINE_GOVERNANCE_V2.md
docs/EVIDENCE_GATE_STAGE_D_FINAL_GATE_SEMANTIC_CLOSURE_V3.md

data/processed/indexes/stage_d_final_acceptance_report_v3.json
SHA-256: 85593491aff2b28fd8be1f5e52c0cafa826b9dbe3c6a33334d209aadf98a7df9

E:\rag-agent-runtime\stage_d_final_acceptance_report_v3.json
E:\rag-agent-runtime\stage_d_final_active_audit_v3.json
E:\rag-agent-runtime\stage_d_final_runtime_probe_v3.json
E:\rag-agent-runtime\stage_d_final_runtime_capture_v3.jsonl
```

## 9. 本阶段没有实施什么

Stage D 没有：

```text
强制 Writer 消费 GateDecision
Qualified 限定语写入正文
Blocked/Human Review 跳过模型
生成后 Claim Audit
Document Hard Gate 聚合
不可变正式发布
```

这些属于 Stage E–F。

## 10. 最终结论

Stage D 已完成并通过。它能够证明：

> 最终 GateDecision 不是由“检索到了相关文本”直接产生，而是由授权范围、Document Profile、结构化证据、Prompt 实际可见内容、Fact Status、Authority、覆盖和冲突共同计算；不在 Prompt 中的事实不能获得生成权限。

Stage E 只能消费 Evidence Bundle 中最终权威的 GateDecision 和 generation_allowed，不得让旧 Assessment 重新夺权。
