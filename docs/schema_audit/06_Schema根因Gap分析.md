# 06 — Schema 根因 Gap 分析

> Phase-1 边界：本文只识别当前 Root Gap。**不提出**字段、Enum、Schema、migration、代码修改或 Schema V2 方案。

## 判定规则

只有在生产代码 + Runtime 同时证明：某个必要业务语义在负责它的 Schema/Transformation 边界上**无法被成功表达**，并且下游 failure 正是由该语义丢失/混淆导致时，才标记 **Root Gap = YES**。

如果当前 Schema 已经能够表达所需区别，而 observed failure 来自 policy、model classification、data population 或 implementation behavior，则标记 **NO**。

如果可以证明存在信息 flatten / overload，但 34 条 Badcase 的证据不足以证明该信息在当前生产任务中“必须保留且具有因果作用”，则标记 **UNCERTAIN**。

---

# Gap 1 — Substantive unknown/deferred statement 没有一个可成功通过的 Generated Claim 语义状态

### 当前 Schema / Transformation

```text
SchemeSectionSchema.content
→ SubstantiveSentenceDetector
→ SemanticClaimClassifier
→ GeneratedClaimSchema
→ ClaimAuditResultSchema
```

### 当前真实行为

一条完整句子如果表达的是：**“当前项目这个值未知 / 尚未确定 / 等待项目方确认”**，会被当成 substantive sentence。这个判断本身是合理的，因为它携带真实业务信息，并不只是一个 placeholder token。

Semantic classifier 技术上可以返回 `claim_types=[]`，但 `WriterClaimAuditor` 会把 substantive sentence 的 empty ClaimType result 转成 `semantic_unclassified_substantive_claim`，并 fail closed。因此，要形成一个成功的 `GeneratedClaimSchema`，实际上必须至少映射到一个现有 `ClaimType`。

`GeneratedClaimSchema` 当前包含：

- `claim_type`
- text/span
- classifier identity/version/confidence
- generic metadata

但它没有拥有一个与 Evidence 侧 `FactStatus` 等价的语义状态，用来区分：

- 正向 current-project assertion
- unknown/deferred/pending confirmation

同时还能让该 substantive statement 处于“已成功分类”的状态。

### 当前真实代码证据

1. `FactEvidenceSchema.fact_status` 明确有 `UNKNOWN/ESTIMATED/PROPOSED/PLANNED/CONFIRMED/DECIDED`，Evidence Governance 会根据这些状态做不同 eligibility 决策。
2. `SemanticClaimClassifier` 明确允许 unmappable sentence 返回 empty `claim_types` array。
3. `WriterClaimAuditor._extract_claims_and_violations()` 会把 substantive sentence 的 empty result 转成 `semantic_unclassified_substantive_claim`。
4. `GeneratedClaimSchema` 没有一等 epistemic/modality field，Claim Audit 主要只拿 `GeneratedClaim.claim_type` 与 Gate allow/forbid set 比较。

### 对应 Badcase

主 Run：

- BC03 — `基础数据尚未明确...待补充` → `current_project_fact` → forbidden
- BC20 — `需...由项目方确认，本方案暂不设定具体数值` → `current_project_fact` → forbidden
- BC25 — `当前存储容量...待补充...进一步确认` → `current_project_fact` → forbidden

17:38 的 all-DeepSeek 复核 Run 又独立复现了多条等价 unknown/deferred sentence，并再次进入 positive ClaimType enforcement。

### 丢失 / 混淆的业务语义

当前 Contract 无法成功保留下面这个区别：

```text
A. “本项目已有/确定事实 X”
vs
B. “本项目 X 尚未知，必须后续确认”
```

A 与 B 都是在谈当前项目，但 B 并不是在正向断言 X 已存在、已确定或已决策。

### 第一次出错的位置

第一次 **Contract-level** 语义丢失发生在 successful substantive-sentence → `GeneratedClaimSchema` 边界。

Runtime 中最直接观察到的错误是 model classification，但当前 Contract 本身没有给 classifier/auditor 一个可以独立表达 unknown/deferred modality 的成功状态。

### 下游影响

- Semantic classifier 被迫把 deferred disclosure 塞进某个 existing positive ClaimType；
- Gate 正确禁止 resulting current-project claim；
- Claim Audit failed；
- section 变成 failed，`formal_deliverable_eligible=False`；
- Document Hard Gate failed。

### 是否属于 Root Gap

**YES**

### 为什么它是 / 不是 Root Gap

这是 Root Gap，因为必要业务区别在 Evidence 侧已经存在（`FactStatus`），在 generated natural language 中也真实存在，但一进入用于 deterministic enforcement 的 Generated Claim semantic Contract 就消失了。

重复 Runtime 行为说明它具有真实因果作用，而且不能用“缺证据”或“Gate invariant 错误”解释。

本结论**不指定**未来应该用什么字段或 Schema 表达该区别。

---

# Gap 2 — Document-level capability 被强制直接成为 Candidate capability，没有独立 Chunk-level ownership

### 当前 Schema / Transformation

```text
DocumentProfileSchema.content_capabilities
→ Chunk.extra.document_profile
→ RetrievalCandidateV2Schema.allowed_claim_types / forbidden_claim_types
→ EvidenceAssessmentV2Schema
```

### 当前真实行为

`RetrievalCandidateV2Schema` validator 要求它的 `allowed_claim_types` 与 `forbidden_claim_types` 必须和 embedded `DocumentProfileSchema.content_capabilities` 完全一致。

Child chunk 虽然可以携带 chunk-local `structured_evidence`，但 candidate claim capability 本身仍是整份 Document 的 capability 原样复制。

对于普通 historical paragraph chunk，`structured_evidence.representation_type=paragraph_text` 并不会独立表达“这个具体 chunk 是 technical-specification content”。

### 当前真实代码证据

- `DocumentProfileSchema` 明确把自己定义为权威的 **Document-level** Contract。
- `build_retrieval_candidate_v2()` 把 profile capability set 复制给 candidate。
- RetrievalCandidate validator 会拒绝任何与 embedded profile capability 不一致的 candidate。
- S5 Runtime 明确看到含 Ceph、technology-selection 等技术内容的 child evidence，但所有 candidate 仍只暴露 `general_method/typical_architecture/reference_case`。

### 对应 Badcase

BC04、BC06、BC16–BC19、BC21–BC24，尤其 BC21–BC24。

### 丢失 / 混淆的业务语义

当前边界无法通过 candidate claim-capability field 独立表达：

```text
“这份 Document 是历史项目”
同时
“这个具体 retrieved passage 具有更具体、可复用的 technical capability”
```

Passage 原文仍然存在，所以 content 本身没有丢；被压平的是 capability granularity。

### 第一次出错的位置

潜在位置是：`DocumentProfile.content_capabilities → RetrievalCandidateV2.allowed_claim_types`，因为该 transformation 被强制规定为 exact COPY，而不是由一个独立 ownership 表达 chunk capability。

### 下游影响

Historical technical passage 到 Writer 时仍只有 historical/reference capability，因此无法向 final Gate union 提供 `technical_specification`。

### 是否属于 Root Gap

**UNCERTAIN**

### 为什么它是 / 不是 Root Gap

Granularity compression 是真实存在的，但当前 Runtime 尚不足以证明“independent chunk-level capability”是唯一正确语义模型。

`ContentCapabilitiesSchema` 本身从语法上可以包含 `technical_specification`，换一种 catalog/policy population 也可能改变 Document-level capability，而不改 Schema。此外，当前 Governance 还有单独的 role-based filter（Gap 3），即使 Document capability 放开，historical-case evidence 的 technical specification 仍可能被过滤。

因此 Phase 1 可以确认 compression，但还不能证明这是 Schema Root Gap，而不是 intentional Document-level authority model + policy behavior 的组合结果。

---

# Gap 3 — `technical_specification` 被 `EvidenceRole.TECHNICAL_REFERENCE` 硬过滤

### 当前 Schema / Transformation

`OnlineEvidenceGovernanceV2._claim_structure_compatible()`。

### 当前真实行为

对于 `ClaimType.TECHNICAL_SPECIFICATION`，函数只有在下面条件成立时才返回 true：

```text
candidate.evidence_role == technical_reference
```

因此一个 `historical_case` candidate 即使 retrieved paragraph 中明确包含 Ceph、replication、erasure coding、snapshot、remote replication、resource-pool design 等技术细节，也不能支持 technical specification。

### 当前真实代码证据

- DocumentProfile 的 historical document types 强制要求 `evidence_role=historical_case`。
- `_claim_structure_compatible(TECHNICAL_SPECIFICATION)` 强制要求 `TECHNICAL_REFERENCE`。
- S5 Runtime 中 candidate 是 historical case，文本包含技术内容，但所有 item assessment 都没有 technical specification。

### 对应 Badcase

BC04、BC06、BC16–BC19、BC21–BC24。

### 丢失 / 混淆的业务语义

Historical identity 与 technical-content usability 被 eligibility rule 耦合在一起。

### 第一次出错的位置

`RetrievalCandidateV2 → EvidenceAssessmentV2` 的 `_claim_structure_compatible()`。

### 下游影响

Technical specification 在 section-wide Gate union 之前已经被移除；Writer 一旦生成这种 text，Claim Audit 就报告 `claim_type_not_allowed`。

### 是否属于 Root Gap

**NO**

### 为什么它是 / 不是 Root Gap

这是一个 **Gate/Evidence Policy restriction**，不是“缺少 Schema 字段”的证据。

Candidate 已经保留 document role、capability、text、structured evidence；随后代码主动执行 role-based rule。该 Policy 是否业务合理，是下一阶段 Owner 决策；Phase 1 不能把它偷换成 Schema defect。

---

# Gap 4 — Structural heading 进入 Substantive Claim 链

### 当前 Schema / Transformation

`SchemeSection.content → extract_substantive_sentence_spans()`。

### 当前真实行为

以下纯 heading：

- `（一）实施准备阶段。`
- `（二）建设部署阶段。`
- `（一）验收依据。`
- `（四）文档交付。`

被纳入 substantive row，随后被分类为 `template_structure`。

### 当前真实代码证据

`SubstantiveSentenceDetector` 已经有明确逻辑，意图过滤 heading、structural label、list marker、pure placeholder。主 Runtime 证明这些具体格式仍然逃过了 filter，因为最终它们出现在 `ClaimAuditResultSchema.generated_claims` 中。

### 对应 Badcase

BC26–BC30、BC32–BC34。

### 丢失 / 混淆的业务语义

Document structure 被误判成 substantive claim。

### 第一次出错的位置

`extract_substantive_sentence_spans()`，发生在任何 GeneratedClaim schema 被构造之前。

### 下游影响

Heading → `template_structure` → 不在 S8 Gate allow set → Claim Audit failed → section failed。

### 是否属于 Root Gap

**NO**

### 为什么它是 / 不是 Root Gap

实现中本来就存在“structure 与 claim 的区别”，detector 也已经试图表达这个区别。问题是具体格式没有命中规则，所以属于 Substantive Detector implementation behavior，而不是生产 Schema 无法表达 section structure 与 claim 的证据。

---

# Gap 5 — `requested_claim_types` 字段存在，但被当前 Claim Resolution Policy 显式忽略

### 当前 Schema / Transformation

```text
EvidenceRequirementsV2Schema.requested_claim_types
→ EvidenceAssessmentV2
→ GateDecisionSchema.allowed_claim_types
```

### 当前真实行为

`EvidenceRequirementsV2Schema` 可以保存 `requested_claim_types`，但当前 metadata 多次明确记录：

```text
claim_resolution_mode = candidate_capability_union
requested_claim_types_ignored = true
```

`_item_assessment()` 与 `_gate()` 不会把 supported claim 与 requested claim types 做 intersection。Final Gate allowed claims 完全来自 eligible evidence capability union。

正常 SchemeWriter retrieval path 也不会为 section 填 explicit requested claim types。

### 当前真实代码证据

- `resolve_evidence_requirements_v2()` metadata。
- `_gate()` 的 supported-claim union 实现。
- 所有 section 的 Runtime GateDecision metadata 都重复 `requested_claim_types_ignored=true`。

### 对应 Badcase

34 条 violation 中，没有任何一条能够证明它是 first cause。Observed failure 主要是 **under-authorization / misclassification**，而不是因为 section request 被忽略导致某个不该允许的 claim 被错误放行。

### 丢失 / 混淆的业务语义

Section-requested claim intent 虽然存在于字段中，但没有进入当前 permission resolution。

### 第一次出错的位置

`EvidenceRequirementsV2Schema` 与 `_gate()` 之间的 Policy execution：字段没有被消费。

### 下游影响

Gate permission 体现 candidate capability union，而不是 explicit section claim intent。

### 是否属于 Root Gap

**NO**

### 为什么它是 / 不是 Root Gap

Schema 已经有这个字段。Observed behavior 是一个明确的 **Policy / implementation choice：忽略 existing field**，不是 Schema expressiveness 不足。因此 Phase 1 不能把它定义成 Schema Root Gap。

---

# Gap 6 — Final Gate claim permission 被压平为 Section-wide Union，WriterGateBinding 不保存 Per-Claim Grant Provenance

### 当前 Schema / Transformation

```text
EvidenceAssessmentV2[]
→ GateDecisionSchema.allowed_claim_types
→ WriterGateBindingSchema
```

### 当前真实行为

`_gate()` 计算：

```text
allowed = UNION(eligible item supported_claim_types)
```

`GateDecisionSchema` 只保存得到的 section-wide claim-type set。`WriterGateBindingSchema` 内嵌 final GateDecision，并保存 evidence contract hash，但它不会直接记录“每一个 allowed ClaimType 到底由哪个具体 Evidence item 授权”。

Item-level provenance 仍然保存在 `EvidenceBundle.extra.evidence_governance_v2.item_assessments`，并且可通过 hash/trace 恢复，所以信息并没有在全局意义上彻底销毁；只是没有进入 Writer 直接消费的 permission Contract。

### 当前真实代码证据

- `_gate()` union logic。
- GateDecision fields。
- WriterGateBinding fields。
- Runtime governance artifact 单独保留 item assessments。

### 对应 Badcase

34 条 failure 中，没有一条证明“这个 flattening 本身”是 first semantic error。按当前 deterministic comparison，Claim Audit 只需要 final allowed/forbidden set 就能工作。

### 丢失 / 混淆的业务语义

WriterGateBinding 中没有直接 mapping：

```text
ClaimType X ← 哪些 EvidenceAssessment 授权了 X
```

### 第一次出错的位置

`EvidenceAssessmentV2[] → GateDecisionSchema` projection。

### 下游影响

Writer/ClaimAudit 消费的是 section-wide permission set，而不是 per-claim evidence-grant lineage。详细 lineage 仍保存在 binding 外部。

### 是否属于 Root Gap

**UNCERTAIN**

### 为什么它是 / 不是 Root Gap

信息 flattening 已确认，但当前没有 Badcase 证明 Phase-1 的正确结果必须要求 Writer 直接拥有 per-claim grant provenance。如果现在就宣布 Root Gap，会超过现有证据边界。

---

# Gap 7 — Generated Text 中 Policy / Template / Technical ClaimType 分类存在分歧

### 当前 Schema / Transformation

`SchemeSection.content → SemanticClaimClassifier → GeneratedClaimSchema.claim_type`。

### 当前真实行为

典型例子：

- BC13/BC14：被 policy/general evidence 支持的文本，被分类成 `technical_specification`。
- BC15：official policy clause 对 architecture-diagram presentation 的要求被分类成 `template_structure`，尽管 Gate 中已有 source 支撑的 `policy_requirement`。
- BC31：`建议以...作为验收依据` 是 qualified recommendation，却被分类成 `policy_requirement`；而 S8 只支持 general method/reference。

### 当前真实代码证据

Semantic classifier 被明确设计成与 Gate 独立，并使用同一套共享 `ClaimType` vocabulary。Runtime output 直接记录了争议 class label。

### 对应 Badcase

BC13–BC15、BC31；BC04/BC06 部分内容也存在 classification ambiguity。

### 丢失 / 混淆的业务语义

以下边界存在 ambiguity：

- recommendation/general method
- policy requirement
- technical specification
- template structure

### 第一次出错的位置

Observed Runtime 中是 Semantic ClaimType classification。

### 下游影响

一个本来有 supported meaning 的 sentence，如果被分到 Gate allowed set 中不存在的 class，就会 Claim Audit failed。

### 是否属于 Root Gap

**NO**

### 为什么它是 / 不是 Root Gap

所需 class 已经存在于共享 Schema 中，而且 BC13–BC15 等 case 中 Gate Runtime 已经携带合适的 supported class。证据更支持 model/classification error 或 taxonomy-boundary calibration 问题，而不是 Schema 缺少 required class。

---

# Gap 8 — 没有 Current-Project Evidence 时 Current-Project Assertion 被拒绝

### 当前 Schema / Transformation

```text
DocumentProfile.project_relation / content_capabilities
→ RetrievalCandidateV2
→ EvidenceAssessmentV2
→ GateDecision
→ ClaimAudit
```

### 当前真实行为

Other-project historical evidence 不能支持 current-project facts/configuration。Writer 生成的 current-project assertion 会被 forbidden。

### 当前真实代码证据

- DocumentProfile historical invariant。
- EvidenceAssessment current-project filtering。
- 所有 historical-only section 的 GateDecision current-project forbidden set。
- BC07–BC12 Runtime。

### 对应 Badcase

BC02 与 BC07–BC12，其中 BC07–BC12 是最明确的 positive-current-project assertion。

### 丢失 / 混淆的业务语义

没有。该区别被正确保留。

### 第一次出错的位置

对于这些 Correct rejection case，真正错误动作是 Writer 生成了 unsupported current-project assertion；Schema/Gate chain 没有制造错误。

### 下游影响

Claim Audit 与 Hard Gate 阻止 unsupported statement 进入 formal deliverable。

### 是否属于 Root Gap

**NO**

### 为什么它是 / 不是 Root Gap

这是正确的 Safety/Evidence invariant，不能仅仅为了提高 E2E pass rate 而削弱。

---

# Root Gap 汇总

## 已确认 Root Gap — 1 个

1. **当前 successful Generated Claim semantics 无法把 substantive unknown/deferred modality 与 positive ClaimType enforcement 独立表达。**

## Uncertain Gap — 2 个

1. **Document-level capability → Candidate capability granularity**：compression 真实存在，但当前证据不能证明一定需要 independent chunk-level capability，而不是 Catalog/Policy 决策。
2. **Section-wide Gate union 没有在 WriterGateBinding 中保存 direct per-claim grant provenance**：flattening 真实存在，但当前没有 Badcase 证明它具有因果作用。

## 已确认的 Non-Schema Cause

- Historical technical content → `technical_specification` 被 role policy 阻断：属于 **Policy / Evidence capability rule**，不是已证明的 Schema defect。
- 括号式 structural heading 进入 Audit：属于 **Substantive Detector implementation issue**。
- `requested_claim_types_ignored`：属于 existing Schema field 上的 **explicit Policy behavior**。
- policy/general/template/technical classification 分歧：属于 **Semantic classifier / taxonomy-classification behavior**。
- no-current-project-evidence → current-project facts forbidden：属于 **正确 Invariant**。

## Step 7 结论

动态审计**不支持**一个笼统结论：“整个 Schema Architecture 都坏了”。

当前证据只确认了 **1 个 Schema-level Root semantic loss**，另外 **2 个 information-compression point 仍是 UNCERTAIN**。其余大多数 observed failure 属于 Policy、Classifier、Detector，或者属于 Gate 的正确行为。

这就是 Phase-1 的证据边界；不能从这里继续推导未来 Schema 设计。
