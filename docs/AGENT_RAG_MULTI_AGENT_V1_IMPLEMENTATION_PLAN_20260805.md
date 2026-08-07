# 企业建设方案 Agent-RAG Multi-Agent V1 实施方案

日期：2026-08-05  
状态：Current / 基于当前仓库与已冻结证据链的后续实施方案  
适用仓库：`D:\MyCode\rag-agent`  
建设方案事实源：`docs/AGENT_RAG_MULTI_AGENT_V1_COMPLETE_BUILD_PLAN_20260803.md`  
历史首轮实现记录：`docs/AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_20260729.md`  
生产主线迁移决策：`docs/AGENT_RAG_MULTI_AGENT_V1_MAINLINE_MIGRATION_ADR_20260731.md`  
Standalone 退休边界：`docs/AGENT_RAG_STANDALONE_RETIREMENT_MATRIX_20260801.md`

---

## 1. 文档目的

本文将已经讨论并写入完整建设方案中的“后续实施路线”独立整理为可执行实施方案。

本文不是新的建设方案，也不是新的功能扩张计划。它只回答：

```text
当前多 Agent 已经做到哪里？
还缺什么？
后续按什么顺序实施？
每一步需要形成什么证据？
什么条件下才能进入下一步？
```

建设目标、Agent 职责、Memory 架构、事实与 Claim 边界仍以完整建设方案为准。

---

## 2. 当前实施基线

### 2.1 已完成并冻结的基础能力

当前证据链 Stage A–F 已完成并冻结：

```text
Stage A  Evidence Contract Freeze
Stage B  Document Catalog
Stage C  Profile-aware Offline Index
Stage D  Online Evidence Governance
Stage E  Writer Enforcement & Claim Audit
Stage F  Atomic Commit & True Idempotency
```

对应实施与验收文档位于：

```text
docs/evidence_chain_stage/
```

因此后续多 Agent 实施必须直接复用：

```text
Document Profile
Retrieval Scope
Prompt-visible Evidence
GateDecision
WriterGateBinding
Claim Audit
Document Hard Gate
Atomic Release Manifest
```

禁止重新实现第二套证据判断、Writer Gate、Claim Audit 或发布链。

### 2.2 当前生产唯一主链

生产执行权属于：

```text
MainlineApplicationService
→ SupervisorAgent
→ EnterpriseDocumentWorkflowEngine
→ SectionExecutionGraph
```

必须继续保持：

```text
唯一 Supervisor
唯一顶层生产 Graph
唯一 Section 生产 Graph
唯一 GraphState / Checkpoint
唯一任务终态
唯一文档装配与发布链
```

Standalone `agent_rag_v1` Runtime 已退休为：

```text
Schema 来源
行为规范
Fixture
专项测试资产
迁移参考
```

不得重新成为第二生产控制平面。

### 2.3 历史首轮旁路实现

2026-07-29 首轮实现已经完成 Phase 1–7 的旁路组件底座，历史专项验收为：

```text
70 passed
```

该结果证明强类型 Schema、StateGateway、Router、四 Agent 外壳、Paragraph、Claim、Document Closure 和 Recovery 组件能够独立工作。

但它不等同于当前生产主线全部完成。

### 2.4 当前已进入主线的能力

根据现有建设方案和迁移文档，生产主线已经具备：

```text
SupervisorAgent
顶层 LangGraph
Section LangGraph
Planner / Research / Writer / Verifier Adapter
官方 LangGraph Checkpointer
TaskManager
ModelGateway
RAGService
Section 并行
Paragraph 持久化节点
Paragraph Precheck 节点
Claim 基础结构
research_more / rewrite / replan
stale Attempt 拒绝
budget draining
crash resume
Human Review durable interrupt
document verify / repair / assembly / review
Generation Report
Version Decision
Stage F 正式发布
```

### 2.5 当前仍未完成的核心能力

后续实施只处理以下真实缺口：

```text
InputSnapshot / FactSnapshot 的统一冻结
Excel 权威事实解析与 Diff
显式 Agent Memory Contract / Store / Projection
角色化 MemoryRetriever / ContextCompiler
Research 多轮检索与 no-gain 停止
Writer 真正逐 Paragraph 生成
Claim Binding 与 Verifier Issue 生命周期
跨章节 Verified Memory
Memory Invalidation 与版本复用
真实 DOCX 终态闭环与 E2E 资产
```

---

## 3. 实施总原则

### 3.1 先审计，后继续开发

Stage A–F 冻结后，下一步先进行代码审计：

```text
主链调用图
事实源清单
Schema 所有权
生产写入点
兼容层
重复实现
测试与验收入口
```

代码审计阶段不增加业务功能。

审计结论用于确认下面各实施项的真实落点，避免继续膨胀。

### 3.2 不建立第二套控制平面

禁止新增：

```text
第二 Supervisor
第二 Shared State
第二 Router
第二 Checkpoint
第二 TaskStatus
第二 Evidence Gate
第二 Document Assembler
第二 Release Service 主链
```

新增能力必须作为现有生产主线的 Port、Service、Schema 或节点能力接入。

### 3.3 Agent 无状态，确定性 Service 控制事实

四个业务 Agent：

```text
PlannerAgent
ResearchAgent
WriterAgent
VerifierAgent
```

负责推理、生成、判断和建议。

以下能力必须由确定性 Service 控制：

```text
状态迁移
权限
版本
预算
事实快照
记忆写入
记忆失效
Artifact 持久化
Checkpoint
文档装配
正式发布
```

### 3.4 Evidence Chain 是不可绕过的下游边界

所有 Agent 输出最终都必须经过：

```text
Stage D GateDecision
→ Stage E Writer / Claim Audit / Document Hard Gate
→ Stage F Atomic Release
```

Agent 的“认为正确”不能替代正式 Credential。

### 3.5 每一步必须证据落盘

每个实施单元完成后，必须生成：

```text
实施文档
验收标准
专项测试
负向测试
Runtime Probe
JUnit
Hash
遗留边界
```

阶段状态必须区分：

```text
设计完成
代码完成
专项通过
主线接入
Runtime 通过
正式冻结
```

---

## 4. 总体实施顺序

```text
IA-00 代码审计与事实基线
  ↓
IA-01 InputSnapshot / FactSnapshot
  ↓
IA-02 Memory Contract / Store
  ↓
IA-03 Memory Projection / Lifecycle
  ↓
IA-04 MemoryRetriever / ContextCompiler
  ├─→ IA-05 Research Memory Loop
  └─→ IA-06 Writer Paragraph Ownership
          ↓
      IA-07 Claim Binding / Verifier Issue
          ↓
      IA-08 Cross-section Verified Memory
          ↓
      IA-09 Invalidation / Version Reuse
          ↓
      IA-10 DOCX Closure / E2E
```

IA-05 与 IA-06 可以在 IA-04 完成后并行设计，但必须共享同一 Memory Package Contract。

---

## 5. IA-00：代码审计与事实基线

### 5.1 目标

重新获得对当前代码的控制权，在不改功能的前提下确认真实主线和事实源。

### 5.2 必须输出

```text
生产主链调用图
四 Agent 调用点清单
GraphState 字段所有权表
Artifact 类型清单
Checkpoint / Event / Memory / Tool Store 关系图
Schema 事实源清单
所有兼容导出与动态转发清单
所有正式写入点清单
所有 Runtime / Acceptance 入口清单
重复或退休代码清单
```

### 5.3 必须回答

```text
每个请求从哪里进入？
每个 Agent 的真实输入输出是什么？
谁能修改 Shared State？
谁拥有 Paragraph、Claim、Section、Document？
哪一份 Evidence / Gate / Audit 是事实源？
哪一个节点负责正式发布？
哪些 Standalone 类仍被生产代码引用？
哪些测试只验证旁路，不验证生产主线？
```

### 5.4 退出条件

```text
唯一生产主链可画出
正式事实源无歧义
生产调用点数量可核对
退休实现与生产实现可区分
下一阶段文件落点明确
```

本阶段不得顺手重构全部代码。

---

## 6. IA-01：InputSnapshot 与 Excel FactSnapshot

### 6.1 目标

把原始 Word/PDF/Excel/模板输入冻结为稳定、可追溯、可比较的输入快照。

### 6.2 实施内容

```text
InputSnapshotSchema
SourceInventorySchema
Excel Workbook Parser Port
FactSnapshotSchema
Fact ID
Source Sheet / Cell
Formula / Calculated Value
Fact normalization
Excel 内部冲突检测
TemplateSchema
Snapshot Hash / Version
FactDiff
TemplateDiff
生产 Graph 接入
```

### 6.3 约束

```text
Agent 不直接读取原始 Excel
设备、型号、规格、数量、金额、公式必须追溯到源单元格
相同输入必须产生相同 Snapshot Version
输入变化必须产生确定性 Diff
```

### 6.4 验收

```text
设备、型号、数量和公式可精确查询
历史方案与 Excel 冲突时 Excel 胜出
修改一个单元格只产生对应 FactDiff
旧 Snapshot 不被覆盖
Stage D/E/F 仍全部通过
```

---

## 7. IA-02：Memory Contract 与 Store

### 7.1 目标

建立外置、版本化、可失效、可审计的 Agent Memory，不把记忆放进 Agent 实例。

### 7.2 Schema

```text
MemoryRecordSchema
MemoryQuerySchema
AgentMemoryPackageSchema
MemoryInvalidationPlanSchema
MemoryReferenceSchema
```

### 7.3 Store

首版使用 SQLite Port/Adapter：

```text
MemoryStorePort
SQLiteMemoryStore
```

至少支持：

```text
Task / Run / Document / Section / Agent Role Scope
Snapshot / Plan / Attempt Identity
Authority
Lifecycle Status
Content Hash
Source Event ID
Created / Superseded / Invalidated
```

### 7.4 约束

```text
Memory 不是第二事实源
只保存已提交业务事件的投影
相同 event_id 重放不得重复创建
Hash 错误 fail closed
stale / invalidated 默认不可读
```

### 7.5 验收

```text
MemoryRecord 可持久化与精确查询
相同事件重放幂等
跨项目/越权读取被拒绝
损坏记录被检测
Agent 实例内不保存跨调用 history
```

---

## 8. IA-03：Memory Projection 与生命周期

### 8.1 目标

把已提交 Domain Event 投影成 MemoryRecord，并建立可恢复的生命周期。

### 8.2 Projection 类型

```text
Input / Fact / Template
Plan / Decision
Research Query / Evidence
Paragraph / Claim
Issue / Verification
Verified Section Summary
Version / Release
Recovery Progress
```

### 8.3 实施内容

```text
MemoryProjectionService
Projection Cursor
Projection Pending
Projection Replay
MemoryProjectedEvent
Supersede
Invalidate
Remove / Archive 语义
```

### 8.4 约束

```text
业务提交成功后才能生成长期记忆
Projection 失败不能反向回滚业务事实
Projection 可重放但不能重复
Memory 不得反向覆盖 GraphState 或 Artifact
```

### 8.5 验收

```text
提交后的 Paragraph 可查询对应 Memory
未提交或 stale Attempt 不产生 active Memory
Projection 中断后可从 Cursor 恢复
同一事件重复投影结果一致
```

---

## 9. IA-04：MemoryRetriever 与 ContextCompiler

### 9.1 目标

每次 Agent 调用前生成可审计、角色化、受预算约束的 Memory Package。

### 9.2 实施内容

```text
Role-based Memory Policy
Exact Filter
Dependency-aware Filter
Authority Ranking
Semantic Candidate Adapter
Context Budget
Context Compactor
Package Hash
Memory Trace
memory_package_ref 注入 Agent Invocation
```

### 9.3 角色边界

#### Planner

可读取：

```text
Input Snapshot
Fact Diff
旧 Plan Decision
Version Summary
影响分析结果
```

#### Research

可读取：

```text
Query History
Accepted / Rejected Evidence
No-gain Round
当前 Section Evidence Gap
```

#### Writer

可读取：

```text
当前 Section Plan
当前 Evidence Package
Fact Snapshot
当前 Section Paragraph / Claim
依赖章节 Verified Claim
未解决 Issue
```

禁止读取其他章节未验证 Draft。

#### Verifier

可读取：

```text
当前 Paragraph / Section
Claim Binding
Evidence / Fact
历史 Issue
DraftDiff
Verified Section Summary
```

### 9.4 验收

```text
每次 Agent 调用都有 memory_package_ref
Package Hash 稳定
越权 Memory 不进入 Context
超预算时按确定性策略压缩
Fact、未解决 Issue、Verified Claim 优先保留
记录 omitted count 与压缩原因
```

---

## 10. IA-05：Research 多轮记忆闭环

### 10.1 目标

让 ResearchAgent 从一次检索整理器升级为有限、可停止、可恢复的研究循环。

### 10.2 实施内容

```text
Query Fingerprint
Query History Memory
Research Round
Evidence Gain
No-gain Counter
Accepted Evidence
Rejected Evidence + Reason
Vendor Validation
Old-project Pollution Filter
Evidence Package Artifact
Exhausted / Conflicted / Human Review 路由
```

### 10.3 停止条件

```text
达到 Evidence Sufficiency
达到 Tool Budget
连续两轮 No Gain
明确 Conflicted
明确需要 Human Review
```

### 10.4 验收

```text
相同或等价 Query 不无意义重复
Rejected Evidence 保留原因
连续无增益自动停止
崩溃后从已提交 Research Round 恢复
Tool Call 数受预算控制
最终 Evidence Package 进入 Stage D
```

---

## 11. IA-06：Writer 真正逐 Paragraph 接管

### 11.1 目标

WriterAgent 成为 Paragraph 生成主体，而不是旧 Section 先生成后由 Writer 判断。

### 11.2 主链

```text
WriterAgent
→ 生成一个 Paragraph
→ 立即持久化 Paragraph Artifact
→ Claim 抽取
→ Paragraph Precheck
→ 通过后移动 Cursor
→ 生成下一 Paragraph
```

### 11.3 实施内容

```text
Paragraph Generation Prompt
Stable Paragraph ID
Paragraph Artifact
ParagraphGeneratedEvent
Generation Cursor
source_adapt
technical_generate
Paragraph Revision
Change Manifest
DraftDiff
Crash Resume
Memory Package 注入
```

### 11.4 约束

```text
每段成功后立即提交
未通过 Precheck 不生成后续 Paragraph
恢复时不得重写已提交 Paragraph
修改范围必须可由 DraftDiff 验证
不存在于 Excel/FactSnapshot 的硬事实不得进入 accepted Paragraph
Stage E WriterGateBinding 必须在每次生成前生效
```

### 11.5 验收

```text
p001/p002 已提交，p003 崩溃后从 p003 恢复
已提交 Paragraph 不重复
错误数量在 Precheck 阻断
修复后继续而非全 Section 重写
所有最终 Paragraph 经过 Claim Audit
```

---

## 12. IA-07：Claim Binding 与 Verifier Issue Memory

### 12.1 目标

把 Paragraph 中的关键声明绑定到 Fact/Evidence，并形成可持续的 Issue 生命周期。

### 12.2 实施内容

```text
Deterministic Claim
Semantic Claim
Claim Merge
Fact Binding
Evidence Binding
Incremental Claim
Issue Stable ID
IssueCreated / IssueResolved Memory
Paragraph Precheck Mode
Section Verify Mode
Unauthorized Writer Change 检查
Partial Accept Paragraph Set
```

### 12.3 硬规则

```text
Excel Claim 必须绑定 Excel Fact
关键 Claim 未绑定不得 accept
Gate 不允许的 Claim 必须失败
Writer 越界修改必须被 DraftDiff 发现
修改一个 Paragraph 只重抽该段 Claim
```

### 12.4 验收

```text
Issue 修复后标记 resolved
同类 Issue 不重复创建
Claim Span 与正文一致
Fact/Evidence 失效时 Claim 同步 stale
Verifier 不能直接绕过 Stage E Claim Audit
```

---

## 13. IA-08：跨章节 Verified Memory

### 13.1 目标

允许后续章节复用已验证事实，同时阻止未验证 Draft 跨章节传播。

### 13.2 实施内容

```text
VerifiedSectionSummary Artifact
Verified Claim Index
Hard Dependency Memory
Consistency Reference Memory
Terminology Memory
Document Verify Memory Package
ConflictRepairPlan
Serial Repair
ImpactAnalyzer
Incremental Reverify
```

### 13.3 核心边界

```text
只有 Verified Claim 可以跨章节传播
其他 Section Raw Draft 禁止进入 Writer Context
跨章节冲突必须生成显式 RepairTask
RepairTask 串行执行
修复后只复验影响范围
```

### 13.4 验收

```text
计算资源已验证“8 台服务器”后，网络章节可读取该 Claim
网络章节不能读取计算资源未验证正文
6 台与 8 台冲突时选择权威事实并生成 RepairPlan
修复后只重跑受影响章节
```

---

## 14. IA-09：Memory Invalidator 与版本复用

### 14.1 目标

当输入、事实、模板、Plan、Evidence 或 Attempt 变化时，精确失效受影响记忆并安全复用无关结果。

### 14.2 实施内容

```text
FactDiff Invalidation
TemplateDiff Invalidation
Paragraph / Claim Invalidation
Evidence Version Invalidation
Plan / Attempt Invalidation
Minor Version Reuse
Major Version Impact
Stale Result Trace
Invalidation Audit Report
```

### 14.3 验收

```text
服务器数量 6 → 8 后，相关 Fact/Claim/Summary/Verification stale
无关章节保持可复用
旧 Attempt 结果只进入 Trace
v1.0 / v1.1 绑定各自 Snapshot、Memory Package、Generation Report
Major Change 产生完整影响分析
```

---

## 15. IA-10：真实 DOCX 与终态 E2E

### 15.1 目标

完成可打开、可审计、不可覆盖的真实交付物。

### 15.2 实施内容

```text
最小 DOCX Template Assembler
标题与目录
Accepted Paragraph 装配
Partial / Failed Placeholder
Generation Report Appendix
Assembly Validator
Final Artifact Review
Immutable Version Path
Openability Test
Success / Partial / Failed Export
Stage F 正式发布
```

### 15.3 E2E 场景

必须覆盖：

```text
Research 不重复检索
Writer p003 崩溃恢复
Verifier Issue 闭环
跨章节 Verified Claim
Excel 6 → 8 失效
旧 Attempt 拒绝
Context Budget 压缩
Partial Success 文档
Failed 仍导出
Minor Version 局部恢复
Major Version 影响分析
Human Review 后新版本
Stage F 幂等发布
```

### 15.4 面试与审计资产

```text
Trace JSONL
Memory Context Trace
Memory Invalidation Report
Paragraph Timeline
Issue Lifecycle
Cross-section Claim Graph
Version Diff
Generation Report
最终 DOCX
Release Manifest
```

---

## 16. 每个实施单元的标准交付模板

每个 IA 单元完成时必须落盘：

```text
1. 实施目标
2. 修改文件清单
3. 新增 Schema / Service / Port
4. 生产调用点
5. 正向测试
6. 负向测试
7. Runtime 场景
8. JUnit 路径与 Hash
9. Artifact / Report 路径与 Hash
10. Active Index / Evidence Chain 不变性
11. 遗留问题
12. 是否允许进入下一单元
```

禁止只写：

```text
测试通过
功能完成
```

必须能由独立验收重新复现。

---

## 17. 复杂度控制

后续开发必须执行：

```text
优先修改现有抽象
新增生产文件前说明必要性
禁止平行 Schema 事实源
禁止第二套 Runtime
禁止一次性重写生产主链
每个 Vertical Slice 独立验收
```

当一个实施单元出现以下情况时停止扩张：

```text
无法画出调用链
同一事实出现两个 Schema
新增 Service 只是转发
测试数量增加但主链行为未增加
Agent 报告完成但无法解释真实写入点
```

此时先做局部代码审计和收敛，不继续叠加功能。

---

## 18. Definition of Done

Multi-Agent V1 只有同时满足以下条件才能正式完成。

### 18.1 生产主线

```text
从 MainlineApplicationService 进入
唯一 Supervisor
唯一顶层 Graph / Section Graph
唯一正式 Artifact / Release 主线
Standalone 不作为生产入口
```

### 18.2 四 Agent

```text
Planner 有真实计划与影响分析输入输出
Research 有真实多轮检索和停止条件
Writer 真正逐 Paragraph 生成
Verifier 完成 Paragraph / Section / Document / Final Review 必要模式
Agent 跨调用无状态
```

### 18.3 Memory

```text
Memory Contract / Store / Projection 完成
Retriever / ContextCompiler 完成
角色化权限完成
失效与版本复用完成
跨章节只传播 Verified Memory
Memory Trace 可审计
```

### 18.4 Fact / Claim

```text
Excel FactSnapshot 不可变
关键 Claim 可追溯
Excel Claim 绑定 Excel
旧 Claim 正确失效
跨章节 Claim 安全复用
```

### 18.5 Recovery

```text
Paragraph Crash Resume
Stale Result Rejection
Human Review Resume
Budget Draining
Minor Version Reuse
Input Update Invalidation
```

### 18.6 Deliverable

```text
success / partial_success / failed 都有真实文档
Generation Report 完整
Document Hard Gate 通过后才能发布
Stage F Manifest 是唯一正式 Commit Point
正式版本不可覆盖
```

---

## 19. 当前下一步

当前不直接开始 IA-01。

下一步先执行：

```text
IA-00 代码审计与事实基线
```

审计完成后，根据真实仓库状态更新：

```text
IA-01～IA-10 的已完成项
仍缺项
重复实现
可删除旁路
准确工作量
```

建设方案保持不变，实施顺序可以基于审计证据收敛，但不得再次扩张范围。
