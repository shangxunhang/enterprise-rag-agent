# RAG–Agent Evidence Contract Authority Audit

日期：2026-08-13  
阶段：P0-A Contract Freeze / Schema Authority Audit  
代码基线：`94b2a5b`  
审计范围：当前 HEAD 的生产代码与下列唯一架构/实施依据；未修改任何生产代码、Schema、测试或行为。

- `docs/RAG_AGENT_EVIDENCE_INTERFACE_CLOSURE_IMPLEMENTATION_PLAN_20260813.md`
- `docs/RAG架构_V2_收敛版_20260809.md`
- `docs/多agent架构_V2_收敛版_20260809.md`
- `docs/MULTI_AGENT_V2_IMPLEMENTATION_PLAN_20260809.md`

本文中的 **Current Owner** 只表示当前代码的定义/控制位置；**Candidate Target Owner** 与 **Codex 推荐** 都只是供人工冻结时选择的建议，不构成架构冻结决定。

---

# 1. Executive Summary

1. `RAGServicePort.retrieve(RAGToolInputSchema) -> EvidenceBundleSchema` 已经是生产 RAG 服务的稳定入口；真实 `RAGService` 通过 `RAGRequestMapper -> RetrievalRuntime -> RAGResultMapper` 返回该类型。
2. `EvidenceBundleSchema` 是当前最接近 canonical Evidence 的对象：它拥有 `E*` evidence identity、选中/丢弃集合、Citation、Context、Lineage、Assessment 和一致性校验；`RAGEvidenceContractSchema` 只是同一 Python 类的直接 alias，不是第二份定义。
3. canonical 角色尚未真正唯一：主链把完整 Bundle 复制进 `RAGContext.extra["evidence_contract"]`、normalized dict、Research payload、Section bundle 和 section state，形成多处可独立漂移的 Evidence truth/projection。
4. 当前唯一直接的 Evidence requirement Schema 是 RAG internal 的章节聚合型 `EvidenceRequirementsV2Schema`；它与 RAG V2 文档中的逐项 `EvidenceRequirement` 字段模型不一致，且由 Application 的 `SchemeEvidenceService` 调用 RAG internal resolver 创建后再经 `extra` 传回 RAG。
5. Evidence status 存在两套并行枚举：public Bundle 使用 `EvidenceAssessmentStatus`，V2 governance/Application Gate 使用 RAG internal `EvidenceStatus`；`ABSENT`/`CONFLICTED` 被映射并压缩成 public `INSUFFICIENT`，信息有损。
6. `DeliveryMode` 和 `GateDecisionSchema` 定义在 Application/Gate，但 RAG governance 反向 import 并直接构造它们；当前 RAG 同时承担 Evidence Assessment 与 Application Delivery Decision。
7. `ClaimType` 定义在 `rag.catalog`，但已经被 RAG candidate/governance、Application Gate、Writer schema、Writer classifier/audit 共同使用，是事实上的跨边界类型，最终 ownership/public export 仍需人工冻结。
8. 未发现正式 `RetrievalAction` public Schema/Enum。当前只有 RAG internal `RetrievalPlan`/`QueryTransformMode`/corrective plan，以及 Agent/Application 的 `ResearchRecommendedAction`、`VerifierAction.RESEARCH_MORE`、`RouterAction.RESEARCH_MORE`；它们不是同一语义，不应在 P0-A 合并或实现 V2 行为。
9. 已确认 14 条（12 个生产文件）Application/Agent/ContextManager -> 指定 `rag.*` internal import，以及 1 条 RAG -> `apps.enterprise_document.*` 反向 import；扫描排除了测试目录。
10. `MainlineResearchToolGateway` 把 `citation_id`（无 Citation 时尝试 chunk id）写入 `accepted_evidence_ids`，而 canonical Bundle 的 evidence id 是 `E*`；ID namespace 污染已在 Research payload/state 中继续传播。

---

# 2. Current Call Chain

## 2.1 当前真实主链

```text
[Application / Section Graph]
SectionExecutionGraph._invoke_research_agent
  backend/apps/enterprise_document/workflows/section_execution_graph.py:275-321
        |
        v
[Agent]
MainlineSectionAgentAdapter.research -> ResearchAgent
  backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_adapter.py:803-873
        |
        v
[Application Tool Boundary]
MainlineResearchReasoner -> MainlineResearchToolSession
  -> MainlineResearchToolGateway.invoke_bound
  backend/apps/enterprise_document/services/scheme_writer/agent_integration/section_agent_ports.py:90-123,137-177,238-247
        |
        v
[Application]
SchemeEvidenceService.retrieve
  -> builds EvidenceRequirementsV2 via RAG internal resolver
  -> builds RAGToolInputSchema
  backend/apps/enterprise_document/services/scheme_writer/evidence_service.py:115-220
        |
        v
[Public Contract]
RAGServicePort.retrieve(RAGToolInputSchema) -> EvidenceBundleSchema
  backend/contracts/rag.py:15-32
        |
        v
[RAG Internal]
RAGService.retrieve
  -> RAGRequestMapper.map
  -> RetrievalRuntime.retrieve(dict)
  -> active ParentChildRAGEngine/retrieval pipeline
  -> RAGResultMapper.map
  -> RAGEvidenceContractBuilder.build
  backend/rag/services/rag_service.py:162-174
  backend/rag/mapping/request_mapper.py:43-176
  backend/rag/runtime/retrieval_runtime.py:300-305
  backend/rag/mapping/result_mapper.py:320-327,516-550
        |
        v
[Public Contract]
EvidenceBundleSchema / RAGEvidenceContractSchema alias
  backend/schemas/rag.py:508-680
        |
        v
[Application projections and hidden channels]
SchemeEvidenceService.extract_rag_output
  -> RAGContext + RetrievedChunk + Citation + normalized dict
  -> context.extra["evidence_contract"] + SHA + lineage
DocumentCitationRegistry remaps Citation IDs and rewrites the copied contract
  backend/apps/enterprise_document/services/scheme_writer/evidence_service.py:223-245
  backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py:180-251
        |
        +-----------------------+
        |                       |
        v                       v
[Agent projection]          [Application projection]
ResearchResultPayload       SectionEvidenceBundle
ResearchEvidencePackage    Section/Document assembly state
  payloads.py:41-64          evidence.py:13-34
        |                       |
        +-----------+-----------+
                    v
[Gate / Writer / Verifier]
GateDecision -> WriterGateBinding -> generation / claim audit / artifact integrity
Section graph routes research_more / generate / finalize
DocumentAssembler aggregates section evidence status
  governance_v2.py:755-900
  writer_governance.py:413-458
  section_execution_graph.py:937-1019,1762-1796
  document_assembler.py:84-172
```

## 2.2 重要分支

- **Document-level precomputed path**：`SchemeGenerationStageService` 可先直接调用 `SchemeEvidenceService`，再把 document Context/Chunks/Citations/Assessment 投影给 Section graph；此时 Research invocation 可能使用 `_UnboundResearchToolSession`，不再次调用工具。证据：`stage_service.py:400-449`、`section_agent_ports.py:126-134`。
- **Section/corrective path**：ResearchAgent 获得 `knowledge.retrieve` 权限后经 `MainlineResearchToolGateway` 调用 `SchemeEvidenceService`。证据：`section_agent_ports.py:157-177,238-247`。
- **RAG internal correction**：RAG 内部存在 bounded correction gate 和 corrective query plan，但它们是内部检索实现，不是 Research -> RAG public `RetrievalAction`。证据：`rag/ports/quality.py:57-102,119-140`、`rag/application/parent_child_retrieval.py:255-329`。

## 2.3 边界判定

| Layer | 当前对象 | 判定 |
|---|---|---|
| Public Contract | `contracts.rag.RAGServicePort`, `schemas.rag.RAGToolInputSchema`, `EvidenceBundleSchema`, access scope | 已存在，但 request 的业务核心语义仍藏在 `extra` |
| Application | `SchemeEvidenceService`, citation registry, Section graph, assembly | 同时承担 public adapter、projection、compatibility 和部分 authority 重建 |
| Agent | `ResearchAgent`, Research invocation/result payload | 消费/产生 compatibility projection；当前不持有 canonical Bundle 对象 |
| RAG Internal | mapper/runtime/engine/governance/internal schemas | 已有内部端口，但 governance 反向构造 Application Gate |
| Gate | `GateDecisionSchema`, `DeliveryMode`, WriterGateBinding, hard gate | 定义在 Application；其决策当前由 RAG governance 产生并嵌入 Bundle extra |

---

# 3. Contract Inventory

| Contract / Schema | Current Location | Current Owner | Producer | Consumers | Current Role | Cross Boundary? |
|---|---|---|---|---|---|---|
| `RAGServicePort` | `backend/contracts/rag.py:15-32` | contracts | Bootstrap provides `RAGService`/Fake/Observed | `SchemeEvidenceService`, LangChain adapters, bootstrap | Application-facing service port | Yes: Application -> RAG |
| `RAGToolInputSchema` | `backend/schemas/rag.py:149-189` | public schemas | `SchemeEvidenceService` | Port, Service, RequestMapper, facade | Public retrieval request, with `extra` side-channel | Yes |
| `EvidenceBundleSchema` | `backend/schemas/rag.py:508-680` | public schemas / RAG boundary | `RAGResultMapper`, Fake/legacy builder | Service caller, projections, LangChain | Closest current canonical EvidencePackage | Yes |
| `RAGEvidenceContractSchema` | `backend/schemas/rag.py:680` | public schemas | Alias only | Builder/Reader, Application compatibility code, ContextManager | Exact alias of `EvidenceBundleSchema` | Yes; no separate class |
| `RAGContextSchema` | `backend/schemas/rag.py:257-290` | public schemas | Contract builder / ResultMapper | ContextManager, Section/Writer, assembly | Context projection, but currently carries copied canonical contract | Yes |
| `RetrievedChunkSchema` | `backend/schemas/rag.py:192-254` | public schemas | EvidenceMapper / ContractReader | Section bundle, document assembly, citation binding | Retrieval/internal compatibility projection | Yes in current code |
| `CitationSchema` | `backend/schemas/citation.py:17-63` | public schemas | EvidenceMapper, legacy adapter, registry remap | Bundle, prompts, Writer, assembly | Source/citation projection | Yes |
| `CitationBindingSchema` | `backend/schemas/citation.py:66-103` | public schemas | document assembly/binding services | Writer verification / document output | Claim-to-citation binding projection | Application boundary |
| `EvidenceRequirementsV2Schema` | `backend/rag/evidence/governance_v2.py:54-82` | RAG internal governance | `resolve_evidence_requirements_v2`; called directly by Application and again in RAG | RAG assessment and gate | Current section-aggregate requirement contract | Yes in fact; not public-exported |
| `EvidenceStatus` | `backend/rag/schema/evidence_assessment_v2.py:34-39` | RAG internal schema | RAG governance set assessment | GateDecision, ResultMapper, governance | Detailed V2 evidence set status | Yes: Application imports internal enum |
| `EvidenceAssessmentStatus` | `backend/schemas/rag.py:359-369` | public schemas | ResultMapper/Fake/contract builder | Bundle consumers | Public Bundle's coarser status | Yes; parallel to `EvidenceStatus` |
| `EvidenceSetAssessmentSchema` | `backend/rag/schema/evidence_assessment_v2.py:124-214` | RAG internal schema | RAG governance | ResultMapper via serialized governance | Coverage/conflict authority inside RAG | Crosses via dict in Bundle extra |
| `ResearchEvidencePackageSchema` | `backend/apps/enterprise_document/schemas/agent_rag_v1/payloads.py:41-51` | Agent/Application | Mainline/standalone Research reasoners | ResearchAgent, Section graph/state | Parallel evidence summary + Agent decision | Yes: RAG projection -> Agent |
| `ResearchResultPayloadSchema` | `backend/apps/enterprise_document/schemas/agent_rag_v1/payloads.py:53-64` | Agent/Application | Research reasoners | ResearchAgent, Section graph/state | Result envelope with ref/hash/round plus summary | Agent/Application |
| `SectionEvidenceBundleSchema` | `backend/apps/enterprise_document/schemas/scheme_writer/evidence.py:13-34` | Application | Section graph/coordinator | assembly, output, stage report | Section compatibility bundle; duplicates context/chunks/citations/status/gate | Application internal, sourced across RAG boundary |
| `GateDecisionSchema` | `backend/apps/enterprise_document/schemas/gate_decision.py:40-149` | Application/Gate | Currently `rag.evidence.governance_v2._gate` | Writer binding, Section graph, artifact integrity | Application delivery/generation decision | Yes because RAG constructs it |
| `DeliveryMode` | `backend/apps/enterprise_document/schemas/gate_decision.py:25-30` | Application/Gate | RAG governance currently selects value | Writer/Gate/Section services | Application delivery decision | Improperly crosses RAG -> App |
| `ClaimType` | `backend/rag/catalog/document_profile.py:66-80` | RAG catalog | RAG classifier/governance and Writer classifier | RAG candidate/assessment, Gate, Writer schemas/services | Factually shared domain taxonomy | Yes |
| `RetrievalAction` | **No formal definition found** | Not frozen | N/A | N/A | Planned public concept only | Planned |
| `RetrievalPlan` / `QueryTransformMode` | `backend/rag/planning/retrieval_planner.py:9,28-45` | RAG internal | `AdaptiveRetrievalPlanner` | RAG engine/runtime | Internal `identity|multi_query|hyde` plan + correction budget | No public boundary intended |
| `CorrectionGateDecision` / `CorrectiveQueryPlan` | `backend/rag/ports/quality.py:57-102` | RAG internal ports | RAG correction gate/planner plugins | RAG retrieval application service | Bounded internal correction decision/query list | No public boundary intended |
| `ResearchRecommendedAction` | `backend/apps/enterprise_document/schemas/agent_rag_v1/enums.py:49-53` | Agent/Application | Research reasoner | ResearchAgent/Section graph/router | Post-retrieval routing (`write`, `research_more`, etc.), not retrieval intent | Agent/Application |

## 3.1 EvidenceRequirement：当前到底有几套

严格按当前生产代码区分，存在 **1 套直接 Requirement Schema、2 套相关但语义不同的 typed projection/state，以及 1 条序列化传输副本**：

1. **直接 requirement Schema**：`EvidenceRequirementsV2Schema`，字段为 `schema_version, policy_id, section_id, section_type, required_topics, requested_claim_types, claim_risk_level, coverage_strategy, allow_framework_only, metadata`。它是“一个 section 的聚合要求”，不是 RAG V2 文档中的“一条 requirement”。定义：`governance_v2.py:54-82`。
2. **Agent request projection**：`ResearchInvocationInputSchema.evidence_topics`（连同 `section_id`、`research_mode`、`query` 等），只表达 topic list，Mainline 目前用 `[section_title]` 创建。定义/创建：`invocations.py:35-59`、`section_agent_adapter.py:803-817`。
3. **Application satisfaction state**：`SectionEvidenceRequirementSchema(section_title, assessment_status, generation_blocked, generation_block_reason, requirement_satisfied)`。它是装配后的“要求是否满足”记录，不是检索 requirement 定义；由 `DocumentAssembler` 创建并供 hard gate/report 消费。定义/创建：`document_assembly.py:47-59`、`document_assembler.py:107-166`。
4. **序列化副本**：Application 创建的 `EvidenceRequirementsV2Schema.model_dump()` 写入 `RAGToolInputSchema.extra.extra_metadata.evidence_requirements`，RAG RequestMapper 再传入 runtime，governance 又从 dict 重建 instance。证据：`evidence_service.py:155-168,214-218`、`request_mapper.py:134-163`、`governance_v2.py:199-256`。

创建/消费事实：

- `SchemeEvidenceService` 是主线第一个 instance 创建者，但它通过 direct import 调用 RAG internal resolver，而不是 Planner 通过 public contract 创建（`evidence_service.py:19-20,155-168`）。
- `OnlineEvidenceGovernanceV2._finish` 会再次调用 resolver 并用结果做 item/set assessment 和 gate（`governance_v2.py:907-950`）。
- `RAGResultMapper` 根据 runtime context 是否含 serialized requirements 启用 strict prompt contract（`result_mapper.py:352-366`）。
- 当前字段与 RAG V2 规划 `requirement_id, section_id, topic, purpose, importance, expected_source_types` 不一致：只有 `section_id` 可直接对齐；`required_topics` 是 list，且没有 `requirement_id/purpose/importance/expected_source_types`。

**Authority 对齐判断：PARTIAL。** 定义在 RAG，符合“RAG owns schema”；实际实例由 Application service 而非 Planner 产生、位置不是 public contract，字段模型也未与 V2 文档冻结。因此不能宣称当前 authority 已与 RAG V2 完全一致。

## 3.2 EvidenceStatus

- 详细 V2 `EvidenceStatus` 定义于 `backend/rag/schema/evidence_assessment_v2.py:34-39`，消费者包括 RAG set assessment/governance、`RAGResultMapper` 和 Application `GateDecisionSchema`。
- 它已经跨 Application 使用：`gate_decision.py:15,45,84-147` 直接 import/校验此 RAG internal enum。
- public Bundle 另有 `EvidenceAssessmentStatus`：`not_assessed|sufficient|partial|insufficient`；V2 则是 `sufficient|partial|absent|conflicted|not_assessed`。
- `RAGResultMapper` 将 V2 `ABSENT` 和 `CONFLICTED` 均映射为 Bundle `INSUFFICIENT`，详细状态只留在 `assessment.details.evidence_governance_v2` / Bundle extra dict（`result_mapper.py:277-315`）。
- 是否公开 `EvidenceStatus`、公开位置、是否让 Bundle 直接使用它，均待人工确认；本轮不复制第三个 Enum。

## 3.3 EvidencePackage

- `EvidenceBundleSchema` 已在事实上承担 canonical 角色：Port 直接返回它，Builder 创建稳定 `E*` ID，并由 schema validator强制 evidence/citation/context 一致性（`contracts/rag.py:22`、`rag.py:508-657`、`contract.py:242-316`）。
- `RAGEvidenceContractSchema = EvidenceBundleSchema` 是直接 alias（`rag.py:680`），不是平行 authority。
- 实际平行 truth/副本仍存在：`RAGContext.extra.evidence_contract`、normalized `evidence/context/retrieved_chunks/citations`、Research summary/state、Section bundle/state、Bundle extra 内 serialized governance/gate，以及 legacy `RAGToolOutputSchema` 的 projection 字段。
- `DocumentCitationRegistry` 从 `RAGContext.extra` 取出完整 contract，重写 Citation IDs、items、context 后重新 `model_validate` 并写回，说明 hidden copy 不是被动 metadata，而是可生成新版本的实际 authority path（`document_citation_registry.py:190-234`）。
- 当前 Bundle 尚不直接具备 RAG V2 文档的 `section_id, evidence_status, stop_reason, requirements, coverage, conflicts, missing_requirements, research_rounds, tool_calls, package_hash` 完整字段，因此“以现有类作为唯一 canonical identity”和“冻结其最终字段”是两个不同决定。

## 3.4 DeliveryMode

- 定义和主要消费者都在 Application/Gate：`gate_decision.py:25-30`、Writer/Section/Artifact services。
- RAG 依赖点集中在 `rag/evidence/governance_v2.py:21-24,755-900`：RAG 根据 Evidence status、claim risk、authority、qualifier、human review 条件直接选择 `GROUNDED/QUALIFIED/FRAMEWORK_ONLY/BLOCKED/HUMAN_REVIEW` 并构造 `GateDecisionSchema`。
- Evidence Assessment 语义：`status, coverage_ratio, covered/missing topics, conflicts, eligible evidence, reason codes`。
- Application Gate Decision 语义：`delivery_mode, human_review_required, allowed/forbidden claim types, required_qualifiers, generation_allowed`。
- 当前代码把两类语义放在同一个 RAG governance result 中；规划要求拆开，但本轮只记录，不改变 Gate 行为。

## 3.5 RetrievalAction

- **正式 public Schema/Enum：NOT FOUND。** 全仓生产代码未定义名为 `RetrievalAction` 的类型。
- RAG internal 已实现：`RetrievalPlan` 选择 `identity|multi_query|hyde` 和 correction budget；`CorrectionGateDecision` 决定是否补检索；`CorrectiveQueryPlan` 提供 query list；bounded loop 在 RAG 内运行。这是内部实现，不是 Agent public action。
- Agent/Application 已实现：`ResearchRecommendedAction.RESEARCH_MORE`、Verifier/Router 的 `RESEARCH_MORE`，用于工作流“是否再研究”的后置路由；Mainline tool 本身只有 `knowledge.retrieve`，没有 `retrieve|rewrite|expand|targeted_retrieve|stop` typed action。
- 结论：当前为 **PARTIAL（内部能力 + workflow routing 已存在，public boundary 未存在）**；本轮禁止实现或伪装成 RetrievalAction V2。

## 3.6 ClaimType

- 定义：`backend/rag/catalog/document_profile.py:66-80`，13 个值。
- RAG：document profile/registry、retrieval candidate、item/set assessment、governance policy 使用它决定 evidence capability/eligibility（例如 `evidence_assessment_v2.py:50-105`）。
- Application/Gate：`GateDecisionSchema.allowed_claim_types/forbidden_claim_types` 直接使用它（`gate_decision.py:10-15,48-49`）。
- Writer：Writer governance schema、semantic classifier、claim audit 使用同一 enum 对句子分类并执行 allowed/forbidden checks（`schemas/writer_governance.py:15,157-198`、`semantic_claim_classifier.py:19,30-42,288`、`writer_governance.py:356-396,472-497`）。
- 证据说明它已经是事实上的 cross-boundary contract；但定义位置和最初 RAG catalog 用途也支持“RAG domain taxonomy”的解释。最终应保留在 RAG、迁到 public contract，还是拆成更稳定的 shared domain taxonomy，必须由 Human Freeze 决定。

---

# 4. Field-Level Authority Mapping

“Candidate Target Owner”仅为候选；`Move/Deprecate` 也只是后续迁移建议，不授权本轮修改。

## 4.1 RAGToolInputSchema

| Field | Current Owner | Producer | Consumers | Candidate Target Owner | Keep / Move / Project / Internal / Deprecate | Evidence |
|---|---|---|---|---|---|---|
| `schema_version` | public schemas | default | validators/serialization | RAG Public Contract | Keep | `schemas/rag.py:155-156` |
| `task_id` | public schemas | SchemeEvidenceService | RequestMapper, Service, trace | RAG Public Contract | Keep | `rag.py:158-161`; `evidence_service.py:199-203` |
| `run_id` | public schemas | SchemeEvidenceService | RequestMapper, Service, trace | RAG Public Contract | Keep | same |
| `agent_name` | public schemas | SchemeEvidenceService | RequestMapper `caller_agent` | RAG Public Contract | Keep | `rag.py:162-163`; `request_mapper.py:140-143` |
| `query` | public schemas | Application/Research | RequestMapper/runtime | RAG Public Contract | Keep | `rag.py:165-166`; `request_mapper.py:43-46` |
| `rewritten_queries` | public schemas | caller or runtime fallback | ResultMapper; trace | RAG internal transformation output (candidate) | Move/Internal; compatibility risk | `rag.py:167-168`; `result_mapper.py:333-338` |
| `kb_ids` | public schemas | Application | RequestMapper narrows authorized scope | Public Contract | Keep as narrowing/compatibility | `rag.py:170-173`; `request_mapper.py:76-84` |
| `access_scope` | public schemas / trusted boundary | Application security boundary | RequestMapper enforcement | Trusted Application + RAG Public | Keep | `rag.py:172-173`; `request_mapper.py:60-121` |
| `filters` | public schemas | Application | RequestMapper, only narrows scope | Public Contract | Keep/Project; legacy tenant duplication | `rag.py:175-178`; `request_mapper.py:55-106` |
| `need_citation` | public schemas | Application requirements | RequestMapper/runtime | Public Contract | Keep | `rag.py:180-181`; `request_mapper.py:152` |
| `max_context_chars` | public schemas | Application | mapper/context packer | RAG Public budget | Keep | `rag.py:183-184`; `request_mapper.py:52,156` |
| `max_context_items` | public schemas | Application | mapper/context gate | RAG Public budget | Keep | `rag.py:185-186`; `request_mapper.py:53,157` |
| `extra` | public schemas, uncontrolled dict | Application; Mapper enriches nested copy | Service, mapper, runtime planner/governance/context/correction | Split by semantics after Human Freeze | Move stable fields / Internal config / keep true metadata | `rag.py:188-189`; §7 |

## 4.2 EvidenceBundleSchema

| Field | Current Owner | Producer | Consumers | Candidate Target Owner | Keep / Move / Project / Internal / Deprecate | Evidence |
|---|---|---|---|---|---|---|
| `schema_version` | public schemas | default | all consumers | RAG Public EvidencePackage | Keep | `rag.py:516-517` |
| `query` | RAG boundary | ResultMapper/Builder | Application/trace | RAG Public | Keep | `rag.py:519-520` |
| `rewritten_queries` | RAG boundary | runtime/ResultMapper | trace/Application | RAG Public result trace or internal trace | Keep/Move pending freeze | `rag.py:521-522`; `result_mapper.py:333-338` |
| `task_id` | RAG boundary | RAGService/ResultMapper | Application/trace | RAG Public | Keep | `rag.py:524-527`; `result_mapper.py:550-554` |
| `run_id` | RAG boundary | RAGService/ResultMapper | Application/trace | RAG Public | Keep | same |
| `status` | RAG boundary | RAGService/ResultMapper | caller | RAG Public execution status | Keep; distinct from evidence status | `rag.py:528-529` |
| `retrieval_trace_id` | RAG boundary | Service/ResultMapper | Gateway/state | RAG Public lineage | Keep | `rag.py:530-531`; `result_mapper.py:546-554` |
| `items` | RAG boundary | ContractBuilder | projections/validator | RAG Public canonical Evidence | Keep | `rag.py:533-534`; `contract.py:242-297` |
| `selected_evidence_ids` | RAG boundary | ContractBuilder | validator/projections | RAG Public canonical identity | Keep | `rag.py:535-536`; `contract.py:252,296` |
| `dropped_evidence_ids` | RAG boundary | ContractBuilder | validator/audit | RAG Public canonical identity | Keep | `rag.py:537-538`; `contract.py:272,297` |
| `citations` | public citation schema | EvidenceMapper/Builder | prompt/Writer/assembly | Citation projection owned by public contract | Project | `rag.py:540-541` |
| `context` | public context schema | Builder/ResultMapper | ContextManager/Writer | Derived projection | Project; prohibit reverse authority | `rag.py:542-543`; `contract.py:178-214` |
| `lineage` | RAG boundary | Builder/ResultMapper | audit/report | RAG Public lineage | Keep | `rag.py:544-545` |
| `assessment` | RAG boundary public facade | ResultMapper | Gateway/Gate/Writer | RAG Evidence Assessment public view | Keep but status model needs Human Freeze | `rag.py:546-549`; `result_mapper.py:277-315` |
| `correction_trace` | RAG boundary | Service/ResultMapper | observability/budget | RAG internal trace projected outward | Internal/Project | `rag.py:551-552`; `rag_service.py:61-80` |
| `budget_usage` | RAG boundary | Service/ResultMapper | observability/Research | RAG Public diagnostics | Keep/Project | `rag.py:553-554` |
| `trace` | RAG boundary | ResultMapper/runtime | observability | RAG diagnostic projection | Project | `rag.py:555-556` |
| `warnings` | public common | Service/ResultMapper | caller | Public Contract | Keep | `rag.py:557-558` |
| `error` | public common | Service | caller | Public Contract | Keep | `rag.py:559-560` |
| `extra` | RAG boundary dict | ResultMapper/Service | Application/Writer | Split: stable V2 fields vs internal trace | Move/Internal; do not grow authority in dict | `rag.py:562-563`; `result_mapper.py:565-574` |

## 4.3 EvidenceRequirementsV2Schema

| Field | Current Owner | Producer | Consumers | Candidate Target Owner | Keep / Move / Project / Internal / Deprecate | Evidence |
|---|---|---|---|---|---|---|
| `schema_version` | RAG internal governance | resolver/default | serialized transport/governance | RAG Public Requirement | Keep | `governance_v2.py:54-57` |
| `policy_id` | RAG internal governance | resolver | GateDecision policy id | RAG policy/internal or public ref | Keep/Project | `governance_v2.py:58,234-236,881-883` |
| `section_id` | RAG internal governance | Application context/resolver | set assessment/gate | RAG Public Requirement | Keep | `governance_v2.py:59,207-221,238` |
| `section_type` | RAG internal governance | resolver | coverage assessment | Requirement/public or policy input | Keep pending mapping | `governance_v2.py:60,218-239` |
| `required_topics` | RAG internal governance | resolver | coverage computation | RAG Public Requirement (may become item `topic`) | Move/reshape after Human Freeze | `governance_v2.py:61,223-240,676-718` |
| `requested_claim_types` | RAG internal + ClaimType | resolver/caller | currently recorded but explicitly ignored for resolution | Public requirement only if semantics frozen | Deprecate or fix later; Human Decision | `governance_v2.py:62,226-248` |
| `claim_risk_level` | imports Application Gate enum | resolver currently forces LOW | gate risk recomputed from supported claims | Application/Gate candidate | Move/Deprecate from requirement | `governance_v2.py:63,242,771` |
| `coverage_strategy` | RAG internal | resolver | set assessment | RAG internal policy or public intent | Internal/Project | `governance_v2.py:64,228-243` |
| `allow_framework_only` | RAG internal requirement | resolver | Gate/Delivery logic context | Application Gate candidate | Move pending Human Freeze | `governance_v2.py:65,244` |
| `metadata` | RAG internal dict | resolver | governance/gate | RAG internal extension | Internal | `governance_v2.py:66,245-255` |

## 4.4 ResearchEvidencePackageSchema

| Field | Current Owner | Producer | Consumers | Candidate Target Owner | Keep / Move / Project / Internal / Deprecate | Evidence |
|---|---|---|---|---|---|---|
| `schema_version` | Agent/Application | Research reasoner | Agent/Section graph | Agent compatibility | Keep during migration | `payloads.py:41-42` |
| `section_id` | Agent/Application | invocation/reasoner | ResearchAgent/state | Agent result | Keep | `payloads.py:43` |
| `accepted_evidence_ids` | Agent/Application | Gateway/reasoner | state/routes | Canonical Package refs only | Project, but current IDs are wrong namespace | `payloads.py:44`; `section_agent_ports.py:333-366` |
| `rejected_evidence` | Agent/Application | reasoner | Research state | Canonical rejected summary projection | Project | `payloads.py:45`; `section_agent_reasoners.py:194-220` |
| `supported_topics` | Agent/Application | copied from invocation topics | Research state/consumers | Canonical assessment projection | Project; currently not evidence-derived | `payloads.py:46`; `section_agent_reasoners.py:221` |
| `evidence_gaps` | Agent/Application | reasoner from assessment | routes/state | Canonical missing requirements projection | Project | `payloads.py:47`; `section_agent_reasoners.py:148-192,222` |
| `conflicts` | Agent/Application | reasoner/tool error | routes/state | Canonical conflicts projection or Agent error | Split/Project | `payloads.py:48`; `section_agent_reasoners.py:223-231` |
| `research_status` | Agent/Application | reasoner | ResearchAgent/Section graph | Agent decision/status | Keep; not EvidenceStatus authority | `payloads.py:49`; `research.py:60-69` |
| `recommended_action` | Agent/Application | reasoner | Section/workflow router | Agent/Application routing | Keep; not RetrievalAction | `payloads.py:50`; `research.py:64-75` |

`ResearchResultPayloadSchema` 的 `evidence_package_ref, evidence_contract_sha256, tool_call_ids, research_round, research_mode, agent_metadata` 属于 Agent result envelope；建议保留 ref/round/decision，Evidence 内容只做单向 projection。当前 `state://section-evidence/...` ref 是由 reasoner合成的逻辑引用，不证明 canonical Bundle 已持久化在该 URI（`section_agent_reasoners.py:211-240`）。

## 4.5 SectionEvidenceBundleSchema

| Field | Current Owner | Producer | Consumers | Candidate Target Owner | Keep / Move / Project / Internal / Deprecate | Evidence |
|---|---|---|---|---|---|---|
| `schema_version` | Application | Section graph/coordinator | assembly/output | Application compatibility | Keep during migration | `evidence.py:21` |
| `section_id` | Application | Section graph | assembly/output | Application projection identity | Keep | `evidence.py:22` |
| `section_title` | Application | Section graph | assembly | Application projection | Keep | `evidence.py:23` |
| `retrieval_scope` | Application | Section graph | assembly/report | Public request/result ref or App metadata | Project | `evidence.py:24` |
| `query` | Application | Section graph | audit | EvidencePackage ref/trace | Project | `evidence.py:25` |
| `tool_call_ids` | Application | Section graph | report | Agent/Application trace | Keep/Project | `evidence.py:26` |
| `rag_context` | public schema copied into App bundle | projection | Writer/assembly | Derived context projection | Project | `evidence.py:27` |
| `retrieved_chunks` | public schema copied into App bundle | projection | assembly/binding | Compatibility projection | Deprecate after direct package refs | `evidence.py:28` |
| `citations` | public citation copied into App bundle | registry/projection | Writer/assembly | Citation projection | Project | `evidence.py:29` |
| `recovery_count` | Application workflow | Section graph | report | Application workflow | Keep | `evidence.py:30` |
| `evidence_contract_sha256` | Application | copied from Context extra | Writer integrity/state | Canonical Package ref/hash | Move to explicit package reference | `evidence.py:31`; `section_execution_graph.py:2230-2260` |
| `writer_gate_binding` | Application/Gate dict | Section graph | output/report | Application Gate | Keep typed outside Evidence projection | Move | `evidence.py:32`; `section_execution_graph.py:2261-2266` |
| `generation_allowed` | Application/Gate | Section graph | output/report | Application Gate | Move | `evidence.py:33`; `section_execution_graph.py:2268-2271` |
| `metadata` | Application dict | Section graph | assembler/report | Application projection metadata | Keep true metadata; remove duplicated assessment truth later | `evidence.py:34`; `section_execution_graph.py:2232-2248` |

## 4.6 GateDecisionSchema

| Field | Current Owner | Producer | Consumers | Candidate Target Owner | Keep / Move / Project / Internal / Deprecate | Evidence |
|---|---|---|---|---|---|---|
| `schema_version` | Application/Gate | default | Gate consumers | Application Gate | Keep | `gate_decision.py:43` |
| `policy_id` | Application/Gate field; currently RAG requirement policy | RAG governance | Writer/audit | Application Gate policy ref | Keep after boundary split | `gate_decision.py:44`; `governance_v2.py:881-883` |
| `evidence_status` | RAG internal enum embedded in App schema | RAG governance | Gate/Writer | RAG public assessment projection | Project into Gate, not Gate-owned authority | `gate_decision.py:45` |
| `delivery_mode` | Application/Gate | RAG governance currently | Writer/Section/hard gate | Application Gate | Keep; producer must move in later phase | `gate_decision.py:46`; `governance_v2.py:812-884` |
| `claim_risk_level` | Application/Gate | RAG governance | Writer policies | Human decision: shared taxonomy or Gate | Keep/Move pending freeze | `gate_decision.py:47` |
| `allowed_claim_types` | shared ClaimType inside Gate | RAG governance | Writer classifier/audit | Application Gate | Keep projection/decision | `gate_decision.py:48` |
| `forbidden_claim_types` | shared ClaimType inside Gate | RAG governance | Writer classifier/audit | Application Gate | Keep projection/decision | `gate_decision.py:49` |
| `required_qualifiers` | Application/Gate | RAG governance | Writer/audit | Application Gate | Keep | `gate_decision.py:50`; `writer_governance.py:522-531` |
| `covered_topics` | RAG assessment copied to Gate | RAG governance | Gate/audit | RAG assessment projection | Project | `gate_decision.py:51` |
| `missing_topics` | RAG assessment copied to Gate | RAG governance | Gate/audit | RAG assessment projection | Project | `gate_decision.py:52` |
| `human_review_required` | Application/Gate | RAG governance currently | Section/Writer/hard gate | Application Gate | Keep; producer must move later | `gate_decision.py:53`; `section_execution_graph.py:991,1790,2335-2338` |
| `reason_codes` | mixed RAG assessment + delivery reasons | RAG governance | Gate/audit | Split RAG reasons vs Gate reasons | Move/Project | `gate_decision.py:54`; `governance_v2.py:806-894` |
| `metadata` | mixed | RAG governance | Writer/audit | Split by owner | Internal/Project | `gate_decision.py:55`; `governance_v2.py:895-900` |

---

# 5. Canonical Evidence Audit

## 5.1 当前最接近 canonical 的对象

**`EvidenceBundleSchema`：CONFIRMED de facto canonical candidate。** 证据：

- public Port 直接以它作为返回值（`contracts/rag.py:22`）；
- `RAGResultMapper` 统一构建并返回它（`result_mapper.py:516-550`）；
- `RAGEvidenceContractBuilder` 分配 `E1...En`，明确 selected/dropped，建立 Citation 关联并生成 Context projection（`contract.py:119-161,178-214,242-316`）；
- model validator 强制 E-ID、Citation-ID、disposition、Context budget/count 一致（`schemas/rag.py:565-657`）。

这只证明当前“最接近”，不代表 V2 最终字段已冻结。

## 5.2 重复维护 Evidence 的对象/位置

| Duplicate / copy | 重复内容 | 是否可反向成为 truth | Evidence |
|---|---|---|---|
| `RAGContext.extra.evidence_contract` | 完整 Bundle dump + hash + lineage | **是**；ContextManager/registry/stage 从中恢复或重写 | `evidence_service.py:234-242`; `context_manager/policies.py:330-336`; `document_citation_registry.py:190-234`; `stage_service.py:1805-1822` |
| normalized dict | `evidence`, `context`, `retrieved_chunks`, `citations`, `extra/gate` | 是；Stage/Writer 从 dict 再读取 | `evidence_service.py:243-245`; `stage_service.py:422-449`; `writer_governance.py:413-455` |
| `ResearchEvidencePackageSchema` | accepted/rejected IDs, topics, gaps, conflicts, evidence-derived status | 是，当前 workflow/state routing直接消费 | `payloads.py:41-51`; `research.py:45-75` |
| `SectionEvidenceBundleSchema` | Context, Chunks, Citations, assessment metadata, hash, Gate binding | 是，assembly/hard gate直接聚合 | `evidence.py:13-34`; `document_assembler.py:84-172` |
| Section research state | ref/hash/accepted/rejected/gaps/assessment | 是，后续 invocation/route消费 | `state_projection.py:169-180`; `section_agent_adapter.py:901-913` |
| `RAGToolOutputSchema` compatibility fields | Bundle 加 parallel chunks/context/citations | 可被 `LegacyEvidenceAdapter` 升级回 Bundle | `schemas/rag.py:683-730`; `legacy_evidence_adapter.py:43-154` |
| Bundle `assessment.details` / `extra` | serialized governance set assessment, gate decision, prompt contract | Gate/Writer 直接读取 dict | `result_mapper.py:304-315,565-574`; `writer_governance.py:413-455` |

## 5.3 仅应视为 projection 的对象

- `RAGContextSchema`：rendered prompt context + used chunk/doc/budget；当前 extra 违规不改变其应有 projection 角色。
- `RetrievedChunkSchema`：从 canonical selected item 恢复的 compatibility view；Reader 把 `evidence_id` 放进 chunk.extra（`contract.py:321-355`）。
- `CitationSchema` / `CitationBindingSchema`：source view 与 claim binding，不是 Evidence item authority。
- `ResearchEvidencePackageSchema`：应只保留 package ref + Agent decision/round/status 或严格的单向摘要。
- `SectionEvidenceBundleSchema`：应只保留 section/application metadata 与 canonical package ref/projections。

## 5.4 hidden side-channel

1. `RAGToolInputSchema.extra`: `retrieval_trace_id`, `retrieval_scope`, nested `extra_metadata` 把 requirement、context budget、document context 和 task semantics 跨边界传入 RAG。
2. `RAGContext.extra["evidence_contract"]`: 完整 canonical object dump；同时携带 SHA/lineage，并被多个生产消费者恢复。
3. `EvidenceBundle.extra`: `evidence_governance_v2`, `gate_decision`, `generation_allowed`, `prompt_evidence_contract` 等稳定业务/Gate 语义以 dict 传出（`result_mapper.py:565-574`）。
4. normalized dict / Section metadata: assessment、Gate、budget、contract hash 再次扁平/嵌套复制。
5. agent runtime general state 的 `evidence.contract` dict：`agent/runtime/state_access.py:210-234`、`graph_state_ops.py:312`；它是另一路显式 dict authority，需要在后续边界清理时确认是否承载同一 canonical package。

---

# 6. Dependency Direction Audit

扫描口径：仅 `backend/apps/**`, `backend/agent/**`, `backend/application/**`, `backend/context_manager/**` 生产 `.py`；排除 `backend/tests/**`。统计的是 import 语句数和唯一文件数。

## 6.1 Application / Agent / ContextManager -> RAG internal

结果：**14 条 import / 12 个生产文件，CONFIRMED。**

| Production file:line | Imported internal module | Classification |
|---|---|---|
| `backend/apps/enterprise_document/quality/plugins.py:13` | `rag.judge.self_rag_judge` | real violation |
| `backend/apps/enterprise_document/schemas/gate_decision.py:10` | `rag.catalog.document_profile` | real cross-boundary schema dependency |
| `backend/apps/enterprise_document/schemas/gate_decision.py:15` | `rag.schema.evidence_assessment_v2` | real cross-boundary schema dependency |
| `backend/apps/enterprise_document/schemas/writer_governance.py:15` | `rag.catalog.document_profile` | real cross-boundary schema dependency |
| `backend/apps/enterprise_document/services/scheme_writer/evidence_service.py:19` | `rag.evidence.contract` | real violation in mainline adapter |
| `backend/apps/enterprise_document/services/scheme_writer/evidence_service.py:20` | `rag.evidence.governance_v2` | real violation; Application creates RAG requirements through internal resolver |
| `backend/apps/enterprise_document/services/scheme_writer/grounding_repair_service.py:18` | `rag.evidence.citation_sources` | real violation |
| `backend/apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py:14` | `rag.evidence.contract` | compatibility-only dependency, still production |
| `backend/apps/enterprise_document/services/scheme_writer/model_service.py:22` | `rag.evidence.citation_sources` | real violation |
| `backend/apps/enterprise_document/services/scheme_writer/prompt_service.py:22` | `rag.evidence.citation_sources` | real violation |
| `backend/apps/enterprise_document/services/scheme_writer/semantic_claim_classifier.py:19` | `rag.catalog.document_profile` | real ClaimType boundary dependency |
| `backend/apps/enterprise_document/services/scheme_writer/writer_governance.py:24` | `rag.catalog.document_profile` | real ClaimType boundary dependency |
| `backend/application/mainline_service.py:33` | `rag.catalog.document_profile_registry` | real violation |
| `backend/context_manager/policies.py:17` | `rag.evidence.citation_sources` | real violation |

说明：

- `backend/agent/**` 本身没有命中指定六类 import，但 Agent 所在 enterprise_document Application/Agent 集成层有上述依赖。
- 未把 `schemas.rag` / `contracts.rag` 计入“RAG internal 六类”数量；这些是当前 public surface。
- Bootstrap 的 `contracts.rag` + `rag.services.rag_service` composition root imports 合法，未计入违规。
- Test-only imports 已排除，不混入生产数量。

## 6.2 RAG -> Application

结果：**1 条 import / 1 个生产文件，CONFIRMED。**

| Production file:line | Imported module | Classification |
|---|---|---|
| `backend/rag/evidence/governance_v2.py:21-24` | `apps.enterprise_document.schemas.gate_decision` (`ClaimRiskLevel`, `DeliveryMode`, `GateDecisionSchema`) | real reverse dependency; RAG constructs Application delivery decision |

这不是 composition root、测试或 compatibility adapter；它位于在线 RAG governance 主逻辑。

## 6.3 已知风险逐项判定

| Risk | Status | Current HEAD evidence |
|---|---|---|
| A. Application/Agent direct import 指定 `rag.*` internal | **CONFIRMED** | 14 imports / 12 files；见 §6.1 |
| B. `backend/rag/** -> apps.enterprise_document.*` | **CONFIRMED** | `rag/evidence/governance_v2.py:21-24`；见 §6.2 |
| C. `RAGContext.extra["evidence_contract"]` 或等价恢复 | **CONFIRMED** | writer `evidence_service.py:237-242`; readers/rebuilders `context_manager/policies.py:330-336`, `document_citation_registry.py:190-234`, `stage_service.py:1805-1822`; legacy path `legacy_evidence_adapter.py:47-65,119-154` |
| D. Research/Section duplicate canonical Evidence semantics | **CONFIRMED** | `payloads.py:41-64`; `evidence.py:13-34`; producers `section_agent_reasoners.py:211-240`, `section_execution_graph.py:2250-2271`; consumers in Research state/assembly |
| E. `accepted_evidence_ids` 实装 Citation ID | **CONFIRMED** | Gateway `section_agent_ports.py:333-366`; Section graph precomputed path `section_execution_graph.py:215-230`; Research payload consumes as evidence IDs `section_agent_reasoners.py:150-153,217-220` |
| F. `LangChainRAGServiceFacade.__getattr__` exposes delegate | **CONFIRMED** | `rag/integrations/langchain/runtime_facade.py:191-192` forwards every unknown attribute to `delegate`; delegate may be `RAGService` with public `retrieval_runtime` (`rag_service.py:137,166`) |

---

# 7. `RAGToolInputSchema.extra` Audit

生产主线唯一显式 constructor 在 `SchemeEvidenceService`：`evidence_service.py:199-218`。`RAGRequestMapper` 消费并补入 canonical identity/derived limits：`request_mapper.py:134-163`。以下只列当前生产链真实写入或由 mapper 写入后真实读取的路径；未把仅在代码中“支持但无生产 writer”的 `retrieval_plan_overrides` 算作真实字段。

分类建议：A = 稳定跨边界业务语义；B = RAG internal implementation/config；C = 真正 optional metadata/extension。

| Field Path | Writer | Reader | Semantic Type | Suggested Classification |
|---|---|---|---|---|
| `extra.retrieval_trace_id` | SchemeEvidenceService | RAGService, ResultMapper, LangChain facade, RequestMapper | invocation lineage identity | A；候选正式 Contract 字段 |
| `extra.retrieval_scope` | SchemeEvidenceService | RAGService/ResultMapper trace id, RequestMapper -> runtime context, governance fallback | document/section/recovery business scope | A；候选正式 Contract 字段 |
| `extra.extra_metadata` | SchemeEvidenceService | RequestMapper copies whole dict into runtime payload | composite side-channel | 拆分；不应整体保持 authority |
| `.task_type` | SchemeEvidenceService | `AdaptiveRetrievalPlanner` changes plan signals/mode | business task intent | A（若允许影响 routing） |
| `.evidence_requirements` | SchemeEvidenceService (`model_dump`) | governance resolver, ResultMapper strict prompt mode | core Evidence requirement | A；最高优先级提升为正式 Contract |
| `.evidence_requirements.schema_version` | resolver/default -> dump | validation/trace | contract version | A |
| `.evidence_requirements.policy_id` | resolver -> dump | Gate policy id | policy reference | A/B，需 Human Freeze |
| `.evidence_requirements.section_id` | SchemeEvidenceService runtime context -> resolver | governance/set assessment | requirement identity/scope | A |
| `.evidence_requirements.section_type` | SchemeEvidenceService -> resolver | governance strategy context | requirement semantic | A |
| `.evidence_requirements.required_topics` | resolver | coverage evaluator | core requirement topics | A |
| `.evidence_requirements.requested_claim_types` | resolver | parsed but metadata says ignored | requested claim policy | A only if semantics fixed; otherwise deprecate |
| `.evidence_requirements.claim_risk_level` | resolver (currently LOW) | serialized only; gate recomputes | mixed Gate policy | Move to Gate or deprecate; not generic extra |
| `.evidence_requirements.coverage_strategy` | resolver | RAG set assessment | RAG policy/config | B or constrained A intent |
| `.evidence_requirements.allow_framework_only` | resolver | delivery/gate context | Application delivery policy | Move out; not RAG generic extra |
| `.evidence_requirements.metadata` | resolver | governance | internal resolution trace | C/B |
| `.section_id` | SchemeEvidenceService | governance fallback | section identity | A |
| `.section_title` | SchemeEvidenceService | governance/fallback query planning | section semantic label | A |
| `.context_requirements` | SchemeEvidenceService; RequestMapper fills defaults | RAG engine/context gate | context packing config | B as typed constrained RAG config, not arbitrary extra |
| `.context_requirements.model_context_window` | ModelGateway capability plan via SchemeEvidenceService | `ContextRequirements.from_mapping` | model/context implementation limit | B |
| `.context_requirements.prompt_reserved_tokens` | SchemeEvidenceService | context gate | packing budget config | B |
| `.context_requirements.section_token_budget` | generation requirements | context gate | packing budget config | B |
| `.context_requirements.max_evidence_items` | generation requirements; mapper default | context gate | packing limit | B (public budget field may subsume it) |
| `.context_requirements.max_context_chars` | generation requirements; mapper default | context gate | packing limit | B (duplicates formal field) |
| `.context_requirements.model_capability_plan` | SchemeEvidenceService | no semantic reader found in RAG; carried as context | diagnostic/config snapshot | C/B |
| `.document_context` | SchemeEvidenceService | corrective query planner | bounded query-rewrite context | Split A/C |
| `.document_context.project_name` | ProjectInput | corrective planner fallback title | business context used for query | A |
| `.document_context.project_type` | ProjectInput | no direct semantic reader found | optional business metadata | C |
| `.document_context.document_title` | ProjectInput | corrective planner | business context used for query | A |
| `.document_context.required_sections` | ProjectInput | corrective planner | coverage/query planning context | A |
| `.document_context.citation_required_sections` | ProjectInput | corrective planner (preferred over required sections) | evidence requirement context | A |
| `.document_context.target_documents` | ProjectInput | no direct semantic reader found | optional metadata | C |
| `.document_context.target_templates` | ProjectInput | no direct semantic reader found | optional metadata | C |
| runtime `.task_id/.run_id/.workflow_run_id/.caller_agent` | RAGRequestMapper derives from formal fields | runtime trace/components | canonical identity | 已有正式字段；derived B, not caller extension |
| runtime `.need_citation` | RAGRequestMapper derives from formal field | runtime/governance | public request semantic | 已有正式字段；derived B |

关键发现：`extra` 虽被 RequestMapper 保持嵌套、防止伪造授权字段（`request_mapper.py:48-50,134-139`），但它并非“仅 optional metadata”；其中 requirement、scope、task type、section identity 和 context controls 都会改变真实生产行为。

---

# 8. Evidence / Citation / Chunk ID Semantic Audit

## 8.1 当前三个主要 namespace

| Namespace | Producer | Intended meaning | Evidence |
|---|---|---|---|
| `E1...En` | `RAGEvidenceContractBuilder` | canonical Bundle item identity | `rag/evidence/contract.py:242-272` |
| Citation ID（registry 后通常为 document-global marker） | EvidenceMapper / `DocumentCitationRegistry` | prompt/source citation identity | `schemas/citation.py:17-63`; `document_citation_registry.py:180-239` |
| Chunk IDs (`matched/context/child/parent/chunk_id`) | retriever/mapper | physical/logical retrieval unit identity | `schemas/rag.py:215-235`; `schemas/citation.py:28-38` |

## 8.2 已确认错配

1. `MainlineResearchToolGateway.accepted_evidence_ids` 首选所有 `citation.citation_id`；因此字段名与内容语义不一致（`section_agent_ports.py:333-366`）。
2. 无 Citation 时 Gateway 尝试 `getattr(RetrievedChunkSchema, "chunk_id")`；该 typed schema 没有 `chunk_id` 字段，只有 `matched_chunk_id/context_chunk_id/child_chunk_id/parent_chunk_id`，所以 typed 主线 fallback 通常得到空列表（`section_agent_ports.py:339-343`; `schemas/rag.py:215-223`）。
3. Section graph 的 `_active_evidence_ids` 重复同一逻辑：Citation IDs 优先，随后同样读取不存在的 `chunk_id`（`section_execution_graph.py:215-230`）。
4. Research reasoner 不校验 namespace，直接把这些值写入 `ResearchEvidencePackageSchema.accepted_evidence_ids`（`section_agent_reasoners.py:150-153,217-220`），ResearchAgent 再写入 `section.research.evidence_ids`（`agents/agent_rag_v1/research.py:45-55`）。
5. RAG governance 内部的 `EvidenceSetAssessmentSchema.evidence_ids` 使用 candidate identity（通常 child chunk/candidate id），public Bundle Builder 随后重新编号为 `E*`；serialized governance dict 和 public Bundle item 的“evidence_id”不是同一 namespace（`parent_child_rag_engine.py:240-250`; `governance_v2.py:710-730`; `contract.py:242-272`）。
6. Legacy adapter 还可能生成 `citation_<run>_<n>` 或 `retrieved_chunk_<n>`，进一步证明不能用字符串前缀猜测语义（`legacy_evidence_adapter.py:73-96`）。

判定：**CONFIRMED semantic mismatch**，不是单纯命名问题。P0-B/P0-C 前必须由 Human Freeze 明确每个跨边界字段允许的 namespace 和映射。

---

# 9. Open Decisions Requiring Human Freeze

## 9.1 EvidenceRequirement 最终 public 字段与粒度

**A. 当前事实**：当前 RAG schema 是 section aggregate；V2 文档是 item-level requirement。Application 创建 instance 并通过 `extra` 传回 RAG。  
**B. 可选方案**：① 冻结现有 aggregate schema 为 public；② 以文档 item-level 模型升级现有 schema（不新建平行 authority）；③ 明确 aggregate 是 envelope、item 是其 typed child。  
**C. 影响**：① 最小迁移但偏离文档字段；② 影响 SchemeEvidenceService、governance、mapper、Research invocation/tests；③ 字段更多但能兼容批量 coverage。  
**D. Codex 推荐**：优先评估③；复用/演进现有 `EvidenceRequirementsV2Schema`，禁止另建第四套 Evidence family。  
**E. Human Decision**：冻结粒度、字段、public import location、Planner 创建责任。

## 9.2 EvidenceStatus 的唯一语义和 public export

**A. 当前事实**：`EvidenceStatus` 与 `EvidenceAssessmentStatus` 并行，映射有损；Application Gate direct imports internal enum。  
**B. 可选方案**：① public Bundle 采用详细 V2 enum；② 保留两层但正式命名 detailed vs public summary，并冻结映射；③ 保留 public enum并扩展 conflict/absent fields。  
**C. 影响**：① 影响 Bundle/consumers；② 保留兼容但持续两层；③ 改 public contract validators。  
**D. Codex 推荐**：冻结一个跨边界 detailed Evidence status；不要复制新 enum；execution status、evidence status、stop reason 保持三维分离。  
**E. Human Decision**：选择唯一跨边界 enum及 export location、兼容映射期限。

## 9.3 EvidencePackage identity 与最终字段

**A. 当前事实**：`EvidenceBundleSchema` de facto canonical，但缺少若干 V2 package fields；完整对象通过多个 dict/copy 通道传播。  
**B. 可选方案**：① 冻结现有 Bundle identity并演进字段；② 冻结现有 Bundle为 v1 public package，V2 字段以兼容升级加入同一 authority；不应选择新建平行 Package。  
**C. 影响**：两者都影响 mapper、Research/Section projection、hash/serialization；② 需版本迁移规则。  
**D. Codex 推荐**：冻结 `EvidenceBundleSchema`/其稳定 public 名称为唯一 identity，再在同一 authority 上解决 `section_id/status/stop_reason/requirements/coverage/conflicts/hash` 字段。  
**E. Human Decision**：最终名称、versioning、required fields、package persistence/reference semantics。

## 9.4 DeliveryMode / GateDecision 生产责任

**A. 当前事实**：类型在 Application，RAG governance 直接构造；这与指定架构“RAG 不拥有 Delivery 决策”冲突。  
**B. 可选方案**：① RAG 只输出 typed assessment，Application policy service构造现有 GateDecision；② 暂保 compatibility adapter但限定为单向 assessment -> gate projection。  
**C. 影响**：① 影响 governance result/Writer binding；② 迁移较缓但必须明确 retirement。  
**D. Codex 推荐**：①为目标、②为兼容迁移；不改变当前 Gate 判定规则，只移动依赖和生产责任。  
**E. Human Decision**：冻结 Gate producer/public boundary和 compatibility期限。

## 9.5 ClaimType ownership

**A. 当前事实**：定义在 RAG catalog，却被 RAG、Gate、Writer schema/classifier共同使用。  
**B. 可选方案**：① RAG-owned public contract export；② shared cross-boundary domain contract；③ Application-owned并由 RAG只消费 public type。  
**C. 影响**：12 类 internal-import实例中的多项、candidate/assessment validators、Writer persisted schemas。复制 enum会破坏反序列化/相等性。  
**D. Codex 推荐**：倾向② shared public domain taxonomy，保持单一定义；不要复制。  
**E. Human Decision**：最终 owner、import surface、version policy。

## 9.6 RetrievalAction 本阶段冻结什么

**A. 当前事实**：没有 public schema；内部 plan/correction和 workflow `research_more` 已有但语义不同。  
**B. 可选方案**：① P0 只冻结 ownership和禁止暴露的 internal knobs；② 同时冻结最小 action values/fields但仍不实现行为。  
**C. 影响**：① 延后 Research V2 request shape；② 会约束未来 router和 RAG request migration。  
**D. Codex 推荐**：本轮至少冻结 ownership、语义边界、与 existing actions 的非等价关系；values/fields只有在人明确选择后写入 public contract。  
**E. Human Decision**：是否在 P0 只冻结 ownership，或连最小 enum/fields一起冻结。

## 9.7 哪些 `extra` 字段提升为正式 Contract

**A. 当前事实**：`evidence_requirements`, `retrieval_scope`, `section identity`, `task_type`, context budgets会改变真实执行；并非 optional metadata。  
**B. 可选方案**：① requirement/action/scope/budget各自typed字段；② typed nested request objects；③ 保持 dict但列严格 allowlist/version。  
**C. 影响**：①/②影响 SchemeEvidenceService和RequestMapper；③迁移小但边界仍弱。  
**D. Codex 推荐**：core business语义采用typed nested objects，RAG internal knobs留在实现/config，真正诊断 metadata才留 `extra`。  
**E. Human Decision**：逐字段确认 §7 的 A/B/C 分类和安全边界。

## 9.8 Evidence/Citation/Chunk ID namespace 与 compatibility projection

**A. 当前事实**：Research `accepted_evidence_ids` 实为 Citation IDs；governance candidate ID 与 Bundle `E*` 也不同。  
**B. 可选方案**：① Research只携带 canonical `E*`; 另设 citation/chunk refs；② Research只携带 package ref，不复制 item IDs；③ 明确 typed union/ref object。  
**C. 影响**：Gateway、Research payload/state、Section graph、Writer provenance、artifact refs。  
**D. Codex 推荐**：优先②，确需列表时采用①；禁止一个 `str` list混装多个 namespace。  
**E. Human Decision**：冻结 ID字段和映射规则、legacy retirement条件。

---

# 10. Safe-to-Freeze Items

以下项目在当前代码和指定规划之间没有实质冲突，可提交人工标记 **SAFE TO FREEZE**；这不授权改代码：

1. **SAFE TO FREEZE — 服务入口形状**：Application-facing RAG能力使用 `RAGServicePort.retrieve(RAGToolInputSchema) -> EvidenceBundleSchema`；具体 runtime/retriever不进入 Application。
2. **SAFE TO FREEZE — Alias 事实**：`RAGEvidenceContractSchema` 是 `EvidenceBundleSchema` 的直接 alias，不能被描述为独立 schema authority，也不应因命名另建新类。
3. **SAFE TO FREEZE — Access scope 原则**：`RetrievalAccessScopeSchema` 由 trusted Application 边界产生，RequestMapper只允许 caller filters缩小范围；`extra` 不能扩大权限。
4. **SAFE TO FREEZE — Identity 类型分离原则**：Evidence ID、Citation ID、Chunk ID 是不同 namespace；现有混用是待修问题，不是允许的兼容语义。
5. **SAFE TO FREEZE — Projection 方向原则**：canonical Evidence可单向投影为 Context/Chunk/Citation/Research/Section view；projection不得反向成为新的 canonical authority（legacy adapter只能作为有 retirement条件的集中兼容例外）。
6. **SAFE TO FREEZE — P0-A 范围**：本阶段不实现 RetrievalAction V2、bounded research、Memory、ContextCompiler，也不改变 Gate/Writer/Verifier行为。

不能标记 SAFE TO FREEZE 的项目：EvidenceRequirement最终字段、EvidenceStatus public enum、EvidencePackage最终字段、ClaimType owner、RetrievalAction values、Delivery producer和 `extra` 提升列表；它们仍在 §9 等待 Human Freeze。

---

# 11. Proposed P0-B Input

仅列下一阶段输入，不实施：

1. 为 Human Freeze 后的 `EvidenceRequirement`, `EvidenceStatus`, `ClaimType` 建立唯一 public export；迁移 §6.1 中 schema-level internal imports，不复制类型。
2. 移除 `SchemeEvidenceService` 对 `rag.evidence.contract/governance_v2` 的直接依赖：只构造 frozen public request并经 `RAGServicePort` 调用；requirement resolver的责任按人工决定迁移。
3. 将 `rag.evidence.governance_v2 -> apps...gate_decision` 拆为 RAG assessment output -> Application Gate policy input；保持现有 Gate规则和 shadow行为不变。
4. 对 §6.1 剩余 citation helper/judge/catalog imports逐文件指定 public contract/port或明确 compatibility adapter；`LegacyEvidenceAdapter` 单列 retirement条件。
5. 收紧 `LangChainRAGServiceFacade` public surface，先扫描生产/测试对 delegate attributes 的依赖；P0-B 不应通过 facade暴露 `retrieval_runtime`。
6. 按 §7 的人工分类，把 core fields从 `RAGToolInputSchema.extra` 提升到同一 public authority；RAG internal config不向 Agent开放。
7. 以上依赖清理完成后再把 `RAGContext.extra.evidence_contract`、Research/Section duplicate truth交给 P0-C/P0-D；P0-B 不越阶段清理 behavior/state。

---

# 12. Verification Evidence

## 12.1 Git evidence

审计开始前 `git status --short` 已有以下用户/仓库未跟踪项，本审计未修改它们：

```text
?? NUL
?? docs/MULTI_AGENT_V2_CURRENT_STATE_GAP_AUDIT_20260813.md
?? docs/RAG_AGENT_EVIDENCE_INTERFACE_CLOSURE_IMPLEMENTATION_PLAN_20260813.md
```

完成本文后最终命令输出记录如下（见末次验证）：

```text
git status --short
?? NUL
?? docs/MULTI_AGENT_V2_CURRENT_STATE_GAP_AUDIT_20260813.md
?? docs/RAG_AGENT_EVIDENCE_CONTRACT_AUTHORITY_AUDIT_20260813.md
?? docs/RAG_AGENT_EVIDENCE_INTERFACE_CLOSURE_IMPLEMENTATION_PLAN_20260813.md

git diff --stat
<no output; all listed files are untracked>

git diff --name-only
<no output; no tracked file changed>

git ls-files --modified --deleted
<no output; no tracked production/schema/test file changed>
```

本轮新增且唯一写入的文件是：

```text
docs/RAG_AGENT_EVIDENCE_CONTRACT_AUTHORITY_AUDIT_20260813.md
```

## 12.2 Import scan counts

```text
Application/Agent/ContextManager -> any rag.*:
  14 import statements / 12 production files

Application/Agent/ContextManager -> specified
rag.evidence|catalog|schema|judge|runtime|retriever:
  14 import statements / 12 production files

backend/rag/** -> apps.enterprise_document.*:
  1 import statement / 1 production file
```

## 12.3 Discovered side-channels / duplicate authority / ID mismatch

- `evidence_contract` side-channel：**CONFIRMED**；writers/readers/rebuilders见 §5.4、§6.3。
- duplicate Evidence authority：**CONFIRMED**；Context extra、normalized dict、Research summary/state、Section bundle/state、legacy output、Bundle extra governance/gate；见 §5.2。
- Evidence ID / Citation ID / Chunk ID mismatch：**CONFIRMED**；Gateway和Section graph把 Citation ID写入 evidence ID字段，typed chunk fallback字段不存在，governance candidate ID与 public `E*`不同；见 §8。

---

# 13. STOP

P0-A 审计到此结束。本文不执行 P0-B、P0-C、P0-D、P0-E，不修改 Schema/import/Adapter/ToolGateway/Gate/Writer/Verifier，不新增架构测试，也不替 Human Freeze 作最终 ownership 决定。

