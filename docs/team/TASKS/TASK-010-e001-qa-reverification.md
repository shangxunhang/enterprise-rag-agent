# TASK-010 E-001 QA Reverification

## 1. Task ID

`TASK-010`

## 2. Title

对 E-001 整改结果进行独立 QA 复核

## 3. Owner Role

QA / Reviewer

## 4. Current Status

`draft`

启动条件：TASK-007、TASK-008、TASK-009 全部完成并已提交 Handoff。

## 5. Objective

独立验证 REVIEW-001 的阻塞项和报告修订项是否被真正关闭，并对 E-001 给出唯一合法的最终结论：

- `approved`
- `changes_requested`
- `blocked`

QA 只负责核验和记录结论，不替 Project Manager 更新状态，不替原 Owner 修改报告，也不修改业务代码。

## 6. Background

REVIEW-001 对 E-001 的结论为 `blocked`，主要原因是：

- `B-001`：五份报告依赖无法由 dirty HEAD 唯一重现的工作树字节；
- `B-002`：高风险 security/durability/identity 测试无法独立收集；
- `M-001`：`LegacyEvidenceAdapter` 分类矛盾；
- `M-002`：TASK-005 漏记同进程并发 Trace 顺序风险；
- `M-003`：无条件 production/active 表述超过已验证边界；
- 两项 minor 修订：Evidence 创建点范围与历史离线入口归因过宽。

TASK-007 应冻结原 As-Is snapshot；TASK-008 应产生仅解决循环导入的派生修复 commit；TASK-009 应由原 Owner 分别修订报告和重绑定证据。本任务验证这些声明，不默认接受任何 Handoff 自报完成。

## 7. Verification Scope

必须独立验证：

### 7.1 TASK-007 / B-001 Closure

- Baseline ID 是否不可变且可复现；
- commit/tree 或 patch/archive hash 是否有效；
- tracked、untracked、deleted 与承重 data artifacts 是否被完整处理；
- 是否能在 clean worktree/restore directory 复现相同内容 hash；
- 五份报告是否引用同一 snapshot；
- 是否仍存在只用 dirty HEAD 代表实际字节的表述。

### 7.2 TASK-008 / B-002 Closure

- 修复是否从 TASK-007 snapshot 派生；
- diff 是否只解决指定 import cycle；
- 目标测试是否在 fresh process 独立 collection；
- 原有测试是否全部被收集、执行并通过；
- 是否存在 skip、xfail、删除、改名、预导入或顺序依赖规避；
- 两种 import order 是否均稳定；
- 修复是否改变其他业务链或测试语义。

### 7.3 TASK-009 Report Closure

- TASK-001 至 TASK-005 是否由原 Owner 分别修订；
- 每份报告是否重新绑定 Baseline ID 并重跑承重验证；
- REVIEW-001 required owner actions 是否全部有具体 closure；
- 摘要、正文、状态表、unknown 和最终结论是否内部一致；
- 五份报告之间是否仍有直接矛盾。

### 7.4 Issue-specific Verification

- `M-001`：TASK-002 与 TASK-005 的 `LegacyEvidenceAdapter` 状态一致，并区分 callable compatibility seam 与 current repository mainline activation；
- `M-002`：TASK-005 准确记录 sequence 分配与 append 非原子导致的同进程并发顺序风险；Trace authority 有明确串行/有序 writer 条件；后续候选已登记；E-001 没有顺手修复 runtime；
- `M-003`：TASK-001、002、004、005 使用 repository-mainline / production-intended 等受控术语；所有 active 结论保留 controlled-runtime、resource-conditional、real-production-unknown 限定；
- TASK-002 minor：Evidence 创建点已限定为 real-RAG canonical success path，并列出 fake/legacy alternatives；
- TASK-003 minor：历史入口已限定为唯一仓库内可归因的高置信报告生产入口，原始命令/commit 保持 unknown。

### 7.5 Cross-baseline Semantics

QA 必须明确区分：

- `E001-ASIS-SNAPSHOT`：冻结前评审字节；
- TASK-008 fix commit：从 snapshot 派生的最小修复字节；
- 哪些报告事实在 snapshot 上验证；
- 哪些高风险测试在 fix commit 上执行；
- TASK-008 diff 是否足够小且与被验证 invariant 无业务语义冲突。

不得把两个 baseline 混写为同一 tree。

## 8. Explicit Non-goals

- 不修改业务代码、测试、配置或数据；
- 不替 TASK-007、008、009 Owner 补证据；
- 不修改 TASK-001 至 TASK-005 Analysis/Handoff；
- 不修改 `STATUS.md`、`ARCHITECTURE.md` 或 `CONTRACTS.md`；
- 不修复 M-002 Trace runtime；
- 不扩大为全仓架构审计或完整产品验收；
- 不以报告自述、文件名、类名或已有测试结果代替独立核验；
- 不输出 `passed`、`failed`、`review`、`accepted` 等其他最终 disposition。

## 9. Required Inputs

- `docs/team/REVIEWS/REVIEW-001-as-is-verification.md`
- `docs/team/STATUS.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- TASK-007、TASK-008、TASK-009 及其 Analysis/Handoff；
- TASK-007 Baseline manifest、commit/tree 或 patch/archive；
- TASK-008 fix commit 和完整 diff；
- 修订后的 TASK-001 至 TASK-005 Analysis/Handoff；
- TASK-009 closure matrix；
- 相关源代码、测试、配置、数据 artifact 和运行环境。

任何必需输入缺失，应直接考虑 `blocked`，不得自行补造。

## 10. Required Verification Steps

1. 记录 QA 自身 branch、HEAD、worktree 和复核时间。
2. 独立恢复/检出 TASK-007 snapshot，验证 commit/tree 或 archive/manifest SHA-256。
3. 抽查并复算所有承重文件和 active index/data artifact hash。
4. 核对 TASK-007 是否完整处理 untracked/deleted 内容和排除项。
5. 检出 TASK-008 fix commit，确认 parent/snapshot 关系和 diff 范围。
6. 在 fresh process 独立执行：

```text
./.venv/Scripts/python.exe -m pytest -q backend/tests/test_p1_security_durability_identity_closure.py
```

记录 collection、execution、pass/fail/skip/xfail。
7. 独立执行两种 import-order probe，确认不依赖聚合 suite 顺序。
8. 审查 TASK-008 diff，确认没有无关业务、测试语义、Trace 或架构修改。
9. 对 TASK-001 至 TASK-005 分别建立 REVIEW-001 条目 -> 修订位置 -> 证据 -> QA 结论矩阵。
10. 重跑 REVIEW-001 中最承重的非重叠验证集合，至少覆盖：
    - coarse path retirement / repository mainline boundary；
    - online RAG explicit node and Evidence contract；
    - active index pointer/manifest/hash/count；
    - algorithm controlled execution and resource limitation；
    - GraphState/Evidence/Citation/Status/Budget/Trace contracts；
    - high-risk isolated test。
11. 对 `LegacyEvidenceAdapter` 做反向 caller 和 runtime evidence复核。
12. 对 Trace M-002 复核代码锁边界；可使用只读并发 probe，但不得修改 runtime 或留下持久业务副作用。
13. 检查五份报告的摘要、状态表和最终结论是否保留运行限定，不能只在正文中出现。
14. 检查 M-002 follow-up candidate 已存在于状态/整改材料，且没有混入 E-001 实现修复。
15. 汇总所有 unresolved unknown；判断其是否阻止 As-Is acceptance，不能因“已有 unknown”自动批准或自动阻塞。
16. 生成 REVIEW-002，并从三个合法 disposition 中选择一个。

## 11. Required Evidence

REVIEW-002 必须包含：

- QA baseline 元数据；
- TASK-007 snapshot 恢复与 hash 结果；
- TASK-008 parent/diff 与 fresh-process 测试结果；
- import-order probe；
- 五报告 closure matrix；
- B-001、B-002、M-001、M-002、M-003 逐项 disposition；
- 两项 minor 修订 disposition；
- 独立重跑命令、测试数量、结果和限制；
- 跨报告一致性矩阵；
- snapshot 与 fix commit 的明确区分；
- 未解决问题及其对最终结论的影响；
- 唯一最终结论。

任何引用的在线/离线事实都必须有文件、函数、调用边、运行输出或内容 hash 证据。

## 12. Expected Output Files

本任务只允许写：

- Final Review：`docs/team/REVIEWS/REVIEW-002-e001-remediation-reverification.md`
- Handoff：`docs/team/HANDOFFS/TASK-010-e001-qa-reverification-handoff.md`

QA 不得直接修改 STATUS、原任务报告或业务代码。最终状态更新由 AI Project Manager 根据 REVIEW-002 执行。

## 13. Acceptance Criteria

### 可判定为 `approved` 的必要条件

- TASK-007 snapshot 不可变、可恢复、hash 一致，B-001 完整关闭；
- TASK-008 目标测试 fresh-process 独立 collection 和执行全部通过，且 diff 仅限指定 cycle，B-002 完整关闭；
- TASK-001 至 TASK-005 全部使用明确不可变 baseline 并重跑承重证据；
- M-001 分类一致且有直接证据；
- M-002 被准确记录、事实所有权已收窄、后续候选已建立、E-001 未修复 runtime；
- M-003 的生产/active 限定出现在摘要和最终表中；
- 两项 minor 修订完成；
- 五报告不存在实质矛盾；
- 所有剩余 unknown 均被准确限定，且不破坏报告所声称的 As-Is 范围。

### 应判定为 `changes_requested` 的情形

- baseline、测试和核心证据已经可复核，但报告仍存在可明确修订的遗漏、术语不一致、状态表错误或 closure 不完整；
- 问题可以由已知 Owner 在不重新冻结基线或新增重大实现工作的情况下修正。

### 应判定为 `blocked` 的情形

- snapshot 不可复现或不完整；
- 目标测试仍无法独立 collection/执行；
- TASK-008 混入无法隔离的业务语义变更；
- 一个或多个原 Owner lane 缺失；
- 承重证据无法绑定到唯一字节；
- 关键事实冲突无法凭现有证据解决；
- 复核依赖的环境、数据或文件缺失，导致无法形成可靠结论。

## 14. Handoff Target

- Primary：AI Project Manager / Workflow Coordinator
- Conditional：各原 Owner，仅当结论为 `changes_requested` 或 `blocked` 且存在明确责任项
- Post-approval consumer：Architect，后续受控汇总 `ARCHITECTURE.md` / `CONTRACTS.md`

Handoff 只传递 QA 结论和责任项，不替 Project Manager 更改 Epic 状态。

## 15. Forbidden Actions

- 禁止在 TASK-007、TASK-008、TASK-009 未全部完成时启动最终复核；
- 禁止修改业务代码、测试、配置、数据、STATUS 或原报告；
- 禁止替 Owner 补写证据或直接修正文档；
- 禁止忽略 dirty/untracked 内容或只验证 HEAD；
- 禁止依赖聚合测试顺序掩盖目标测试独立收集问题；
- 禁止把 TASK-008 fix commit 与 E001-ASIS-SNAPSHOT 混为同一 baseline；
- 禁止在 QA 中修复 M-002 Trace runtime；
- 禁止因多数测试通过而跳过 blocker closure；
- 禁止输出除 `approved`、`changes_requested`、`blocked` 之外的最终结论；
- 禁止在证据不足时使用模糊通过措辞。
