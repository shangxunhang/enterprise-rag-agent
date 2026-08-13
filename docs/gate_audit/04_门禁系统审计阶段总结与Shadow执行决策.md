# RAG-Agent 门禁系统审计阶段总结与 Shadow 执行决策

> 文档性质：Gate Audit 阶段总结 / 架构决策记录 / 后续实施基线  
> 项目目录：`D:\MyCode\rag-agent`  
> 记录日期：2026-08-11  
> 当前阶段：Gate P0 审计已基本收敛，准备从“持续审计”切换到“主链路功能闭环 + Gate 渐进修复”  
> 关联文档：
>
> - `docs/gate_audit/01_门禁系统总表.md`
> - `docs/gate_audit/02_门禁判定逻辑与状态机.md`
> - `docs/gate_audit/03_门禁失败传播与根因矩阵.md`
>
> 本文不替代 01/02/03，而是记录：为什么开始审计、审计过程中如何改变判断、最终确认的问题是什么、哪些不是 Root、用户最终给出的推进方案是什么，以及后续实施时必须遵守哪些边界。

---

# 一、为什么开始做 Gate 审计

Gate 审计不是为了单独建设一个“复杂门禁系统”，而是因为 RAG-Agent 主链路在真实建设方案生成过程中被 Gate 大量阻断，已经影响到后续 Multi-Agent、Memory、Evaluation 等模块继续推进。

最初暴露出的现象包括：

1. 章节能够生成，但大量 Section 被判失败；
2. Claim Audit 出现大量 `claim_type_not_allowed` / `forbidden_claim_type_generated`；
3. Section Failure 会继续放大到 Formal Eligibility、G39、G40、G41，最终整份文档被拒绝；
4. 表面现象很像“门禁太严格”，但无法确认究竟是：
   - Gate policy 本身太严格；
   - 数据标签 / Document Profile 有问题；
   - Evidence Capability 过窄；
   - Claim 分类错误；
   - Writer 模型能力不足；
   - 还是最终 G40/G41 设计不合理。

因此开始 Gate Audit，目标不是继续扩 Gate，而是回答：

> **到底哪几个 Gate 真正在做业务判断，哪几个只是派生状态；最早的失败 Root 在哪里；哪些失败是正确拦截，哪些失败是误判。**

同时，从一开始就有明确的范围控制：

- 不因为审计再新增大量 Gate；
- 不把未来 Feature Backlog 当成当前 Gap；
- 不为了“企业级”无限扩展；
- 先弄清现有系统，再做最小修复；
- Gate 审计不能无限阻塞 Multi-Agent / Evaluation 主线。

---

# 二、审计前后最重要的认知变化

## 2.1 最初直觉：门禁太严格

一开始从运行结果看，最直观的感觉是：

> Gate 数量很多，章节写之前、写之后、文档最终还有多层 Gate，系统可能被设计得过度严格。

尤其当一条 Claim Audit violation 最终导致：

`G15 FAIL → Section FAILED → Formal Ineligible → G39 FAIL → G40 FAIL → G41 FAIL → Final Rejected`

时，很容易把 G40/G41 看成新的强业务判断。

## 2.2 静态审计后的结论：不是 50 个同等级业务门

通过 01/02/03 静态审计，已经确认：

- G01-G50 是控制点，不是 50 个等价业务 Gate；
- 其中有 Detector、Validator、Policy Resolver、Derived Status、Aggregator；
- 很多后置 Gate 并不会创建新的业务失败事实，只是把上游失败继续投影或汇总。

因此必须区分：

- **Root Failure**：失败事实第一次出现的位置；
- **Derived Failure**：只是把上游失败映射成新的状态；
- **Aggregated Failure**：把多个已有失败收集到文档级；
- **Technical Failure**：运行、Schema、Model Call 等技术失败；
- **Independent Parallel Failure**：来自新的独立输入，但不一定属于主失败链。

这成为后续判断的核心原则：

> **Earliest-Failure Principle：一个失败事实第一次出现在哪里，哪里才是 Root。后面重复出现的 false 不能重复计算为新 Root。**

---

# 三、门禁链路最终被还原成什么样

当前真实主链路可以概括为：

```text
User / Query
  ↓
Project Input
  ↓
RAG / Retrieval
  ↓
G01 CRAG Evidence Quality
  ↓
G02 Corrective Retrieval Route
  ↓
G04 Evidence Governance / Eligibility
  ↓
G05 Evidence Set Assessment
  ↓
G06 Evidence Gate / Delivery Contract
  ↓
G10 Writer Binding
  ↓
G11 Pre-Generation Enforcement
  ↓
Writer 真正生成章节
  ↓
G13 Substantive Sentence Detector
  ↓
G14 Semantic Claim Classifier
  ↓
G15 Claim Audit
  ↓
G19 Citation Grounding
  ↓
G20 Self-RAG
  ↓
G21 Semantic Judge
  ↓
G23 Section Quality
  ↓
G24 Section Status Projection
  ↓
G25 Formal Eligibility
  ↓
8 章完成
  ↓
G34 Cross-Section Verification
  ↓
G35 Repair Resolution
  ↓
G36 Assembly Validation
  ↓
G37 Final Artifact Review
  ↓
G38 Citation Occurrence Integrity
  ↓
G39 Writer Artifact Integrity / Formal Recompute
  ↓
G40 Document Hard Gate
  ↓
G41 Closure
  ↓
G42 Route
  ↓
G43/G44 Explicit Human Approval（仅显式配置时）
  ↓
G47/G48 Release Revalidation
```

其中最重要的架构事实是：

1. **G11 是真正的写前最终放行点之一；**
2. **G15 是当前 Claim Policy 的关键写后业务判断点；**
3. **G39 是最终落盘 Artifact 的确定性重算，不应被理解成第二套业务策略；**
4. **G40/G41 是文档级汇总 / closure，不应重新定义 Claim 业务语义；**
5. **G43 才是唯一已经确认的 LangGraph `interrupt()` 人工审批点。**

---

# 四、用户对 Gate 业务权力层级的最终要求

审计过程中曾经出现一个容易混淆的问题：

从“失败影响范围”看，G40/G41 一旦失败会拒绝整份文档，看起来杀伤力最大；但用户明确指出：

> **业务政策上的判断权不应该越往后越大，而应该越往前越强。**

最终应当区分：

## 4.1 业务政策权威（用户要求）

```text
Section 写前 Gate
    >
Section 写后 Gate
    >
G40 / G41 文档总门
```

含义：

### Section 写前 Gate
负责最强的业务边界：

- 能不能写；
- 允许写什么 Claim；
- 禁止写什么 Claim；
- 哪些内容必须带 qualifier；
- 当前证据允许生成到什么程度。

### Section 写后 Gate
负责验证 Writer 是否遵守写前 Contract：

- 有没有越界；
- 有没有把历史项目写成本项目；
- 有没有写禁止的 current-project fact；
- required qualifier 是否满足；
- Claim semantics 是否与 Contract 一致。

### G40/G41
只负责：

- 文档完整性；
- Section 结果聚合；
- Closure；
- 跨章节冲突；
- 最终 Artifact 可交付性；
- 最终 veto。

G40/G41 **可以保留最终否决权**，因为有些问题只有文档级才能知道，例如跨章节冲突、最终组装、引用 occurrence、最终 Artifact 泄漏等。

但 G40/G41 **不能成为新的业务 Claim Policy 决策中心**。

这一区分必须长期保留：

> **业务判断权前置，最终完整性否决权后置。**

---

# 五、两轮 Full DeepSeek 实验得到的真实事实

## 5.1 原始标签 Full DeepSeek

Runtime：

`E:\rag-agent-runtime\gov_cloud_deepseek_all_roles_ab_20260810_141342`

结果：

- 8 个 Section：0/8 Success；
- Claim violations：55；
- 约 41 条 `forbidden_claim_type_generated`；
- 约 14 条 `claim_type_not_allowed`。

此时还不能判断是模型弱还是 Gate 严，因为数据 / Document Profile 也可能有问题。

## 5.2 手工重新分类 31 份文档后的 Full DeepSeek

Runtime：

`E:\rag-agent-runtime\gov_cloud_deepseek_all_roles_ab_20260810_161856`

结果：

- 8 个 Section：2/8 Success；
- Claim violations：34；
- `forbidden` 从约 41 降到 10；
- `not_allowed` 从约 14 增到 24。

按章节大致为：

- 项目背景：3；
- 建设目标：3；
- 总体架构：9；
- 计算资源：5；
- 存储资源：5；
- 网络资源：0；
- 安全设计：0；
- 实施与验收：9。

这一轮最重要的事实不是“仍然失败”，而是：

> **文档重新分类后 violations 明显下降，证明数据身份 / Evidence Role / Capability 对 Gate 结果有重大影响。**

同时最新 Run 中：

- 8 个章节的 `writer_generation_allowed` 全部为 true；
- Evidence boundary 为 true；
- Citation Grounding 基本通过；
- High-risk grounding 通过；
- Assembly / Document Verification 通过；
- substantive sentence 全部完成分类；
- `semantic_unclassified_sentence_count = 0`；
- Semantic Classifier Call 成功；
- Writer Artifact Recompute 能够复算一致。

因此当前失败高度收敛到：

> **G15 Claim Policy 及其上游语义输入。**

而不是 Citation、G40、Assembly 或模型调用失败。

---

# 六、为什么 34 条 violation 不能被当成 34 个 Root

这是 Gate Audit 最重要的结论之一。

一条 `claim_type_not_allowed` 可以继续导致：

```text
G15 violation
  ↓
claim_audit.passed = false
  ↓
human_review_required = true
  ↓
G24 Section FAILED
  ↓
G25 formal_deliverable_eligible = false
  ↓
G39 多个 writer_* check = false
  ↓
G40 多个 check = false
  ↓
G41 / G42 rejected
```

例如同一条 Claim Root 可以在 G40 上表现为：

- `writer_human_review_clear = false`
- `writer_claim_audits_passed = false`
- `writer_section_status_deliverable = false`
- `writer_formal_deliverable_eligible = false`
- `section_status_valid = false`

这不是 5 个 Root，而是同一个 Root 被放大了 5 次。

因此：

> **不能通过统计最终 false 数量判断 Gate 数量和问题数量。必须向上追到最早失败事实。**

---

# 七、截至 2026-08-11 已经确认的最早错误 Root

当前已经能够确认：并不是所有 34 条都是正确失败，其中包含至少 3 类明确错误 Root，以及 1 类高度可疑的系统性语义 Contract 问题。

---

## 7.1 Root A：G13 Structural / Heading 正则误判

典型文本：

```text
（一）实施准备阶段。
（二）建设部署阶段。
（三）迁移上线阶段。
（四）试运行与优化阶段。
（一）验收依据。
（二）验收内容。
（三）验收组织。
（四）文档交付。
```

这些文本本质是结构标题，不是 substantive claim。

但当前 G13：

1. 先去掉句尾 `。！？!?；;`；
2. `_STRUCTURAL_LABEL_PATTERN` 没有覆盖这种“编号 + 标题 + 无冒号”形式；
3. `_TITLE_SUFFIX_PATTERN` 也没有覆盖“阶段”等后缀；
4. 最终被 `len(normalized) >= 8 and contains Chinese` 的兜底规则判为 substantive；
5. 进入 G14；
6. G14 把它合理分类成 `template_structure`；
7. G15 因 `template_structure` 不在 allowed_claim_types 中而报错。

因此这类失败的最早错误点是：

> **G13 Structural Detection False Positive。**

不是 G14，不是 G15，更不是 G40。

当前最新 Run 中，这一类至少直接贡献约 8/34 violations。

这里暴露出的核心问题是：

> **正则已经开始承担超出其能力范围的自然语言语义判断。**

---

## 7.2 Root B：Deferred / Unknown / Not-Committed 被误判为 Current Project Fact

典型文本：

```text
由于项目具体现状、用户规模、资源需求量及投资金额等基础数据尚未明确，
相关建设规模与配置需在后续调研中结合实际情况进行测算和确认（待补充）。
```

```text
本项目计算资源建设涉及的具体设备数量、品牌型号、配置参数、投资金额及实施工期等信息，
需结合项目现状调研和需求分析后由项目方确认，本方案暂不设定具体数值。
```

```text
本项目当前存储容量、性能指标、设备品牌型号、投资金额及实施工期等信息待补充，
需结合项目调研和详细设计阶段进一步确认。
```

这些句子的真实语义是：

- UNKNOWN；
- DEFERRED；
- NOT COMMITTED；
- NEED CONFIRMATION。

但 G14 当前会把它们分类成：

`current_project_fact`

随后 G15 认为 Writer 生成了 forbidden current-project fact。

这里的本质问题不是简单“DeepSeek 分类错”，而是当前 Claim Ontology / Prompt 对承诺状态表达不足：

- 没有显式区分 confirmed / proposed / deferred / unknown；
- `current_project_fact` 定义对“本项目 + 将/拟/当前”表达过于敏感；
- Prompt 没有清楚定义：
  - “待补充”不是已确定事实；
  - “尚未明确”不是已确定事实；
  - “后续确认”不是已确定事实；
  - “暂不设定”不是当前配置结论。

因此这一类属于：

> **Claim Semantic Boundary / Commitment-State Root。**

当前至少有 3 条非常明确的 false positive，另有部分 Proposed 语句需要继续区分。

安全边界必须保留：

```text
本项目部署100台服务器。
```

仍必须识别为 current-project quantity / fact，不能因为修 Deferred 而放松真实当前项目事实。

---

## 7.3 Root C：Document Profile → Evidence Capability 过粗 / 不一致

当前 Catalog 中存在明显情况：

某些文档的真实内容同时具有：

- policy requirement；
- template structure；
- technical/design guidance；
- general method。

但 Document Profile 只暴露非常窄的 `allowed_claim_types`。

典型例子：

`2-陕西省省级政务信息化项目初步设计和投资概算编制指南（建设类）（试行）.doc`

Profile：

```text
document_type = policy_standard
evidence_role = policy_standard
allowed_claim_types = [policy_requirement]
```

但真实检索内容包含：

- 总体架构章节结构；
- 编制要求；
- 图表结构要求；
- 技术方案设计要求；
- 资源测算要求。

Writer 根据这些材料写：

```text
总体架构图应标明各层级之间的关联关系……
```

G14 分类为 `template_structure` 是合理的，但 G06 Contract 只允许 `policy_requirement`，于是 G15 报 `claim_type_not_allowed`。

这里真正错误更早：

> **Document Profile / Evidence Capability 没有完整表达材料能够支撑什么类型的 Claim。**

类似情况还包括：

`陕投集团智算中心建设项目可行性研究报告模板-v1.doc`

Catalog 自己已经识别：

- filename 有 template marker；
- reason code 包含 feasibility_template_structure；
- source_document_form = feasibility_template。

但最终仍被放进 historical_case，Capability 只给：

- general_method；
- typical_architecture；
- reference_case。

没有 `template_structure`。

这说明：

> **当前 Evidence Role / Document Type 粗粒度地绑定 Capability，会丢失同一文档内部真实存在的多种语义能力。**

注意：这不意味着要全局给 historical_case 放开 technical_specification。

必须基于真实 badcase 按最小边界修复，尤其要继续禁止历史方案支撑：

- current_project_fact；
- current_project_configuration；
- current_project_quantity；
- current_project_cost；
- current_project_schedule。

---

## 7.4 Root D（系统性高风险）：Writer 与 Claim Auditor 的语义 Contract 不完全共享

当前写前 Gate 会把类似下面的 Contract 给 Writer：

```text
allowed_claim_types:
- general_method
- policy_requirement
- reference_case
- typical_architecture

forbidden_claim_types:
- current_project_fact
- current_project_configuration
- current_project_quantity
- current_project_cost
- current_project_schedule
```

但 Writer Prompt 并没有完整共享 G14 所使用的 ClaimType 语义定义。

因此 Writer 可能认为：

```text
资源池应支持主流虚拟化技术，具备统一调度能力。
```

属于 general_method；

而 G14 按自己的定义把它分类为：

`technical_specification`

G15 再判：

`claim_type_not_allowed`

形成：

```text
G06：只允许 general_method
Writer：我认为这句属于 general_method，所以写了
G14：我认为它是 technical_specification
G15：越权
```

这种问题不是简单的 Writer “不服从 Gate”，而是：

> **Writer 与 Auditor 对 ClaimType 的语义边界并没有使用完全相同的 Contract。**

因此当前大量 `technical_specification not_allowed` 不能直接解释为 Gate 太严，也不能直接全量扩大 Capability。

先要判断：

1. Evidence 本来是否有能力支持 technical specification；
2. Writer 是否应该被允许写这种句子；
3. ClaimType 本身是否定义过细；
4. Writer 和 Auditor 是否需要共享同一个语义定义。

---

# 八、哪些失败是正确的，绝对不能为了 8/8 通过而修掉

最新 Run 中也存在明显正确拦截。

例如总体架构章节生成：

```text
用户层：覆盖本项目建设所服务的各类用户群体……
业务应用层：涵盖已建系统的接入整合……
基础设施层：依托政务云平台……利用已建或在建机房环境……
```

而 ProjectInput 明确缺失：

- 项目现状；
- 用户规模；
- 计算资源数量；
- 存储容量；
- 网络带宽；
- 品牌型号；
- 投资金额；
- 实施工期。

这种情况下 Writer 直接把“当前项目有哪些用户、应用、机房、网络”写成既定事实，G14 判成 `current_project_fact` / `current_project_configuration`，G15 拦截是正确的。

因此 Gate 修复必须坚持一条硬边界：

> **目标是消灭 false positive，不是放开 current_project_*。**

以下边界必须长期严格：

- `current_project_fact`
- `current_project_configuration`
- `current_project_cost`
- `current_project_quantity`
- `current_project_schedule`

不得用历史项目材料直接支撑当前项目既定事实。

---

# 九、为什么最终不能继续靠堆正则修 Gate

随着 Root 追踪深入，已经明显看到：

很多所谓“Gate 太严格”的真正源头是：

```text
上游文本/语义分类错误
  ↓
错误 ClaimType / Evidence Capability
  ↓
G15 正确执行了错误 Contract
  ↓
Section FAIL
  ↓
G39 / G40 / G41 放大
```

特别是 G13 暴露出：

- 正则很适合做确定性格式识别；
- 但当它开始决定“这句话是不是 substantive claim”时，就已经进入自然语言语义边界；
- 越继续补 regex，越容易形成脆弱规则系统；
- 每补一个 badcase，可能产生新的 negative case。

因此审计最终形成的方向是：

> **语义判断交给模型，确定性校验交给代码。**

未来 Gate V2 应逐渐收敛成：

```text
Deterministic Pre-Check
  ↓
Semantic Evaluator
  ↓
Structured Judgment
  ↓
Deterministic Policy Engine
  ↓
ALLOW / QUALIFIED / REVIEW / BLOCK
```

Semantic Evaluator 可以判断：

- heading / claim / placeholder / structural；
- claim type；
- commitment state；
- evidence support；
- current vs historical；
- supported / unsupported / ambiguous。

代码继续负责：

- identity；
- hash；
- citation marker；
- binding；
- schema；
- deterministic policy；
- final artifact integrity。

模型负责“理解”，代码负责“执行政策”。

---

# 十、关于 CRAG 思路与 Gate 的最终理解

当前 Gate 的很多设计思想实际上与 CRAG 类似：

- 先判断 evidence 是否可靠；
- 再根据判断决定是否继续、修正、降级或补检索；
- 不应该仅靠固定 lexical rule 推断语义质量。

因此长期方向不应是继续扩大 regex policy，而应该逐渐转成 Model-based Evaluator。

但这不意味着删除所有正则。

应保留两类不同能力：

## 10.1 Deterministic Guard

适合程序完成：

- Hash；
- ID；
- Citation；
- Binding；
- Schema；
- Artifact 完整性；
- 极高确定性的结构格式；
- 数值 / marker occurrence 等机械事实。

## 10.2 Semantic Evaluator

适合模型完成：

- 是标题还是正文；
- 是建议还是承诺；
- 是待确认还是已确认；
- general method vs technical specification；
- historical reference vs current project fact；
- Evidence 是否真正支持 Claim。

未来如果需要降低成本，可以：

```text
DeepSeek Teacher / Oracle
  ↓
沉淀 Gate Trace
  ↓
蒸馏专用 1.5B / 3B Semantic Evaluator
```

但模型尺寸不是当前 Gate Audit 必须立即解决的问题；架构应先通过统一接口固定，模型后续可替换。

---

# 十一、为什么现在不应该继续把 Gate 修到“完美”才推进项目

到当前节点，审计已经解决了最重要的问题：

- Gate 在哪里；
- 每一类 Gate 做什么；
- 哪些是真业务 Gate；
- 哪些是 Derived；
- 哪些是 Aggregated；
- Failure 如何传播；
- G40/G41 为什么会表现出巨大杀伤力；
- 当前最早错误 Root 在哪里；
- 当前哪些失败是合理安全拦截；
- 后续应该从哪个方向演进。

继续要求：

> “Gate 必须全部修好、8/8 全 PASS、G40/G41 全 PASS，才能继续 Multi-Agent”

会导致项目再次被 Gate 单模块卡死。

而当前项目真正还需要继续推进：

- Multi-Agent；
- Memory；
- Supervisor 协同；
- Agent 工具调用；
- 最终 Artifact 链路；
- Evaluation Foundation；
- 后训练 Trace 数据。

因此用户提出：

> **Gate 先调到最松，让真实系统一路生成到底。最终结果即使是错的也可以，只要 Gate 仍然知道哪里错、错误仍然记录下来。先拿到 Functional Closure，再逐步收紧 Gate。**

这是当前阶段的正式推进决策。

---

# 十二、用户最终方案：全局 Gate Shadow Enforcement 总控

最终方案不是：

- 删除 Gate；
- 把所有 `passed=false` 改成 true；
- 注释掉 G15/G40/G41；
- 一个一个手工把 40 多个 Gate 放松。

而是引入一个 **全局 Enforcement Mode**。

建议概念：

```text
gate_enforcement_mode = strict | shadow
```

也可以使用其他名字，但语义必须保持一致。

---

## 12.1 Shadow Mode 的核心定义

一句话：

> **审计真执行，处罚不执行。**

或者更准确地说：

> **Gate Decision 真实，Enforcement 暂时降级。**

例如：

```text
G15.passed = false
G15.violations = [...]
G15.human_review_required = true
```

这些必须原样保留。

不能做：

```text
if shadow:
    gate.passed = true
```

因为这样会污染 Trace，失去后续修 Gate 的真实依据。

正确行为是：

```text
gate.passed = false
enforcement_action = CONTINUE
```

---

# 十三、Shadow Mode 下哪些东西继续真实执行

Shadow Mode 不是跳过 Audit。

整条 Gate 链仍真实执行：

```text
G01
G04
G05
G06
G10
G11
Writer
G13
G14
G15
G19
G20
G21
G23
G24
G25
G34
G35
G36
G37
G38
G39
G40
G41
G42
...
```

每一个 Gate 仍然：

- 计算真实结果；
- 保留真实 `passed`；
- 保留真实 violation；
- 保留真实 reason codes；
- 保留 human review 状态；
- 保留 Section status；
- 保留 G40/G41 failures；
- 写入 Trace / Artifact metadata；
- 允许后续统计 badcase。

唯一变化：

> **业务质量失败不再自动停止主流程，也不再阻止最终 Artifact 产生。**

最终即使：

```text
G40 = FAIL
G41 = FAIL
```

仍然应该能够得到：

```text
final_artifact = generated
business_gate_status = failed
release_status = shadow_only / non_production
```

具体字段名称实施时按现有 Schema 最小改动确定，不在本文预先扩 Schema。

---

# 十四、Shadow Mode 与 Strict Mode 的行为差异

## Strict Mode

用于未来正式收口：

```text
Business Gate FAIL
  ↓
BLOCK / REJECT
  ↓
不进入正式交付
```

## Shadow Mode

用于当前开发阶段：

```text
Business Gate FAIL
  ↓
记录真实失败
  ↓
Trace / Warning / Need Review
  ↓
CONTINUE
  ↓
生成最终 Artifact
```

因此 Shadow Mode 的目标不是提升 Gate Pass Rate。

它的目标是：

> **在 Gate 仍然完整观察系统的前提下，先打通端到端 Functional Closure。**

---

# 十五、Shadow Mode 不能放过什么

Shadow 只针对 **Business Quality Enforcement**。

不能把所有错误都忽略。

## 15.1 可以 Shadow 的业务失败

例如：

- Evidence 不足；
- Claim Audit fail；
- `claim_type_not_allowed`；
- `forbidden_claim_type_generated`；
- Section quality 不达标；
- human review required；
- Formal Delivery Eligibility false；
- G40/G41 因业务质量失败而 reject。

这些可以：

```text
FAIL → RECORD → CONTINUE
```

## 15.2 仍然必须 Hard Stop 的技术失败

例如：

- Python Exception；
- Pydantic / Schema 构造失败；
- State 丢失；
- LangGraph 运行状态损坏；
- 节点无法返回；
- Artifact 根本没有生成；
- 必要对象 ID / binding 无法建立导致后续逻辑无法执行；
- 数据结构不完整导致程序本身无法继续；
- Model Call 技术性彻底失败且没有 fallback。

这些不是“质量不好”，而是“程序坏了”。

因此必须保持：

```text
Technical Failure → HARD STOP
Business Quality Failure → SHADOW CONTINUE
```

这条边界是 Shadow 总控设计中最重要的安全边界之一。

---

# 十六、为什么 Shadow 总控比逐门放松更好

不应采用：

```text
G06 改松
G11 改松
G15 改松
G25 改松
G39 改松
G40 改松
G41 改松
```

原因：

1. 会破坏现有 Gate Audit 的可解释性；
2. 以后无法知道具体改了多少业务规则；
3. Strict 恢复困难；
4. Trace 可能失真；
5. Gate 逻辑与“开发期执行策略”混在一起；
6. 容易产生新的 Derived bug。

正确做法是：

```text
Gate Decision Layer
  ↓
Enforcement Controller
  ↓
strict / shadow
```

也就是：

- Gate 负责判断；
- 总控负责“判完以后是否阻断”。

这样将来恢复 Strict，只需要切换 Enforcement Mode，不需要重写 40 多个 Gate。

---

# 十七、当前项目推进阶段重新定义

在最终方案下，项目不再要求“Gate 完美”才能继续。

后续分成三个闭环：

## Phase A：Functional Closure

目标：

> Query 能真实进入系统，经过 RAG、Writer、Section、Assembly，最终一定拿到完整 Artifact。

此阶段：

- Gate 使用 Shadow；
- Business Failure 继续记录但不阻断；
- Technical Failure 仍 Hard Stop。

完成条件：

- 真实输入；
- 真实检索；
- 真实模型生成；
- 8 个章节真实执行；
- 最终文档真实组装；
- Final Artifact 能落地；
- 全 Gate Trace 仍存在。

## Phase B：Governance Closure

目标：

> 基于完整 E2E 的真实 badcase，逐步修复 Gate Root。

修复顺序遵守：

```text
一个 Root
→ 一个最小修改
→ Positive Test
→ Negative Control
→ 确认没有削弱 current_project_* 安全边界
→ 再进入下一个 Root
```

不再要求一次把所有 Gate 修完。

## Phase C：Quality Closure

目标：

- Evaluation 接入；
- Gate + Eval + Badcase 共同收敛；
- Shadow 逐步切回 Strict；
- 最终 Strict 下稳定可交付。

---

# 十八、Gate 后续优先修复顺序

当主链路打通以后，再回来慢慢修 Gate。

当前 Root 优先级建议保持：

## P0-1：Structural / Heading Detection

原因：

- Root 已完全证实；
- 约 8/34；
- 风险最低；
- 不涉及业务事实安全边界。

## P0-2：Deferred / Unknown / Proposed Commitment State

修复必须同时验证：

```text
具体数量待确认
→ 不应当作为 confirmed current-project fact
```

以及 negative control：

```text
本项目部署100台服务器
→ 必须仍然识别为 current_project_quantity / fact
```

## P0-3：Document Profile / Evidence Capability

按真实内容 / Evidence Item Capability 修，不允许简单全局放开 historical_case。

## P0-4：Writer / Auditor ClaimType Contract 对齐

判断是否需要共享 ClaimType definition、commitment semantics、evidence semantics。

---

# 十九、当前明确不作为 P0 的事项

为了防止审计重新膨胀，以下内容当前不作为 Gate P0：

- 为所有 Gate 重新设计 Schema；
- G28 细节重构；
- G35 Repair Lifecycle 全量重做；
- FRAMEWORK_ONLY 是否立刻正式启用；
- 全面重写 G40/G41；
- 把 Human Review 系统整体重构；
- 立即把全部 Semantic Gate 蒸馏成 1.5B；
- 为了面试再增加新的 Gate；
- 为每个 badcase 新增一条 regex；
- 把 historical_case 全局扩权。

这些只有当真实 E2E / Multi-Agent / Evaluation 后续证明存在必要性时再进入 Gap。

---

# 二十、Human Review 的当前审计结论

当前代码中存在两个不同概念：

## 20.1 Risk / Review State

来自：

- Evidence Gate；
- Claim Audit；
- Section quality；
- 其他业务风险。

它可能导致：

`human_review_required = true`

并继续影响 G25/G39/G40。

## 20.2 Real Human Approval Workflow

当前真正会触发 LangGraph `interrupt()` 的是 G43，并且需要显式：

`generation_requirements.extra.require_human_approval`

G43 不会自动因为 Claim Audit / Section need_review 而 pause。

因此当前存在语义不一致：

> “需要人工复核”可能被当成“不能正式交付”，但系统并没有真的进入人工审批流程。

用户此前明确倾向：

> **不能仅仅因为需要 Human Review 就什么都不生成，更合理的是先生成，再提示 / 建议 / 复核。**

Shadow Mode 会暂时解决“Review 状态阻断整个功能闭环”的问题，但 Human Review 最终语义是否需要调整，留到后续 Governance Closure，不在当前 P0 继续扩展。

---

# 二十一、当前最终架构判断

截至本次审计，最重要的架构判断已经收敛：

## 21.1 当前问题并不是单纯“门禁太严格”

真实情况是：

```text
Data / Document Profile
        ↓
Evidence Capability
        ↓
Semantic Classification
        ↓
Claim Policy
        ↓
Derived Status
        ↓
Document Aggregation
```

任何上游语义错误都会被下游放大。

所以 G40/G41 表面上最“狠”，但通常不是最早 Root。

## 21.2 Gate 的长期演进方向

```text
Deterministic Guard
        ↓
Semantic Evaluator
        ↓
Deterministic Policy Engine
        ↓
Section Governance
        ↓
Document Aggregation / Closure
```

而不是不断扩大 regex。

## 21.3 当前开发阶段的执行策略

```text
Gate 全执行
Gate 真实判定
Trace 全保留
Business FAIL 不阻断
Technical FAIL 仍阻断
最终 Artifact 必须产出
```

也就是：

> **Shadow Enforcement。**

---

# 二十二、当前阶段正式决策

从本文件落盘开始，Gate Audit 阶段应视为已经达到“足以继续项目”的程度。

下一阶段不再以：

> “把全部 Gate 修到 Strict 8/8”

作为继续 Multi-Agent 的前置条件。

正式推进决策为：

1. 保留 01/02/03 Gate Audit 作为现状事实基线；
2. 新增全局 Shadow Enforcement 总控；
3. 不篡改 Gate 原始判定；
4. Business Gate Failure 记录但继续；
5. Technical Failure 继续 Hard Stop；
6. 让真实主链路生成完整 Final Artifact；
7. Functional Closure 后继续 Multi-Agent / Memory / Evaluation；
8. Gate 降为渐进治理任务，根据真实 E2E badcase 慢慢修；
9. 每次 Gate 修复必须以 Earliest Root 为单位，不允许通过修改 Derived / Aggregated Gate 掩盖上游问题；
10. 最终再从 Shadow 逐步切回 Strict。

---

# 二十三、后续实施时必须遵守的禁止项

实施 Shadow Mode 时禁止：

- 直接删除 G15 / G40 / G41；
- 把所有 `passed=false` 强制改成 true；
- 修改 Trace 让系统看起来“通过”；
- 通过放开 current_project_* 来追求 8/8；
- 一次性修改所有 Gate policy；
- 为每个 badcase新增 regex；
- 把 Technical Failure 也 shadow；
- 因为 Shadow Mode 再新增复杂发布状态机；
- 同时开始大规模重构 Gate V2；
- 在没有运行证据时继续扩 Gate Architecture Audit。

---

# 二十四、后续验证建议

Shadow 总控实施后，第一次 E2E 的目标不是质量，而是确认 Functional Closure：

应至少验证：

1. Query 真输入；
2. RAG 真检索；
3. Writer 真生成；
4. 8 Section 全部执行到结束；
5. G13/G14/G15 仍然真实产生 violation；
6. G25/G39/G40/G41 仍能真实计算；
7. 即使 G40/G41 false，最终 Artifact 仍然存在；
8. Trace 中能够明确看到：
   - business_gate_passed = false（或等价现有字段）；
   - enforcement_action = continue（具体字段实施时最小设计）；
9. 技术异常仍然能够阻断流程；
10. Final Artifact 标记为开发 / Shadow 结果，不能误当正式发布结果。

第一次成功的定义是：

> **系统从真实 Query 一路走到 Final Artifact，Gate 错误全部可见，但不再因为业务 Gate 提前死亡。**

不是：

> “Gate 全通过”。

---

# 二十五、最终一句话总结

本轮 Gate Audit 的最终结论不是“Gate 太严格，所以删 Gate”，而是：

> **当前系统的很多失败来自更早的文本语义识别、Claim commitment、Document Capability 和 Writer/Auditor Contract 错位；G39/G40/G41 大多是在放大和汇总这些上游失败。Gate 的长期方向应是“模型做语义判断、代码做确定性政策”，但当前项目不应继续被 Gate 完美化阻塞。先通过全局 Shadow Enforcement 让所有 Gate 真实审计、真实失败、真实落 Trace，但暂时取消业务失败的阻断权，先拿到完整真实 Final Artifact，继续推进 Multi-Agent 和 Evaluation，再以 Earliest Root 为单位逐步修 Gate，最终恢复 Strict。**
