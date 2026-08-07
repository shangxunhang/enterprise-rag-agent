# RAG-Agent 项目上下文文档索引

日期：2026-08-05  
适用仓库：`D:\MyCode\rag-agent`  
目的：统一标记多 Agent 建设、实施、迁移、退休和证据链 A–F 的事实文档，避免在新窗口中重复讨论或使用过期口径。

---

## 1. 多 Agent 建设方案

### 当前建设方案事实源

```text
docs/AGENT_RAG_MULTI_AGENT_V1_COMPLETE_BUILD_PLAN_20260803.md
```

用途：

```text
定义 Multi-Agent V1 的目标
定义范围与主动裁剪
定义唯一生产主链
定义四个业务 Agent
定义复杂 Memory Layer
定义 Input / Fact / Claim / Recovery / Deliverable
定义最终 Definition of Done
```

状态：

```text
保留
不重复重写
后续架构决策以该文档为建设目标基线
```

---

## 2. 多 Agent 实施方案

### 当前实施方案事实源

```text
docs/AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_PLAN_20260805.md
```

用途：

```text
基于当前仓库状态整理后续执行顺序
先执行代码审计与事实基线
再实施 InputSnapshot / FactSnapshot
再实施 Memory Contract / Projection / ContextCompiler
再完成 Research / Writer / Verifier / Cross-section / Invalidation / DOCX E2E
规定每个实施单元的验收和证据落盘要求
```

状态：

```text
Current
下一步从 IA-00 代码审计开始
```

---

## 3. 多 Agent 历史实施记录

### 首轮旁路实现

```text
docs/AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_20260729.md
```

说明：

```text
记录 2026-07-29 的首轮 agent_rag_v1 旁路实现
历史专项结果 70 passed
证明组件底座可工作
不代表当前生产主线全部完成
```

### Standalone Runtime 说明

```text
docs/AGENT_RAG_MULTI_AGENT_V1_STANDALONE_RUNTIME_20260729.md
```

说明：历史旁路 Runtime 文档，不是当前生产入口。

---

## 4. 多 Agent 生产主线迁移决策

### 主线迁移 ADR

```text
docs/AGENT_RAG_MULTI_AGENT_V1_MAINLINE_MIGRATION_ADR_20260731.md
```

冻结决策：

```text
保留唯一 SupervisorAgent
保留唯一 EnterpriseDocumentWorkflowEngine
保留唯一 SectionExecutionGraph
不接入第二套 Shared State / Checkpoint / Router / TaskStatus / Release
Standalone 组件逐项迁入现有生产主线
```

### Standalone 退休矩阵

```text
docs/AGENT_RAG_STANDALONE_RETIREMENT_MATRIX_20260801.md
```

冻结决策：

```text
Standalone Orchestrator / RuntimeFactory / ApplicationService 不再是生产入口
历史场景由生产 Mainline E2E 测试接管
Standalone 仅保留为 fixture / reference
```

---

## 5. 证据链 Stage A–F 实施与验收

目录：

```text
docs/evidence_chain_stage/
```

### Stage A

```text
docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_A_IMPLEMENTATION_ACCEPTANCE.md
```

主题：Evidence Contract Freeze。

### Stage B

```text
docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_B_IMPLEMENTATION_ACCEPTANCE.md
```

主题：Document Catalog。

### Stage C

```text
docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_C_IMPLEMENTATION_ACCEPTANCE.md
```

主题：Profile-aware Offline Index。

### Stage D

```text
docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_D_IMPLEMENTATION_ACCEPTANCE.md
```

主题：Online Evidence Governance。

### Stage E

```text
docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_E_IMPLEMENTATION_ACCEPTANCE.md
```

主题：Writer Enforcement、Claim Audit、Identity 与 Evidence Lineage。

### Stage F

```text
docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_F_IMPLEMENTATION_ACCEPTANCE.md
```

主题：Atomic Commit、True Idempotency、Concurrency、Recovery、Tamper Detection。

状态：

```text
Stage A–F 已冻结
不再增加新的证据链功能阶段
下一步只允许代码审计、冗余清理、架构文档和项目说明整理
```

---

## 6. 证据链详细历史文档

`docs/evidence_chain_stage/` 同时保留各阶段更细的历史实施文档，例如：

```text
EVIDENCE_GATE_STAGE_A_CONTRACT_FREEZE_V1.md
EVIDENCE_GATE_STAGE_B_DOCUMENT_CATALOG_V1.md
EVIDENCE_GATE_STAGE_C_PROFILE_AWARE_INDEX_V1.md
EVIDENCE_GATE_STAGE_C_FINAL_V2.md
EVIDENCE_GATE_STAGE_D_ONLINE_GOVERNANCE_V2.md
EVIDENCE_GATE_STAGE_D_FINAL_GATE_SEMANTIC_CLOSURE_V3.md
EVIDENCE_GATE_STAGE_E_WRITER_ENFORCEMENT_V1.md
EVIDENCE_GATE_STAGE_E_CLAIM_AUDIT_INTEGRITY_V2.md
EVIDENCE_GATE_STAGE_E_MANDATORY_IDENTITY_LINEAGE_V3.md
EVIDENCE_GATE_STAGE_F_IMMUTABLE_RELEASE_V1.md
```

使用规则：

```text
先读 EVIDENCE_CHAIN_STAGE_*_IMPLEMENTATION_ACCEPTANCE.md 了解阶段全貌
需要追查具体实现和历史演进时，再读 EVIDENCE_GATE_STAGE_* 文档
```

---

## 7. 当前新窗口建议读取顺序

### 讨论多 Agent 建设目标

```text
1. PROJECT_CONTEXT_DOCUMENT_INDEX_20260805.md
2. AGENT_RAG_MULTI_AGENT_V1_COMPLETE_BUILD_PLAN_20260803.md
```

### 讨论下一步怎么实施

```text
1. PROJECT_CONTEXT_DOCUMENT_INDEX_20260805.md
2. AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_PLAN_20260805.md
3. AGENT_RAG_MULTI_AGENT_V1_MAINLINE_MIGRATION_ADR_20260731.md
4. AGENT_RAG_STANDALONE_RETIREMENT_MATRIX_20260801.md
```

### 讨论已经做过什么

```text
1. AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_20260729.md
2. docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_A_IMPLEMENTATION_ACCEPTANCE.md
3. docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_B_IMPLEMENTATION_ACCEPTANCE.md
4. docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_C_IMPLEMENTATION_ACCEPTANCE.md
5. docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_D_IMPLEMENTATION_ACCEPTANCE.md
6. docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_E_IMPLEMENTATION_ACCEPTANCE.md
7. docs/evidence_chain_stage/EVIDENCE_CHAIN_STAGE_F_IMPLEMENTATION_ACCEPTANCE.md
```

### 开始代码审计

```text
1. AGENT_RAG_MULTI_AGENT_V1_IMPLEMENTATION_PLAN_20260805.md 的 IA-00
2. code_walkthrough/02_AGENT_WORKFLOW_MAINLINE.md
3. code_walkthrough/07_AGENT_RAG_V1_STANDALONE.md
4. AGENT_RAG_MULTI_AGENT_V1_MAINLINE_MIGRATION_ADR_20260731.md
5. AGENT_RAG_STANDALONE_RETIREMENT_MATRIX_20260801.md
```

---

## 8. 当前冻结口径

```text
多 Agent 建设方案：已有，不重写
多 Agent 实施方案：已独立整理
历史首轮实现：保留为历史证据
生产主线迁移：已完成唯一控制平面决策
Standalone Runtime：已退休
证据链 Stage A–F：已完成并冻结
当前下一工作：代码审计与复杂度收敛
```

禁止在没有新证据的情况下重新声明：

```text
Multi-Agent V1 已全部完成
Complex Memory 已完整实现
Writer 已完全接管 Paragraph 生成
Research 已完成完整多轮记忆闭环
真实 DOCX E2E 已全部完成
```

这些结论必须等待后续实施和独立验收。
