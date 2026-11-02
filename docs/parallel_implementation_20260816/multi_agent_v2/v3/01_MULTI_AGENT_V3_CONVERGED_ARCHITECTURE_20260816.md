# 企业建设方案 Agent-RAG Multi-Agent V3 收敛架构

日期：2026-08-16  
仓库：`D:\MyCode\rag-agent`  
状态：DRAFT / ARCHITECTURE FREEZE CANDIDATE  
性质：Target Architecture / Design Source of Truth Candidate

---

# 0. V3 的核心定位

V3 不是重新发明一套 Multi-Agent，也不是把项目扩成通用 Agent Platform。

V3 的目标是：

> **保留 V2 已经正确的业务边界与 Agent-RAG 核心原则，把原先散落的 Runtime 能力正式收敛成一套层次清晰、面试可解释、代码可落地、但不过度平台化的 Multi-Agent Target Architecture。**

V3 继续只服务当前真实业务：

```text
根据当前项目事实、客户材料、历史方案结构参考和企业知识库，
生成可审查、可追溯、可恢复的建设方案正文型 Word 文档。
```

V3 的技术深度集中在：

```text
Agent Runtime Foundation
Memory / Context Engineering
RAG-grounded Research
Paragraph-native Generation
Verification / Verified Claim
Recovery / Idempotency
Decision Trace / Evaluation Evidence
```

而不是集中在：

```text
增加更多 Agent
增加更多数据库
增加更多输出类型
增加通用平台能力
增加多租户 / 用户画像 / 插件市场
```

---

# 1. 一眼看懂的 V3 总体架构

## 1.1 四层主架构

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                         Layer 4 — ORCHESTRATION                             │
│                                                                              │
│  MainlineApplicationService                                                 │
│          ↓                                                                   │
│  SupervisorAgent / EnterpriseDocumentWorkflowEngine                         │
│          ↓                                                                   │
│  Section DAG / Routing / Parallel / Retry / Repair / Dependency Unlock      │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         Layer 3 — BUSINESS AGENTS                            │
│                                                                              │
│   PlannerAgent        ResearchAgent        WriterAgent        VerifierAgent  │
│       │                    │                   │                   │          │
│   SectionPlan        EvidencePackage      Paragraph          Verify Result   │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                   Layer 2 — SHARED DOMAIN FOUNDATION                        │
│                                                                              │
│  RunContext / FactSnapshot / SourceInventory / SectionPlan                  │
│  Evidence refs / Paragraph Artifact / Claim / Verified Claim                │
│  VerificationResult / ReviewReport / Domain Events                          │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Layer 1 — AGENT RUNTIME FOUNDATION                       │
│                                                                              │
│  Session / Run Lifecycle        State                                        │
│  Agent Contract / Invocation    Working Memory                               │
│  Durable Memory                 ContextCompiler                              │
│  Artifact                       Checkpoint                                    │
│  Recovery                       Idempotency                                   │
│  Tool Gateway                   Model Gateway                                 │
│  Trace                          Budget                                        │
│                                                                              │
│  Persistence closure: Artifact / Checkpoint Retention + Reference-aware GC   │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          EXTERNAL BOUNDARIES                                 │
│                                                                              │
│       RAG Interface        Gate / Release        Evaluation Foundation       │
│       Input Parsers        Artifact Renderer     Existing Storage            │
└──────────────────────────────────────────────────────────────────────────────┘
```

这张图是 V3 最核心的结构表达。

关键理解：

```text
Memory / Session / Context / Recovery
不是 Planner / Writer 的私有功能，
而是所有 Agent 共享的 Runtime Foundation。

Planner / Research / Writer / Verifier
是建立在 Runtime Foundation 上的四个业务认知角色。

Supervisor / Graph
负责确定性编排，不成为第二套业务事实 authority。
```

---

## 1.2 一次 Agent Invocation 的基础闭环

```text
Session / Run Identity
        ↓
Current State / Working Memory
        ↓
ContextCompiler ← Durable Memory / Fact / Evidence / Artifact refs
        ↓
Typed AgentInvocationRequest
        ↓
Business Agent
        ↓
ModelGateway / ToolGateway
        ↓
Typed AgentInvocationResult
        ↓
Validate / Commit
        ↓
Artifact / Domain Event / State Update
        ↓
Memory Projection
        ↓
Checkpoint
        ↓
Decision Trace
```

发生失败时：

```text
Checkpoint
+ State
+ committed Artifact
+ Durable Run Memory
→ Recovery
```

发生 Retry / Late Result 时：

```text
Run / Attempt / Invocation identity
+ Idempotency
+ Active Attempt validation
→ prevent duplicate / stale commit
```

Run 结束后：

```text
Lifecycle
+ Retention Policy
+ Reference Reachability
→ safe GC / checkpoint pruning / artifact cleanup
```

---

# 2. V3 设计原则

## 2.1 功能面完整，但实现面克制

V3 必须具备完整 Agent Runtime 所需的基础能力，但每项能力只实现建设方案场景真实需要的最小范围。

统一原则：

```text
Capability completeness
!=
Platform generalization
```

例如：

```text
有 Session
≠ 做通用 Session Service

有 Durable Memory
≠ 做跨用户人格记忆平台

有 Tool Gateway
≠ 做插件市场

有 Model Gateway
≠ 做通用模型云平台

有 GC
≠ 做分布式存储生命周期平台
```

## 2.2 Agent 做认知工作，Runtime / Service 做确定性工作

Agent 负责：

```text
理解
规划
检索策略判断
文本生成
语义验证
```

Runtime / Service / Graph 负责：

```text
Identity
State
Session
Context assembly
DAG
Retry
Budget
Checkpoint
Recovery
Idempotency
Artifact persistence
Tool permission
Model lifecycle
Trace
Release
```

## 2.3 Agent 必须跨调用无状态

四个业务 Agent 实例不能把以下内容保存在自身实例 history：

```text
当前 Run 进度
Section State
Research History
Draft History
Verifier Issue
Checkpoint State
用户会话历史
```

每次调用重新由 Runtime 编译当前所需 Context。

## 2.4 Memory 不是 Context

```text
History != Memory != Context
```

Memory 是结构化、可追溯的运行期持久信息。

Context 是某个具体 Agent Invocation 为完成当前任务被允许看到的信息集合。

所有 Memory 进入模型前必须经过 ContextCompiler。

## 2.5 Checkpoint 不是业务内容 Authority

```text
Checkpoint
→ workflow recovery position / working-state snapshot

Artifact
→ committed business content

Memory
→ committed business truth 的查询投影

State
→ 当前 active working state / refs
```

禁止让四者同时各自保存一份完整正文并各自成为事实源。

## 2.6 Paragraph 是生成与恢复原子

```text
Paragraph
= generation atom
= commit atom
= repair atom
= recovery atom
```

Section 是组织与验证边界，不再作为唯一大块生成原子。

## 2.7 Binding 不等于 Verification

```text
Claim Candidate
→ Fact / Evidence Binding
→ Verifier
→ Verified / Rejected
```

绑定成功只能证明“存在候选支持关系”，不能由 Binding Service 自行授予 `Verified` 语义。

## 2.8 只有 Verified Claim 能跨 Section 传播

允许：

```text
Fact
Evidence refs
Verified Claim
Governed projection / summary refs
```

禁止默认传播：

```text
其他 Section raw draft
模型私有 reasoning
全部 Research trace
全部 Tool raw result
```

## 2.9 所有自主循环必须有硬预算

Research、Writer fallback、Repair 都必须有：

```text
round limit
attempt limit
tool-call limit
token / cost budget
stop reason
```

不存在无限 Agent loop。

## 2.10 Hard Gate 仍是最终发布 Authority

Multi-Agent V3 负责生成、验证和提供事实。

最终 release 决策仍属于独立 Gate / deterministic release path。

---

# 3. Layer 1 — Agent Runtime Foundation

这是 V3 最重要的架构层。

V3 必须具备以下 14 项基础能力，但只实现 Minimum Required Scope。

---

## 3.1 Session / Run Lifecycle

### 目标

回答：

> 当前请求属于哪个任务、哪次运行、哪个 Section、哪个 Attempt、哪个 Agent Invocation？

V3 不把 Session 设计成聊天机器人的 `messages[]`。

当前业务的会话语义是长事务执行会话：

```text
Task
  ↓
Run
  ↓
Section
  ↓
Attempt
  ↓
Invocation
```

最小身份：

```text
task_id
run_id
section_id
attempt_id
invocation_id
```

必须明确：

- 完整新输入默认创建新 Run；
- crash / transient failure 使用同 Run resume；
- stale Attempt 不能覆盖新的 active Attempt；
- 不实现复杂跨 Run memory reuse / user profile memory。

### Minimum Required Scope

只支持：

```text
new task
new run
resume run
retry attempt
agent invocation identity
run completion / failure
```

不做：

```text
multi-tenant session service
cross-month conversation history
cross-user memory
complex branching conversation
```

---

## 3.2 State

V3 继续以现有 GraphState / SectionState 为工作流状态基础。

State 只表达当前 active execution truth，例如：

```text
current section
current stage
active attempt
paragraph cursor
research round
current evidence refs
active issue refs
budget state
artifact refs
```

State 不应长期内嵌完整大 Artifact 作为唯一业务事实。

---

## 3.3 Agent Contract / Invocation Protocol

四个 Agent 不通过自由聊天互相协作。

每次调用必须具有 typed invocation envelope，至少能表达：

```text
invocation_id
run_id
section_id
attempt_id
agent_role
input refs / typed payload
context package ref / hash
budget
allowed capabilities
```

输出至少能表达：

```text
invocation_id
status
typed result
reason / action code
artifact refs
usage / cost
error semantics
```

V3 不为了“统一协议”制造几十层通用 RPC Schema；协议只服务当前四 Agent。

---

## 3.4 Working Memory

Working Memory 回答：

> 当前这一步正在做什么？

由：

```text
GraphState
SectionState
LangGraph Checkpoint working snapshot
```

承担。

典型内容：

```text
current section
current attempt
current research round
current paragraph cursor
current evidence package ref
current unresolved issue
current budget
```

Working Memory 不等于 Durable Memory。

---

## 3.5 Durable Run Memory

Durable Memory 回答：

> 在同一个长 Run 中，跨 Agent invocation、跨 Section、跨 crash，需要持续记住什么？

核心主题保持小而明确：

```text
Fact
Evidence
Verified Claim
Research History
Writer Progress
Verifier Issue
Decision
Artifact Reference
```

Memory 不是第二事实源。

所有 Memory 必须能回溯到：

```text
FactSnapshot
EvidenceBundle
Claim
Artifact
Committed Event
```

### Memory 写入原则

LLM 不直接写 Durable Memory。

```text
Business Commit
→ Domain Event
→ MemoryProjection
→ MemoryRecord
```

关键事件示例：

```text
ResearchRoundCommitted
EvidencePackageCommitted
ParagraphCommitted
ClaimVerified
VerifierIssueCreated
VerifierIssueResolved
SectionAccepted
DecisionCommitted
```

### 生命周期

只做当前 Run 真正需要的：

```text
active
resolved
superseded
invalid
```

Verifier Issue 可使用：

```text
active
→ repaired
→ resolved
```

不做跨月 TTL Memory taxonomy 或复杂 global invalidation graph。

---

## 3.6 ContextCompiler

ContextCompiler 是 Agent 读取 Runtime Memory / Domain truth 的统一入口。

```text
AgentInvocationRequest
→ Role Policy
→ Memory / Fact / Evidence Retrieval
→ Authority Filter
→ Lifecycle Filter
→ Relevance Selection
→ Context Compression
→ Token Budget Check
→ AgentContextPackage
→ Agent
```

选择维度：

```text
role
authority
relevance
verification status
lifecycle
recency
token cost
```

### 角色边界

Planner 主要读取：

```text
Template
FactSnapshot
SourceInventory
必要 Plan Decision
```

Research 主要读取：

```text
Evidence Requirements
Fact subset
Query History
Accepted / Rejected Evidence refs
Current Evidence Gap
```

Writer 主要读取：

```text
SectionPlan
Fact subset
EvidencePackage
Current Section committed Paragraph projection
unresolved Issue
Authorized Verified Claim
```

Writer 默认禁止读取：

```text
other section raw draft
all research reasoning
all tool raw output
resolved old issue history
```

Verifier 主要读取：

```text
Current Paragraph / Section
Fact / Evidence Binding
active Claim
unresolved Issue
DraftDiff
necessary Verified Claim
```

### Context Compression

只保留 V3 面试和业务真正需要的层次：

```text
A. Lossless structured fact
B. Structured compression
C. Controlled summary
D. Reference-only
```

关键 Fact / ID 不允许被自然语言摘要丢失。

---

## 3.7 Artifact

Artifact 保存 committed business content 和较大的稳定业务对象。

重点对象：

```text
Paragraph Artifact
Section output refs
Evidence Package refs
Verification / Review artifact
Final Document Artifact
必要 Tool / intermediate committed artifact
```

原则：

```text
Artifact immutable or versioned
active version explicit
superseded version explicit
content hash traceable
```

Artifact 不负责工作流当前位置。

---

## 3.8 Checkpoint

继续复用 LangGraph 官方 Checkpointer；不新增第二套 checkpoint system。

Checkpoint 主要保存：

```text
workflow position
working state
active ids / refs
recovery metadata
```

Checkpoint 不应长期承担重复保存所有 committed large content 的职责。

---

## 3.9 Recovery

V3 重点恢复原子只做两个：

```text
Research Round
Paragraph
```

### Paragraph Recovery

```text
p001 committed
p002 committed
p003 running
→ crash
→ resume p003
```

p001 / p002 不重新生成。

### Research Recovery

```text
round1 committed
round2 committed
round3 running
→ crash
→ resume round3
```

已提交 round 不重新执行。

不要求所有内部函数都具备通用 workflow replay 能力。

---

## 3.10 Idempotency

所有有业务副作用的 commit 必须有稳定逻辑身份，例如：

```text
run_id
section_id
attempt_id
paragraph_id / research_round_id / operation_id
```

同一 operation 重放：

```text
first → commit
second → existing commit / safe no-op
```

stale Attempt：

```text
old attempt result
+ newer active attempt
→ reject from active state
→ trace only
```

---

## 3.11 Tool Gateway

Tool Gateway 是 Agent 调用确定性外部能力的受控入口。

```text
Agent
→ Structured Tool Request
→ Tool Gateway
   ├─ role allowlist
   ├─ schema validation
   ├─ timeout
   ├─ retry
   ├─ budget
   ├─ idempotency
   ├─ trace
   └─ result normalization
→ Actual Service
```

V3 当前主要真实使用场景：

```text
ResearchAgent → RAG Interface
Fact query
Artifact read
必要 query transform / retrieval capability
```

不做插件市场，不开放 DB / Milvus / Checkpoint 给 Agent 自由操作。

---

## 3.12 Model Gateway

继续复用现有 ModelGateway 作为唯一模型执行边界。

负责：

```text
role / model config
provider invocation
timeout
error normalization
usage / token collection
trace
必要模型路由
```

业务 Agent 不直接创建 provider client，也不持有 model lifecycle。

V3 不为面试单独建设复杂模型调度平台。

---

## 3.13 Trace

V3 Trace 是 Decision Trace，不是简单 stdout log。

禁止保存模型私有 chain-of-thought。

保存：

```text
structured input refs
selected action
reason code
result / outcome
cost
latency
selected context refs
artifact refs
```

关键问题必须能回答：

```text
Research 为什么继续 / 停止？
Writer 为什么触发 fallback？
Verifier 为什么要求 repair？
Context 为什么选这条 Memory？
为什么拒绝 stale Attempt？
Recovery 从哪里继续？
```

---

## 3.14 Budget

Budget 是受控 Agent 的基础能力，不只是 token 限制。

V3 至少管理：

```text
research rounds
tool calls
generation attempts
repair attempts
token / model usage
必要 latency / cost
```

每个循环必须有：

```text
remaining budget
stop reason
```

不允许模型自行无限扩展预算。

---

## 3.15 Storage Lifecycle / Retention / GC

这是 V3 相对 V2 明确补齐的持久化闭环。

目标不是做通用 GC 平台，而是解决：

> **Checkpoint / Artifact / Attempt / Tool result 随 Run 数量持续增长时，如何在不破坏恢复和审计的前提下安全控制磁盘占用。**

统一原则：

```text
Lifecycle
+ Reference Reachability
+ Retention Policy
→ Safe Cleanup
```

至少区分：

```text
active artifact
committed current artifact
superseded artifact
stale attempt artifact
temporary tool result
recovery checkpoint
old checkpoint history
release / audit evidence
```

允许清理的典型对象：

```text
superseded artifact
AND no active reference
AND retention satisfied
→ GC candidate
```

禁止仅按“文件超过 N 天”无脑删除。

Minimum Required Scope：

```text
checkpoint compaction / pruning policy
superseded artifact cleanup
stale attempt cleanup
temporary result retention
reference-aware safety check
storage usage observability
```

不做分布式对象存储生命周期平台。

---

# 4. Layer 2 — Shared Domain Foundation

这一层不是 Agent Runtime，也不是某个 Agent 的私有 Schema。

它表达四 Agent 共同消费的业务事实与契约。

核心对象控制在：

```text
RunContext
InputSnapshot
FactSnapshot
SourceInventory
SectionPlan
EvidenceBundle / Evidence refs
Paragraph Artifact
ClaimRecord
Verified Claim
VerificationResult
ReviewReport
Domain Event
```

---

## 4.1 RunContext

RunContext 是一次 Run 的稳定执行上下文身份，不等于所有 Runtime State。

至少要能定位：

```text
run identity
input snapshot identity
fact snapshot ref
config / budget refs
trace root
checkpoint namespace
```

不要把所有 Section mutable state 塞进 RunContext。

---

## 4.2 FactSnapshot

FactSnapshot 是其覆盖范围内当前项目事实的 authority。

必须具有稳定 identity / version / hash / lineage。

Agent 不直接读原始 Excel 并自行解释成事实。

```text
Raw Input
→ Deterministic Parser / Input Service
→ FactSnapshot
→ Agent consumption
```

历史方案与企业知识不能覆盖当前项目 FactSnapshot 的权威事实。

---

## 4.3 Evidence

RAG 的 canonical Evidence authority 属于 RAG Interface / EvidenceBundle。

Multi-Agent 只消费：

```text
evidence_id
selected_evidence_ids
source / provenance
assessment / support facts
```

Multi-Agent 不重新创造第二套 Evidence identity。

---

## 4.4 SectionPlan

Planner 输出 SectionPlan，至少表达：

```text
section_id
section_title
applicable
section_strategy
required_facts
evidence_topics
dependencies
consistency_refs
risk_hints
allow_suggested_design
parallel_group
```

它是后续 Research / Writer / Verifier 的执行约束，不是自由自然语言建议。

---

## 4.5 Claim / Verified Claim

Writer 输出 Claim Candidate / Binding Hint。

Claim 最终语义：

```text
Candidate
→ Bound / Supported / Conflicted
→ Verifier
→ Verified / Rejected
```

跨 Section 传播只允许 Verified Claim。

Claim 生命周期至少与 active projection 一致：

```text
active
invalidated
removed
superseded
```

---

## 4.6 Verification Result / Review Report

Verifier 输出结构化问题与建议 action；不直接修改正文。

Review Report 是最终用户审阅和 Gate/Release 的可观察产物之一。

Human Review 不作为正常运行中的 durable interrupt。

---

# 5. Layer 3 — 四个 Business Agents

V3 仍然只保留四个业务 Agent。

```text
PlannerAgent
ResearchAgent
WriterAgent
VerifierAgent
```

不新增 MemoryAgent / RouterAgent / CriticAgent / ReflectionAgent / DocumentManagerAgent。

---

## 5.1 PlannerAgent

Planner 回答：

> 这个建设方案的每个 Section 应该怎么执行？

输入：

```text
Template
FactSnapshot
Current Project Material summary
SourceInventory
RunContext
```

输出：

```text
SectionPlan + Section DAG hints
```

职责：

```text
Section applicability
Section Strategy
required_facts
evidence_topics
hard dependencies
consistency refs
parallel group
risk hints
allow_suggested_design
```

不负责：

```text
具体 Query
底层检索
正文生成
事实验证
文档装配
```

---

## 5.2 ResearchAgent

Research 只解决：

> 当前 Section 写作需要的证据是否足够、可靠、可追溯？

核心闭环：

```text
Evidence Requirement
→ Current Evidence State
→ Retrieval Action Selection
→ ToolGateway → RAG
→ Evidence Candidates
→ Evidence Evaluation
→ Coverage / Conflict
→ enough?
   ├─ yes → commit EvidencePackage
   └─ no  → next bounded round
```

### V3 保留的技术深度

```text
bounded multi-round retrieval
query/action history
coverage delta
no-gain detection
stop reason
Evidence status: sufficient / insufficient / conflicted
```

Adaptive Router / C-RAG-like evaluator 可以保留，但不能为了论文复现把 Research 做成复杂自治系统。

Research 不直接控制全局 Graph，也不直接执行底层 RAG internals。

---

## 5.3 WriterAgent

Writer 是正文认知生成 authority。

输入：

```text
SectionPlan
FactSnapshot subset
EvidencePackage
Current Section Context
Authorized Verified Claims
Unresolved Verifier Issue
```

### Fact / Design 语义

Writer 内至少区分：

```text
fact
supported_design
suggested_design
uncertain
```

`suggested_design` 不得自动升级成当前项目 Fact。

### Paragraph-native Writer

```text
Paragraph Task
→ ContextCompiler
→ Writer
→ Paragraph Artifact Commit
→ Claim Candidate / Binding Hint
→ Precheck
```

Paragraph 是最小生成与恢复原子。

### Generation Fallback

默认先普通生成。

只有质量不足且值得增加计算时才触发 bounded fallback：

```text
initial generation
→ low-cost evaluation
→ pass → done
→ fail → targeted rewrite / limited regeneration
→ select best
→ stop by budget
```

不追求无限 Self-RAG。

### Evidence Gap Backtrack

只有明确：

```text
evidence_gap
```

才允许：

```text
Writer result
→ Supervisor routing
→ Research
→ Writer regenerate
```

普通 `generation_quality` 不重新检索。

---

## 5.4 VerifierAgent

Verifier 负责语义审查，不直接修改正文。

三层验证：

```text
Paragraph Precheck
Section Verify
Document Verify
```

### Paragraph Precheck

优先确定性检查：

```text
数量
型号
金额
项目名称
FactSnapshot conflict
Evidence / Citation identity
```

### Section Verify

语义检查：

```text
Planner coverage
Fact / Evidence Binding
Evidence sufficiency
technical coherence
suggested_design masquerading as fact
Claim / prose consistency
historical-project contamination
```

输出类似：

```text
passed
repairable
need_more_evidence
review_required
```

### Document Verify

检查：

```text
cross-section fact consistency
scope consistency
terminology
technical contradictions
duplicate conclusions
Verified Claim consistency
```

Verifier 只报告 Issue / action，Supervisor 决定 repair route。

---

# 6. Layer 4 — Orchestration

## 6.1 唯一生产控制面

继续保持：

```text
MainlineApplicationService
→ SupervisorAgent
→ EnterpriseDocumentWorkflowEngine
→ SectionExecutionGraph
```

不恢复 Standalone 第二控制面。

---

## 6.2 Deterministic Controller

以下决策保持 deterministic：

```text
Evidence sufficient → Writer
Need more evidence → Research
Writer completed → Verifier
Repairable → Writer repair
Budget exhausted → Stop / degrade
Hard dependency completed → unlock downstream
Hard Gate fail → no publish
```

LLM 不负责 State Machine、Retry、Budget、Checkpoint 或最终发布。

---

## 6.3 Section DAG / Parallelism

DAG 只表达 Hard Dependency。

```text
             ┌→ Compute ──┐
Planner ─────┼→ Network ───┼→ Document Verify
             └→ Security ──┘
```

没有真实依赖的 Section 并行执行。

只有需要前序 Verified Claim 才建立 hard dependency。

“主题相关”不自动等于依赖。

---

## 6.4 Repair Loop

典型路由：

```text
Verifier repairable
→ Writer targeted repair
→ Verifier
```

或：

```text
Verifier need_more_evidence
→ Research
→ Writer regenerate affected paragraph
→ Verifier
```

所有 loop 受 Budget 控制。

---

# 7. External Boundaries

V3 必须保持 Multi-Agent 与 RAG / Gate 的边界清晰。

## 7.1 RAG Boundary

```text
ResearchAgent
→ ToolGateway
→ RAG Interface
→ EvidenceBundle
```

RAG 是 Evidence Source of Truth。

Multi-Agent 不嵌入 Retriever / Reranker / Milvus implementation。

---

## 7.2 Gate Boundary

```text
Multi-Agent
→ Final Artifact / Verification Facts / Review Report
→ Gate
→ Release / Reject
```

Gate 是独立生产主线。

Multi-Agent 不拥有最终 release PASS authority。

---

## 7.3 Evaluation Boundary

V3 Runtime 负责暴露可评测 Trace / Signal。

独立 Evaluation Foundation 负责：

```text
Gold
Metric Authority
Judge
Result
Report
Benchmark
```

避免 Multi-Agent 自己发明第二套 Eval authority。

---

# 8. Session / Memory / Context 的完整关系

这是 V3 面试时最重要的一组解释链。

```text
Session / Run
    ↓
Working State
    ↓
Durable Run Memory / Domain Truth refs
    ↓
ContextCompiler
    ↓
Agent Context
    ↓
Agent Invocation
```

## 8.1 Session 回答“谁的运行”

```text
task_id / run_id / attempt_id / invocation_id
```

## 8.2 State 回答“现在在哪”

```text
stage / section / cursor / active attempt
```

## 8.3 Working Memory 回答“当前步骤需要保留什么”

```text
current round / current issue / current budget / active refs
```

## 8.4 Durable Memory 回答“跨步骤持续记住什么”

```text
Fact / Evidence / Verified Claim / Progress / Issue / Decision
```

## 8.5 ContextCompiler 回答“这一次模型到底允许看到什么”

```text
role + authority + lifecycle + relevance + budget
```

这五者不能混成一个 `history`。

---

# 9. Artifact / Checkpoint / Recovery / Storage 闭环

## 9.1 Truth Separation

```text
Artifact = committed business content authority
Checkpoint = workflow recovery authority
State = active execution projection
Memory = committed truth query projection
Trace = decision/audit evidence
```

## 9.2 Recovery

```text
load checkpoint
→ resolve active refs
→ load committed artifact / memory
→ ContextCompiler rebuild context
→ resume current operation
```

而不是恢复时把所有历史 prompt / raw output 全塞回模型。

## 9.3 Storage Control

运行期写入必须配套生命周期：

```text
Create
→ Active
→ Superseded / Resolved / Completed
→ Retention
→ Reference check
→ Cleanup
```

目标：

```text
Recovery correctness preserved
AND
Disk growth bounded by explicit policy
```

---

# 10. V3 不做的平台化能力

明确不进入当前 V3：

```text
通用 Agent SDK
通用 Workflow 平台
多租户 Session Service
用户人格长期记忆
跨项目自动 Memory reuse
Memory 全量向量数据库化
复杂知识图 Memory
通用 Event Bus
分布式 Artifact Platform
通用 GC 服务
插件市场
复杂模型动态调度平台
第二套 Checkpoint
第二套 Supervisor
第二套 Router 主控制面
Neo4j / MongoDB / 微服务化
```

如果当前业务只需要一个轻量结构化实现，就使用轻量实现。

---

# 11. V3 面试主轴

V3 不以“有四个 Agent”为卖点。

统一项目陈述应接近：

> 我把建设方案生成限制为 Planner、Research、Writer、Verifier 四个无状态业务 Agent，并把 Session、State、Memory、Context、Checkpoint、Recovery、Idempotency、Tool/Model Gateway、Trace 和 Budget 下沉到统一的 Agent Runtime Foundation。业务 Agent 只处理非确定性认知任务，LangGraph 和 deterministic service 负责执行控制。长期运行记忆由已提交业务事件投影，ContextCompiler 按 Role、Authority、Lifecycle、Relevance 和 Token Budget 动态编译输入；正文按 Paragraph 提交和恢复，只允许 Verified Claim 跨章节传播；所有 Research / Repair loop 都有硬预算，并用 Decision Trace 和 Eval 证明 Context、Recovery、Evidence 和生成质量的真实收益。

但 V3 必须用真实代码 / Trace / Benchmark 支撑，而不能只停留在架构名词。

---

# 12. V3 必须准备的面试级 Badcase / Verification Scenarios

## RUN-01 Session / Run Identity

新完整输入创建新 Run；旧 Attempt 晚到不能覆盖新 Attempt。

## MEM-01 Working vs Durable Memory

能明确展示 Working State 与 Durable Memory 的不同生命周期和 owner。

## MEM-02 Context Budget

Memory 超预算时：

```text
Authoritative Fact retained
Verified Claim retained
unresolved Issue retained
low-authority / resolved history omitted or compressed
```

Trace 能说明为什么。

## MEM-03 Unauthorized Context Leakage

Writer 默认不能读取其他 Section raw draft。

## MEM-04 Cross-section Verified Claim

Section A 已验证：

```text
server_count = 16
```

Section B 可以读取该 Verified Claim，但不能读取 A 的未验证正文。

## REC-01 Paragraph Crash Recovery

```text
p001 committed
p002 committed
p003 crash
```

恢复从 p003，前两段不重新生成。

## REC-02 Research Round Recovery

已提交的 Research Round 不重复 Tool Call。

## IDEM-01 Retry

同一 commit 重放不生成重复 active Artifact。

## IDEM-02 Stale Result

旧 attempt result 只进入 Trace，不污染 active State / Memory / Artifact / Claim。

## RES-01 Bounded Research

连续 no-gain 后正确停止，记录 evidence status + stop reason。

## GEN-01 Evidence Gap Routing

只有 evidence gap 回 Research；普通 generation quality 只做 Writer repair。

## VER-01 Binding != Verification

Binding 成功不能直接标记 Verified；必须经过 Verifier authority。

## STORAGE-01 Storage Lifecycle

superseded / stale artifact 在满足 retention 且无 active reference 后可清理；active recovery artifact 不得误删。

## TRACE-01 Decision Trace

可以从一个 Run 回答：

```text
为什么 Research 搜？
为什么停？
为什么 Writer repair？
Context 选了什么？
为什么拒绝 stale result？
```

---

# 13. V3 关键评测信号

V3 不建立独立 Eval 平台，但必须暴露信号。

## Memory / Context

```text
context token reduction
critical fact retention
unauthorized context leakage
memory selection accuracy / rule correctness
```

## Research

```text
evidence coverage
no-gain rate
tool calls / section
stop correctness
```

## Generation / Verification

```text
fact hallucination
repair success
cross-section contradiction
verified claim correctness
```

## Runtime

```text
recovery correctness
idempotency violation count
stale-result rejection correctness
resume duplicate-call count
```

## Storage

```text
checkpoint growth
artifact growth
superseded bytes reclaimed
active-reference deletion violation = 0
```

## System

```text
tokens / section
latency / section
tool calls / section
Gate pass rate
Verifier repair rate
```

至少完成少量 baseline vs V3 A/B，而不是追求指标数量。

---

# 14. V2 → V3 关系

V3 继承 V2 绝大多数核心方向。

## 14.1 保持不变

```text
唯一 Supervisor
唯一 top graph / section graph
四业务 Agent
Agent 无状态
Memory 外置
ContextCompiler
Paragraph generation/recovery atom
Verified Claim cross-section
bounded Research / generation loop
ToolGateway
ModelGateway
Decision Trace
Hard Gate final authority
Human Review 不作为 normal durable interrupt
```

## 14.2 V3 显式提升

```text
Session / Run Lifecycle
Agent Contract / Invocation Protocol
Agent Runtime Foundation 分层
```

这些在 V2 已有基础语义，但未作为一级架构层表达。

## 14.3 V3 新补齐

```text
Storage Lifecycle
Retention
Reference-aware GC
Checkpoint / Artifact growth closure
```

这是 V2 持久化设计的真实工程闭环补充。

## 14.4 V3 收缩

V3 更明确禁止把基础能力实现成通用平台。

```text
Foundation capability = 必须有
Platform generalization = 不做
```

---

# 15. V3 架构完整性检查

## Requirement

PASS：业务目标仍是建设方案正文生成，没有扩大业务面。

## Architecture

PASS FOR FREEZE CANDIDATE：四层架构、Authority、外部边界明确。

## Schema

NOT YET FROZEN：本文件只定义对象与职责，不冻结 exact fields。

后续至少需要 closure：

```text
Run / Invocation identity
RunContext
AgentInvocationRequest / Result
MemoryRecord
AgentContextPackage
Paragraph Artifact
Claim / Verified Claim
Verification Result
Storage lifecycle metadata
```

## Interface / Contract

NOT YET FROZEN：需要后续 Contract Closure。

## Config

NOT YET FROZEN：Research / Generation / Retention 等 exact limits 后续计划定义。

## Dependency Boundary

PASS AT ARCHITECTURE LEVEL：

```text
Runtime Foundation
→ supports Agents
Shared Domain
→ consumed by Agents/Runtime
Agents
→ no lower-level storage ownership
Orchestration
→ coordinates Agents
RAG / Gate / Eval
→ external authority boundaries
```

## Acceptance / Verification

MACRO LEVEL DEFINED：第 12～13 节已定义面试级 badcase 与 signals。

## Freeze Point

当前：

```text
V3 ARCHITECTURE = FREEZE CANDIDATE
V3 CONTRACT = NOT FROZEN
V3 FORMAL IMPLEMENTATION PLAN = NOT CREATED
NO PRODUCTION IMPLEMENTATION AUTHORIZED BY THIS DOCUMENT
```

---

# 16. V3 最终冻结原则候选

1. 当前只做建设方案正文型 DOCX，不扩业务功能面。
2. 只保留 Planner / Research / Writer / Verifier 四业务 Agent。
3. Agent Runtime Foundation 是四 Agent 共享基础设施，不属于某一个 Agent 私有实现。
4. Runtime Foundation 必须包含 Session、State、Contract、Working Memory、Durable Memory、ContextCompiler、Artifact、Checkpoint、Recovery、Idempotency、Tool Gateway、Model Gateway、Trace、Budget。
5. 每项基础能力只实现当前业务 Minimum Required Scope，不平台化。
6. Agent 跨 invocation 无状态。
7. Memory 外置，Durable Memory 由已提交业务事件投影产生，不成为第二事实源。
8. Context 不是 History；ContextCompiler 是 Agent 上下文统一编译入口。
9. FactSnapshot 是其覆盖范围内当前项目 Fact authority；RAG EvidenceBundle 是 Evidence authority。
10. Writer 是认知正文生成 authority；Paragraph 是 generation / commit / repair / recovery atom。
11. Binding 不等于 Verification；只有 Verifier authority 可产生 Verified 语义。
12. 只有 Verified Claim 可跨 Section 传播，raw draft 不跨 Section 传播。
13. Checkpoint 负责 workflow recovery；Artifact 负责 committed business content；State/Memory/Trace 各自保持单一职责。
14. Research / Writer / Repair 等所有自主循环都有硬 Budget 和 stop reason。
15. Recovery 重点只做 Research Round 和 Paragraph，不建设通用 replay 平台。
16. Idempotency 和 stale-result rejection 必须保护 active State / Artifact / Memory / Claim。
17. ToolGateway / ModelGateway 保持唯一受控执行边界，不建设第二套执行系统。
18. Artifact / Checkpoint 必须具有最小 Storage Lifecycle / Retention / Reference-aware GC，避免磁盘无限增长。
19. Human Review 不作为正常 durable runtime interrupt；最终统一审阅。
20. Hard Gate 保持 deterministic final release authority。
21. Runtime 关键设计必须用 Trace、badcase 和 benchmark 证明，而不是只展示架构图。
22. 不通过增加 Agent、数据库、通用平台、复杂跨 Run Memory 或外围输出制造技术深度。

---

# 17. 当前结论

```text
MULTI-AGENT V3 TARGET
= V2 core principles
+ explicit Agent Runtime Foundation
+ explicit Session / Invocation lifecycle
+ explicit storage lifecycle closure
+ minimum-required-scope implementation rule
```

V3 的目标不是更“大”，而是更清楚：

> **功能能力完整，Authority 清楚，Runtime 有闭环，Business Agent 保持薄，工程证据可验证，同时拒绝平台化扩张。**

当前状态：

```text
V3 ARCHITECTURE DRAFT = COMPLETE
V3 ARCHITECTURE FREEZE = PENDING USER / AUDIT DECISION
V3 CONTRACT CLOSURE = NOT STARTED
V3 IMPLEMENTATION = NOT AUTHORIZED
```
