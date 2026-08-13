# 02 — Schema 字段与职责字典

> 本文同时覆盖 Step 2 的核心范围确认与 Step 3 的 Schema Card。内容完全来自当前生产代码，不包含任何未来 Schema 设计方案。

# Core Schema 范围

## Confirmed-by-code

`ProjectInputSchema`、`DocumentProfileSchema`、`ContentCapabilitiesSchema`、共享语义 Enum（`DocumentType`、`EvidenceRole`、`ProjectRelation`、`AuthorityLevel`、`ClaimType`）、`StructuredEvidenceSchema`、`FactEvidenceSchema/FactStatus`、`RetrievalCandidateV2Schema`、`EvidenceRequirementsV2Schema`、`EvidenceAssessmentV2Schema`、`EvidenceSetAssessmentSchema`、`GateDecisionSchema`、`WriterGateBindingSchema`、`GeneratedClaimSchema`、`ClaimAuditResultSchema`、`CitationSchema`、`CitationBindingSchema`、`SchemeSectionSchema`、`SchemeDraftSchema`、`HardGateResultSchema`。

分类依据：这些 Contract 都会被某个生产 transformation 直接消费，并改变 evidence admissibility、claim permission、grounding/citation integrity、section deliverability 或 document hard-gate result。

## Candidate-core

`SourceMaterialSchema`、`CleanedTextUnitSchema`、企业级 parent/child Chunk dictionary contracts、`TableEvidenceSchema`、`PolicyEvidenceSchema`、`TemplateEvidenceSchema`、`RetrievalAccessScopeSchema`、`EvidenceBundleSchema`、`PromptEvidenceCandidateV1Schema`、`ClaimAuditIntegritySchema`、`SectionEvalSchema`、`SectionEvidenceBundleSchema`、`RejectedDraftArtifactSchema`。

分类依据：这些 Contract 位于生产链路中，并保留有意义的边界数据，但其架构 ownership 是否属于核心决策层，仍可与 Core Decision Contract 分开讨论。

## Supporting but important

`GenerationRequirementsSchema`、`ProjectRelationSchema`、`AuthorityProfileSchema`、`ParsingProfileSchema`、`ClassificationProfileSchema`、`RAGToolInputSchema`、`RetrievedChunkSchema`、`EvidenceConflictSchema`、`SchemeWriterInputSchema`、`SchemeWriterOutputSchema`、trace/task-state records、release-publication records。

## Excluded legacy/test-only

- `backend/rag/ports/quality.py::EvidenceAssessment` 与 correction-gate dataclasses 属于 CRAG/retrieval-plugin interface，不是 Stage-D Writer Evidence Gate 的权威 Contract。
- `backend/rag/schema/eval_schema.py` 仅用于 Evaluation。
- fake tool / test fixture schemas 仅用于测试。
- 大范围 `agent_rag_v1` memory/recovery/router contracts 不属于当前 evidence→claim 根因审计，除非某个具体 artifact 被 Scheme Generation E2E 直接消费。

---

# Schema Cards

## 1. ProjectInputSchema

**文件：** `backend/apps/enterprise_document/schemas/project_input_schema.py`  
**层级：** L0  
**职责：** 进入企业文档主链路的规范业务输入 Contract。  
**负责回答：** 当前正在生成哪个项目、用户要求什么、显式提供了哪些 source material / facts、哪些信息缺失或冲突、要求生成哪些章节及输出。  
**不应回答：** 检索到的历史证据能支持什么；哪些 claim types 被 Evidence Gate 允许；模型最终生成了什么。  
**Producer：** project-input factory / normalization。  
**Consumer：** 顶层业务图、SchemeWriter input、document gate 及相关 service。  
**生命周期：** task ingress → 整个 run。  
**持久化：** 嵌入 task/runtime state。  

| Field | Type | 业务含义 | Source | 是否可变 | Consumer | Gate 影响 |
|---|---|---|---|---|---|---|
| schema_version | str | Contract 版本 | 代码默认值 | 不应发生语义性修改 | 全链路 | 仅审计 |
| task_id | str | Task 身份 | caller | 否 | workflow | 间接 |
| tenant_id | str | Tenant 边界  （租户标识） | caller/default | 否 | retrieval authorization | evidence scope |
| project_name | optional str | 当前项目的可读名称 | caller | 可 normalization | planning/writer | 间接 |
| current_project_id | optional str | 用于区分 current-project evidence 的项目身份 | caller | 否 | authorization/evidence semantics | 高 |
| task_type | str | 业务任务类型 | caller | 否 | workflow | 间接 |
| user_query | str | 用户要求完成的生成任务 | caller | 否 | retrieval/planning/writer | 高，间接影响 |
| source_materials | list[SourceMaterial] | 用户/业务侧声明提供的材料 | caller | 可 normalization | retrieval/input normalization | 高，间接影响 |
| generation_requirements | object | section/citation/budget requirements | caller | validator 会对齐 sections | planner/writer | 间接 |
| output_schema | object | 输出 title / required sections / fields | caller | validator 会对齐 sections | assembler/gate | section completeness |
| metadata | dict | 扩展 metadata | caller | 是 | 按需消费 | 不确定 |
| project_type/customer_type/business_goal | scalar | 项目/业务语义 | caller/derived | business_goal 可由 query 派生 | planner/writer | 间接 |
| target_documents | list[str] | 期望输出的 document 类型 | caller | 是 | planning | 间接 |
| total_staff / department counts/groups | optional facts | 显式提供的组织事实 | caller | 禁止推断 | writer/table normalization | claim content |
| hardware_resources | list | 显式提供的硬件事实 | caller | 禁止推断 | writer | claim content |
| target_templates / policy_requirements | list | 指定的 template/policy constraints | caller | 是 | planning/retrieval | 间接 |
| manual_boundaries | list | 明确禁止 LLM 推断的事项 | caller | 否 | writer/gate | 高 |
| missing_information | list | 显式 unknown 信息 | caller/normalizer | 是 | writer/gate | 高，属于语义信号 |
| conflicting_information | list | 显式 conflict 信息 | caller/normalizer | 是 | writer/human review | 高 |
| extra | dict | 扩展字段 | caller | 是 | 按需消费 | 不确定 |

**问题记录：这些total_staff / department counts/groups、hardware_resources 的真实生产来源是谁？是前端表单、业务系统、文件解析、人工录入，还是上游 Agent？当前代码只定义了 caller，并没有在这个 Schema 本身解决来源问题。**

**Invariant / validator：** generation 与 output 的 required sections 必须一致，否则按确定性单向规则复制；至少要求一个 section；citation-required sections 必须是 required sections 的子集；安全默认值可补充 business goal / document title，但人员、硬件、预算、当前现状等事实明确禁止虚构。  
**Semantic ownership：** 当前 run 的业务输入事实，以及显式缺失/冲突声明。  
**Potential overload：** `missing_information` 已能表达显式 unknown，但该信号不会自动转换成下游 generated-claim modality Contract。

## 2. SourceMaterialSchema

**文件：** 同上  
**层级：** L0/L1 边界  
**职责：** 声明一份业务材料及其 file/doc identity。  
**不负责回答：** 该材料权威的 `DocumentType/EvidenceRole/ClaimType` capability；这些属于 DocumentProfile。  

| Field | Type | 含义 | Source | 是否可变 | Consumer | Gate 影响 |
|---|---|---|---|---|---|---|
| material_type | str | caller 对材料的业务分类 | ProjectInput | 是 | normalization | 间接 |
| material_name | optional str | 展示名称 | caller | 是 | UI/logging | 无 |
| status | str | availability/status | caller/default unknown | 是 | normalization | 间接 |
| description | optional str | 自由文本描述 | caller | 是 | writer/context | 间接 |
| file_ids | list[str] | file identity | caller | 否 | retrieval scope | evidence access |
| doc_ids | list[str] | canonical doc ids | caller | 否 | retrieval scope | evidence access |
| metadata | dict | 扩展信息 | caller | 是 | 按需消费 | 不确定 |

**Invariant：** 继承 input schema class 配置，使用严格 `extra=forbid`。  
**Semantic ownership：** 用户声明的 source-material membership，不拥有 evidence capability 的定义权。

## 3. DocumentProfileSchema

**文件：** `backend/rag/catalog/document_profile.py`  
**层级：** L1  
**职责：** 针对一份 raw document 的权威、持久化语义身份。  
**负责回答：** 这是什么文档、它的 evidence role、project relation、authority，以及整份文档允许/禁止支撑哪些 claim types。  
**不应回答：** 某个具体 retrieved chunk 对某个 section 是否相关；某个 generated sentence 属于哪个 claim type。  
**Producer：** 经过 review 的 catalog classification pipeline / manual seed。  
**Consumer：** Stage C projection、registry、RetrievalCandidate validator。  
**生命周期：** ingestion/catalog → index build → online retrieval lineage。  
**持久化：** `data/catalog/document_profiles_v1.jsonl`。  

| Field | Type | 含义 | Source | 是否可变 | Consumer | Gate 影响 |
|---|---|---|---|---|---|---|
| schema_version | literal | Contract 版本 | code | 否 | 全链路 | 无 |
| document_id | str | canonical source identity | catalog | 否 | 整条 lineage | 高 |
| document_type | enum | source 的语义类型 | reviewed classification | 受控 | role invariant/governance | 高 |
| evidence_role | enum | 预期 evidence role | reviewed classification | 受控 | governance | 高 |
| kb_id | str | Knowledge Base membership | catalog | 受控 | retrieval filters | 高 |
| tenant_id | str | tenant 边界 | catalog/default | 受控 | retrieval | 高 |
| source_uri/source_name/title | optional | provenance / 展示信息 | source/catalog | 受控 | index/citation | grounding |
| project_relation | object | current/other/general/unknown + project id | catalog | 受控 | governance | 高 |
| authority | object | authority level/version/date | catalog | 受控 | governance | 高 |
| content_capabilities | object | claim allow/forbid 集合 | catalog | 受控 | candidate/governance | **关键** |
| parsing | object | parser/chunker strategy | catalog | 受控 | Stage C | semantic preservation |
| classification | object | method/confidence/review evidence | catalog | 受控 | audit | 间接 |
| metadata | dict | source hash/index flags/taxonomy 等 | catalog | 受控 | Stage C | 对 hash/index 等间接或高影响 |

**Invariant：** document type 必须映射到规定的 evidence role；current-project doc types 必须使用 current-project relation；historical doc types 必须使用 other-project relation；historical-case document 不得允许 current-project claim types；template document 只能允许 template/style claims；unclassified document 不得允许 claims。  
**为什么存在：** 防止 Document identity 获得其本身不具备的 authority。  
**Semantic ownership：** Document-level source identity 与 Document-level claim capability。  
**Potential overload：** `content_capabilities` 是 Document-level，但后续会原样复制给该 Document 的每一个 candidate chunk。

## 4. ContentCapabilitiesSchema

**文件：** 同上  
**层级：** L1  
**职责：** 封闭的 Document-level allow/forbid capability 集合。

| Field | Type | 含义 | Source | 是否可变 | Consumer | Gate 影响 |
|---|---|---|---|---|---|---|
| allowed_claim_types | list[ClaimType] | 该 Document 允许支撑的 claims | catalog classification | 受控 | candidate/governance | 关键 |
| forbidden_claim_types | list[ClaimType] | 该 Document 明确禁止支撑的 claims | catalog classification | 受控 | candidate/governance | 关键 |

**Invariant：** 两个 list 内部必须唯一，且彼此不能重叠。  
**Semantic ownership：** Document-level capability，而非 sentence/chunk-level capability。  
**Potential overload：** 同一组 allow/forbid list 会直接成为 chunk candidate capability，没有单独的 chunk-local semantic refinement。

## 5. Shared semantic vocabulary

**文件：** 同上  
**层级：** L1–L5 共享  
**职责：** 跨持久化边界复用的稳定 Enum 语义词汇。

- `DocumentType`：current-project requirement/decision/inventory、historical feasibility/construction、policy、technical whitepaper、tender、template、unknown。
- `EvidenceRole`：project fact、historical case、policy standard、technical reference、procurement requirement、template style、unclassified。
- `ProjectRelation`：current project、other project、general、unknown。
- `AuthorityLevel`：authoritative、official、confirmed、reference、draft、unknown。
- `ClaimType`：general method、typical architecture、reference case、五种 current-project factual types、policy requirement、technical specification、procurement requirement、template structure、style guidance。

**Semantic ownership：** 仅定义共享语义词汇，本身不执行分类决策。  
**Invariant：** 由 Pydantic Enum parsing 与上层 validator 强制执行。  
**Potential overload：** `ClaimType` 同时被用作 Evidence capability vocabulary 与 generated-sentence classification vocabulary，因此两个使用场景之间的错位会直接转化为 Gate failure。

## 6. CleanedTextUnitSchema

**文件：** `backend/rag/schema/cleaned_text_unit.py`  
**层级：** L2  
**职责：** Data Platform → RAG 之间的标准化 text-unit 边界，供当前 chunking 消费。  
**Producer：** data-platform record migration + Stage C profile projection。  
**Consumer：** cleaned-text chunker/offline builder。  
**持久化：** cleaned/index-build artifacts。  

| Field | Type | 含义 | Source | 是否可变 | Consumer | Gate 影响 |
|---|---|---|---|---|---|---|
| schema_version | literal | boundary 版本 | code | 否 | chunker | 无 |
| unit_id/doc_id | optional str | unit/document identity | producer | 受控 | chunker | lineage |
| tenant_id/kb_id/file_id | scalar | access/index identity | profile projection | 受控 | index | 高，间接 |
| source_type/uri/name/format/batch_id | scalar | source provenance | producer/profile | 受控 | index/citation | grounding |
| title/section/section_level/pages | scalar | document structure | producer | 受控 | chunker/retriever | relevance/grounding |
| unit_type/unit_order | scalar | source unit kind/order | producer | 受控 | chunker | semantic preservation |
| text/text_length | scalar | normalized source text | producer | 受控 | chunker/index | 关键内容 |
| language/quality_score/quality_flags | quality metadata | producer | 是 | chunker/index | 间接 |
| cleaning_version | str | preprocessing lineage | producer | 否 | index/audit | lineage |
| extra | dict | 已批准迁移的 producer extensions + DocumentProfile projection | adapter/profile projection | 受控 | chunker/index | 高 |

**Invariant：** 使用严格类型和 `extra='forbid'`；已知 producer extension 会显式迁移进 `extra`；未知扩展直接失败，而不是静默丢弃。  
**Semantic ownership：** cleaned unit payload 与 source preprocessing lineage，不拥有 evidence claim authority。

## 7. Enterprise Parent/Child Chunk dictionary contracts

**文件：** `backend/rag/schema/Chunk_Schema.py`  
**层级：** L2  
**职责：** 为可索引的 child match 与 parent context 提供稳定 dictionary Contract，并保留 doc identity 与 `extra` lineage。  
**Producer：** type-aware / parent-child chunkers。  
**Consumer：** vector/BM25 index builders 与 retriever。  
**持久化：** vector/BM25 index records。

**通用业务字段：** `schema_version`、`tenant_id`、`kb_id`、`file_id`、`doc_id`、`source_type`、`text`、`text_length`、`token_count`、`text_hash`、`source_unit_ids`、`title`、`section`、`section_level`、`page_start`、`page_end`、cleaning/chunk version fields、timestamps、`extra`。

**Parent 专用字段：** `parent_chunk_id`、`child_chunk_ids`、`child_count`、`parent_chunk_index`、`parent_chunk_strategy`、`updated_at`、`is_active`。

**Child 专用字段：** `chunk_id`、`child_chunk_id`、`parent_chunk_id`、`child_chunk_index`、`child_index_in_parent`、`child_chunk_strategy`、`char_start_in_parent`、`char_end_in_parent`、`parent_chunk_version`、`child_chunk_version`、`updated_at`、`is_active`。

**Invariant：** builder 确定性计算 text length/hash；child count 默认使用 child-id count；复制 `extra`，避免与 caller 共享可变对象。  
**Semantic ownership：** chunk identity/text boundary 与 lineage transport。  
**Potential overload：** semantic source identity 位于 `extra.document_profile` 中传递，同时又存在用于 filtering 的 scalar mirror。

## 8. StructuredEvidenceSchema

**文件：** `backend/rag/schema/structured_evidence.py`  
**层级：** L2  
**职责：** Stage C child evidence 的标准在线 typed projection。  
**Producer：** `build_structured_evidence(record)`，输入来自 child/extra/type-aware metadata。  
**Consumer：** RetrievalCandidateV2、PromptEvidenceCandidate、Evidence Governance。  

| Field | Type | 含义 | Gate 影响 |
|---|---|---|---|
| schema_version | literal | Contract 版本 | 无 |
| representation_type | str | evidence 的实际 representation | claim-structure compatibility |
| original_strategy / actual_strategy | optional str | chunk strategy lineage | 间接 |
| fact | optional FactEvidence | structured fact payload | 高 |
| table | optional TableEvidence | structured table row | 对 cost/quantity/config 高影响 |
| policy | optional PolicyEvidence | policy structural payload | 高 |
| template | optional TemplateEvidence | template structural payload | 高 |
| fallback | FallbackEvidence | 是否使用 generic fallback | audit/间接 |

**Invariant：** `representation_type` 不得为空。  
**Semantic ownership：** 一个 chunk/evidence item 的 structured form，不拥有 Document identity。

### 8.1 FactEvidenceSchema / FactStatus

| Field | Type | 含义 | Gate 影响 |
|---|---|---|---|
| record_id | optional str | structured record identity | lineage |
| fact_category | optional str | cost/quantity/config/schedule 等 | claim compatibility |
| subject/predicate/value/unit | optional | structured fact content | 高 |
| fact_status | enum | unknown/estimated/proposed/planned/confirmed/decided | **关键** |
| source_span | optional dict | 精确 source text span | prompt visibility/grounding |

`resolve_fact_claim_eligibility()` 对 `UNKNOWN` 的处理是：current-project claims 不可 eligible，并附加 qualifier / human-review semantics；estimated/proposed/planned 可以在带 qualifier 时 eligible；confirmed 需要 authoritative source；decided 在代码中走单独 policy branch。  
**Potential overload：** 这套 certainty model 只存在于 structured fact 输入中；普通 generated text 在 `GeneratedClaimSchema` 里没有等价的一等字段。

### 8.2 TableEvidenceSchema

字段：`table_id`、`table_title`、`header`、`row_index`、`cells`、`normalized_fields`、`inherited_cells`、`merged_cell_relations`、`calculation_record`。  
Ownership：table structure/value context。Consumer：claim-structure compatibility 与 fact conflict logic。

### 8.3 PolicyEvidenceSchema

字段：`policy_name`、chapter/article/clause identities/titles/path、`is_table_of_contents`。  
Ownership：policy clause structure。Consumer：policy evidence compatibility。

### 8.4 TemplateEvidenceSchema

字段：`section_path`、`section_title`、`required_fields`、`placeholder_fields`、`style_hints`、`is_example_content`。  
Ownership：template structure/style evidence。Consumer：template claim compatibility。

## 9. RetrievalAccessScopeSchema

**文件：** `backend/schemas/rag.py`  
**层级：** L3  
**职责：** 对 tenant/KB 以及可选 file/doc whitelist 建立正向授权边界。  
字段：`schema_version`、`tenant_id`、`authorized_kb_ids`、`allowed_file_ids`、`allowed_doc_ids`。  
**Producer：** mainline/E2E request。**Consumer：** request mapper/retriever。  
**Invariant：** 代码校验 tenant 非空，authorized KB set 非空。  
**Semantic ownership：** 决定哪些 source 可以被检索，不决定这些 source 可以证明什么。

## 10. EvidenceBundleSchema

**文件：** `backend/schemas/rag.py`  
**层级：** L3  
**职责：** 跨 RAG/Agent 的标准 evidence package；旧 chunk/context/citation fields 只是 projection。  

| Field | 含义 | Gate 影响 |
|---|---|---|
| query / rewritten_queries | 实际 retrieval request | relevance |
| task_id/run_id/status/retrieval_trace_id | runtime lineage | audit |
| items | 全部 selected+dropped canonical evidence items | evidence truth |
| selected_evidence_ids / dropped_evidence_ids | context inclusion decision | 高 |
| citations | 仅对应 selected evidence 的 citations | grounding |
| context | 受 budget 约束的 rendered prompt context | **高** |
| lineage | index/model/plan versions | audit |
| assessment | legacy availability/sufficiency projection | 间接 |
| correction_trace | CRAG/correction history | 间接 |
| budget_usage/trace/warnings/error/extra | runtime metadata | 间接 |

**Invariant：** evidence IDs 必须唯一；selected/dropped 互斥且都必须存在；disposition 必须一致；citation IDs 唯一；dropped evidence 不得暴露 citation；rendered context 的 char/token budget 必须与真实文本一致；context item count 必须等于 selected evidence count；assessment counts 重新计算。  
**Semantic ownership：** selected prompt evidence package 与 context integrity。  
**Potential overload：** legacy `assessment` 与 Stage-D V2 assessment 同时存在；必须避免把兼容性 assessment 错当成 V2 Gate authority。

## 11. RetrievalCandidateV2Schema

**文件：** `backend/rag/schema/retrieval_candidate_v2.py`  
**层级：** L3  
**职责：** 针对一条 retrieved child/parent lineage 的 Stage-D candidate truth。  
**Producer：** `build_retrieval_candidate_v2(result)`。  
**Consumer：** Evidence Governance V2。  

| Field group | Fields | 含义 | Transformation |
|---|---|---|---|
| identity | candidate_id, child_chunk_id, parent_chunk_id, document_id, tenant_id, kb_id, file_id | retrieval/source identity | COPY/NORMALIZE |
| source semantics | document_type, evidence_role, project_relation, project_id, authority_level | 权威 DocumentProfile 的 mirror | COPY，并做 equality validation |
| claim boundary | allowed_claim_types, forbidden_claim_types | Document capability 复制到 candidate | COPY |
| hashes/profile | profile_sha256, source_sha256, document_profile | lineage 与 embedded authoritative profile | COPY |
| typed content | type_aware_chunking, structured_evidence | chunk-local semantic structure | DERIVE |
| rank/scores | rank, retrieval_score, rerank_score, retrieval_sources | retrieval ranking | COPY/NORMALIZE |
| text | match_text, context_text | child match + parent context | COPY/SELECT |
| metadata | section/title/pages/matched-child ids/CRAG signals | retrieval context | FILTER |

**Invariant：** claim lists 必须唯一且互斥；embedded profile 必须包含所有 Stage-D fields；每个 scalar identity/role/authority/hash 必须与 embedded profile 一致；candidate project id 必须一致；allow/forbid set 必须与 DocumentProfile capability 完全相等。  
**Semantic ownership：** online candidate identity，以及复制来的 Document-level capability 与 chunk-local structure。  
**Potential overload：** candidate capability 被设计为必须与 whole-document capability 完全一致；在这一 Schema 边界，chunk text 不能自行缩小或扩展该 capability set。

## 12. PromptEvidenceCandidateV1Schema

**文件：** `backend/rag/schema/prompt_evidence_candidate_v1.py`  
**层级：** L3/L4  
**职责：** 把 Writer 实际可见的精确文本与 RetrievalCandidate 绑定。  

字段：identity（`candidate_id`、child/parent/doc ids）；`prompt_visible_text`、char/token counts；`citation_ids`、`context_rank`；truncation fields；完整 `document_profile`、`structured_evidence`、embedded `retrieval_candidate`；profile/source hashes；`fact_visible_in_prompt`。

**Invariant：** citation IDs 唯一；char/token count 必须与精确文本一致；所有 identity/hash/profile/structured evidence 必须与 embedded RetrievalCandidate 一致；truncated item 必须有 source id。Strict builder 要求显式 `prompt_visible_text`；compatibility mode 可以 fallback 到 context/text，但不会直接暴露完整 parent text。  
**Semantic ownership：** Writer 在 prompt 中实际看见了什么 evidence。  
**Potential overload：** 它只收窄 visible text / fact visibility，但保留 Document-level capability 不变。

## 13. EvidenceRequirementsV2Schema

**文件：** `backend/rag/evidence/governance_v2.py`  
**层级：** L4  
**职责：** 进入 Stage-D Governance 的 section-local evidence requirement request。  

| Field | 含义 | 当前行为 |
|---|---|---|
| schema_version | Contract 版本 | 固定 |
| policy_id | policy identity | 默认 `candidate_capability_union_v1` |
| section_id / section_type | 目标 section identity/type | normalized slug |
| required_topics | topic coverage request | 实际参与决策 |
| requested_claim_types | requested claim classes | 会解析并保存，但当前 metadata 明确标记为 ignored |
| claim_risk_level | request risk | 当前构造为 LOW；final risk 根据 supported claims 重新计算 |
| coverage_strategy | set coverage algorithm | 实际参与决策 |
| allow_framework_only | framework permission input | 传递 |
| metadata | resolution flags/source | 包含 `claim_resolution_mode=candidate_capability_union` 与 `requested_claim_types_ignored=True` |

**Invariant：** topics 与 requested claims 各自唯一；section id/type 非空；required topics 非空。  
**Semantic ownership：** 一个 section 要求 Evidence 覆盖什么。  
**Potential overload：** Schema 本身能够表达 `requested_claim_types`，但当前 transformation 明确不使用它去约束 Gate allowed claims。

## 14. EvidenceAssessmentV2Schema

**文件：** `backend/rag/schema/evidence_assessment_v2.py`  
**层级：** L4  
**职责：** Per-candidate eligibility 与 claim support result。  

字段：identity/source semantics（`evidence_id`、`document_id`、`document_type`、`evidence_role`、`project_relation`、`authority_level`）；scoring（`semantic_relevance`、`eligibility`、`relevance`）；outputs（`supported_topics`、`supported_claim_types`、`forbidden_claim_types`、`reason_codes`、`metadata`）。

**Producer transformation：** `_item_assessment()` 从 candidate allow minus forbid 开始；relation 不是 current 时移除 current-project claims；移除 structure-incompatible claims；应用 `FactStatus`/authority policy；拒绝 CRAG-irrelevant evidence；最终输出 supported 与扩展后的 forbidden set。  
**Invariant：** list 唯一；supported/forbidden 互斥；ineligible evidence 不得暴露 supported claims；irrelevant evidence 不得暴露 topics/claims；historical evidence 不得支持 current-project claims；template evidence 只能支持 template/style claims。  
**Semantic ownership：** 在经过 section relevance / structure / status filtering 后，这一条 retrieved evidence item 实际能够支撑什么。

## 15. EvidenceSetAssessmentSchema

**文件：** 同上  
**层级：** L4  
**职责：** 汇总某个 section 全部 eligible evidence 的 topic coverage / conflict status。  
字段：assessment/section identity、coverage strategy/status/ratio、required/covered/missing topics、evidence counts/ids、conflicts、reason codes、metadata。  
**Producer：** `_set_assessment()`。  
**Consumer：** `_gate()`。  
**Invariant：** topic sets 唯一且互斥；covered/missing 必须属于 required；status-specific coverage invariant 必须满足；sufficient 要求 ratio=1、无 missing、无 conflict；partial 必须同时有 covered 与 missing；absent 要求 zero coverage/support；conflicted 必须存在 conflict；not-assessed 不得宣称 coverage。  
**Semantic ownership：** section topic coverage 与 structured conflict status，不拥有最终 claim permission。

## 16. GateDecisionSchema

**文件：** `backend/apps/enterprise_document/schemas/gate_decision.py`  
**层级：** L4  
**职责：** Writer 最终消费的、可审计的 Stage-D policy resolution。  

| Field | 含义 | Gate 角色 |
|---|---|---|
| policy_id | policy identity | audit |
| evidence_status | set evidence status | delivery precondition |
| delivery_mode | grounded/qualified/framework/blocked/human_review | **generation boundary** |
| claim_risk_level | resolved risk | delivery/human review |
| allowed_claim_types | Writer 可以生成的 claims | **关键** |
| forbidden_claim_types | 禁止生成的 claims | **关键** |
| required_qualifiers | 必须出现的 qualifier wording | **关键** |
| covered_topics/missing_topics | coverage summary | delivery semantics |
| human_review_required | human review flag | output status |
| reason_codes/metadata | decision explanation | audit |

**Producer：** `_gate()` 对所有 eligible evidence 的 `supported_claim_types` 做 union；对 forbidden types 做 union 后再移除 allowed；从 supported union 派生 risk；再应用 evidence-status/reference/authority policy。  
**Invariant：** allow/forbid 与 topics 互斥；conflicted 必须 blocked/review；absent/not-assessed 不得 grounded/qualified；grounded 要求 sufficient；qualified 必须有 qualifiers；framework-only 只能允许 template/style 且不得有 current-project facts；blocked 不允许任何 claims；human-review mode 必须带对应 flag。  
**Semantic ownership：** 最终 Writer claim boundary 与 delivery mode。  
**Potential overload：** 当前 claim boundary 是 eligible candidate capability 的 union，而不是与 section request 做 intersection；metadata 已明确记录这一事实。

## 17. WriterGateBindingSchema

**文件：** `backend/apps/enterprise_document/schemas/writer_governance.py`  
**层级：** L5  
**职责：** 把一次 Writer invocation 与精确 GateDecision 及 evidence hash 做不可变绑定。  
字段：schema version；section identity/order；document/run identity；`gate_decision`；`generation_allowed`；evidence contract hash；prompt evidence source/hash；metadata。  
**Invariant：** generation allowed 当且仅当 delivery mode 属于 grounded/qualified/framework-only；blocked 不得暴露 allowed claims；human review flag 必须一致；普通 generation 还要求完整 identity 与 evidence-lineage hash。  
**Semantic ownership：** 哪一份精确 Gate/evidence Contract 约束了该 generated section。

## 18. ClaimAuditIntegritySchema

**文件：** 同上  
**层级：** L5/L8  
**职责：** 使用 hash 将 Claim Audit 与精确 content、gate binding、evidence contracts、classifier policy 绑定。  
字段：section id/title；content 与 normalized-content hashes；writer-gate hash；evidence/prompt-evidence hashes；classifier id/version/policy hash；substantive detector version。  
**Invariant：** evidence-lineage completeness 要求两个 evidence hashes 都存在。  
**Semantic ownership：** integrity/provenance，不负责 semantic classification。

## 19. GeneratedClaimSchema

**文件：** 同上  
**层级：** L5  
**职责：** 把一条 post-generation sentence/span 分类到共享 `ClaimType`。  
字段：claim/section ids、sentence index、`claim_type`、text、span start/end、classifier id/version/confidence、metadata。  
**Producer：** WriterClaimAuditor 中的 semantic/deterministic claim classifier。  
**Consumer：** ClaimAuditResult。  
**Invariant：** end > start。  
**Semantic ownership：** generated-text claim type 与 span。  
**Potential overload：** 没有针对 unknown/deferred/proposed 等 epistemic/modality state 的一等字段；generated semantic enforcement 主要依赖 `ClaimType` + text/qualifier check。

## 20. ClaimAuditResultSchema

**文件：** 同上  
**层级：** L5  
**职责：** 证明精确 final generated content 是否遵守最终 GateDecision。  
字段：section id；execution status；passed；human-review flag；integrity；timestamp；generated claims；violations；allowed/forbidden claims；required/applied qualifiers；boolean checks；metadata。  
**Invariant：** timestamp 必须 timezone-aware；section ids 一致；claim ids 唯一；allowed/forbidden 唯一且互斥；qualifier lists 唯一；`passed == no violations AND all(checks)`；blocked/human-review/not-assessed execution 不得发布 generated claims。  
**Semantic ownership：** Writer 侧 compliance result，不拥有 source evidence authority。

## 21. CitationSchema

**文件：** `backend/schemas/citation.py`  
**层级：** L6  
**职责：** 可追踪的 evidence source 与精确 quote/location。  
字段：version/id/source type；doc/file/source-document ids；parent/child/generic chunk ids；table/row ids；title/section/page range；`quote_text`、summary、confidence、extra。  
**Producer：** evidence mapper/context packer/document citation registry。  
**Consumer：** prompt、CitationService、grounding/integrity/document gate。  
**Semantic ownership：** Evidence source provenance 与可审计 source text。

## 22. CitationBindingSchema

**文件：** 同上  
**层级：** L6  
**职责：** 将 Citation 绑定到精确 generated target claim/paragraph marker，并保存 source/claim snapshot。  
字段：binding/citation ids；target document/section/paragraph/claim ids；marker index/ordinal/start/end；source doc/chunk/parent ids；claim text；quote text；confidence；metadata。  
**Producer：** `CitationService.bind_citations()` 与 reconciliation。  
**Consumer：** citation integrity、high-risk checks、document assembly/gate。  
**Semantic ownership：** claim-to-source grounding relation。

## 23. SectionEvalSchema

**文件：** `backend/apps/enterprise_document/schemas/scheme_writer/evaluation.py`  
**层级：** L6  
**职责：** 独立于 Claim Audit Contract 的 section-level quality/gate result。  
字段：`passed`、check map、failures、warnings、optional semantic gate。  
**Producer：** section quality evaluator/generation service。  
**Consumer：** SchemeSection/document gate。  
**Semantic ownership：** section quality checks，不拥有 evidence claim capability。

## 24. SectionEvidenceBundleSchema

**文件：** `backend/apps/enterprise_document/schemas/scheme_writer/evidence.py`  
**层级：** L7  
**职责：** Per-section RAG context/chunks/citations package，并携带 Writer evidence-lineage projection。  
字段：section identity/title/scope/query/tool ids；`rag_context`；retrieved chunks；citations；recovery count；evidence-contract hash；writer-gate-binding dict；generation_allowed；metadata。  
**Producer：** SchemeEvidenceService/section coordinator。  
**Consumer：** section writer/document assembly/output。  
**Semantic ownership：** Per-section evidence package，而不是最终 Stage-D decision 本身。

## 25. SchemeSectionSchema

**文件：** `backend/apps/enterprise_document/schemas/scheme_writer/document.py`  
**层级：** L7  
**职责：** 标准 section artifact。  
字段：version；section id/title/level/order；input/prompt/model output/final content；status/error；citation ids/bindings；source fact ids；truncation result；section eval；warnings/timestamps；`extra`。  
**Producer：** section generation/coordinator。  
**Consumer：** assembler、verifier、hard gate、rejected artifact。  
**Semantic ownership：** 精确 final section content 及其附属 audit/binding/evaluation state。  
**Potential overload：** 核心 Stage-E governance object（`writer_gate_binding`、`claim_audit`、`formal_deliverable_eligible`）当前放在 `extra` 中，而不是一等字段。

## 26. SchemeDraftSchema

**文件：** 同上  
**层级：** L7  
**职责：** 由多个 SchemeSection 组装成 whole-document draft。  
字段：version；draft/document/task/run ids；title/full text；section list；required/missing sections；citation bindings；truncation flag；summary；timestamps；extra。  
**Producer：** DocumentAssembler。  
**Consumer：** document verifier、hard gate、release/rejected path。  
**Semantic ownership：** assembled document content，以及 section/citation composition。

## 27. HardGateResultSchema

**文件：** `backend/apps/enterprise_document/schemas/scheme_writer/evaluation.py`  
**层级：** L7  
**职责：** 最终 document-level Gate 结果。  
字段：version、`passed`、failures、warnings、check map、metadata。  
**Producer：** `services/document_gate.py` 中的 `evaluate_scheme_draft()`。  
**Consumer：** output routing 与 release-publication validation。  
**Semantic ownership：** 最终 deliverability decision，不拥有 source/claim classification。

## 28. RejectedDraftArtifactSchema

**文件：** `backend/apps/enterprise_document/schemas/scheme_writer/output.py`  
**层级：** L7/L8  
**职责：** 持久化一个已经生成但不可交付的 draft。  
字段：artifact type、`draft`、rejection code/reasons、`hard_gate`。  
**Producer：** stage-service output routing。  
**Consumer：** runtime artifact/report。  
**Semantic ownership：** rejected output package 与 failure proof。

---

# 由代码确认的跨 Schema Invariant

1. **Historical evidence 不能证明 current-project facts。** 该约束同时在 DocumentProfile 与 EvidenceAssessment 中执行；这是正确 invariant，不能为了提高 pass rate 而削弱。
2. **Template evidence 受结构性限制。** DocumentProfile 与 EvidenceAssessment 对 template-role evidence 只允许 template/style claims。
3. **RetrievalCandidate identity/capability 必须与其 embedded DocumentProfile 完全一致。** 混合 hit/profile semantics 会直接被拒绝。
4. **Prompt Governance 不能把 Writer 看不见的 source text 当作证据。** PromptEvidenceCandidate 会绑定精确 visible text 与 fact visibility。
5. **Blocked GateDecision 不得允许 claims 或 generation。** WriterGateBinding 会确定性重复校验这一约束。
6. **ClaimAudit 同时绑定 content、Gate、Evidence 与 classifier policy。** Artifact integrity 会重新计算，而不是信任 stale boolean。
7. **Final Hard Gate 位于 section claim audit 与 citation/integrity checks 之后。** 一个 section 即使已经生成文本，也仍然可能处于正式不可交付状态。

# Step 2/3 结论

当前核心 semantic ownership 边界如下：`ProjectInput` 拥有 current-run input truth；`DocumentProfile` 拥有 Document identity/capability；`StructuredEvidence` 拥有 chunk-local typed structure/certainty；`EvidenceAssessment` 拥有 item-level eligible support；`GateDecision` 拥有 Writer permission；`GeneratedClaim` 拥有 generated-text claim classification；`CitationBinding` 拥有 source-to-claim grounding；`SchemeSection/SchemeDraft/HardGate` 拥有 deliverability artifact。

因此，本次审计最大的风险并不一定是“缺少某个 class”，而是这些 ownership boundary 之间的 transformation 是否完整保留了下游决策所需要的业务语义差异。
