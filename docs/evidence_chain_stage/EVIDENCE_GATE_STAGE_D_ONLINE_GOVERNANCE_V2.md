# Stage D：在线 Retrieval Candidate、Evidence Assessment 与 Gate 接入 V2

## 1. 结论

Stage D 已完成在线检索与证据治理接入，并通过真实 Active Index 验证。

当前在线链路为：

```text
Trusted Retrieval Scope
→ Dense / BM25 同范围召回
→ Child Candidate Fusion / Parent Backfill / Rerank
→ RetrievalCandidateV2
→ Item EvidenceAssessmentV2
→ EvidenceSetAssessment
→ GateDecision
→ 仅 eligible 证据进入 Context Packer
→ 按最终 Prompt 可见证据重新计算 Gate
→ Evidence Bundle 发布 Gate
```

Stage D 不负责让 Writer 执行 Gate。Writer 对 `allowed_claim_types`、
`required_qualifiers`、`blocked` 和 `human_review` 的强制消费属于 Stage E。

## 2. Stage D 解决的问题

Stage C 已经把完整 `DocumentProfile` 固化到 Child、Vector、BM25 与 Milvus，
但 Stage C 结束时在线链仍有四个缺口：

1. Dense 使用 Milvus `filter_expr`，BM25 可能使用另一套或空的结构化范围；
2. 在线 Retrieval Result 没有冻结的 Candidate V2 事实源；
3. 旧 C-RAG 只判断“检索结果是否值得继续纠错”，不能决定证据允许支撑哪些 Claim；
4. Context Packer 可能只保留候选集的一部分，若 Gate 仍使用检索前集合，会把未进入 Prompt 的证据错误计入覆盖率。

Stage D 分别建立了 Canonical Scope、RetrievalCandidateV2、Evidence Governance V2，
并在 Context Packer 之后重新执行最终 Gate。

## 3. Canonical Retrieval Scope

新增：

```text
backend/rag/application/retrieval_scope.py
```

### 3.1 支持的可信过滤语法

Stage D 只接受 `RAGRequestMapper` 生成的窄语法：

```text
tenant_id == "..."
kb_id in ["..."]
file_id in ["..."]
doc_id in ["..."]
```

不支持任意调用方传入的 Milvus 表达式。

### 3.2 Dense 与 BM25 统一范围

`resolve_canonical_retrieval_scope()` 同时读取：

```text
filter_expr
keyword_scope
keyword_doc_ids
access_scope_enforced
```

行为：

- Dense 有范围、BM25 无范围：从 Dense 可信表达式派生 BM25 Scope；
- BM25 有范围、Dense 无范围：生成统一 Dense Filter；
- 两边同时存在且不一致：硬失败；
- `access_scope_enforced=true` 时缺失 tenant 或 KB：硬失败。

真实 Active Index 验证覆盖五个 KB：

```text
historical_case_kb
policy_standard_kb
project_fact_kb
technical_reference_kb
template_style_kb
```

五个范围投影全部通过；故意构造的 Dense/BM25 KB 不一致被拒绝。

## 4. RetrievalCandidateV2

新增：

```text
backend/rag/schema/retrieval_candidate_v2.py
```

Candidate V2 冻结字段包括：

```text
candidate_id
child_chunk_id
parent_chunk_id
document_id
tenant_id
kb_id
file_id

document_type
evidence_role
project_relation
project_id
authority_level
allowed_claim_types
forbidden_claim_types

profile_sha256
source_sha256
document_profile
type_aware_chunking

rank
retrieval_score
rerank_score
retrieval_sources
match_text
context_text
```

约束：

- Candidate 与 Profile 的 `document_id` 必须一致；
- Candidate 与 Profile 的 `profile_sha256/source_sha256` 必须一致；
- Profile 必须包含 Stage D 所需完整字段；
- `allowed_claim_types` 与 `forbidden_claim_types` 不得重叠；
- 缺失或损坏 Profile 不允许静默生成 Candidate。

`build_retrieval_result_v2()` 会在 Retrieval Result 的 metadata 中发布：

```text
metadata.retrieval_candidate_v2
```

若构造失败则发布：

```text
metadata.retrieval_candidate_v2_error
```

全量 Active Index 审计结果：

```text
Child Records                 10247
RetrievalCandidateV2          10247
Distinct Candidate IDs        10247
Distinct Documents               30
Distinct Profile Hashes          30
Distinct Source Hashes           30
Candidate Build Errors            0
```

## 5. Milvus `extra_json` 在线解析

修改：

```text
backend/rag/retriever/milvus_child_retriever.py
```

Dense Hit 现在解析 `extra_json`，恢复：

```text
document_profile
type_aware_chunking
```

损坏的 `extra_json` 或缺失 Profile 不再被静默视为空 metadata，而会进入 Candidate V2 错误路径。

## 6. Evidence Governance V2

新增：

```text
backend/rag/evidence/governance_v2.py
```

### 6.1 Evidence Requirements

`EvidenceRequirementsV2Schema` 冻结：

```text
policy_id
section_id
section_type
required_topics
requested_claim_types
claim_risk_level
coverage_strategy
allow_framework_only
```

`SchemeEvidenceService` 在调用 RAG 前生成章节 Evidence Requirements，放入：

```text
extra_metadata.evidence_requirements
```

显式需求优先；没有显式需求时才按 Query、章节标题和章节类型做保守规则推导。

### 6.2 Item Assessment

每个 Candidate 生成 `EvidenceAssessmentV2Schema`，分别判断：

```text
semantic_relevance
eligibility
supported_topics
supported_claim_types
forbidden_claim_types
reason_codes
```

Eligibility 同时要求：

1. Profile 允许目标 Claim；
2. Profile 未禁止目标 Claim；
3. 当前项目 Claim 必须来自 `current_project`；
4. Claim 与结构化记录类型兼容；
5. 语义相关性不低于保守阈值。

历史项目即使文本高度相关，也不能支撑当前项目数量、成本、工期、配置和事实 Claim。
模板只能支撑 `template_structure/style_guidance`。

### 6.3 Set Assessment

`EvidenceSetAssessmentSchema` 按章节聚合：

```text
required_topics
covered_topics
missing_topics
coverage_ratio
status
direct_evidence_count
partial_evidence_count
distinct_document_count
conflicts
```

支持的状态：

```text
sufficient
partial
absent
conflicted
not_assessed
```

结构化 Fact 冲突要求：同一 predicate、不同文档、不同 value，才形成显式 Conflict。

### 6.4 Gate Decision

Gate 输出 Stage A 冻结的：

```text
GateDecisionSchema
```

交付模式：

```text
grounded
qualified
framework_only
blocked
human_review
```

主要规则：

- 充分且证据权威：`grounded`；
- 充分但含历史、参考或草案材料：`qualified`；
- 低/中风险部分覆盖：`qualified` + qualifier；
- 高/关键风险部分覆盖：`human_review`；
- 冲突：`human_review`；
- 无事实证据但模板 Claim 合法：`framework_only`；
- 其余缺失：`blocked`。

## 7. Context Gate 前后双重评估

这是 Stage D 的核心语义。

### 7.1 Pre-context Gate

Rerank 后对全部结果评估：

```text
retrieval.results
→ governance_v2_pre_context
→ eligible_retrieval_results
```

只有 eligible 结果进入 Context Packer。

### 7.2 Final Bundle Gate

Context Packer 可能因 Token、字符、条目数、去重和位置策略丢弃部分 eligible 证据。
因此 `RAGResultMapper` 会对 `selected_results` 再执行一次 Governance：

```text
selected_results
→ final EvidenceSetAssessment
→ final GateDecision
→ Evidence Bundle
```

这防止“检索到了但模型没看到”的证据被错误用于放行。

## 8. Evidence Bundle 发布

Evidence Bundle 的 `extra` 发布：

```text
evidence_governance_v2
gate_decision
generation_allowed
```

`assessment.judge_name` 固定为：

```text
document_profile_gate_v2
```

旧 C-RAG Assessment 被保留在：

```text
assessment.details.legacy_corrective_assessment
```

其用途只剩：是否值得进行纠错检索。它不能覆盖 V2 Claim Gate。

不合格证据不会进入 Context 和 Citation，但仍会作为 Dropped Evidence 保留，
`drop_reason=evidence_ineligible_v2`，用于审计。

## 9. 真实 Active Index 验证

Active Index：

```text
idx_type_aware_parent_child_v2_m3e_base_local_m3e_base_profiled_v2_f382dcfcb28e
```

Manifest SHA-256：

```text
18d1aedb6bdd1fe6e6093e5879e0f205808482010ff8d48b8a9215b55287fc02
```

### 9.1 全量 Candidate 审计

```text
10247 / 10247 Candidate V2 构造成功
Profile 缺失                          0
Candidate Contract Error              0
unknown / unclassified                0
Gold / non-indexable                  0
历史材料当前项目 Claim 泄漏             0
模板非模板 Claim 泄漏                   0
当前 Claim 项目关系错误                  0
Profile 标量镜像不一致                   0
```

KB 分布：

```text
historical_case_kb       3533
policy_standard_kb        490
project_fact_kb          5140
technical_reference_kb    156
template_style_kb         928
```

### 9.2 在线 Runtime Scope 验证

Query：

```text
大件运输许可系统升级项目预算金额和投资估算
```

仅向 Runtime 传入 Dense Filter：

```text
tenant_id == "default" and kb_id in ["project_fact_kb"]
```

不传 Keyword Scope，Stage D 自动派生。

结果：

```text
P2 Results                 5
Final Retrieval Results    5
Candidate V2               5
Complete Profile           5
Returned KB                project_fact_kb only
Candidate Contract Error   0
```

Pre-context Gate：

```text
eligible candidates      2
ineligible candidates    3
coverage ratio         1.0
status           sufficient
delivery mode    qualified
```

3 条候选是因 Claim/语义不适配被正确拒绝，不是 Candidate 构造失败。

### 9.3 Evidence Bundle 最终 Gate 验证

Context Packer 最终只保留 1 条可用证据，另一主题未进入 Prompt：

```text
selected evidence      1
dropped evidence       4
citations              3
Candidate V2           5
Complete Profile       5
selected KB            project_fact_kb only
```

最终 Gate：

```text
coverage ratio          0.5
covered topic           预算金额
missing topic           投资估算
status                  partial
claim risk              high
delivery mode           human_review
generation_allowed      false
```

这不是失败，而是 Stage D 应有的保守行为：高风险成本 Claim 只有部分 Prompt 可见证据，
必须转人工复核，不能因为检索前候选集合曾覆盖完整而放行。

同一次运行中旧 C-RAG 给出：

```text
insufficient
```

该值被保留用于检索质量审计，但最终 Claim 权限由 `document_profile_gate_v2` 决定。

## 10. 测试

Stage D 专项：

```text
16 passed
```

最终相关回归：

```text
366 passed
0 failed
3 warnings
117.79 seconds
```

覆盖：

```text
Stage A/B/C/D Contracts
Canonical Retrieval Scope
Milvus Authorization Probe
Retrieval Candidate V2
Evidence Governance V2
RAG Plugin Architecture
Query Transform / Dense / BM25 / RRF / Parent Backfill
Reranker / CRAG Correction
Context Packer
Evidence Contract
SchemeEvidenceService
Agent-RAG V1
Section Mainline
Document Assembler
Mainline Closure
Real RAG Runtime
```

警告仅来自 Transformers Cache 与 FAISS/SWIG 弃用信息。

## 11. 主要变更文件

新增：

```text
backend/rag/application/retrieval_scope.py
backend/rag/schema/retrieval_candidate_v2.py
backend/rag/evidence/governance_v2.py
backend/tests/test_stage_d_retrieval_candidate_and_scope.py
backend/tests/test_stage_d_evidence_governance_v2.py
backend/tests/test_stage_d_online_bundle_integration.py
scripts/audit_stage_d_online_v2.py
scripts/run_stage_d_online_acceptance.py
```

修改：

```text
backend/rag/mapping/request_mapper.py
backend/rag/application/parent_child_retrieval.py
backend/rag/retriever/milvus_child_retriever.py
backend/rag/schema/Retrieval_Result_Schema.py
backend/rag/plugins/candidate_enrichers/plugin.py
backend/rag/retriever/hybrid_parent_child_retriever.py
backend/rag/rag_engine/parent_child_rag_engine.py
backend/rag/mapping/evidence_mapper.py
backend/rag/mapping/result_mapper.py
backend/rag/runtime/retrieval_runtime.py
backend/apps/enterprise_document/services/scheme_writer/evidence_service.py
backend/tests/helpers/milvus_scope_probe.py
```

## 12. 交付物

仓库内：

```text
data/processed/indexes/stage_d_online_acceptance_report_v2.json
docs/EVIDENCE_GATE_STAGE_D_ONLINE_GOVERNANCE_V2.md
```

运行证据：

```text
E:\rag-agent-runtime\stage_d_online_acceptance_report_v2.json
E:\rag-agent-runtime\stage_d_online_runtime_probe_v2.json
E:\rag-agent-runtime\stage_d_online_runtime_capture_v2.jsonl
```

## 13. Stage E 入口

Stage E 必须直接消费 Evidence Bundle 中的最终：

```text
gate_decision.delivery_mode
gate_decision.allowed_claim_types
gate_decision.forbidden_claim_types
gate_decision.required_qualifiers
gate_decision.human_review_required
```

Stage E 不得重新根据“有无检索结果”推断是否可写，也不得使用旧 C-RAG sufficient 状态
替代 GateDecision。

Stage E 最小闭环：

1. Writer 输入绑定最终 Gate；
2. `blocked/human_review` 时禁止普通章节正文提交；
3. `qualified` 自动注入限定语；
4. 每个生成 Claim 标注 Claim Type；
5. Post-generation Claim Audit 检查 Claim 是否在 allowed 集合；
6. Document Hard Gate 聚合章节 Gate 与人工复核状态。
