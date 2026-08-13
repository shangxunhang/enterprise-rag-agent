# 05 — Schema 真实 Badcase 审计

> 主审计 Runtime：`E:\rag-agent-runtime\gov_cloud_deepseek_all_roles_ab_20260810_161856`  
> 复核性重跑：`E:\rag-agent-runtime\gov_cloud_deepseek_all_roles_ab_20260810_173830`  
> 本文沿当前 Schema 链穿透真实 runtime 事实，不提出修复方案，也不进行 Schema V2 设计。

## 1. 证据基础

主 Run 中可以直接检查以下证据：

- `e2e_report.json`
- `runs/gov_cloud_deepseek_all_roles_ab_20260810_161856_trace.jsonl`
- `captures/rejected/gov_cloud_deepseek_all_roles_ab_20260810_161856_rejected.jsonl`
- `captures/eval/gov_cloud_deepseek_all_roles_ab_20260810_161856_eval.jsonl`
- `captures/raw/gov_cloud_deepseek_all_roles_ab_20260810_161856_raw.jsonl`
- model usage 与 generated/rejected draft artifacts

Rejected capture 保存了每一个 final `SchemeSectionSchema`。每个 section 的 `input.rag_context.extra.evidence_contract` 又包含 canonical `EvidenceBundleSchema`、`RetrievalCandidateV2Schema`、`PromptEvidenceCandidateV1Schema`、`EvidenceAssessmentV2Schema` item assessments、`GateDecisionSchema`、final `WriterGateBindingSchema`、final `ClaimAuditResultSchema`、citations 与 section output。

因此，下面 34 条 violation 都是**直接来自 Runtime 的事实**，不是根据 class name 倒推出来的。

## 2. 主 Run 对账

| Section | Final status | Claim Audit | Violations | `claim_type_not_allowed` | `forbidden_claim_type_generated` |
|---|---:|---:|---:|---:|---:|
| 项目背景 | failed | failed | 3 | 1 | 2 |
| 建设目标 | failed | failed | 3 | 3 | 0 |
| 总体架构 | failed | failed | 9 | 3 | 6 |
| 计算资源建设方案 | failed | failed | 5 | 4 | 1 |
| 存储资源建设方案 | failed | failed | 5 | 4 | 1 |
| 网络资源建设方案 | success | passed | 0 | 0 | 0 |
| 安全设计 | success | passed | 0 | 0 | 0 |
| 实施与验收建议 | failed | failed | 9 | 9 | 0 |
| **合计** | **2 success / 6 failed** | — | **34** | **24** | **10** |

与 Phase-1 审计目标完全对账。

## 3. Section-level Evidence 快照

§4 的每个 Badcase 会引用这里的快照，使每条 violation 都能保留其 retrieved evidence、source profile、candidate projection、item assessment 与 final GateDecision，而不需要重复粘贴数 MB JSON。

### S1 — 项目背景

**Final GateDecision**

- `evidence_status = sufficient`
- `delivery_mode = qualified`
- allowed = `general_method, reference_case, typical_architecture`
- forbidden = 全部五种 `current_project_*` ClaimType
- qualifiers = `建议 / 参考同类项目 / 需结合本项目确认`
- `claim_resolution_mode = candidate_capability_union`
- `requested_claim_types_ignored = true`

**Selected evidence / source profiles / candidate / assessment**

| Evidence | Source document | DocumentProfile | RetrievalCandidate | EvidenceAssessment |
|---|---|---|---|---|
| S1-E1 | `doc_04da71abf394414281038785`，历史项目背景/必要性 | `historical_feasibility_study`；role=`historical_case`；relation=`other_project`；authority=`reference`；profile 允许 `general_method/typical_architecture/reference_case` | 与 profile 完全一致；`paragraph_text` | eligible/relevant；只支持 `general_method/reference_case/typical_architecture`；current-project types forbidden |
| S1-E2 | `doc_c718e22f8c459e2d4f447d3e`，历史项目建设必要性章节 | 相同语义类型：historical/other-project/reference；相同 profile capability set | mirrors profile；`paragraph_text` | eligible/partial；相同 supported set；current-project types forbidden |

**观察：** 本章没有任何 selected policy-standard 或 current-project evidence。

### S2 — 建设目标

**Final GateDecision：** sufficient/qualified；只允许 `general_method/reference_case/typical_architecture`；全部 current-project types forbidden；同样使用三个 qualifier。

| Evidence | Source document | DocumentProfile | RetrievalCandidate | EvidenceAssessment |
|---|---|---|---|---|
| S2-E1 | `doc_dc9385d9cce409724dabdd8b`，铜川政务云安全能力提升历史可研 | `historical_feasibility_study`；`historical_case`；`other_project`；authority=`reference`；只允许 `general_method/typical_architecture/reference_case` | mirrors profile；paragraph evidence | eligible/partial；只支持相同三种 type |

没有选中 policy-standard 或 technical-reference document。

### S3 — 总体架构

**Final GateDecision**

- sufficient/qualified
- allowed = `general_method, policy_requirement, reference_case, typical_architecture`
- forbidden = 全部 current-project types
- medium risk
- 存在 authoritative policy evidence

| Evidence | Source | DocumentProfile → RetrievalCandidate | EvidenceAssessment |
|---|---|---|---|
| S3-E1 | `doc_2cdec2800cd37b2235191a53`，官方政策/建设规范，章节 `5.1.1 总体架构` | `policy_standard`；role=`policy_standard`；relation=`general`；authority=`official`；profile 只允许 `policy_requirement`；structured representation=`policy_clause` | eligible/relevant；支持 `policy_requirement` |
| S3-E2 | `doc_24a730ad03ea740d395043ce`，历史建设方案 | `historical_construction_scheme`；`historical_case`；`other_project`；`draft`；允许 general/typical/reference | eligible/partial；支持 general/typical/reference |
| S3-E3 | `doc_f177b8ea62f7902916d914b7`，历史可研总体架构 | `historical_feasibility_study`；historical/other-project/reference；允许 general/typical/reference | eligible/relevant；相同 support |
| S3-E4 | `doc_dc9385d9cce409724dabdd8b`，历史可研架构/安全内容 | historical/other-project/reference；同样三种 capability | eligible/partial；相同 support |
| S3-E5 | `doc_2cdec2800cd37b2235191a53`，官方政策，章节 `5.5 基础设施设计` | policy/general/official；只允许 `policy_requirement` | eligible/partial；支持 `policy_requirement` |

重要 source fact：S3-E1 本身就包含架构图编制要求，包括层级关系、新建内容标注等。该 source 以 policy clause 表示，DocumentProfile 授权 `policy_requirement`，而不是 `template_structure/style_guidance`。

### S4 — 计算资源建设方案

**Final GateDecision：** sufficient/qualified；allowed=`general_method, policy_requirement, reference_case, typical_architecture`；全部 current-project types forbidden。

| Evidence | Source | DocumentProfile → RetrievalCandidate | EvidenceAssessment |
|---|---|---|---|
| S4-E1 | `doc_32990271df71b0e416965211`，历史建设方案 | historical/other-project/reference；general/typical/reference | eligible/partial；相同 support |
| S4-E2 | `doc_da8cd497ed9aa16c5cd1dd16`，历史可研基础设施方案 | historical/other-project/reference；general/typical/reference | eligible/partial；相同 support |
| S4-E3 | `doc_04da71abf394414281038785`，历史计算资源池方案 | historical/other-project/reference；general/typical/reference | eligible/relevant；相同 support |
| S4-E4 | `doc_24a730ad03ea740d395043ce`，历史平台建设方案 | historical/other-project/draft；general/typical/reference | eligible/partial；相同 support |
| S4-E5 | `doc_2cdec2800cd37b2235191a53`，官方政策/建设规范 `5.1.1` | policy/general/official；只允许 `policy_requirement` | eligible/relevant；支持 `policy_requirement` |

没有任何 selected document 的 `evidence_role=technical_reference`，因此 `_claim_structure_compatible()` 不可能从这些 selected candidate 中暴露 `technical_specification`。

### S5 — 存储资源建设方案

**Final GateDecision：** sufficient/qualified；allowed=`general_method, reference_case, typical_architecture`；全部 current-project types forbidden。没有选中 policy 或 technical-reference evidence。

| Evidence | Source | DocumentProfile → RetrievalCandidate | EvidenceAssessment |
|---|---|---|---|
| S5-E1 | `doc_04da71abf394414281038785`，历史 `4.2 存储需求分析`；retrieved text 明确包含 **Ceph 分布式存储** 与 storage architecture 内容 | `historical_feasibility_study`；`historical_case`；`other_project`；authority=`reference`；profile 只允许 general/typical/reference；`paragraph_text` | eligible/partial；只支持 general/typical/reference |
| S5-E2 | `doc_704cfac7c23f824343b67ad4`，历史 `2.存储资源池方案` | historical/other-project/reference；只允许 general/typical/reference | eligible/relevant；相同 support |
| S5-E3 | 同一 Document，`1.存储技术选型分析` | 相同 profile 与 candidate capability | eligible/relevant；相同 support |
| S5-E4 | `doc_3b172ec9c11f675d267f6870`，历史 XSSD storage construction section | historical/other-project/reference；相同 capability | eligible/relevant；相同 support |
| S5-E5 | `doc_8162a3ff9c0a0fc8fb663c59`，历史普通存储资源方案 | historical/other-project/draft；相同 capability | eligible/relevant；相同 support |

本章一条持久化 Citation（`C22`，source=`doc_704cf...`，`2.存储资源池方案`）向 Writer 暴露的 prompt-visible evidence 明确写成：

- `【历史案例，仅供参考】`
- allowed use = `general_method / reference_case / typical_architecture`
- 数量、容量、品牌、型号、设备配置、工期不能作为 current-project truth 暴露

因此 retrieval/citation 层正确保留了 historical identity。无法授权 `technical_specification` 的限制，在 Claim Audit 之前就已经存在于 Document capability / role semantics 中。

### S6 — 网络资源建设方案

**Final GateDecision：** sufficient/qualified；allowed=`general_method, policy_requirement, reference_case, typical_architecture`；final Claim Audit passed。

这是一个 control case：即使继续使用相同的 historical/reference restriction，只要 generated text 落在 Gate boundary 内，section 仍然可以成功。

### S7 — 安全设计

**Final GateDecision：** sufficient/qualified；allowed=`general_method, reference_case, typical_architecture`；四份 selected document 都是 historical case；final Claim Audit passed。

这是第二个 control case：历史技术内容并不必然导致失败，只要 Writer 把内容表达成允许的 reference/method/architecture semantics。

### S8 — 实施与验收建议

**Final GateDecision：** sufficient/qualified；只允许 `general_method, reference_case, typical_architecture`；全部 current-project types forbidden。

| Evidence | Source | DocumentProfile → RetrievalCandidate | EvidenceAssessment |
|---|---|---|---|
| S8-E1 | `doc_24a730ad03ea740d395043ce`，历史建设方案/编制依据 | historical/other-project/draft；general/typical/reference | eligible/partial；支持三种 allowed type |
| S8-E2 | `doc_dc9385d9cce409724dabdd8b`，历史可研验收管理内容 | historical/other-project/reference；general/typical/reference | eligible/partial；支持相同三种 type |
| S8-E3 | `doc_68469c6a660a694702cdee97`，历史实施保障内容 | historical/draft；被 Context Packer drop | 不进入 final prompt evidence assessment |

Final S8 prompt evidence 中没有 selected template 或 policy-standard document。

---

## 4. 全部 34 条 Claim Audit Violation

本次审计统一使用以下分类：

- **A** Correct rejection：正确拒绝
- **B** Document classification issue：Document 分类问题
- **C** Evidence capability issue：Evidence capability 问题
- **D** Schema transformation information loss：Schema transformation 信息丢失
- **E** Claim classification issue：Claim 分类问题
- **F** Substantive detection issue：Substantive detector 问题
- **G** Citation / grounding issue：Citation / Grounding 问题
- **H** Gate policy issue：Gate Policy 问题
- **I** Insufficient evidence to conclude：证据不足，无法判断

`First divergence` 指的是 Runtime 语义第一次偏离预期的位置，而不是最后抛出 failure 的位置。

### 项目背景 — Badcases 01–03

| ID | Generated sentence | Runtime ClaimType / violation | Evidence chain | Gate / final | First divergence | 审计分类 |
|---|---|---|---|---|---|---|
| BC01 | 从政策导向看，国家及地方层面均对政务信息系统整合共享和政务云建设提出了明确要求，强调依托统一云平台实现基础设施共建共用，促进业务协同与数据汇聚。 | `policy_requirement` / `claim_type_not_allowed` | S1-E1/E2 全是 historical case；profile/candidate/assessment 都没有 policy capability | Gate 只允许 general/reference/typical；section failed | 没有证据表明上游语义被错误转换。Generated sentence 在没有 policy-capable selected evidence 时直接断言政策要求。 | **A primary — Correct rejection** |
| BC02 | 本项目拟结合当前政务信息化现状及未来业务增长趋势，统筹规划政务云的整体架构。 | `current_project_fact` / `forbidden_claim_type_generated` | S1 只有 historical evidence；没有 current-project evidence；assessment 正确禁止全部 current-project types | Gate 正确禁止 current-project claims；section failed | Writer 在只有 historical/reference evidence 的情况下生成了 current-project intent/commitment。Schema/Gate 一直正确保留了 prohibition。 | **A primary — Correct rejection** |
| BC03 | 由于项目具体现状、用户规模、资源需求量及投资金额等基础数据尚未明确，相关建设规模与配置需在后续调研中结合实际情况进行测算和确认（待补充）。 | `current_project_fact` / `forbidden_claim_type_generated` | S1 chain 正确表达 historical-only；无 current facts | Gate 禁止 current facts | `SchemeSection.content → substantive sentence → SemanticClaimClassifier → GeneratedClaimSchema`：一个**否定/延期确认声明**被重新分类成正向 current-project fact。Runtime Claim schema 只有 `claim_type`，没有独立 assertion/modality state；而 substantive sentence 若返回 empty ClaimType 又会 fail closed。 | **E primary + D secondary** |

### 建设目标 — Badcases 04–06

| ID | Generated sentence | Runtime ClaimType / violation | Evidence chain | Gate / final | First divergence | 审计分类 |
|---|---|---|---|---|---|---|
| BC04 | 面向政务业务的不确定性增长与突发流量，平台应具备按需分配、弹性伸缩的服务能力，支持资源池的动态调整与快速交付，满足跨部门业务系统部署、扩容及灾备需求。 | `technical_specification` / `claim_type_not_allowed` | S2 只有一个 historical-case candidate；profile/candidate 只允许 general/typical/reference。不存在 `technical_reference` role，因此 item assessment 不可能支持 technical specification。 | Gate 不包含 technical specification；failed | 最早限制来自 Evidence capability semantics：historical technical content 保持 historical-case capability，不能转化成 technical-spec support。 | **C primary — Evidence capability issue** |
| BC05 | 遵循国家网络安全等级保护要求与政务云安全相关标准，构建涵盖物理安全、网络安全、主机安全、应用安全、数据安全及安全管理的纵深防御体系，强化数据隔离、访问控制、加密传输与审计追溯能力，确保云平台及承载业务的安全稳定运行。 | `policy_requirement` / `claim_type_not_allowed` | S2 selected evidence 全为 historical；没有 policy-standard candidate | Gate 无 policy permission | Policy requirement 没有 policy-capable selected evidence。 | **A primary — Correct rejection** |
| BC06 | 同 BC05 | `technical_specification` / `claim_type_not_allowed` | 相同 S2 historical candidate；无 technical-reference role/capability | Gate 无 technical specification | 与 BC04 相同的 technical label Evidence capability boundary。 | **C primary** |

### 总体架构 — Badcases 07–15

| ID | Generated sentence | Runtime ClaimType / violation | Evidence chain | Gate / final | First divergence | 审计分类 |
|---|---|---|---|---|---|---|
| BC07 | **用户层**：覆盖本项目建设所服务的各类用户群体，包括本部门（单位）内部用户、其他相关部门（单位）以及社会公众等。 | `current_project_fact` / forbidden | S3 有 policy + historical evidence，但无 current-project evidence | Gate 正确禁止全部 current-project claims | Writer 把 reference architecture 改写成 current-project population fact。 | **A** |
| BC08 | **展现层**：面向不同用户场景提供多渠道、多形态的访问入口，包括但不限于大屏展示系统、移动终端、桌面终端等已建、在建及新建的展示设备。 | `current_project_fact` / forbidden | S3 无 current-project inventory evidence | 同上 | Writer 断言 current inventory/build state。 | **A** |
| BC09 | **业务应用层**：承载各类政务业务应用系统，涵盖已建系统的接入整合、在建系统的迁移上云以及新建系统的部署运行。 | `current_project_fact` / forbidden | 无 current-project inventory/decision source | 同上 | Writer 断言 current-project system state。 | **A** |
| BC10 | **应用支撑层**：提供应用系统运行所需的公共支撑能力，包括依托上级政务云提供的公共支撑组件与服务，以及本项目新建的应用支撑软件。 | `current_project_fact` / forbidden | 无 current-project decision source | 同上 | Writer 把 reference architecture 变成 current-project implementation fact。 | **A** |
| BC11 | **数据资源层**：构建数据治理与共享交换的基础能力，包括数据采集、清洗、转换、去重、质量管理、标准管理等数据治理实施服务，以及原始库、基础库、主题库、专题库等数据资源建设。 | `current_project_fact` / forbidden | 无 current-project decision source | 同上 | Writer 断言 current project scope。 | **A** |
| BC12 | **基础设施层**：依托政务云平台提供计算、存储、网络、安全及密码应用等基础设施资源，使用电子政务外网/内网、互联网等网络资源，并利用已建或在建机房环境。 | `current_project_configuration` / forbidden | 无 current-project configuration/inventory evidence | 同上 | Writer 断言 current deployment/configuration。 | **A** |
| BC13 | **标准规范体系**：遵循国家及省市政务信息化建设相关标准，结合本项目实际建立健全数据、技术、管理等方面的标准规范，确保架构的合规性与一致性。 | `technical_specification` / not allowed | S3-E1/E5 是 official policy clauses，而且 Gate **已经允许 `policy_requirement`**；historical candidate 再提供 general/reference/typical | Gate 不允许 technical specification | Source/Gate 已能把这句话表达成 policy requirement，但 semantic classifier 生成 `technical_specification`。最早观察到的偏差是 generated-text ClaimType classification。 | **E primary** |
| BC14 | **安全管理体系**：建立覆盖体制机制、管理制度、技术防护和运营服务的安全保障框架，落实网络安全等级保护要求，保障云平台及业务系统的安全稳定运行。 | `technical_specification` / not allowed | S3 有 official policy capability + historical methods | Gate 允许 policy/general/reference/typical | 同样：generated semantics 被压成 technical specification，尽管 Gate 中已有受证据支持的 policy/general class。 | **E primary** |
| BC15 | 总体架构图应标明各层级之间的关联关系，本项目新建内容应在架构图中以特殊颜色标注。 | `template_structure` / not allowed | S3-E1 official policy clause 直接包含 architecture-diagram requirement；profile/candidate 允许 `policy_requirement`，assessment 也支持它 | Gate 允许 `policy_requirement`，不允许 template structure | Semantic classifier 把一条有来源的 policy requirement 重新分类成 template structure。DocumentProfile→Candidate 没有丢失 source；Gate 已经拥有 supported policy class。 | **E primary** |

### 计算资源建设方案 — Badcases 16–20

| ID | Generated sentence | Runtime ClaimType / violation | Evidence chain | Gate / final | First divergence | 审计分类 |
|---|---|---|---|---|---|---|
| BC16 | 资源池应支持主流虚拟化技术，具备计算、存储、网络资源的统一调度能力，并可根据业务负载变化进行弹性扩缩容。 | `technical_specification` / not allowed | S4 包含 historical technical content + 一个 policy candidate。Historical profile 只允许 general/typical/reference；policy candidate 只允许 policy requirement。没有任何 candidate 是 technical_reference。 | Gate 无 technical specification | Evidence capability/role boundary 阻止 historical technical content 被表示成 technical-spec support。 | **C** |
| BC17 | 计算资源应纳入统一的云管理平台进行管理，实现资源申请、审批、分配、监控、回收的全生命周期管理。 | `technical_specification` / not allowed | 同 S4 | 同上 | 相同 capability boundary | **C** |
| BC18 | 管理平台应支持多租户隔离，满足不同部门、不同业务系统之间的安全隔离要求； | `technical_specification` / not allowed | 同 S4 | 同上 | 相同 | **C** |
| BC19 | 应提供资源使用情况的实时监控与统计分析能力，为资源优化调整提供依据。 | `technical_specification` / not allowed | 同 S4 | 同上 | 相同 | **C** |
| BC20 | 本项目计算资源建设涉及的具体设备数量、品牌型号、配置参数、投资金额及实施工期等信息，需结合项目现状调研和需求分析后由项目方确认，本方案暂不设定具体数值。 | `current_project_fact` / forbidden | S4 Gate 正确禁止 current-project fact，而且不存在 current-project evidence | Gate rejects | 与 BC03 一样，这句话表达的是**缺失 / 延期确认**，不是 positive project fact。最早偏差发生在 substantive sentence 被迫进入当前 ClaimType enforcement，并被分类为 `current_project_fact` 时。 | **E primary + D secondary** |

### 存储资源建设方案 — Badcases 21–25

| ID | Generated sentence | Runtime ClaimType / violation | Evidence chain | Gate / final | First divergence | 审计分类 |
|---|---|---|---|---|---|---|
| BC21 | 存储资源池应支持多副本或纠删码等数据冗余机制，保障数据高可靠； | `technical_specification` / not allowed | S5-E1 明确包含 Ceph/distributed-storage technical content；S5-E2/E3 是 storage-resource/technology-selection historical section。所有 profile 仍为 `historical_case`，只允许 general/typical/reference。Candidate 精确 mirror，assessment 保留该限制。 | Gate 无 technical specification | **Document/candidate capability semantics，发生在 Gate 之前。** 技术内容真实存在，但 historical-case document 无法表达 technical-spec capability；candidate copy 是忠实复制，不是 corruption。 | **C primary** |
| BC22 | 同时提供快照、克隆、远程复制等数据保护能力，满足政务数据安全与业务连续性要求。 | `technical_specification` / not allowed | 同 S5 historical technical sources | 同上 | 相同 capability restriction | **C** |
| BC23 | 存储资源池应具备在线横向扩展能力，支持在不中断业务的情况下按需扩容，满足未来3至5年的业务发展需要。 | `technical_specification` / not allowed | 同 S5；无 technical-reference candidate | 同上 | 相同 capability restriction；另外 3–5 年 horizon 也没有被建立为 current-project fact，但实际 violation 是 technical specification。 | **C primary** |
| BC24 | 存储系统应提供多层次数据保护机制，包括副本/纠删码冗余、定期快照、远程复制等，并结合备份系统实现数据本地备份与异地容灾。 | `technical_specification` / not allowed | 同 S5，且有直接相关 historical technical content | 同上 | 相同 capability restriction | **C** |
| BC25 | 本项目当前存储容量、性能指标、设备品牌型号、投资金额及实施工期等信息待补充，需结合项目调研和详细设计阶段进一步确认。 | `current_project_fact` / forbidden | S5 无 current-project evidence；Gate 正确禁止 current facts | Gate rejects | 该句明确表示 value **unknown/pending**，但 generated ClaimType 变成 positive current-project fact。First divergence 与 BC03/BC20 相同。 | **E primary + D secondary** |

### 实施与验收建议 — Badcases 26–34

| ID | Generated sentence | Runtime ClaimType / violation | Evidence chain | Gate / final | First divergence | 审计分类 |
|---|---|---|---|---|---|---|
| BC26 | （一）实施准备阶段。 | `template_structure` / not allowed | S8 只有 historical-method evidence；无 template capability | Gate 无 template structure | **SubstantiveSentenceDetector** 把纯 structural heading 放进 substantive row；之后 semantic classifier 才把它标为 template structure。 | **F primary** |
| BC27 | （二）建设部署阶段。 | `template_structure` / not allowed | 同上 | 同上 | 相同 detector boundary | **F** |
| BC28 | （三）迁移上线阶段。 | `template_structure` / not allowed | 同上 | 同上 | 相同 | **F** |
| BC29 | （四）试运行与优化阶段。 | `template_structure` / not allowed | 同上 | 同上 | 相同 | **F** |
| BC30 | （一）验收依据。 | `template_structure` / not allowed | 同上 | 同上 | 相同 | **F** |
| BC31 | 建议以国家及地方关于政务云建设相关标准规范、本项目批复文件及招投标文件、合同约定及经确认的设计方案等作为验收依据。 | `policy_requirement` / not allowed | S8 selected evidence 只有 historical/reference；但 wording 明确是 `建议...作为验收依据`，属于 Gate 已允许的 method/recommendation form，可归 `general_method` | Gate 无 policy requirement | Generated-text ClaimType classification 把 qualified recommendation 提升成 policy requirement。没有 selected policy-standard candidate。 | **E primary** |
| BC32 | （二）验收内容。 | `template_structure` / not allowed | S8 historical-only | 同上 | pure heading 被当 substantive | **F** |
| BC33 | （三）验收组织。 | `template_structure` / not allowed | 同上 | 同上 | 相同 | **F** |
| BC34 | （四）文档交付。 | `template_structure` / not allowed | 同上 | 同上 | 相同 | **F** |

## 5. 34 条 Badcase 的主因对账

每条 violation 只取一个 primary category（secondary tag 已保留在上文）：

| Primary category | 数量 | Badcase IDs |
|---|---:|---|
| A. Correct rejection | 9 | 01, 02, 05, 07–12 |
| B. Document classification issue | 0 | — |
| C. Evidence capability issue | 10 | 04, 06, 16–19, 21–24 |
| D. Schema transformation information loss | 单独作为 primary 为 0；secondary 为 3 | secondary on 03, 20, 25 |
| E. Claim classification issue | 7 | 03, 13–15, 20, 25, 31 |
| F. Substantive detection issue | 8 | 26–30, 32–34 |
| G. Citation / grounding issue | 0 | — |
| H. Gate policy issue | 作为单独 primary 未被证明 | — |
| I. Insufficient evidence to conclude | 34 条主因中为 0 | — |
| **总计** | **34** | — |

这一分布非常重要：**9/34 是正确拒绝**。如果把 34 条全部当成 Schema Bug，会与 Runtime 事实冲突。

## 6. 必须重点验证的四类问题

### 6.1 Unknown / Deferred Modality

主 Run 中对应 BC03、BC20、BC25。17:38 的复核 Run 又独立生成了多条相似句子，例如：

- `鉴于项目现状、用户规模、资源需求量等关键基础数据尚未明确...均需结合项目实际情况进一步确认...`
- `机房使用情况需项目方确认。`
- `上述资源规模、设备配置及部署方式均需结合本项目调研情况确认，待补充。`
- `安全运营团队配置及值班模式需项目方确认。`

这些句子再次进入 Claim Audit，并被当成 current-project fact 或其他 positive ClaimType。

**最早语义偏差：** 不在 DocumentProfile、RetrievalCandidate、EvidenceAssessment 或 GateDecision。上述层级都正确表示 current-project facts 当前不可用/被禁止。偏差发生在 generation 之后：

```text
SchemeSection.content
  → SubstantiveSentenceDetector
  → SemanticClaimClassifier
  → GeneratedClaimSchema.claim_type
```

Detector 把“完整解释缺失信息”的句子视为 substantive 是合理的——它并不是一个单独 `TBD` token。问题在于当前 semantic contract 最终只有 ClaimType label。虽然 Semantic classifier prompt 技术上允许输出 empty `claim_types` array，但 `WriterClaimAuditor` 对任何 substantive sentence 的 empty result 都会产生 `semantic_unclassified_substantive_claim`。

因此，一个有业务意义的 **unknown/deferred disclosure 在当前 substantive-claim audit contract 中没有成功状态**，除非它被硬塞进某个已有 ClaimType。实际 Run 中 DeepSeek 把它塞进 `current_project_fact`，随后与 Gate 正确发生冲突。

**审计分类：** Runtime 实际 classifier event 是 E；Contract boundary 是 D，因为“unknown/deferred”这一必要语义在成功 GeneratedClaim state 中没有被独立表达。

### 6.2 Structural Headings

BC26–30 与 BC32–34 都是纯标题，但最终变成了 `GeneratedClaimSchema` object，证明它们已经通过 `extract_substantive_sentence_spans()`。

Detector 本身已有 structural filter，但 final text 中这类“中文括号序号 + 标题”的形式没有被排除。一旦进入 substantive chain，Semantic classifier 把它们映射成 `template_structure` 其实是合理的；随后因为 S8 没有 template evidence，Gate 才拒绝。

**最早错误：** substantive detection，发生在 `GeneratedClaimSchema` 之前。  
**审计分类：** F。  
**Schema 结论：** 这不足以证明 Schema 缺少某种可表达语义，更像是 implementation/detector behavior。

### 6.3 Historical technical content

S5 是最有决定性的 Runtime 证据：

```text
historical feasibility document
  content includes Ceph / storage architecture / technology selection
  ↓
DocumentProfile:
  evidence_role = historical_case
  allowed_claim_types = general_method / typical_architecture / reference_case
  ↓ COPY exactly
RetrievalCandidateV2:
  same role and same allow set
  ↓ FILTER
EvidenceAssessmentV2:
  same supported set
  ↓ UNION
GateDecision:
  technical_specification absent
  ↓
Writer sentence classified technical_specification
  ↓
claim_type_not_allowed
```

`DocumentProfile→RetrievalCandidate` 之间**没有 accidental information loss**：candidate validator 明确要求 capability 完全相等。最早的 capability restriction 已经编码在 DocumentProfile level，`_claim_structure_compatible()` 又进一步规定：只有 `TECHNICAL_REFERENCE` role 才能支持 `technical_specification`。

因此，这类 C Badcase 不是 retrieval corruption，而是一个 **Evidence capability expressiveness / ownership boundary** 问题：同一份 source 可以既是 historical project，又包含可以复用的技术细节，而当前 Document-level role/capability projection 无法把这部分内容授权成 `technical_specification`。

### 6.4 Correct current-project rejection

BC07–BC12 是最干净的 control group。Run 中不存在 current-project source evidence；相关 profile 全是 other-project historical case 或 general policy。以下句子属于明确 current-project assertion：

- `本项目新建的应用支撑软件`
- `已建系统的接入整合、在建系统的迁移上云以及新建系统的部署运行`
- `依托政务云平台提供...并利用已建或在建机房环境`

从 DocumentProfile 到 GateDecision，拒绝逻辑完全一致，因此应视为**正确行为**，不能当成 Schema defect。

## 7. Policy / Model / Data / Implementation 分离

| 观察到的现象 | 当前审计类型 | 原因 |
|---|---|---|
| historical/other-project evidence 不能证明 current-project facts | Correct invariant / policy | 全链路一致表达并正确执行 |
| 纯标题进入 Claim Audit | Implementation / substantive detector behavior | 区分本身存在，但 detector 没排除当前 observed form |
| policy sentence 被分成 technical/template，尽管 Gate 已有 supported `policy_requirement` | Model/classification behavior | Gate 已存在被证据支持的 semantic type，但 classifier 选择了另一 type |
| historical technical passage 只能 general/typical/reference | Evidence capability / Schema ownership boundary | 限制在 model generation 前已存在，并一致复制到下游 |
| unknown/deferred disclosure 变成 `current_project_fact` | Model classification + Schema-expression boundary | model 选择错误 positive class，同时 successful substantive-claim result 没有独立 modality/certainty dimension |
| `requested_claim_types_ignored=true` | 当前 Gate policy behavior | 代码/runtime 已确认，但这 34 条 violation 并未证明它是首因 |
| Citation/Grounding | 不是 34 条 Claim Audit violation 的 primary | 上述 violation 没有一个最早从 CitationSchema/CitationBindingSchema 开始 |

## 8. 17:38 全 DeepSeek 重跑的复核证据

Run：`gov_cloud_deepseek_all_roles_ab_20260810_173830`。

- 8 个 section 都完成 generation，但 Claim Audit 之后 final section status 全部 failed
- Hard Gate failed
- 31 条 Claim Audit violation = 21 `claim_type_not_allowed` + 10 `forbidden_claim_type_generated`
- 与旧 Run 的 2 success / 6 failed 不同，本 Run 8/8 failed

虽然具体句子会因为 Writer 与 semantic classification 的模型输出而变化，但以下机制再次出现：

1. deferred/unknown statement 再次被映射到 positive ClaimType 并被拒绝；
2. technical/policy ClaimType mismatch 仍然存在；
3. 在没有 current-project evidence 的情况下，current-project claim 继续被禁止；
4. violation count 与 per-section success 会受 model output 影响，因此 **raw count 本身不是稳定的 architecture metric**。

旧 Run 的 34 条仍然是 Phase-1 强制穿透的数据集；17:38 Run 只作为 corroboration，不替代原数据集。

## 9. Step 6 结论

主 Run 的 34 条 Claim Audit violation 已全部沿生产链完成穿透。它们的首因并不统一：

- **9 条正确 Gate rejection**
- **10 条 Evidence capability mismatch**
- **7 条 Generated ClaimType classification failure**，其中 3 条还暴露 deferred modality 的 Schema-expression / semantic-loss boundary
- **8 条 Substantive detector failure**
- **0 条 primary Citation/Grounding failure**
- **0 条由该 Runtime 证明的 primary Document classification failure**

因此，最终 Claim Audit 更多是在**暴露上游语义错位**，而不是所有错误最初产生的位置。
