# 07 — Schema 控制审计总报告

> 审计任务：**RAG-Agent Schema Architecture Control Audit — Phase 1**  
> 生产路径：当前政务云 Scheme Generation E2E  
> 主 Runtime：`gov_cloud_deepseek_all_roles_ab_20260810_161856`  
> 复核性全 DeepSeek 重跑：`gov_cloud_deepseek_all_roles_ab_20260810_173830`

本报告用于 Owner 回来后的逐项讨论入口。详细代码与 Runtime 证据保存在 01–06 文档中。本报告不包含 Schema V2 或 implementation design。

---

# 1. 当前 Schema Architecture 总览

当前生产架构并不是一个单体 Document Schema，而是一组具有不同 semantic ownership 的 Contract 串联成完整链路。

```text
当前 Run 的业务输入事实
ProjectInputSchema
        ↓
持久化 Source identity / authority / Document-level claim capability
DocumentProfileSchema
        ↓
Cleaned source units + parent/child index records
CleanedTextUnit / Chunk contracts
        ↓
在线 retrieved source identity + copied document capability + chunk structure
RetrievalCandidateV2Schema
        ↓
Writer 实际看见的精确 Evidence
PromptEvidenceCandidateV1Schema
        ↓
Item-level Evidence eligibility 与 support
EvidenceAssessmentV2Schema
        ↓
Section topic coverage / conflict state
EvidenceSetAssessmentSchema
        ↓
最终 Section-wide Claim boundary
GateDecisionSchema
        ↓
Writer invocation 与精确 Gate/Evidence hash 绑定
WriterGateBindingSchema
        ↓
Generated section text
SchemeSectionSchema
        ↓
Substantive sentence detection + 独立 semantic classification
GeneratedClaimSchema
        ↓
Deterministic Claim-Policy comparison
ClaimAuditResultSchema
        ↓
Citation source / binding integrity
CitationSchema / CitationBindingSchema
        ↓
Whole-document assembly
SchemeDraftSchema
        ↓
最终正式可交付判定
HardGateResultSchema
```

主 transformation pattern 为：

```text
Document capability
  COPY
→ Candidate capability
  FILTER
→ EvidenceAssessment supported claims
  UNION
→ GateDecision allowed claims
  独立 RECLASSIFY final generated text
→ GeneratedClaim.claim_type
  SET-COMPARE
→ ClaimAudit
```

因此，最终出现 `claim_type_not_allowed` 或 `forbidden_claim_type_generated` 时，它只是一个下游 symptom。第一个 semantic error 可能发生在更早的位置；也可能根本没有 error，而是一次正确 rejection。

---

# 2. Core Schema 清单

Registry-level 审计结果：扫描目标目录以及 Scheme Generation 实际 import/call path 后，共分类 **49 个生产相关 Schema / Contract 条目**。

## L0 — User / Workflow input

### Confirmed Core
- `ProjectInputSchema`

### Candidate-core
- `SourceMaterialSchema`

## L1 — Document / Catalog identity

### Confirmed Core
- `DocumentProfileSchema`
- `ContentCapabilitiesSchema`
- 共享 semantic vocabulary：`DocumentType`、`EvidenceRole`、`ProjectRelation`、`AuthorityLevel`、`ClaimType`

Supporting nested contract 包括 project relation、authority、parsing、classification profile。

## L2 — Parsed / Chunk / Structured Evidence

### Confirmed Core
- `StructuredEvidenceSchema`
- `FactEvidenceSchema / FactStatus`

### Candidate-core
- `CleanedTextUnitSchema`
- parent/child Chunk contracts
- `TableEvidenceSchema`
- `PolicyEvidenceSchema`
- `TemplateEvidenceSchema`

## L3 — Retrieval

### Confirmed Core
- `RetrievalCandidateV2Schema`

### Candidate-core
- `RetrievalAccessScopeSchema`
- `EvidenceBundleSchema`
- `PromptEvidenceCandidateV1Schema`

## L4 — Evidence Governance

### Confirmed Core
- `EvidenceRequirementsV2Schema`
- `EvidenceAssessmentV2Schema`
- `EvidenceSetAssessmentSchema`
- `GateDecisionSchema`

## L5 — Writer / Claim Governance

### Confirmed Core
- `WriterGateBindingSchema`
- `GeneratedClaimSchema`
- `ClaimAuditResultSchema`

### Candidate-core
- `ClaimAuditIntegritySchema`

## L6 — Citation / Verification

### Confirmed Core
- `CitationSchema`
- `CitationBindingSchema`

### Candidate-core
- `SectionEvalSchema`

## L7 — Section / Document / Hard Gate

### Confirmed Core
- `SchemeSectionSchema`
- `SchemeDraftSchema`
- `HardGateResultSchema`

### Candidate-core
- `SectionEvidenceBundleSchema`
- `RejectedDraftArtifactSchema`

## L8 — Trace / Artifact / Persistence

Trace/task/release contracts 在 Phase 1 中属于 Supporting 或 Conditional。它们保存 forensic evidence，但不是决定 claim semantics 的 authority。

## 分类数量

- **Confirmed-by-code / Core：19** 个 registry entry/group
- **Candidate-core：11**
- **Supporting：15**
- **其余 Conditional / Legacy / Test-only：4**

这些数量使用 `01_SCHEMA_REGISTRY.md` 的 grouping 规则；共享 Enum vocabulary 有意按一个 registry group 统计，而不是拆成五个膨胀数量。

---

# 3. 核心 Transformation Chain

最重要的端到端 semantic chain 为：

```text
DocumentProfileSchema
  content_capabilities
  evidence_role
  project_relation
  authority
        ↓ exact catalog projection
Chunk.extra.document_profile
        ↓ retrieval
RetrievalCandidateV2Schema
  allowed_claim_types == DocumentProfile allowed_claim_types
  forbidden_claim_types == DocumentProfile forbidden_claim_types
  structured_evidence = chunk-local projection
        ↓ item policy FILTER
EvidenceAssessmentV2Schema
  supported_claim_types
  forbidden_claim_types
        ↓ section-wide UNION
GateDecisionSchema
  allowed_claim_types
  forbidden_claim_types
  required_qualifiers
  delivery_mode
        ↓ immutable binding
WriterGateBindingSchema
        ↓ model generates text
SchemeSectionSchema.content
        ↓ substantive detector
substantive sentence
        ↓ independent semantic RECLASSIFY
GeneratedClaimSchema.claim_type
        ↓ deterministic comparison
ClaimAuditResultSchema
        ↓ section status / recomputed formal eligibility
SchemeDraftSchema
        ↓ final checks
HardGateResultSchema
```

其中语义杠杆最大的三个边界：

1. **DocumentProfile → RetrievalCandidate：** Document-level capability 被精确复制，Candidate 不允许独立修改 capability set。
2. **EvidenceAssessment → GateDecision：** Eligible candidate support 被 UNION；当前 Policy 显式忽略 `requested_claim_types`。
3. **Generated text → GeneratedClaim：** Final text 独立 RECLASSIFY；Gate 不影响 classifier。这种独立性对 Audit 是正确的，但会直接暴露 taxonomy/model/schema mismatch。

---

# 4. 已确认 Root Gap

Phase 1 只确认 **1 个** Schema-level Root Gap。

## RG-1 — Unknown / Deferred Generated Semantics 无法在成功的 Substantive Claim Audit 状态中被独立表达

证据：

- Evidence 侧有一等 `FactStatus`，包括 `UNKNOWN/PROPOSED/PLANNED/...`；
- Generated 侧 `GeneratedClaimSchema` 有 ClaimType，但没有自己拥有的等价 epistemic/modality semantic；
- substantive sentence 必须得到 valid ClaimType 才能 pass；empty classification 会 fail closed；
- BC03、BC20、BC25 都是完整且有用的说明，表达 current-project value **unknown / pending confirmation**，但被分成 `current_project_fact`，随后又被 Gate 正确禁止；
- 独立 17:38 all-DeepSeek 重跑在新 generated unknown/deferred sentence 上再次复现同一机制。

第一次 Contract-level 丢失位置：

```text
substantive generated sentence
→ successful GeneratedClaim semantic state
```

本结论不规定未来如何表示该语义。

---

# 5. Uncertain Gap

Phase 1 保留 **2 个**问题为 UNCERTAIN，避免超过证据边界。

## UG-1 — Document-level capability → Candidate capability granularity

已确认事实：

- `RetrievalCandidateV2.allowed/forbidden_claim_types` 必须与 DocumentProfile capability set 完全相等；
- 存储类 candidate 的可见文本明确包含 Ceph / storage-technology content，但 capability 仍只有 historical-case 的 general/typical/reference；
- 普通 paragraph structured evidence 不独立拥有 chunk-local claim-capability set。

不确定点：

- Runtime 并不能证明 independent chunk-level capability 是唯一正确 Architecture；Document-level capability population 与 Governance Policy 同样可以解释这种行为。

## UG-2 — GateDecision 把 Per-Item Supported Claims 压平为 Section-Wide Union

已确认事实：

- final allowed claims = `UNION(item.supported_claim_types)`；
- GateDecision/WriterGateBinding 不直接暴露 `ClaimType → granting evidence item` mapping；
- item-level provenance 仍然保存在 Evidence Governance artifact 中。

不确定点：

- 34 条 violation 中，没有一条能够证明这个 flattening 本身是 first causal error。

---

# 6. 绝对不能破坏的正确行为

以下 control 同时得到生产代码与 Runtime 支持，不能因为它们降低 E2E pass rate 就误判成 Bug。

## 6.1 Historical Evidence 不能直接证明 Current-Project Facts

```text
historical_case / other_project
≠
current-project fact/configuration/quantity/cost/schedule evidence
```

BC07–BC12 是代表性 correct rejection。

## 6.2 Candidate Semantic Identity 必须与 Embedded DocumentProfile 一致

一个 retrieval hit 不能静默修改：

- document type
- evidence role
- project relation/project id
- authority
- document claim capabilities
- profile/source hashes

该约束用于防止 mixed/stale retrieval metadata。

## 6.3 Prompt Governance 只能计算 Writer 实际看见的 Evidence

`PromptEvidenceCandidateV1Schema` 绑定 exact prompt-visible text，并校验 structured fact visibility。隐藏的 full parent text 不会被静默用于授权 Writer claim。

## 6.4 Blocked GateDecision 不能生成 Claims

`WriterGateBindingSchema` 会独立校验 generation permission 与 delivery mode 是否一致。

## 6.5 Claim Classification 必须独立于 Gate Allow/Forbid

Semantic classifier 有意不被要求“选一个能过 Gate 的 type”。这样可以避免 self-validating audit loop。

## 6.6 ClaimAudit 与 Formal Deliverability 必须重新计算，不能信任 Stale Boolean

Document Hard Gate 会根据 exact content/binding/evidence/classifier policy 重新计算 Writer artifact integrity 与 `formal_deliverable_eligible`。

## 6.7 Citation Source 不能来自 Dropped Prompt Evidence

Canonical EvidenceBundle invariant 禁止 dropped evidence 暴露 citation，避免出现 Writer 实际没看过却被当成引用来源的情况。

---

# 7. 本次审计已确认的 Non-Schema 问题

这些问题必须与 Schema Root Gap 分开讨论。

## 7.1 Substantive Detector 问题

BC26–30、BC32–34 是 `（一）实施准备阶段。` 这类纯 heading，却进入 substantive Claim 链。Detector 已经有 structural-exclusion 概念，只是 observed form 没被规则排除。这属于 implementation/detector issue。

## 7.2 Evidence / Gate Policy 问题

当前只有 `evidence_role=technical_reference` 才 structurally allow `technical_specification`。因此 historical case 即使文本非常技术化，也无法在当前 Governance 中提供该 capability。这是 BC04/06/16–19/21–24 的真实原因，但 immediate restriction 是 Policy，而不是已证明的 missing Schema expressiveness。

## 7.3 Claim Semantic Classification 问题

BC13–15、BC31 表明：某些句子存在被 evidence 支持的 policy/general-method reading，但 DeepSeek 会把句子分类成 technical/template/policy 中的另一种 type，最终落在 Gate set 之外。

## 7.4 Explicit Policy Behavior：Requested ClaimTypes 被忽略

`EvidenceRequirementsV2Schema` 已经包含 `requested_claim_types`，但当前生产 Policy 明确写入并执行：

```text
claim_resolution_mode = candidate_capability_union
requested_claim_types_ignored = true
```

这不是 missing field，因此 Phase 1 不把它定义成 Schema Root Gap。

---

# 8. 动态 Badcase 结论

主 Run 已经完整对账并穿透：

```text
34 Claim Audit violations
= 24 claim_type_not_allowed
+ 10 forbidden_claim_type_generated
```

Primary cause 分布：

- **9 条 Correct rejection**
- **10 条 Evidence capability issue**
- **7 条 Claim classification issue**
- **8 条 Substantive detection issue**

其中 7 条 classification case 里的 3 条（BC03、BC20、BC25）还额外暴露了 Confirmed Root Gap RG-1：Generated Claim semantic Contract 缺少可成功表达 deferred modality 的状态。

34 条中没有确认 primary Citation/Grounding failure，也没有确认 primary Document classification error。

17:38 all-DeepSeek 重跑产生 **31 条 violation（21 + 10）**，并且 8 个 section 全部 failed。它复现了多个 semantic mechanism，但具体 sentence/count 发生变化。因此 raw E2E violation count 受 model output 影响，不能误当成稳定 Schema property。

---

# 9. Owner 回来后需要逐项确认的问题

以下问题是 Phase 1 之后用于 Architecture Control 的具体问题，只提出问题，不隐含任何设计方案。

### Q1 — Unknown / Deferred Statement Ownership

完整句子：

```text
“具体数量、配置和投资金额尚未确定，需项目方后续确认。”
```

它是 substantive，但不是 positive current-project fact。**生产 Claim Contract 是否应把这种 semantic distinction 纳入 deterministic governance，而不是只依赖 ClaimType classification？**

### Q2 — Document Capability Ownership

`DocumentProfile.content_capabilities` 明确是 Document-level，而 `RetrievalCandidateV2` 又被强制精确复制它。**即使同一份 historical document 中不同 passage 的实际 semantic use 明显不同，这种 exact ownership 是否仍是项目预期？**

### Q3 — Historical Technical Content

一份 Document 可以正确是 `historical_case / other_project`，但某个 retrieved passage 又真实包含可复用 Ceph/replication/erasure-code 技术内容。Current Policy 规定 `technical_specification` 必须来自 `technical_reference` role。**“Historical identity” 是否应该天然让 technical-spec support 不可能，还是这只是当前 Policy artifact？**

### Q4 — ClaimType Request Semantics

`EvidenceRequirementsV2.requested_claim_types` 已存在，但当前 Runtime 明确忽略它，Gate claim permission 由 candidate capability union 决定。**在 V1 中 `requested_claim_types` 只是 descriptive field，还是原本就应该约束 section claim permission？**

### Q5 — Gate Permission Provenance

`GateDecision.allowed_claim_types` 是 flat union；WriterGateBinding 只知道 Evidence hash，并不直接知道“哪个 Evidence item 授权哪个 ClaimType”。**Section-wide permission set 是否就是 Writer 的预期 ownership boundary，还是 item-level claim-grant provenance 原本也应该在这个边界可观察？**

### Q6 — Structural Heading

Pure heading 不是 substantive business claim，Detector 代码本身也显然想排除它。**Owner 是否确认 `（一）实施准备阶段` 这类 heading 永远不应该进入 Claim Audit？**

### Q7 — ClaimType Semantic Boundary

Runtime 反复暴露 `general_method`、`policy_requirement`、`technical_specification`、`template_structure` 之间的 ambiguity。**当前 ClaimType definitions 是否已经冻结并视为 authority，错误归因于 classifier calibration；还是这些业务定义本身仍需要 Owner 审查？**

### Q8 — Correct-Rejection Invariant

没有 current-project evidence 时，`本项目依托... / 本项目采用... / 本项目建设...` 即使来自 historical case，也会被拒绝。**Owner 应明确确认：后续任何调整都不能破坏这一 invariant。**

---

# 10. Phase-1 Control Position

当前 Architecture 已经存在一套较强 Control：

- authoritative DocumentProfile identity；
- evidence-role/project-relation separation；
- strict candidate/profile consistency；
- prompt-visible evidence governance；
- deterministic GateDecision；
- immutable WriterGateBinding；
- independent post-generation semantic Claim Audit；
- citation binding/integrity；
- recomputed formal-deliverable eligibility 与 Document Hard Gate。

因此，本次审计**不支持**为了追求 pass rate 而整体替换 Architecture，或者大幅放宽 Gate。

现有证据只支持一个更窄、更精确的结论：

- 有 1 个 Generated Claim semantic distinction 在成功 Contract state 中确实缺失；
- 有 2 个 compression point 需要 Owner review，但尚未被证明是 Root Gap；
- 多个可见 E2E failure 属于 Policy / Model / Detector 行为，而不是 Schema Architecture；
- 相当一部分 failure 是正确的 Safety/Evidence rejection。

---

# 11. STOP POINT

```text
本轮 Schema Architecture Control Audit Phase 1
到此结束。

尚未进行：
- Schema V2 Design
- Schema Migration Design
- Code Implementation
```

**Phase 1 到此停止。**
