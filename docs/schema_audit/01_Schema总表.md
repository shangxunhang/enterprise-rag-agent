# 01 — Schema 总表

> 审计范围：仅覆盖当前 Scheme Generation E2E 的生产链路。本总表完全依据现有生产代码整理，并有意排除与当前主链路无关的 Pydantic model。本阶段不进行任何 Schema V2 设计。

## 分类说明

- **Confirmed-by-code / Core**：已由生产代码确认，直接跨越生产模块边界，并会改变检索证据语义、Writer claim 边界、Citation/Grounding、章节可交付性或 Document Hard Gate 行为。
- **Candidate-core**：生产代码表明其具有直接语义作用，但其架构 ownership / 核心地位仍需由项目 Owner 最终确认。
- **Supporting**：承载或投影 Core 数据，但自身不是该语义的最终定义权所有者。
- **Conditional**：仅在特定链路上启用，例如 repair、release、parallel section 等。
- **Legacy**：兼容或历史 Contract，不是本次审计中的权威 Contract。
- **Test-only**：不进入生产 E2E。

## Schema 总表

| 层级 | Schema / Contract | 文件 | 作用 | Producer | Consumer | 是否持久化 | 是否影响 Gate | 状态 |
|---|---|---|---|---|---|---|---|---|
| L0 | `ProjectInputSchema` | `backend/apps/enterprise_document/schemas/project_input_schema.py` | 规范化后的业务输入 Contract；保存当前项目身份、已知事实/材料、所需章节，以及显式缺失或冲突的信息。 | `application/project_input_factory.py`、ProjectInput normalization | 顶层业务图、SchemeWriter、document gate | Task/runtime state | 是 | **Confirmed-by-code / Core** |
| L0 | `GenerationRequirementsSchema` | 同上 | 描述 required sections、citation requirements、generation budgets 和 human-review intent。 | ProjectInput 构造/校验 | planning、SchemeWriter、section execution | 嵌套于 ProjectInput | 间接 | Supporting |
| L0 | `SourceMaterialSchema` | 同上 | 声明用户提供的 source material 身份，以及 doc/file 引用。 | ProjectInput 构造 | retrieval authorization、input normalization | 嵌套于 ProjectInput | 间接 | Candidate-core |
| L1 | `DocumentProfileSchema` | `backend/rag/catalog/document_profile.py` | 一份源文档长期、权威的语义身份。 | Catalog build / 人工复核后的 profile pipeline | Stage C projection、registry、retrieval candidate validation | `data/catalog/document_profiles_v1.jsonl` | **是** | **Confirmed-by-code / Core** |
| L1 | `ProjectRelationSchema` | 同上 | 定义一份源文档与 current/other/general project 的关系，并携带 project id。 | DocumentProfile 构造 | DocumentProfile validator / projection | 嵌套于 DocumentProfile | 是 | Supporting |
| L1 | `AuthorityProfileSchema` | 同上 | 定义 source authority level、issuer、version、date window。 | DocumentProfile 构造 | Stage C projection、Evidence Governance | 嵌套于 DocumentProfile | 是 | Supporting |
| L1 | `ContentCapabilitiesSchema` | 同上 | 定义 Document-level 的 `ClaimType` allow/forbid 集合。 | DocumentProfile 构造 | Stage C projection、RetrievalCandidateV2、Evidence Governance | 嵌套于 DocumentProfile | **是** | **Confirmed-by-code / Core** |
| L1 | `ParsingProfileSchema` | 同上 | 定义该文档使用的 parser/chunker strategy。 | DocumentProfile 构造 | Stage C builder/chunker | 嵌套于 DocumentProfile | 不直接影响 Writer Gate | Supporting |
| L1 | `ClassificationProfileSchema` | 同上 | 记录 classification method、confidence、review status 和 reason。 | Catalog classification/review | 审计与 Stage C projection | 嵌套于 DocumentProfile | 间接 | Supporting |
| L1 | `DocumentType`、`EvidenceRole`、`ProjectRelation`、`AuthorityLevel`、`ClaimType` | 同上 | Catalog、retrieval candidate、evidence assessment、gate 共享的封闭语义词汇。 | Enum 定义 | Stage C–E 各 Contract | 以标量值序列化 | **是** | **Confirmed-by-code / Core vocabulary** |
| L2 | `CleanedTextUnitSchema` | `backend/rag/schema/cleaned_text_unit.py` | Type-aware chunking 之前的标准化 Stage C source record。 | data-platform / cleaned record migration + `project_source_record` | offline builder/chunker | source/index build artifacts | 间接 | Candidate-core |
| L2 | Chunk dictionary contracts（`build_chunk`、`build_parent_chunk`、`build_child_chunk`） | `backend/rag/schema/Chunk_Schema.py` | 定义 parent/child chunk 身份、文本范围及 `extra` 传递结构。 | Stage C chunkers | vector/BM25 index builders、retriever | index records | 间接 | Candidate-core |
| L2 | `StructuredEvidenceSchema` | `backend/rag/schema/structured_evidence.py` | Stage C child record 携带的 typed fact/table/policy/template evidence 的稳定在线投影。 | `build_structured_evidence()` | RetrievalCandidateV2、PromptEvidenceCandidate、Governance | 嵌入 candidate/trace | **是** | **Confirmed-by-code / Core** |
| L2 | `FactEvidenceSchema` + `FactStatus` | 同上 | 结构化事实断言与确定性状态（`unknown/estimated/proposed/planned/confirmed/decided`）。 | type-aware chunk data → structured projection | `resolve_fact_claim_eligibility()` | Embedded | **是** | **Confirmed-by-code / Core** |
| L2 | `TableEvidenceSchema`、`PolicyEvidenceSchema`、`TemplateEvidenceSchema` | 同上 | 用于 claim compatibility 检查的 typed structural evidence projection。 | `build_structured_evidence()` | Evidence Governance | Embedded | 是 | Candidate-core |
| L3 | `RetrievalAccessScopeSchema` | `backend/schemas/rag.py` | Retrieval 的 tenant/KB/doc/file 授权边界。 | E2E/mainline input | RAG request mapper/retriever | runtime/task identity | 对 evidence admissibility 有影响 | Candidate-core |
| L3 | `RAGToolInputSchema` | 同上 | 进入 RAG service 的稳定请求 Contract。 | SchemeEvidenceService / mainline | RAG service | trace/capture | 间接 | Supporting |
| L3 | `RetrievedChunkSchema` | 同上 | SchemeWriter 和 evidence bundle 使用的兼容/runtime retrieved chunk view。 | RAG result mapper / contract reader | SchemeWriter evidence service、citation construction | runtime/capture | 间接 | Supporting |
| L3 | `EvidenceBundleSchema` | 同上 | 稳定的 RAG evidence Contract，包含 selected/dropped evidence、citations、context、lineage 和 legacy assessment。 | `RAGEvidenceContractBuilder` / result mapper | SchemeWriter / document assembly | capture/runtime | 间接 | Candidate-core |
| L3 | `RetrievalCandidateV2Schema` | `backend/rag/schema/retrieval_candidate_v2.py` | 一条 retrieved child/parent lineage 的在线语义投影，同时携带权威 document identity 与 claim capability 镜像。 | `build_retrieval_candidate_v2()` | Evidence Governance V2 | governance artifact/trace | **是** | **Confirmed-by-code / Core** |
| L3 | `PromptEvidenceCandidateV1Schema` | `backend/rag/schema/prompt_evidence_candidate_v1.py` | Writer 真正可见的 prompt evidence 投影，用于保证 Governance 判断针对 Writer 实际看见的证据。 | prompt-evidence projection / result mapper | Evidence Governance V2 | governance artifact/trace | **是** | Candidate-core |
| L4 | `EvidenceRequirementsV2Schema` | `backend/rag/evidence/governance_v2.py` | 章节 evidence requirements：topics、requested claim types、risk/coverage input、policy id。 | `resolve_evidence_requirements_v2()` | item/set assessment、gate | governance artifact/trace | **是** | **Confirmed-by-code / Core** |
| L4 | `EvidenceAssessmentV2Schema` | `backend/rag/schema/evidence_assessment_v2.py` | 单条 evidence 的 eligibility-aware assessment，包含 supported/forbidden claim types。 | `_item_assessment()` | set assessment、gate | governance artifact/trace | **是** | **Confirmed-by-code / Core** |
| L4 | `EvidenceSetAssessmentSchema` | 同上 | Section-level topic coverage / conflict state。 | `_set_assessment()` | `_gate()` | governance artifact/trace | **是** | **Confirmed-by-code / Core** |
| L4 | `EvidenceConflictSchema` | 同上 | 记录尚未解决的 structured-fact conflict。 | `_conflicts()` | set assessment/gate | nested | 是 | Supporting |
| L4 | `GateDecisionSchema` | `backend/apps/enterprise_document/schemas/gate_decision.py` | 最终、可审计的 delivery mode 与 Writer claim boundary。 | Evidence Governance `_gate()` | WriterGateBinding / SchemeWriter | section artifact/trace | **是** | **Confirmed-by-code / Core** |
| L5 | `WriterGateBindingSchema` | `backend/apps/enterprise_document/schemas/writer_governance.py` | 将 section writer invocation 与最终 Stage D gate/evidence hash 做不可变绑定。 | writer governance binding builder | section generation + artifact integrity | section extra/artifact/trace | **是** | **Confirmed-by-code / Core** |
| L5 | `GeneratedClaimSchema` | 同上 | 生成后 sentence/span 的 claim annotation，并携带 `ClaimType`。 | `WriterClaimAuditor` / semantic classifier | ClaimAuditResult | section artifact | **是** | **Confirmed-by-code / Core** |
| L5 | `ClaimAuditViolationSchema` | 同上 | 单条 claim-policy violation 记录。 | `WriterClaimAuditor` | section/document integrity 与 report | section artifact | **是** | Supporting |
| L5 | `ClaimAuditIntegritySchema` | 同上 | 将 audit 与精确 content、gate binding、classifier policy 做加密绑定。 | `WriterClaimAuditor` | artifact integrity recomputation | section artifact | 是 | Candidate-core |
| L5 | `ClaimAuditResultSchema` | 同上 | Writer 侧最终证明：generated claims 是否遵守 Gate。 | `WriterClaimAuditor.audit()` / blocked audit path | section generation、artifact integrity、document gate | section extra/rejected artifact/trace | **是** | **Confirmed-by-code / Core** |
| L5 | `SchemeWriterInputSchema` | `backend/apps/enterprise_document/schemas/scheme_writer/generation.py` | SchemeWriter invocation 的稳定输入 Contract。 | stage service | SchemeWriter use case/coordinator | runtime/capture | 间接 | Supporting |
| L6 | `CitationSchema` | `backend/schemas/citation.py` | 可追踪的 evidence source 与 exact quote/location identity。 | RAG evidence mapper/context packer/citation registry | prompt、citation binding、grounding、document gate | evidence bundle/section/draft | **是** | **Confirmed-by-code / Core** |
| L6 | `CitationBindingSchema` | 同上 | 将一个 citation source 绑定到 generated target claim/paragraph location。 | `CitationService.bind_citations()` | grounding/integrity/document assembly/gate | section/draft | **是** | **Confirmed-by-code / Core** |
| L6 | `SectionEvalSchema` | `backend/apps/enterprise_document/schemas/scheme_writer/evaluation.py` | 生成后的 deterministic/semantic section checks。 | section quality evaluator/generation service | SchemeSection + document gate | section artifact | 是 | Candidate-core |
| L6 | `SemanticGateResultSchema` | 同上 | Conditional path 启用时的 semantic judge result。 | semantic section judge | section eval | nested | 是 | Conditional |
| L7 | `SchemeSectionSchema` | `backend/apps/enterprise_document/schemas/scheme_writer/document.py` | 标准 generated section artifact，包含 content、citations、bindings、eval 和 `extra` governance data。 | section generation/coordinator | document assembler/verifier/gate | task/runtime/rejected draft | **是** | **Confirmed-by-code / Core** |
| L7 | `SchemeDraftSchema` | 同上 | 由 SchemeSection artifacts 组装出的完整生成草稿。 | DocumentAssembler | verifier、hard gate、publication/rejected artifact | rejected/deliverable artifact | **是** | **Confirmed-by-code / Core** |
| L7 | `SectionEvidenceBundleSchema` | `backend/apps/enterprise_document/schemas/scheme_writer/evidence.py` | Per-section evidence/context/citation package，并携带 evidence hash 与 writer binding projection。 | SchemeEvidenceService/coordinator | section writer、document assembly | runtime/output | 是 | Candidate-core |
| L7 | `HardGateResultSchema` | `backend/apps/enterprise_document/schemas/scheme_writer/evaluation.py` | 最终 document gate result，以及 checks/failures。 | `evaluate_scheme_draft()` | release/publication/output routing | E2E report/output | **是** | **Confirmed-by-code / Core** |
| L7 | `RejectedDraftArtifactSchema` | `backend/apps/enterprise_document/schemas/scheme_writer/output.py` | 持久化 rejected draft 与 Hard Gate 原因。 | stage service output assembly | E2E/report/user-facing non-deliverable route | runtime root | 是 | Candidate-core |
| L7 | `DeliverableFinalResultSchema` | 同上 | 已通过 Gate 的 deliverable wrapper，以及可选 release publication。 | stage service | output/release path | runtime/release | 是但位于 Gate 之后 | Conditional |
| L7 | `SchemeWriterOutputSchema` | 同上 | SchemeWriter 的稳定 output envelope。 | stage service | top-level workflow/mainline | runtime/capture | 间接 | Supporting |
| L8 | `RunTraceEventSchema` | `backend/schemas/trace.py` | 生产执行的持久化 event/lineage record。 | run trace recorder | audit/debug/evaluation | `runs/*_trace.jsonl` | 仅观察，不是 policy authority | Supporting but important |
| L8 | `TaskStateRecordSchema` / `TaskStateSnapshotSchema` | `backend/schemas/task_state.py` | 持久化 task lifecycle/status state。 | task state store/mainline | resume/status/runtime | task state JSONL | 不直接控制 claim policy | Supporting |
| L8 | `ReleaseEligibilityProofSchema` / `ReleaseManifestSchema` | `backend/apps/enterprise_document/schemas/release_publication.py` | Hard Gate 之后的 release-time proof/manifest。 | release publication service | final publication validator | release artifacts | 是但位于 Gate 后 | Conditional |
| — | `backend/apps/enterprise_document/schemas/agent_rag_v1/*` 大范围 multi-agent memory/execution contracts | 多个 | Multi-Agent V1 execution/memory Contract。 | agent runtime | agent runtime | 不同 Contract 不同 | 除少数 section/document closure Contract 外，不直接属于当前 evidence→claim 根因链 | Supporting / 本 Phase Core 排除 |
| — | `backend/rag/ports/quality.py::EvidenceAssessment` 与 correction-gate dataclasses | `backend/rag/ports/quality.py` | 与 Evidence Governance V2 不同的 Legacy/CRAG plugin quality interface。 | CRAG plugins | parent-child corrective retrieval | runtime | 不是 Stage D gate authority | Legacy/Supporting |
| — | `backend/rag/schema/eval_schema.py` | 同上 | 仅供 Evaluation 使用的 dataclasses。 | eval code | eval code | eval output | 否 | 本审计视为 Test/evaluation-only |

## Core 候选清单

### Confirmed-by-code

1. `ProjectInputSchema`
2. `DocumentProfileSchema`
3. `ContentCapabilitiesSchema`
4. 共享语义词汇：`DocumentType`、`EvidenceRole`、`ProjectRelation`、`AuthorityLevel`、`ClaimType`
5. `StructuredEvidenceSchema`
6. `FactEvidenceSchema` / `FactStatus`
7. `RetrievalCandidateV2Schema`
8. `EvidenceRequirementsV2Schema`
9. `EvidenceAssessmentV2Schema`
10. `EvidenceSetAssessmentSchema`
11. `GateDecisionSchema`
12. `WriterGateBindingSchema`
13. `GeneratedClaimSchema`
14. `ClaimAuditResultSchema`
15. `CitationSchema`
16. `CitationBindingSchema`
17. `SchemeSectionSchema`
18. `SchemeDraftSchema`
19. `HardGateResultSchema`

### Candidate-core，等待 Owner 复核

1. `SourceMaterialSchema`
2. `CleanedTextUnitSchema`
3. Parent/child Chunk dictionary contracts
4. `TableEvidenceSchema`、`PolicyEvidenceSchema`、`TemplateEvidenceSchema`
5. `RetrievalAccessScopeSchema`
6. `EvidenceBundleSchema`
7. `PromptEvidenceCandidateV1Schema`
8. `ClaimAuditIntegritySchema`
9. `SectionEvalSchema`
10. `SectionEvidenceBundleSchema`
11. `RejectedDraftArtifactSchema`

## 本 Phase 明确不纳入 Core 的内容

- 大范围 `agent_rag_v1` memory/router/recovery contracts，除非当前 Scheme Generation 的 evidence/claim transformation 直接依赖某个具体 artifact。
- Evaluation-only schemas。
- Test fixtures 与 fake tool DTOs。
- Legacy CRAG quality dataclasses 不作为权威 evidence gate；它们可能影响 retrieval，但 Stage D 的 `EvidenceAssessmentV2Schema` / `GateDecisionSchema` 才是 Writer boundary。
- 未来 Multi-Agent Memory、DurableMemoryService、ContextCompiler V2、Evaluation Foundation、安全/UI/微服务设计。

## Step 1 结论

当前生产架构并不是一个单体 Schema，而是一条 Contract 链：**持久化 Document identity → typed index evidence → retrieval projection → evidence governance → writer claim governance → citation grounding → section/draft hard-gate artifacts**。

因此，后续根因审计必须追踪这些 Contract 之间的 transformation，不能只孤立地审查某一个 class 定义。
