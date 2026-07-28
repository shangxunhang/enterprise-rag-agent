# TASK-009 Revise E-001 Analysis Reports

## 1. Task ID

`TASK-009`

## 2. Title

按 REVIEW-001 修订 E-001 调查报告并重新绑定证据

## 3. Owner Role

- Coordination Owner：AI Project Manager / Workflow Coordinator
- Execution Owners：TASK-001 至 TASK-005 的原 Owner
  - TASK-001：Agent/Workflow Engineer
  - TASK-002：RAG Engineer
  - TASK-003：Data Engineer
  - TASK-004：Algorithm Lead
  - TASK-005：Architect

每个原 Owner 只能修改自己原有的 Analysis/Handoff。Coordination Owner 不得替原 Owner 改写报告结论。

## 4. Current Status

`draft`

启动前置条件：`TASK-007` 已完成并提供不可变 Baseline ID。

TASK-005 的高风险测试验证完成条件：`TASK-008` 已完成。

## 5. Objective

解决 REVIEW-001 对 TASK-001 至 TASK-005 提出的全部报告修订要求，统一证据基线、状态分类、运行边界和事实所有权表述，使五份 Analysis/Handoff 能够再次提交独立 QA 复核。

本任务必须实现：

1. 所有报告引用同一个不可变 `E001-ASIS-SNAPSHOT`，并明确 TASK-008 修复 commit 与原 snapshot 的关系；
2. 每个原 Owner 在冻结字节上重跑本报告的承重验证，而不是只改文字；
3. 完整吸收 `B-001`、`B-002`、`M-001`、`M-002`、`M-003` 以及 REVIEW-001 的两项 minor 报告修订；
4. 五份报告之间不再存在 `LegacyEvidenceAdapter`、algorithm active、production boundary、Trace authority 等直接矛盾；
5. M-002 仅作为 Finding 和后续实现候选记录，不在本任务修改 runtime。

## 6. Background

REVIEW-001 最终结论为 `blocked`。五份报告的主要调用链大体得到支持，但无法接受的原因包括：

- dirty worktree 没有不可变内容身份；
- TASK-005 声明的高风险测试无法独立收集；
- `LegacyEvidenceAdapter` 分类不一致；
- Trace 同进程并发顺序风险遗漏；
- “真实生产主链”和无限定 `active` 超出实际验证边界；
- TASK-002 Evidence 创建点与 TASK-003 历史入口归因范围过宽。

本任务是调查报告整改，不是架构重构或业务修复任务。

## 7. Revision Scope by Original Owner

### 7.1 TASK-001 Owner — Agent/Workflow Engineer

必须：

- 将“当前真实生产主链”“生产入口”“production engine”等超范围表述收窄为：
  - `current repository mainline`；或
  - `production-intended composition path`；
- 明确已确认的是仓库 CLI/application/composition-root reachability，不是实际部署 ingress；
- 将仓库外 HTTP/API adapter、服务启动命令、部署环境继续标记为 `unknown`；
- 在 Analysis/Handoff 的摘要、状态表和最终结论中同步修改，不能只在正文加限制；
- 引用 TASK-007 Baseline ID，并在该 baseline 上重跑原报告承重测试/调用边核验；
- 不改变已确认的 coarse SchemeWriter/UseCase 当前 repository mainline 不可达结论，除非冻结基线证据发生变化。

### 7.2 TASK-002 Owner — RAG Engineer

必须：

- 将“Evidence 唯一 canonical 创建点”收窄为：
  - `real-RAG canonical success path creation point`；
- 明确列出其他可构造 canonical bundle 的路径及状态：
  - `FakeRAGService._bundle_from_tool_output`：fake/test/runtime alternative，按实际证据分类；
  - `LegacyEvidenceAdapter.extract`：compatibility upgrade path，当前 confirmed repository mainline 为 `legacy/inactive`，除非提供直接当前 caller 与运行证据；
- 与 TASK-005 统一 `LegacyEvidenceAdapter` 分类；
- 在摘要和最终状态表保留：mainline-wired、controlled-runtime-confirmed、resource-conditional、real-production-execution-unknown；
- 不把代码默认 `USE_REAL_RAG_TOOL=true` 写成实际部署进程已使用 real RAG；
- 保留 active index trusted-scope、`pymilvus`、real model/provider 的 `unknown`；
- 引用 TASK-007 Baseline ID，并重跑承重检索链、Evidence contract 和相关定向验证。

### 7.3 TASK-003 Owner — Data Engineer

必须：

- 将 `scripts/run_step_11_2_acceptance.py::main` 的归因从“已证实历史真实构建/验收入口”收窄为：
  - `唯一仓库内可归因的高置信报告生产入口`；
- 明确保留以下内容为 `unknown`：
  - 原始 CLI argv；
  - override 来源；
  - active artifact 构建时精确 commit/tag；
  - 签名或外部 producer identity；
- 区分“当前可执行 producer schema 与 artifact 高置信对应”及“历史进程调用已被直接证明”；
- 不削弱已经独立确认的 artifact count、manifest hash、pointer、JSONL 字段统计；
- 引用 TASK-007 Baseline ID，并重跑承重只读 artifact/hash/数量验证。

### 7.4 TASK-004 Owner — Algorithm Lead

必须：

- 所有 `active` 状态都附带适用层级，不再使用无条件全局标签；
- 最终摘要表至少区分：
  - `mainline-wired`；
  - `controlled-runtime-confirmed`；
  - `resource-conditional`；
  - `real-production-execution-unknown`；
- 对 Hybrid、RAG-Fusion、HyDE、CRAG、Self-RAG、Adaptive-RAG、Lost-in-the-Middle 分别保留触发条件、资源前提和真实运行未知；
- 不把受控测试替身、静态 spec、对象构造或当前 shell 默认值写成部署执行事实；
- 对实际部署环境变量、real Milvus/model、trusted scope、真实触发频率保持 `unknown`；
- 引用 TASK-007 Baseline ID，并在冻结基线上重跑算法挂链和受控运行承重验证。

### 7.5 TASK-005 Owner — Architect

必须：

- 将 `LegacyEvidenceAdapter` 改为与 TASK-002 一致的：
  - `legacy/inactive for confirmed current repository mainline`；
  - callable/tested compatibility seam 不等于 active；
- 补充 M-002 Trace 同进程并发顺序 Finding：
  - `_next_sequence` 的锁在 event 构造和 append 前释放；
  - append 使用后续独立锁；
  - 并发 interleaving 可产生 file order `2,1`；
  - `validate_trace_v2` 要求 file-order sequence 为 `[1..n]`；
  - 默认 `SECTION_MAX_CONCURRENCY=1` 只避免默认串行路径，不消除支持并发 1..32 时的条件风险；
- 将 operational Trace authority 收窄为：serial writer 或已保证 ordered/atomic writer 条件下的 per-run JSONL authority；并发 writer 顺序安全标记为 unresolved/failed；
- 在 Analysis/Handoff 中写入 Finding，并引用：
  - `TASK-CANDIDATE-011-trace-recorder-atomic-sequence-append`；
- 本任务不得修改 Trace runtime；
- 修正事实所有权表中的适用条件，避免把 instance lock 写成完整顺序保证；
- TASK-008 完成后，在 fresh process 重新执行目标高风险测试，记录修复 baseline/commit、collected tests、执行结果；
- 明确该验证发生在 TASK-008 派生修复 commit 上，不反向声称冻结前 snapshot 已通过独立 collection；
- 引用 TASK-007 Baseline ID，重跑其余承重 Contract/ownership 验证。

## 8. Explicit Non-goals

- 不修改任何业务代码；TASK-008 的代码修复不属于本任务；
- 不替其他 Owner 修改其 Analysis/Handoff；
- 不更新 `ARCHITECTURE.md` 或 `CONTRACTS.md`；
- 不提出架构重构、算法优化、数据重建或 Contract redesign；
- 不修复 M-002 Trace runtime；
- 不解决 REVIEW-001 未要求本轮修订的其他 Finding；
- 不通过删除 unknown、降低证据要求或改写 QA 结论获得一致性；
- 不把 TASK-008 修复 commit 与冻结前 As-Is snapshot 混写为同一基线。

## 9. Required Inputs

所有 Owner 必须阅读：

- `docs/team/REVIEWS/REVIEW-001-as-is-verification.md`
- `docs/team/STATUS.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- `docs/team/TASKS/TASK-007-freeze-e001-baseline.md`
- TASK-007 Analysis/Handoff/Baseline manifest；
- 自己原始 TASK 文件、Analysis 和 Handoff；
- REVIEW-001 中分配给自己的 blocker/major/minor/required owner action；
- 冻结基线中的相关代码、测试、配置和数据证据。

TASK-005 Owner 还必须读取 TASK-008 Analysis/Handoff 和修复 commit。

## 10. Required Execution Steps

### Coordination Owner

1. 建立五个 revision lane，登记 Owner、输入 baseline、需修订项和完成状态。
2. 确认每个 Owner 使用 TASK-007 的同一 Baseline ID。
3. 不代写报告，只检查分工、文件所有权和依赖。
4. TASK-008 完成后通知 TASK-005 Owner 完成高风险验证。
5. 收集五个 Owner 的自检结果，执行跨报告术语/分类一致性检查。
6. 形成 TASK-009 coordination Analysis/Handoff，交给 TASK-010。

### 每个原 Owner

1. 在开始前记录 TASK-007 Baseline ID、branch/tree/archive hash 和工作目录状态。
2. 逐项引用 REVIEW-001 的分配项，不得只做泛化文字调整。
3. 在冻结字节上重新核实和运行本报告的承重证据；记录命令、结果和失败。
4. 修改自己原有 Analysis 和 Handoff，使摘要、正文、状态表、unknown、最终结论一致。
5. 所有跨报告共享对象使用统一词汇与状态条件。
6. 不删除原有有效证据；若冻结基线与原报告不一致，明确记录差异并以冻结基线为准。
7. 对无法确认项继续标记 `unknown`，不得自行补推断。
8. 完成自检并将结果交给 Coordination Owner。

### Cross-report Consistency Checks

必须核对：

- `LegacyEvidenceAdapter`：TASK-002 与 TASK-005 完全一致；
- Evidence creation：real-RAG success path、fake path、legacy upgrade path范围一致；
- production/mainline：TASK-001、002、004、005 使用同一验证边界；
- algorithm status：TASK-002 与 TASK-004 条件一致；
- active index provenance：TASK-002、003、004、005 不超过 TASK-003 证据；
- Trace：TASK-005 与 REVIEW-001 M-002 一致；
- baseline：十个 Analysis/Handoff 全部引用同一个 TASK-007 snapshot，并单独标识 TASK-008 commit。

## 11. Required Evidence

每个 Owner 必须提供：

- TASK-007 Baseline ID；
- 修改前后 report diff；
- REVIEW-001 条目到具体修订段落的映射；
- 重跑的承重静态/运行验证命令和结果；
- 状态分类依据；
- 保留的 unknown 和理由；
- 未修改业务代码的 Git evidence。

Coordination Owner 必须提供：

- 五个 lane 完成矩阵；
- 跨报告一致性矩阵；
- 所有 REVIEW-001 required changes 的 closure matrix；
- TASK-008 与 TASK-005 高风险验证的依赖完成证据；
- M-002 follow-up candidate 已记录且未在 E-001 修复的证明。

## 12. Expected Output Files

### Coordination Owner 允许写入

- `docs/team/ANALYSIS/TASK-009-revise-e001-analysis-reports.md`
- `docs/team/HANDOFFS/TASK-009-revise-e001-analysis-reports-handoff.md`

### 原 Owner 允许修改

- TASK-001 Owner：
  - `docs/team/ANALYSIS/TASK-001-online-agent-workflow-chain.md`
  - `docs/team/HANDOFFS/TASK-001-online-agent-workflow-chain-handoff.md`
- TASK-002 Owner：
  - `docs/team/ANALYSIS/TASK-002-online-rag-chain.md`
  - `docs/team/HANDOFFS/TASK-002-online-rag-chain-handoff.md`
- TASK-003 Owner：
  - `docs/team/ANALYSIS/TASK-003-offline-index-chain.md`
  - `docs/team/HANDOFFS/TASK-003-offline-index-chain-handoff.md`
- TASK-004 Owner：
  - `docs/team/ANALYSIS/TASK-004-algorithm-placement.md`
  - `docs/team/HANDOFFS/TASK-004-algorithm-placement-handoff.md`
- TASK-005 Owner：
  - `docs/team/ANALYSIS/TASK-005-state-contract-ownership.md`
  - `docs/team/HANDOFFS/TASK-005-state-contract-ownership-handoff.md`

每个 AI 窗口只能写其所属 lane 文件。禁止一个 Owner 批量替其他 Owner 修改报告。

## 13. Acceptance Criteria

- TASK-007 Baseline ID 已写入全部十个原报告文件；
- 每个 Owner 已在冻结基线上重跑承重验证；
- TASK-001 已收窄生产表述；
- TASK-002 已修正 Evidence 创建范围和 `LegacyEvidenceAdapter` 分类；
- TASK-003 已收窄历史入口归因；
- TASK-004 的所有 active 标签均携带受控运行、资源条件和真实生产未知限定；
- TASK-005 已修正 adapter 分类、Trace 并发顺序风险和事实所有权适用条件；
- TASK-005 已在 TASK-008 修复 commit 上完成高风险测试 fresh-process 验证；
- M-002 只记录为 Finding，并引用正式后续候选，没有修改 Trace runtime；
- 五份 Analysis/Handoff 摘要、正文、状态表和最终结论内部一致；
- 跨报告无直接状态矛盾；
- 所有 REVIEW-001 required owner actions 和 minor report revisions 均有 closure mapping；
- 未修改业务代码、ARCHITECTURE.md 或 CONTRACTS.md；
- TASK-010 可以仅依据冻结基线、修复 commit、修订报告和证据矩阵执行独立复核。

任何一个原 Owner lane 未完成，TASK-009 都不得标记完成。

## 14. Handoff Target

- Primary：QA / Reviewer / TASK-010
- Coordination：AI Project Manager / Workflow Coordinator

Handoff 必须提供：

- `E001-ASIS-SNAPSHOT`；
- TASK-008 修复 commit；
- 五个报告 revision diff；
- REVIEW-001 closure matrix；
- 重跑测试/验证结果；
- unresolved unknowns；
- M-002 后续候选及 E-001 不修复声明。

## 15. Forbidden Actions

- 禁止 Coordination Owner 替原 Owner 修改调查报告；
- 禁止任何 Owner 修改他人 Analysis/Handoff；
- 禁止修改业务代码、测试、配置、索引、ARCHITECTURE.md 或 CONTRACTS.md；
- 禁止在 TASK-009 中修复循环导入或 Trace 并发问题；
- 禁止把 TASK-008 修复后的测试结果写成冻结前 snapshot 的直接运行事实；
- 禁止保留无限定“真实生产主链”或无条件 `active` 标签；
- 禁止把 callable、constructed、registered 或 controlled test 直接等同于 deployed active；
- 禁止将 unknown 改写为推断；
- 禁止删除 REVIEW-001 不利结论来获得一致性；
- 禁止在五个 lane 未全部完成时提交 TASK-010。
