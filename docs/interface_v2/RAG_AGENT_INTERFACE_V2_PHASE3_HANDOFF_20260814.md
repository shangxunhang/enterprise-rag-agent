# RAG–Agent Interface V2 Phase 3 完成后交接文档

日期：2026-08-14  
项目：`D:\MyCode\rag-agent`  
当前基线 HEAD：`94b2a5b`  
当前分支：`checkpoint/controlled-architecture-20260728`  
交接节点：**Phase 1–3C 已完成并通过审计；下一步 Phase 4 — Canonical Evidence State Migration**

---

# 一、当前总状态

本轮 RAG–Agent Interface V2 的核心目标，是把 RAG 与 Application/Agent 的职责、Public Contract、Gate ownership 和依赖方向收口，避免出现：

```text
RAG 内嵌 Application 业务决策
Application 直接读取 RAG internal state
extra/details 成为隐藏 authority
同一 Evidence truth 在多处重复定义
```

当前已完成：

```text
Phase 1 — Shared/Public Contract Convergence
✅ ACCEPTED

Phase 2 — RAG Request extra Closure
✅ ACCEPTED

Phase 3A — Public Evidence Assessment Facts Closure
✅ ACCEPTED

Phase 3B — Application Evidence Gate Policy + Old/New Parity
✅ ACCEPTED

Phase 3C — Remove RAG Gate Responsibility / Reverse Dependency
✅ ACCEPTED

----------------------------------------

Phase 3 COMPLETE

Phase 4 — Canonical Evidence State Migration
➡️ NEXT
```

Phase 3 完成后的最终依赖方向已经变成：

```text
Application / Agent
        ↓
Public Contract
        ↓
RAG
```

已经禁止：

```text
RAG
 ↓
Application Gate / Application Schema
```

当前关键结论：

```text
APPLICATION GATE IS PRODUCTION AUTHORITY
RAG PRODUCES EVIDENCE FACTS ONLY
RAG -> APPLICATION IMPORTS = 0
```

---

# 二、必须继续遵守的 Frozen Architecture

后续任何实现都必须继续遵守以下边界。

## 2.1 RAG 单一职责

RAG 负责：

- retrieval；
- candidate eligibility；
- semantic relevance；
- coverage；
- missing requirements；
- conflicts；
- evidence quality / assessment；
- Evidence capability；
- lineage / trace；
- corrective retrieval；
- context packing。

RAG 可以描述：

> 证据是什么、能支持什么、缺什么、有什么冲突、可信度和使用限制是什么。

RAG 不得决定：

- `DeliveryMode`；
- `human_review_required`；
- `generation_allowed`；
- `publish_allowed`；
- `GateDecisionSchema`；
- 任何等价的业务动作。

核心原则：

```text
Evidence Fact != Business Action
```

## 2.2 Application Gate 职责

Application 独占：

- Claim risk；
- DeliveryMode；
- human review；
- generation allowed；
- publish allowed；
- Gate policy identity；
- `GateDecisionSchema`。

当前 Application canonical Gate policy id：

```text
candidate_capability_union_v1
```

## 2.3 Public Contract

Public Contract 是跨 RAG / Application 边界的唯一稳定 authority。

禁止：

```text
Application -> rag.internal schema
RAG -> Application schema
extra/details -> reconstruct canonical semantics
```

允许：

```text
Canonical Public Contract
        ↓
deterministic projection
```

禁止：

```text
projection / extra / details
        ↓
reconstruct canonical truth
```

---

# 三、Phase 1 最终状态 — Public Contract Convergence

## 3.1 ClaimType

唯一 canonical 定义：

```text
backend/schemas/claim.py
```

当前 13 个 ClaimType：

```text
GENERAL_METHOD
TYPICAL_ARCHITECTURE
REFERENCE_CASE
CURRENT_PROJECT_FACT
CURRENT_PROJECT_COST
CURRENT_PROJECT_QUANTITY
CURRENT_PROJECT_SCHEDULE
CURRENT_PROJECT_CONFIGURATION
POLICY_REQUIREMENT
TECHNICAL_SPECIFICATION
PROCUREMENT_REQUIREMENT
TEMPLATE_STRUCTURE
STYLE_GUIDANCE
```

`backend/rag/catalog/document_profile.py` 只 import / re-export，不再定义第二份 enum。

## 3.2 EvidenceStatus

唯一 canonical EvidenceStatus：

```python
class EvidenceStatus(str, Enum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICTED = "conflicted"
```

旧：

```text
PARTIAL
ABSENT
NOT_ASSESSED
```

已退出 canonical status。

表达方式：

```text
PARTIAL-like
→ status = INSUFFICIENT
→ coverage_ratio > 0
→ missing_topics 非空

ABSENT-like
→ status = INSUFFICIENT
→ coverage_ratio = 0

NOT_ASSESSED
→ assessment_state = pending
→ status = None
```

## 3.3 EvidenceRequirement

Public authority：

```text
EvidenceRequirementSchema
EvidenceRequirementsSchema
```

canonical authority：

```text
requirements[]
```

`required_topics` 只允许作为 read-only compatibility projection，不得重新成为 canonical input authority。

---

# 四、Phase 2 最终状态 — RAG Request extra Closure

Public Request：

```text
RAGToolInputSchema
```

已经正式承载：

```text
retrieval_trace_id
retrieval_scope
task_type
evidence_requirements
document_context
max_context_tokens
max_context_chars
max_context_items
```

`SchemeEvidenceService` 已停止通过 `extra/extra_metadata` 传递 canonical request semantics。

当前允许留在 `extra` 的主要内容是 metadata / diagnostics，例如：

```text
project_type
target_documents
target_templates
```

RAG internal safety controls：

```text
model_context_window
prompt_reserved_tokens
```

由 RAG 自己拥有。

caller 的：

```text
max_context_tokens
max_context_chars
max_context_items
```

只能做 narrowing，不能突破 RAG internal safe limit。

## 4.1 FIX-P2-01

`SchemeEvidenceService` 已彻底删除死的 `model_gateway` dependency。

真实 ModelGateway owner 仍是：

```text
SectionModelService
SemanticClaimClassifier
SemanticSectionJudge
```

EvidenceService 不再拥有 ModelGateway。

---

# 五、Phase 3A 最终状态 — Public Evidence Facts

Public Evidence Assessment：

```text
RAGEvidenceAssessmentSchema
```

当前稳定 Evidence facts 包括：

```text
assessment_state
status
evidence_available
selected_evidence_count
dropped_evidence_count
citation_count
judge_name
judge_version
score
coverage_ratio
required_topics
covered_topics
missing_topics
conflicts
supported_claim_types
forbidden_claim_types
authoritative_evidence_present
historical_or_reference_evidence_present
required_qualifiers
prompt_evidence_source
prompt_evidence_contract_missing
reason_codes
details
```

以下业务动作明确不属于 Public Assessment：

```text
human_review_required
claim_risk_level
delivery_mode
generation_allowed
gate_decision
publish_allowed
```

## 5.1 FactClaimPolicyResult 最终边界

当前 RAG internal：

```text
FactClaimPolicyResult
├── eligible
├── certainty_level
├── required_qualifiers
└── reason_codes
```

已经删除：

```text
human_review_required
```

也就是说，RAG 只描述 Evidence 使用约束，不再决定人工审核动作。

## 5.2 Set-level Evidence Facts

当前 `_SetEvidenceFacts` 只承载：

```text
supported_claim_types
forbidden_claim_types
authoritative_evidence_present
historical_or_reference_evidence_present
required_qualifiers
prompt_evidence_source
prompt_evidence_contract_missing
reason_codes
```

没有：

```text
fact_status_human_review_required
needs_review
delivery_hint
decision_hint
```

---

# 六、Phase 3B 最终状态 — Application Gate Policy

Application Gate policy：

```text
backend/apps/enterprise_document/services/scheme_writer/evidence_gate_policy.py
```

核心接口：

```text
RAGEvidenceAssessmentSchema
        ↓
EvidenceGatePolicy.evaluate(...)
        ↓
GateDecisionSchema
```

Application 自己计算：

- ClaimRiskLevel；
- DeliveryMode；
- human_review_required；
- allowed / forbidden claim actions；
- Gate reason codes；
- Gate policy id。

Application canonical policy id：

```text
APPLICATION_EVIDENCE_GATE_POLICY_ID = "candidate_capability_union_v1"
```

## 6.1 Gate 行为已冻结

以下行为在 Phase 3B old/new parity 中已经锁定：

```text
sufficient authoritative
→ GROUNDED

sufficient template-only
→ GROUNDED

sufficient historical/reference
→ QUALIFIED

sufficient current-project non-authoritative
→ QUALIFIED (+ review for high risk)

estimated/proposed/planned
→ QUALIFIED + review

partial-like high-risk
→ HUMAN_REVIEW

partial-like low/medium-risk
→ QUALIFIED + review

absent-like
→ BLOCKED

conflicted
→ HUMAN_REVIEW

prompt contract missing
→ BLOCKED

legacy prompt fallback
→ QUALIFIED + review
```

后续 Interface Phase 不允许重新设计这些 Gate rules。

---

# 七、Phase 3C 最终状态 — Production Gate Authority Migration

这是当前最新完成节点。

## 7.1 修改前

```text
RAG governance
    ↓
RAG _gate()
    ↓
GateDecision
    ↓
ResultMapper extra/details
    ↓
Application
```

## 7.2 修改后

真实 production path 已变成：

```text
RAG public_assessment
        ↓
EvidenceBundle.assessment
        ↓
SectionExecutionCoordinator
        ↓
extract_writer_gate_binding()
        ↓
EvidenceGatePolicy.evaluate(evidence_bundle.assessment)
        ↓
WriterGateBindingSchema
        ↓
existing Writer / Document consumers
```

Application Gate production producer 位于：

```text
backend/apps/enterprise_document/services/scheme_writer/writer_governance.py
```

`extract_writer_gate_binding()` 当前：

```text
EvidenceBundleSchema.model_validate(...)
        ↓
EvidenceGatePolicy().evaluate(evidence_bundle.assessment)
        ↓
GateDecisionSchema
        ↓
generation_allowed derived in Application
```

## 7.3 RAG 已删除的业务责任

`backend/rag/evidence/governance_v2.py` 已删除：

```text
_gate()
_claim_risk_level()
GateDecisionSchema import
DeliveryMode import
EvidenceGovernanceV2Result.gate_decision
production generation_allowed
human_review_required
fact_status_human_review_required
```

`EvidenceGovernanceV2Result.to_dict()` 不再输出：

```text
gate_decision
generation_allowed
human_review_required
```

## 7.4 ResultMapper 已清理

RAG ResultMapper 不再写入：

```text
assessment.details["gate_decision"]
EvidenceBundle.extra["gate_decision"]
EvidenceBundle.extra["generation_allowed"]
```

当前 RAG extra 仍可保留纯 RAG diagnostics：

```text
rag_service
retrieval_plan
evidence_quality
evidence_governance_v2
prompt_evidence_contract
prompt_evidence_source
packing diagnostics
```

## 7.5 Reverse Dependency 已归零

最终扫描：

```text
backend/rag/**/*.py

apps.enterprise_document           0
GateDecisionSchema                 0
DeliveryMode                       0
generation_allowed                 0
gate_decision                      0
human_review_required              0
fact_status_human_review_required  0
```

这条是 Phase 3 最重要的 architecture acceptance evidence。

---

# 八、Phase 3C 最终测试证据

独立审计复跑：

## 8.1 Phase 1–3C + Stage D/E + corrective/config-driven RAG

```text
139 passed
0 failed
```

## 8.2 Phase 3C production wiring

```text
27 passed
0 failed
```

## 8.3 Phase 3C negative architecture / corrective retrieval

```text
29 passed
0 failed
```

## 8.4 py_compile

```text
PASS
```

## 8.5 git diff --check

```text
PASS
```

只有既有 CRLF → LF 提示，无 whitespace error。

---

# 九、当前已知非阻塞问题

这些问题不要混进 Interface Phase 4，除非它们真正阻塞 Phase 4。

## 9.1 Strict Document Hard Gate 失败

额外运行 facade/mainline 测试时：

```text
test_phase5_langchain_takeover_and_legacy_retirement
```

出现：

```text
27 passed
1 failed
```

失败发生在：

```text
evaluate_document_gate
mode=strict
```

被现有 Document Hard Gate 阻断。

没有证据表明该失败来自：

- Application Gate wiring；
- RAG Gate 删除；
- reverse dependency；
- Public Assessment 缺字段。

因此：

```text
NON-BLOCKING FOR PHASE 3C
DO NOT MODIFY GATE STRICTNESS IN INTERFACE PHASE 4
```

## 9.2 GroundingRepairService test double

此前 `test_existing_gate_three_bug_regression.py -x` 出现：

```text
9 passed
1 failed
```

失败是测试替身缺少：

```text
model_gateway
```

属于既有 test double / GroundingRepairService 问题，不是 Interface ownership 问题。

不要在 Phase 4 顺手修。

---

# 十、当前 Git / Working Tree 状态

当前 HEAD：

```text
94b2a5b
```

最新观察到累计：

```text
35 tracked files changed
1005 insertions(+)
728 deletions(-)
```

当前工作树是 Phase 1–3C 累计未提交状态。

包含：

- Phase 1 contract changes；
- Phase 2 request closure；
- FIX-P2-01；
- Phase 3A；
- Phase 3B；
- FIX-P3B-01；
- Phase 3C；
- 对应 tests；
- `docs/interface_v2/**` audit docs。

当前无意外 `NUL` 文件；Phase 3C 报告中已清理。

不要因为工作树文件很多就误判为 Phase 4 scope 扩张。

进入 Phase 4 前建议先：

```bash
git status --short
git diff --stat
git diff --name-only
```

确认没有新的意外修改。

---

# 十一、现有审计文档

目录：

```text
docs/interface_v2/
```

重点文档：

```text
RAG_AGENT_INTERFACE_V2_CODEX_IMPLEMENTATION_PLAN_20260813.md
RAG_AGENT_INTERFACE_V2_PHASE1_CODE_AUDIT_20260813.md
RAG_AGENT_INTERFACE_V2_PHASE2_CODE_AUDIT_20260813.md
RAG_AGENT_INTERFACE_V2_PHASE3A_CODE_AUDIT_20260814.md
RAG_AGENT_INTERFACE_V2_PHASE3B_CODE_AUDIT_20260814.md
RAG_AGENT_INTERFACE_V2_PHASE3C_CODE_AUDIT_20260814.md
```

新窗口应优先读取这些文档，不要重新从零设计 Interface V2。

---

# 十二、下一步：Phase 4 — Canonical Evidence State Migration

Phase 4 的目标不是继续改 Gate。

Phase 4 唯一核心目标：

> **让 EvidenceBundle 成为唯一 canonical Evidence truth，禁止从 `RAGContext.extra["evidence_contract"]` 或 Research/Section projection 反向恢复另一份 Evidence truth。**

当前真实残留已经确认存在：

```text
backend/apps/enterprise_document/services/scheme_writer/document_citation_registry.py

(context.extra or {}).get("evidence_contract")
```

这说明 Phase 4 尚未开始，正好是下一阶段入口。

## 12.1 当前重复 truth 形态

当前大致存在：

```text
EvidenceBundle
        ↓
RAGContext.extra["evidence_contract"]
        ↓
downstream restore / rebuild
```

同时 Application/Research/Section 还有若干 compatibility projection：

```text
SectionEvidenceBundleSchema
ResearchEvidencePackageSchema
Citation Registry projection
state projection
```

Phase 4 要做的是 ownership migration，不是暴力删所有 compatibility schema。

## 12.2 Phase 4 Frozen Target

目标：

```text
EvidenceBundle = canonical evidence object
```

允许：

```text
EvidenceBundle
  ↓
RAGContext projection
Section projection
Research projection
Citation projection
```

禁止：

```text
projection / context.extra / state dict
  ↓
rebuild canonical EvidenceBundle truth
```

## 12.3 RAGContext

Phase 4 后：

```text
RAGContext
```

只能是 deterministic context projection。

`extra` 可以保留：

```text
package_hash
package_ref
diagnostic info
```

但不得保留完整 canonical dump 作为恢复 authority。

## 12.4 Section compatibility

`SectionEvidenceBundleSchema` 暂时不要暴力删除。

允许：

```text
canonical EvidenceBundle/ref
+
Context / Chunk / Citation projection
+
Application workflow metadata
```

禁止：

```text
从 Section schema 反向重建 canonical evidence truth
```

`generation_allowed / writer_gate_binding` 是 Application workflow state，不是 Evidence truth。

## 12.5 Research compatibility

`ResearchEvidencePackageSchema` 应降级为 compatibility projection。

Research 可保留：

```text
evidence_package_ref / hash
research_round
research_mode
recommended_action
agent_metadata
```

Evidence 内容只能从 canonical package 投影。

## 12.6 Citation Registry

`DocumentCitationRegistry` 只负责：

```text
Citation remap / binding
```

禁止再通过：

```text
RAGContext.extra["evidence_contract"]
```

重建另一份 Evidence truth。

## 12.7 Phase 4 最终验收核心

至少必须达到：

```text
production code:

(context.extra or {}).get("evidence_contract")
```

作为 canonical recovery path：

```text
0
```

注意：

不是要求整个 repo 字符串 `evidence_contract` 全部归零。

Prompt evidence contract、hash、lineage、字段命名等仍可能合法存在。

要消灭的是：

> **完整 Evidence canonical dump 被塞进 projection/extra 后，又被 downstream 当 truth 恢复。**

---

# 十三、Phase 4 开始前必须先审计什么

不要直接给 Codex 一个“大迁移 EvidenceBundle”的 prompt。

建议 Phase 4 仍按：

```text
4A — Canonical Recovery Path Audit / Freeze
4B — RAGContext Canonical Dump Removal
4C — Section / Research / Citation Projection Migration
4D — Architecture tests / cleanup
```

是否最终就按这四段拆，需要先根据真实 consumer 数量决定，不要机械增加阶段。

第一步必须做全链 audit：

搜索：

```text
evidence_contract
RAGContext.extra
SectionEvidenceBundleSchema
ResearchEvidencePackageSchema
DocumentCitationRegistry
EvidenceBundleSchema.model_validate
model_validate(... evidence_contract ...)
```

然后对每一条读写链分类：

```text
A. Canonical EvidenceBundle producer
B. Projection
C. Hash/ref/lineage
D. Illegal recovery path
E. Application workflow metadata
```

只迁 D 类。

不要因为看到 `evidence_contract` 字符串就全部删除。

---

# 十四、Phase 4 严格禁止扩张

下一窗口必须明确告诉 Codex：

本阶段禁止：

- 不重新设计 EvidenceBundle；
- 不新增 EvidencePackageV2；
- 不新增数据库；
- 不新增 Durable Memory；
- 不做 Research multi-round；
- 不做 ContextCompiler；
- 不重构 Writer；
- 不做 Verifier lifecycle；
- 不修 ToolGateway Evidence/Citation ID；
- 不进入 Phase 5；
- 不修改 Gate policy；
- 不修改 shadow/strict；
- 不修改 G40/G41；
- 不修已知 Document Hard Gate failure；
- 不顺手修 GroundingRepairService test double；
- 不做无关 cleanup。

---

# 十五、后续 Interface V2 计划仍保持不变

Phase 4 后还有：

```text
Phase 5 — ToolGateway / ID Namespace Closure
Phase 6 — Application -> RAG Internal Import Cleanup
Phase 7 — Facade + Architecture Boundary Tests
```

已知 Phase 5 问题：

```text
accepted_evidence_ids
当前某路径错误使用 citation_id
```

目标后续改为：

```text
accepted_evidence_ids
← EvidenceBundle.selected_evidence_ids
```

不要在 Phase 4 提前修。

Phase 7 已知关注：

```text
LangChain facade broad __getattr__ forwarding
```

不要提前处理。

---

# 十六、与 RAG V2 / Multi-Agent V2 的关系

当前 Interface V2 Phase 1–7 是 **P0 解耦与 contract closure**。

它不是完整 RAG V2，也不是完整 Multi-Agent V2。

当前禁止把以下功能提前混入 Interface P0：

```text
Adaptive Router
RetrievalAction
Research bounded loop
Self-RAG V2
Durable Memory
Memory Retrieval
ContextCompiler
Writer paragraph authority restructure
Verifier Issue lifecycle
cross-section Verified Claims
```

正确顺序仍然是：

```text
先完成 Interface P0
        ↓
再实现 RAG V2 / Multi-Agent V2 真实功能 Gap
```

---

# 十七、多个 DevSpace 的并行规则

当前 Interface V2 邻接阶段存在强依赖：

```text
Phase 4 → Phase 5 → Phase 6 → Phase 7
```

不要并行做相邻 phase。

判断能否并行的标准：

> Branch B 的正确实现是否需要知道 Branch A 最后到底怎么改？

如果 YES：串行。

如果 NO：可以独立 worktree + DevSpace 并行。

多个 DevSpace 必须：

```text
一个 worktree
+
一个 workspace
+
一个 branch
```

禁止两个 DevSpace 在同一 checkout 上同时切 branch / 修改。

---

# 十八、新窗口建议第一句话

可以直接把这份文档交给新窗口，并说：

```text
你先读取：

docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE3_HANDOFF_20260814.md

docs/interface_v2/RAG_AGENT_INTERFACE_V2_PHASE3C_CODE_AUDIT_20260814.md

docs/interface_v2/RAG_AGENT_INTERFACE_V2_CODEX_IMPLEMENTATION_PLAN_20260813.md

然后用 DevSpace 审计当前实际代码。

Phase 1–3 已冻结并完成，不重新设计、不回滚。

本轮只准备 Phase 4 — Canonical Evidence State Migration。
先做 recovery path / producer-consumer audit，先把 Phase 4 真实边界和最小拆分方案给我，不要直接改代码，不要进入 Phase 5，不要无限扩张。
```

---

# 十九、最终交接结论

当前阶段已经真正完成：

```text
RAG Request authority closure
Public Evidence contract closure
Application Gate ownership migration
RAG Gate responsibility removal
RAG -> Application reverse dependency removal
```

当前系统核心关系已经从：

```text
RAG
既做 evidence
又做 business gate
```

变成：

```text
RAG
只产 Evidence Facts
        ↓
Public Contract
        ↓
Application
做 Gate / Business Action
```

下一步不要再回头讨论 Gate ownership。

下一阶段只解决：

> **EvidenceBundle 是否真的是唯一 canonical Evidence truth。**

当前 Phase 4 起点已经明确：

```text
DocumentCitationRegistry
仍存在：
(context.extra or {}).get("evidence_contract")
```

从这里开始做 Canonical Evidence State Migration。

---

# 二十、状态标记

```text
PHASE 1: ACCEPTED
PHASE 2: ACCEPTED
PHASE 3A: ACCEPTED
PHASE 3B: ACCEPTED
PHASE 3C: ACCEPTED

PHASE 3: COMPLETE

APPLICATION GATE IS PRODUCTION AUTHORITY
RAG PRODUCES EVIDENCE FACTS ONLY
RAG -> APPLICATION IMPORTS = 0

PHASE 4: READY FOR AUDIT / PLANNING
PHASE 4 IMPLEMENTATION: NOT STARTED
PHASE 5+: NOT STARTED
```
