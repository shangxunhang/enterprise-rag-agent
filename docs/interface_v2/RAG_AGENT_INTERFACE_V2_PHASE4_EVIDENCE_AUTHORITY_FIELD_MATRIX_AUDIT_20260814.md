# RAG-Agent Interface V2 Phase 4 — Evidence Authority Field Matrix Audit

> Audit date: 2026-08-14  
> Baseline commit: `f43f959 checkpoint: complete rag-agent interface v2 phases 1-3`  
> Scope: Phase 4 only  
> Mode: audit only, no production code changes

---

## 1. 本轮目标

本轮只回答四个问题：

1. 当前生产链中，谁有能力创建、修改、恢复或重新定义 Evidence truth？
2. 这些 authority candidate 各自保存哪些字段？
3. 已冻结的 `EvidenceBundleSchema` 是否足以承担唯一 canonical Evidence authority？
4. 如果不足，最小需要补哪些 **Evidence-domain typed fields**；哪些字段必须继续留在 Application / Workflow / Projection 侧？

本轮不实施 Phase 4B/4C，不修 Gate，不修 Evidence/Citation/Chunk ID namespace，不清理 RAG internal imports，不处理 Facade。

---

## 2. 已冻结前提

Phase 1–3 已接受，以下前提不重新讨论：

```text
RAGServicePort.retrieve(RAGToolInputSchema)
    -> EvidenceBundleSchema
```

以及：

```text
EvidenceBundleSchema
= 唯一 canonical EvidencePackage
```

`RAGEvidenceContractSchema` 只是 `EvidenceBundleSchema` 的 import compatibility alias，不是第二套 Schema。

Gate authority 已在 Phase 3 迁移到 Application：

```text
RAG
-> Evidence facts

Application
-> EvidenceGatePolicy
-> GateDecision / DeliveryMode / generation action
```

因此 Phase 4 不能把 `generation_allowed / gate_decision / human_review_required / DeliveryMode` 等字段重新塞回 Evidence authority。

---

## 3. Authority 判定标准

本轮不把“保存过 Evidence 字段”自动判定为 authority。

只有满足以下任一能力的对象/路径，才进入 authority candidate 审计：

1. 能创建新的 `EvidenceBundleSchema`；
2. 能从 projection / dict / extra 恢复 `EvidenceBundleSchema`；
3. 能修改 `items / citations / context / assessment / lineage` 后重新产出 Bundle；
4. 被代码或注释声明为 Evidence truth；
5. 在 checkpoint/resume 后可以独立恢复出被下游当作 canonical 的完整 Evidence 数据。

纯 summary、hash、ref、workflow state、prompt projection 不自动成为 authority。

---

# 4. Authority Candidate Inventory

## 4.1 Candidate A — RAG `EvidenceBundleSchema`

位置：

- `backend/schemas/rag.py`
- `EvidenceBundleSchema`

当前注释明确：

```text
跨越 RAG/Agent 边界的唯一规范证据包
```

并明确：

```text
items / selected-dropped ids / citations / lineage / assessment
= structured facts

context / RAGToolOutput legacy fields
= deterministic projections
```

### 判定

**CANONICAL AUTHORITY — 保留。**

这是 Phase 4 最终唯一 authority。

---

## 4.2 Candidate B — `RAGResultMapper + RAGEvidenceContractBuilder`

位置：

- `backend/rag/mapping/result_mapper.py`
- `backend/rag/evidence/contract.py`

生产主链：

```text
raw retrieval result
    -> RAGResultMapper
    -> RAGEvidenceContractBuilder.build(...)
    -> EvidenceBundleSchema
```

`ResultMapper` 再写入：

- `task_id`
- `run_id`
- `status`
- `retrieval_trace_id`
- `correction_trace`
- `assessment`
- `budget_usage`
- `trace`
- `extra`

### 判定

**LEGITIMATE CANONICAL PRODUCER — 保留。**

正常生产 EvidenceBundle 的主要 producer。

---

## 4.3 Candidate C — `RAGService` failure/fallback bundle constructors

位置：

- `backend/rag/services/rag_service.py`

`RAGService.retrieve()` 捕获 RAG 边界失败后，会直接构造一个 failed `EvidenceBundleSchema`。

`_bundle_from_tool_output()` 在旧 ToolOutput 未携带 bundle 时，也会补一个最低限度 Bundle。

### 判定

**LEGITIMATE RAG-BOUNDARY PRODUCER — 保留。**

它没有创造第二套 authority，而是在同一 public contract 上构造失败/兼容结果。

---

## 4.4 Candidate D — `LegacyEvidenceAdapter`

位置：

- `backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py`

它有两种行为：

### D1. 旧 payload 已有 `evidence` dict

```text
payload["evidence"]
-> RAGEvidenceContractSchema.model_validate(...)
-> canonical Bundle
```

### D2. 只有旧 chunks / citations

```text
legacy chunks/citations
-> RAGEvidenceContractBuilder.build(...)
-> canonical Bundle
```

### 判定

**ALLOWED COMPATIBILITY PRODUCER — 保留，但严格限于 legacy edge。**

允许它作为旧协议升级边界创建 canonical Bundle。

但它当前随后又把完整 Bundle 写回：

```text
context.extra["evidence_contract"]
normalized["evidence"]
```

这部分不是合法 authority 行为，应在 Phase 4C 去除。

---

## 4.5 Candidate E — `RAGToolOutputSchema.evidence`

位置：

- `backend/schemas/rag.py`
- `RAGToolOutputSchema`

它的 typed field：

```python
evidence: Optional[EvidenceBundleSchema]
```

Schema 自己明确：

```text
EvidenceBundle 是事实源
context/citations 等为兼容投影
```

### 判定

**TYPED CARRIER — 不是第二 authority。**

可以保留。

---

## 4.6 Candidate F — `SchemeEvidenceService.extract_rag_output().normalized["evidence"]`

位置：

- `backend/apps/enterprise_document/services/scheme_writer/evidence_service.py`

当前行为：

```text
EvidenceBundleSchema
-> contract.model_dump()
-> normalized["evidence"]
```

同时写：

- `normalized["context"]`
- `normalized["retrieved_chunks"]`
- `normalized["citations"]`
- `normalized["trace"]`

### 判定

**FULL SERIALIZED COPY / COMPATIBILITY CARRIER — 当前不是 producer，但具备形成第二 authority 的风险。**

如果下游把它当成 canonical 并重新 `model_validate`，它就从 projection/copy 升格成了 authority recovery path。

Phase 4 目标不是立即删除所有 normalized 输出，而是：

```text
normalized output 不得再承担 canonical ownership
```

---

## 4.7 Candidate G — `RAGContext.extra["evidence_contract"]`

生产写入位置：

- `SchemeEvidenceService.extract_rag_output()`
- `LegacyEvidenceAdapter.extract()`
- `DocumentCitationRegistry.remap_bundle()`

当前保存：

```text
完整 EvidenceBundle model_dump
```

而且被以下路径反向读取：

- `DocumentCitationRegistry`
- `SectionGenerationContextPolicy`
- `StageService.build_generation_report`

### 判定

**ILLEGAL FULL-DUMP AUTHORITY COPY — Phase 4 必须移除。**

`RAGContextSchema` 的职责是最终有界模型上下文 projection，不应内嵌完整 canonical package。

允许保留 projection-only metadata，例如：

- render metadata
- citation remap metadata
- context packing metadata

但不允许保存完整 Bundle 用于反向恢复 authority。

---

## 4.8 Candidate H — `ContextBundle.evidence.contract`

位置：

- `backend/schemas/context.py::EvidenceContextSchema`
- `backend/agent/runtime/state_access.py::set_evidence_context`

当前 Schema 注释直接写：

```text
contract 是结构化证据真相
```

字段：

```python
contract: Dict[str, Any]
```

`SharedStateWriter` 会执行：

```python
evidence.contract = dict(evidence_contract or {})
```

### 判定

**DUPLICATE AUTHORITY — Phase 4 必须降级。**

它与已冻结：

```text
EvidenceBundleSchema = 唯一 canonical Evidence truth
```

直接冲突。

`ContextBundle.evidence` 可以继续存在，但只能作为 Application shared-context projection，不得再声明/保存第二套 full canonical contract。

---

## 4.9 Candidate I — `DocumentCitationRegistry.remap_bundle()`

位置：

- `backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py`

当前流程：

```text
RAGContext.extra["evidence_contract"]
    -> dict
    -> 修改 citations
    -> 修改 item.citation_ids
    -> 修改 context.context_text
    -> RAGEvidenceContractSchema.model_validate(...)
    -> 新 Bundle
    -> normalized["evidence"]
    -> context.extra["evidence_contract"]
```

它还重新计算：

```text
evidence_contract_sha256
```

### 判定

**ILLEGAL APPLICATION-SIDE CANONICAL MUTATOR / REBUILDER — Phase 4 必须关闭。**

CitationRegistry 的合法职责是：

```text
canonical local citation ids
-> Application document-level citation projection
-> id_map / remapped context / remapped citations
```

它不应改写 canonical EvidenceBundle。

---

## 4.10 Candidate J — `SectionGenerationContextPolicy._evidence_contract()`

位置：

- `backend/context_manager/policies.py`

当前流程：

```text
rag_context.extra["evidence_contract"]
-> RAGEvidenceContractSchema.model_validate(...)
-> 遍历 selected_evidence_ids
-> 生成 ContextItemSchema
```

### 判定

**ILLEGAL CANONICAL RECOVERY CONSUMER — Phase 4 必须改为直接消费 typed EvidenceBundle 或其显式 projection。**

它没有修改 Bundle，但它证明 `RAGContext.extra` 已经被当作 authority source 使用。

---

## 4.11 Candidate K — `WriterGovernance.extract_writer_gate_binding()`

位置：

- `backend/apps/enterprise_document/services/scheme_writer/writer_governance.py`

当前流程：

```text
normalized_output["evidence"]
-> EvidenceBundleSchema.model_validate(...)
-> EvidenceGatePolicy.evaluate(evidence_bundle.assessment)
```

### 判定

**CANONICAL RECOVERY CONSUMER — Phase 4 应改为直接接收 typed Bundle。**

Gate ownership 本身正确，问题仅是 Evidence carrier 仍是 dict copy。

---

## 4.12 Candidate L — Stage / Section `normalized["evidence"]` readers

发现位置包括：

- `stage_service.py`
- `section_execution_coordinator.py`
- `section_execution_graph.py`
- `section_agent_ports.py`

主要读取：

```text
assessment
extra
hash
selected/candidate information
```

### 判定

**SERIALIZED COPY CONSUMERS — 不是独立 producer，但应迁移到 typed canonical carrier。**

---

## 4.13 Candidate M — Checkpoint `document_rag_output / all_rag_outputs / rag_outputs`

Schema：

- `SchemeGenerationGraphPayloadSchema.document_rag_output`
- `SchemeGenerationGraphPayloadSchema.all_rag_outputs`
- `SectionExecutionGraphPayloadSchema.document_rag_output`
- `SectionExecutionGraphPayloadSchema.rag_outputs`

这些字段是 `dict[str, Any]`，其中继续嵌套：

```text
["evidence"] = full EvidenceBundle dump
```

### 判定

**PERSISTED FULL-COPY CARRIER — 当前会让 resume 后继续具备第二 authority recovery 能力。**

Phase 4B 应增加显式 typed canonical package carrier；Phase 4C 再让这些 dict 输出退回兼容/观测用途。

---

## 4.14 Candidate N — `SectionEvidenceBundleSchema`

位置：

- `backend/apps/enterprise_document/schemas/scheme_writer/evidence.py`

字段：

- section_id
- section_title
- retrieval_scope
- query
- tool_call_ids
- rag_context
- retrieved_chunks
- citations
- recovery_count
- evidence_contract_sha256
- writer_gate_binding
- generation_allowed
- metadata

### 判定

**WORKFLOW PROJECTION — 不是 canonical Evidence authority。**

虽然名字含 `Bundle`，但实际内容是“这个 Section 最终使用了什么证据投影 + workflow/gate metadata”。

当前没有发现它被反向 `model_validate` 成 `EvidenceBundleSchema`。

因此：

- 不需要在 Phase 4 强制改名；
- 必须在注释/边界测试中明确它不是 canonical EvidencePackage；
- 其中 `writer_gate_binding / generation_allowed` 必须继续属于 Application workflow，不得迁回 EvidenceBundle。

---

## 4.15 Candidate O — `ResearchEvidencePackageSchema`

位置：

- `backend/apps/enterprise_document/schemas/agent_rag_v1/payloads.py`

字段：

- section_id
- accepted_evidence_ids
- rejected_evidence
- supported_topics
- evidence_gaps
- conflicts
- research_status
- recommended_action

### 判定

**RESEARCH SUMMARY / ACTION PROJECTION — 不是 canonical EvidencePackage。**

名字含 `Package`，但语义是 Agent result summary。

其中：

- accepted/rejected IDs 应来自 canonical Evidence；
- gaps/conflicts 应投影 canonical assessment；
- research_status/recommended_action 属于 Agent/Application decision。

它不应反向定义 Evidence facts。

---

## 4.16 Candidate P — `ResearchStateSchema`

位置：

- `backend/apps/enterprise_document/schemas/scheme_writer/state_projection.py`

Schema 注释明确：

```text
Typed projection of section retrieval and evidence assessment
```

字段：

- status
- evidence_package_ref
- evidence_contract_sha256
- accepted_evidence_ids
- rejected_evidence_ids
- evidence_gaps
- research_rounds
- tool_call_ids
- recommended_action
- assessment
- metadata

### 判定

**HEALTHY WORKFLOW PROJECTION — 保留。**

问题不在它保存 projection，而在当前：

```text
evidence_package_ref = state://section-evidence/...
```

没有真正 resolver/load path；hash 又从 `RAGContext.extra` 获取。

Phase 4 需要让这些 projection 指向/来源于真实 canonical package carrier。

---

## 4.17 Candidate Q — `MainlineResearchRetrieval`

位置：

- `backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_ports.py`

当前字段：

- success
- context
- chunks
- citations
- citation_registry
- normalized
- assessment
- retrieval_trace_id
- error_message

### 判定

**TRANSIENT CARRIER，但当前缺 typed `EvidenceBundleSchema`。**

这是 Phase 4 的真实传播缺口。

Research 生产主链拿到的不是 canonical object，而是：

```text
context + chunks + citations + normalized dict + assessment dict
```

导致后续只能继续依赖副本和 hash/ref。

---

## 4.18 Candidate R — retired Standalone `artifact_type="evidence_package"`

位置：

- `backend/apps/enterprise_document/runtime/agent_rag_v1/standalone_adapters.py`

对应 runtime / orchestrator / application_service 文件头已经明确：

```text
Archived fixture
retired Standalone runtime
```

### 判定

**ARCHIVED FIXTURE — 不进入生产 Phase 4 migration。**

它存储的是 `ResearchEvidencePackageSchema` summary，不是当前 RAG canonical EvidenceBundle。

---

## 4.19 Candidate S — Fake RAG producer

位置：

- `backend/tools/fake_rag_tool.py`

### 判定

**TEST/FAKE PRODUCER — 保持与 canonical schema 对齐，但不是生产 authority owner。**

---

# 5. Authority Candidate 总结

| Candidate | 当前角色 | 是否能定义/恢复 canonical | Phase 4 目标 |
|---|---|---:|---|
| EvidenceBundleSchema | 唯一 public contract | 是 | 保留唯一 authority |
| RAGResultMapper / Builder | 正常 producer | 是 | 保留 |
| RAGService failure/fallback | RAG 边界 producer | 是 | 保留 |
| LegacyEvidenceAdapter | legacy compatibility producer | 是 | 保留为唯一兼容例外 |
| RAGToolOutput.evidence | typed carrier | 否 | 保留 |
| normalized["evidence"] | full serialized copy | 可被恢复 | 降级成兼容输出 |
| RAGContext.extra.evidence_contract | hidden full copy | 是 | 删除 full dump |
| ContextBundle.evidence.contract | duplicate truth | 是 | 降级 |
| DocumentCitationRegistry | Application mutator/rebuilder | 是 | 禁止改 canonical |
| ContextManager recovery | recovery consumer | 是 | 改 typed consume |
| WriterGovernance recovery | recovery consumer | 是 | 改 typed consume |
| checkpoint rag_outputs | persisted copies | 可被恢复 | typed canonical carrier 优先 |
| SectionEvidenceBundle | workflow projection | 否 | 保留 projection |
| ResearchEvidencePackage | research summary/action | 否 | 保留 projection |
| ResearchState | typed workflow projection | 否 | 保留 projection |
| MainlineResearchRetrieval | transient carrier | 当前没有 canonical | 增加 typed carrier |
| retired Standalone artifact | archived fixture | 非生产 | 不迁移 |

---

# 6. Canonical `EvidenceBundleSchema` 当前字段盘点

## 6.1 Package identity / execution

现有：

- `schema_version`
- `query`
- `rewritten_queries`
- `task_id`
- `run_id`
- `status`
- `retrieval_trace_id`

## 6.2 Evidence item truth

现有：

- `items[]`
- `selected_evidence_ids[]`
- `dropped_evidence_ids[]`

`RAGEvidenceItemSchema` 已包含：

- evidence_id
- disposition
- rank / pre_context_rank
- matched_chunk_id / context_chunk_id
- child_chunk_id / parent_chunk_id
- doc_id
- match_text / context_text
- title / section / page_start / page_end
- score / score_type
- rerank_score / rerank_score_type
- retrieval_sources
- citation_ids
- drop_reason
- metadata / extra

### 结论

Evidence identity / source / ranking / retrieval / grounding 字段已经足够丰富。

没有发现需要从 Section/Research projection 反向补进 item schema 的生产字段。

---

## 6.3 Citation truth

现有：

- `citations[]`
- item `citation_ids[]`

### 结论

RAG canonical Bundle 已能描述 RAG 返回的 citation relationship。

Application 的 document-global `C1/C2/...` remap 应是下游 projection，不应修改这套 canonical citation identity。

---

## 6.4 Context projection

现有：

- `context: RAGContextSchema`

包含：

- context_text
- used_context_chunk_ids
- matched_chunk_ids
- used_doc_ids
- max_context_chars
- used_context_chars
- context_item_count
- token_budget
- tokens_used
- truncated_item_ids
- context_format
- extra

### 结论

上下文 projection 本身字段完整。

缺口不是 Context 字段不足，而是 Application 把 full EvidenceBundle 又塞回 `context.extra`。

---

## 6.5 Lineage

现有 `RAGEvidenceLineageSchema`：

- index_name
- index_version
- dataset_version
- vector_db
- embedding_model
- embedding_version
- embedding_dim
- reranker_model
- reranker_version
- retrieval_plan_id
- static_retrieval_spec_id
- static_retrieval_spec_version
- static_retrieval_spec_hash
- extra

### 结论

Lineage 已经足够覆盖当前下游 `GenerationReport` 的真实消费。

当前 `GenerationReport` 从 `RAGContext.extra["evidence_contract"]` 反解 lineage，不是因为 canonical 缺 lineage，而是因为 canonical object 没有贯穿到 Stage。

---

## 6.6 Assessment facts

现有 `RAGEvidenceAssessmentSchema`：

- assessment_state
- status
- evidence_available
- selected_evidence_count
- dropped_evidence_count
- citation_count
- judge_name
- judge_version
- score
- coverage_ratio
- required_topics
- covered_topics
- missing_topics
- conflicts
- supported_claim_types
- forbidden_claim_types
- authoritative_evidence_present
- historical_or_reference_evidence_present
- required_qualifiers
- prompt_evidence_source
- prompt_evidence_contract_missing
- reason_codes
- details

### 结论

Phase 3A 已经把 Gate 所需的 public Evidence facts 补齐。

当前 Section/Research 的：

- supported_topics
- evidence_gaps
- conflicts

可以从 canonical assessment 投影，不需要再加平行字段。

---

## 6.7 Runtime / observability

现有：

- correction_trace
- budget_usage
- trace
- warnings
- error
- extra

### 结论

RAG 内部纠错、预算、Trace、错误已经可以由 canonical Bundle 表达。

Application 的 section recovery round / multi-call aggregate 不属于单个 RAG Bundle，应继续放 workflow state。

---

# 7. Evidence Authority Field Matrix

符号：

- `C` = 当前 canonical field
- `P` = projection/copy
- `W` = workflow/application state
- `D` = derived identity
- `M` = 当前缺失但值得补入 canonical
- `X` = 不允许进入 canonical

| Field / semantic | EvidenceBundle | ContextBundle.evidence | RAGContext.extra | SectionEvidence | Research projection | 归属结论 |
|---|---|---|---|---|---|---|
| query | C | P | P/hidden | P | P | canonical 已有 |
| rewritten_queries | C | - | hidden | - | - | canonical 已有 |
| task_id / run_id | C | shared state | - | W | W | canonical 已有 |
| retrieval_trace_id | C | - | - | tool ids aggregate | W | canonical 已有 |
| retrieval_scope | **M** | - | P | W | W | 建议补 canonical typed field |
| evidence requirements envelope | **M** | - | buried in governance details | - | topics/gaps projection | 建议补 canonical typed field |
| section_id/title/type | derive from requirements | - | hidden/indirect | W | W | 不单独重复；由 requirements 表达 |
| items[] | C | full copy inside contract | hidden full copy | chunks projection | IDs only | canonical 已有 |
| selected_evidence_ids | C | full copy | hidden full copy | indirect | accepted IDs projection | canonical 已有 |
| dropped_evidence_ids | C | full copy | hidden full copy | - | rejected IDs projection | canonical 已有 |
| citations | C | P/full copy | hidden full copy | P | - | canonical local citation truth |
| context | C | P | context itself + hidden full copy | P | - | canonical projection 已有 |
| lineage | C | hidden through contract | duplicate extra lineage | hash only | hash only | canonical 已有 |
| assessment | C | reduced duplicate fields | hidden through contract | metadata | summary/state | canonical 已有 |
| correction_trace | C | - | hidden | recovery_count W | research_rounds W | 单次 RAG vs workflow 分开 |
| budget_usage | C | - | hidden | workflow budget W | - | canonical 已有 |
| trace | C | - | hidden | - | - | canonical 已有 |
| warnings/error | C | - | hidden | workflow warnings/error | - | canonical 已有 |
| evidence_contract_sha256 | D | - | D | D | D | derived；不要自包含进 Bundle |
| evidence_package_ref | - | - | - | - | D/W | persistence locator，不是 Evidence fact |
| citation_id_map | X | - | projection metadata | projection | - | Application projection |
| recovery_count | X | - | - | W | W | workflow state |
| tool_call_ids aggregate | X | - | - | W | W | workflow state |
| research_status | X | - | - | - | W | Agent/Application decision |
| recommended_action | X | - | - | - | W | Agent/Application decision |
| writer_gate_binding | X | - | - | W | - | Application Gate |
| generation_allowed | X | - | - | W | - | Application Gate |
| human_review_required | X | - | - | W | W | Application decision |

---

# 8. EvidenceBundle 真正缺什么？

## 8.1 建议补：`retrieval_scope`

请求 Schema 已有：

```python
RAGToolInputSchema.retrieval_scope: str = "document"
```

当前 RAG 内部也持续使用：

```text
document / section / recovery
```

但是返回的 canonical Bundle 没有 typed `retrieval_scope`。

结果 Application 只能把 scope 保存在：

- SectionEvidenceBundle
- Research invocation/state
- RAGContext.extra
- normalized.extra

### 判定

这是 **RAG invocation/evidence provenance**，属于 canonical package identity/lineage 范畴。

建议 Phase 4B 增加：

```python
retrieval_scope: str = "document"
```

暂不增加新 Enum，避免本阶段扩张；若当前 scope 已有稳定公共类型再复用，否则保留 string contract。

---

## 8.2 建议补：public `EvidenceRequirementsSchema`

请求端已经正式 typed：

```python
RAGToolInputSchema.evidence_requirements:
    Optional[EvidenceRequirementsSchema]
```

内容包括：

- section_id
- section_title
- section_type
- requirements[]
  - requirement_id
  - topic
  - purpose
  - importance
  - expected_source_types

RAG governance 会基于这份要求计算：

- coverage_ratio
- covered_topics
- missing_topics
- conflicts
- assessment status

但返回 `EvidenceBundleSchema` 只公开了结果：

```text
assessment.required_topics / covered / missing
```

完整 resolved requirement envelope 被埋在：

```text
assessment.details["evidence_governance_v2"]["requirements"]
```

以及：

```text
bundle.extra["evidence_governance_v2"]
```

### 判定

这不是“为了字段齐全而扩 Schema”。

Assessment 的语义本身是：

```text
Evidence against Requirements
```

如果 canonical package 保存 assessment 却不保存其公开 requirements envelope，assessment provenance 不完整。

建议 Phase 4B 增加：

```python
evidence_requirements: Optional[EvidenceRequirementsSchema] = None
```

注意只能放 **public EvidenceRequirementsSchema**。

不要把 RAG-internal：

- policy_id
- requested_claim_types internal control
- coverage_strategy internal policy
- governance private metadata

直接提升为 public canonical field。

---

## 8.3 不建议额外增加 `section_id / section_title / section_type`

如果 `evidence_requirements` 已进入 canonical Bundle，则：

```text
bundle.evidence_requirements.section_id
bundle.evidence_requirements.section_title
bundle.evidence_requirements.section_type
```

已经能表达本次 evidence assessment envelope。

单独再加三份字段会再次制造 duplicated semantics。

---

## 8.4 不建议把 `evidence_contract_sha256` 作为 Bundle 自身字段

理由：

```text
hash = canonical serialization of Bundle
```

如果 hash 字段本身进入被 hash 的 Bundle，就会产生 self-reference / exclude-field 规则。

更干净的做法：

```text
EvidenceBundleSchema
+ adjacent derived hash
```

例如 carrier / checkpoint / binding 中保存：

```text
evidence_contract_sha256
```

但它必须由 canonical Bundle 单向派生。

### 冻结语义

```text
canonical evidence hash
= RAG-returned EvidenceBundle 的 canonical hash
```

Application citation remap 不得改变该 hash。

---

## 8.5 不建议增加 `evidence_package_ref`

`evidence_package_ref` 是：

```text
where the package is persisted / addressed
```

不是：

```text
what the evidence is
```

所以它属于 persistence/workflow carrier。

Phase 4 应先让现有 checkpoint 持久化 typed Bundle，再让 ref 指向这个持久化对象。

不需要为了 ref 在 EvidenceBundle 里增加自引用字段。

---

# 9. 明确禁止补进 EvidenceBundle 的字段

以下字段即使下游在用，也不得因为“统一 authority”迁回 RAG Evidence：

## 9.1 Gate / Delivery

- gate_decision
- delivery_mode
- generation_allowed
- publish_allowed
- human_review_required
- writer_gate_binding
- writer_execution_status

归属：Application。

---

## 9.2 Research / Agent Action

- research_status
- recommended_action
- RESEARCH_MORE
- WRITE
- PARTIAL_DELIVERY
- HUMAN_REVIEW

归属：Agent/Application policy projection。

Evidence 只提供：

- status
- coverage
- gaps
- conflicts
- supported/forbidden claim types
- source facts

---

## 9.3 Workflow aggregate

- recovery_count
- research_rounds
- generation_attempt
- aggregate tool_call_ids
- active_scope across multiple calls
- section lifecycle

归属：Section workflow / checkpoint。

单个 EvidenceBundle 只描述一次 RAG response 及其 RAG-internal correction trace。

---

## 9.4 Application citation remap

- document-global citation id map
- C1/C2 remap registry state
- registry scope
- registry ordering

归属：Application citation projection。

不能反向修改 RAG canonical citation identity。

---

# 10. Current Duplicate Field Analysis

## 10.1 `ContextBundle.evidence`

当前重复保存：

- query
- contract(full dump)
- context_text
- retrieved_chunks
- citations
- used_doc_ids
- evidence_available
- assessment_status
- evidence_sufficient

其中真正需要保留作为 shared context projection 的是：

- query（可保留）
- context_text
- retrieved_chunks projection
- citations projection
- used_doc_ids
- evidence_available projection
- assessment_status projection
- evidence_sufficient compatibility projection
- metadata

必须取消 authority 语义的是：

```text
contract: Dict[str, Any]
```

Phase 4B/C 可选择：

1. 删除 `contract`，由 Graph payload 显式 typed Bundle 持有；或
2. 暂时保留字段做 legacy projection，但注释和所有读路径禁止把它恢复为 authority。

优先方案：**typed Bundle 放 Graph/Section payload；ContextBundle 只保留 projection。**

---

## 10.2 `RAGContext.extra`

当前混合了：

合法 projection metadata：

- derived_from
- packing_strategy
- citation_ids_normalized
- retrieval_scope projection
- citation_id_map projection

非法 authority copy：

- evidence_contract full dump

可迁出的 duplicated canonical data：

- lineage full dump
- evidence_contract_sha256（可改为 adjacent carrier/hash field）

### 目标

`RAGContext.extra` 不再能单独恢复 EvidenceBundle。

---

## 10.3 normalized RAG output

当前 normalized 同时保存：

- evidence full dump
- context
- chunks
- citations
- trace
- extra

### 目标

兼容输出可以继续存在，但：

1. 生产决策代码不再通过它恢复 canonical Bundle；
2. typed canonical Bundle 在同一调用链显式传播；
3. normalized 用于 compatibility / capture / observability，不再拥有 write authority。

---

# 11. Citation Authority 分离

当前 `DocumentCitationRegistry` 会：

```text
RAG local citation ids
-> Application global ids
-> 回写 EvidenceBundle
-> 重新 hash
```

这会把两个身份域混在一起：

```text
Evidence identity
Citation presentation identity
```

Phase 4 应冻结：

```text
RAG EvidenceBundle.citations
= canonical RAG citation relation

Application DocumentCitationRegistry
= document-level citation projection
```

Application 可以返回：

- remapped citations
- remapped context text
- citation_id_map

但不能重写：

- bundle.items[].citation_ids
- bundle.citations
- bundle.context
- bundle hash

Phase 5 再进一步关闭 Evidence/Citation/Chunk namespace 问题。

---

# 12. Persistence / Checkpoint Audit

现有：

- `SchemeGenerationGraphStateCodec`
- `SectionExecutionGraphStateCodec`

都使用：

```text
Pydantic model
-> model_dump(mode="json", round_trip=True)
-> JSON
-> LangGraph checkpoint
-> model_validate restore
```

因此 `EvidenceBundleSchema` 本身已经满足现有 checkpoint 的序列化技术路线。

### 结论

Phase 4 不需要新增：

- EvidenceStore
- Artifact DB
- MongoDB
- Redis
- Neo4j
- 新 persistence service

只需要在现有 top-level / section payload 中增加/调整 typed carrier。

---

# 13. 推荐的 Phase 4 Target Authority Shape

逻辑目标：

```text
RAGService
    -> EvidenceBundleSchema
       [唯一 Evidence authority]
              |
              +--> canonical hash (derived)
              |
              +--> RAGContext projection
              |
              +--> Application citation projection
              |
              +--> Gate consumes assessment
              |
              +--> ContextManager consumes selected items/context
              |
              +--> Research summary consumes ids/gaps/conflicts
              |
              +--> Checkpoint persists typed Bundle
```

不能再存在：

```text
projection -> model_validate -> new canonical truth
```

LegacyEvidenceAdapter 是唯一允许的 compatibility exception：

```text
legacy protocol -> canonical Bundle
```

但升级完成后也必须进入同一 canonical pipeline。

---

# 14. Phase 4B 最小 Schema/Carrier 变更建议

本轮不实施，仅作为审计输出。

## 14.1 `EvidenceBundleSchema`

最小新增候选：

```python
retrieval_scope: str = "document"
evidence_requirements: Optional[EvidenceRequirementsSchema] = None
```

其余当前字段不需要大改。

---

## 14.2 Top-level document graph payload

目标需要有显式 typed field，例如语义上：

```text
document_evidence_bundle: EvidenceBundleSchema | None
```

字段名在实施前可按现有命名规范最终确认。

`document_rag_output` 可继续作为 compatibility/capture 输出，但不再作为 canonical recovery source。

---

## 14.3 Section graph payload

目标需要能显式保存：

```text
document canonical bundle
active canonical bundle
```

原因：Section retrieval / recovery 会产生新的独立 RAG calls；`active_context` 只是 projection，不能代表 active canonical evidence。

---

## 14.4 `MainlineResearchRetrieval`

应增加 typed canonical carrier：

```text
evidence_bundle: EvidenceBundleSchema | None
```

Research downstream 再从 bundle 投影：

- assessment
- accepted IDs
- gaps
- hash/ref

而不是从 `normalized` dict 重新解释 Evidence。

---

# 15. Phase 4C Removal Targets

实现新 typed path 后再删除旧 recovery path，顺序不能反过来。

明确 removal targets：

1. `RAGContext.extra["evidence_contract"]` producer
2. `RAGContext.extra["evidence_contract"]` readers
3. `ContextBundle.evidence.contract` authority semantics
4. `DocumentCitationRegistry` Bundle recovery/rebuild
5. ContextManager Bundle recovery from RAGContext
6. WriterGovernance Bundle recovery from normalized dict
7. Stage lineage recovery from Context.extra full contract
8. Research/Section helpers从 normalized dict 读取 canonical assessment 的路径，逐步改 typed bundle

LegacyEvidenceAdapter 的 legacy input validation/build 保留。

---

# 16. Hash Semantics Freeze Recommendation

建议正式冻结：

```text
Evidence hash authority
= canonical_sha256(RAG-returned canonical EvidenceBundle)
```

Application 后续：

- global citation remap
- prompt packaging
- context compilation
- writer gate binding

不得改变该 Evidence hash。

Application 自己的完整性使用独立 hash：

- prompt_evidence_contract_sha256
- context_sha256
- writer_gate_binding_sha256
- artifact hashes

这样避免：

```text
Evidence identity
== Citation presentation identity
== Prompt identity
```

---

# 17. Phase 4 与 Phase 5 的边界

本轮发现 `accepted_evidence_ids` 当前仍可能来自：

```text
citation_id
fallback chunk_id
```

这是已经确认的 Phase 5 Evidence/Citation/Chunk namespace bug。

Phase 4 只做：

```text
canonical Bundle typed propagation
```

不要在 Phase 4 顺手修 ID namespace。

Phase 4 只需保证未来 Research projection 的源是：

```text
EvidenceBundle.selected_evidence_ids
```

真正切换和 namespace regression 放 Phase 5。

---

# 18. Final Field Ownership Decision

## 18.1 Evidence canonical authority owns

- query / rewritten query
- task/run/retrieval trace identity
- **retrieval scope（建议新增）**
- **public evidence requirements envelope（建议新增）**
- evidence items
- selected/dropped Evidence IDs
- RAG-local canonical citation relations
- canonical RAG context projection
- lineage
- public assessment facts
- RAG correction trace
- RAG budget usage
- RAG trace
- warnings/error
- RAG-owned extension metadata

---

## 18.2 Application/Workflow owns

- section lifecycle
- section recovery round aggregation
- aggregate tool call IDs
- Research status/action
- Writer gate binding
- generation permission
- human review
- delivery mode
- publication permission
- document-global citation remap
- prompt/context package identity
- checkpoint location/ref

---

## 18.3 Derived-only values

- evidence_contract_sha256
- evidence_package_ref
- prompt hash
- writer gate hash

这些值可以被保存和验证，但不能成为新的 Evidence fact authority。

---

# 19. 结论

本轮结论不是“重新设计一套 Evidence V2”。

相反，当前 `EvidenceBundleSchema` 已经足够接近唯一 authority。

真正问题是：

```text
canonical object 没有被持续传播
        ↓
Application 复制 full dump
        ↓
Context / normalized / shared state / checkpoint 保存副本
        ↓
下游从副本恢复 Bundle
        ↓
CitationRegistry 甚至修改并重建 Bundle
```

因此 Phase 4 的核心不是“大扩 Schema”，而是：

```text
1. EvidenceBundle 增加最小缺失的 typed provenance
   - retrieval_scope
   - public EvidenceRequirementsSchema

2. typed EvidenceBundle 贯穿 Document / Section / Research / Checkpoint

3. 其他结构全部降级为 projection / summary / ref / hash

4. 禁止 projection 反向恢复并重新定义 canonical Evidence
```

---

# 20. Phase 4 Authority Freeze Candidate

建议在进入代码实施前冻结以下一句：

> `EvidenceBundleSchema` is the only canonical Evidence authority. RAG and the legacy compatibility edge may create it; Application consumers may persist, reference, hash, summarize and project it, but may not reconstruct a new canonical EvidenceBundle from projections or mutate its Evidence facts. `retrieval_scope` and the public `EvidenceRequirementsSchema` are the only Phase-4 candidate additions required to close current provenance gaps.

中文：

> `EvidenceBundleSchema` 是唯一 canonical Evidence authority。RAG 与 legacy compatibility edge 可以创建它；Application 只能持久化、引用、计算哈希、摘要和投影，不得从 projection 反向恢复新的 canonical EvidenceBundle，也不得修改其中的 Evidence facts。Phase 4 当前仅建议补齐 `retrieval_scope` 与 public `EvidenceRequirementsSchema` 两个 provenance 缺口。

---

## 21. Audit Outcome

```text
Authority candidates inventoried: COMPLETE
Field ownership matrix: COMPLETE
Canonical field-gap audit: COMPLETE
Production code modified: NO
Phase 1-3 reopened: NO
Gate logic modified: NO
Phase 5 ID namespace modified: NO
New persistence/service introduced: NO
```

下一阶段如果接受本审计结论：

```text
Phase 4B
Typed Canonical Evidence Carrier Implementation
```

实施顺序必须坚持：

```text
先建立 typed canonical path
-> 验证 checkpoint/resume
-> 再拆 full-dump recovery path
```

不能先删兼容链再补 canonical carrier。
