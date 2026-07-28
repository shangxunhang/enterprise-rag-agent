# TASK-007 Freeze E-001 Baseline

## 1. Task ID

`TASK-007`

## 2. Title

冻结 E-001 不可变、可复现调查基线

## 3. Owner Role

AI Project Manager / Workflow Coordinator

## 4. Current Status

`draft`

## 5. Objective

在任何整改代码修改和原报告证据重绑定之前，冻结一个能够唯一标识 E-001 实际调查字节的不可变、可复现基线，解决 REVIEW-001 的 `B-001`。

冻结结果必须让后续 Owner 和 QA 能够回答：

- TASK-001 至 TASK-005 调查的代码、测试、配置、脚本和关键数据证据到底是哪一组字节；
- 如何从不可变标识恢复或验证这些字节；
- 当前 `main@07dda2e80645cac67f8b5f5e30002d21c2ab01eb` 之外的 dirty 内容是否被完整纳入；
- 哪些外部模型、环境和数据未纳入归档，以及为什么不能被当作基线事实；
- TASK-008 后续最小修复相对于冻结基线的精确差异是什么。

不得只记录 dirty worktree 的 HEAD commit，也不得通过丢弃、清理或改写现有内容来制造“干净基线”。

## 6. Background

REVIEW-001 记录：

- Branch：`main`；
- HEAD：`07dda2e80645cac67f8b5f5e30002d21c2ab01eb`；
- Worktree：dirty；
- 当前 changed/untracked entry count：`82`；
- 变更范围包含核心 workflow、RAG、state、tests、TASK、ANALYSIS 和 HANDOFF；
- TASK-001 至 TASK-005 均没有 patch hash、archive hash、完整文件 hash manifest 或不可变 tree identifier。

因此 HEAD 不能证明五份报告与 REVIEW-001 检查了完全相同的字节。TASK-007 是所有报告整改与后续代码修复的首个 Gate。

## 7. Investigation / Execution Scope

必须覆盖：

1. 当前 branch、HEAD、tag、worktree 状态和 capture 时间；
2. 所有 tracked 修改、删除、新增和与 E-001 证据相关的 untracked 文件；
3. TASK-001 至 TASK-006、对应 ANALYSIS/HANDOFF、REVIEW-001 及团队治理文档；
4. 被报告引用的业务代码、测试、配置、脚本、依赖锁文件；
5. 被报告用作事实源的 active index pointer、manifest、JSONL/NPY/Milvus/acceptance/lifecycle 产物的内容身份或明确外部引用；
6. 完整文件清单、文件大小、内容哈希、纳入/排除规则；
7. 恢复或验证步骤；
8. 后续 TASK-008 修复必须从该冻结基线派生并产生独立变更标识。

可接受的冻结方式仅限以下三类之一：

- 独立 baseline commit；
- 独立 review branch/worktree commit；
- 具有完整文件 manifest 和内容哈希的完整 patch/archive。

无论选择哪种方式，都必须处理相关 untracked 文件和被引用数据产物；只提交 tracked 文件而遗漏承重证据，不构成完成。

## 8. Explicit Non-goals

- 不修改、修复、格式化或重构任何业务代码；
- 不解决 TASK-008 的循环导入；
- 不修改 TASK-001 至 TASK-005 的 Analysis/Handoff；
- 不评价调查结论是否正确；
- 不运行真实外部模型或改写 Milvus/index 数据；
- 不把 `.venv`、本地模型权重或仓库外服务自动纳入基线；若未纳入，只记录其外部依赖状态；
- 不将冻结动作等同于 QA approval；
- 不删除现有 dirty 改动以获得 clean status。

## 9. Required Inputs

- `docs/team/REVIEWS/REVIEW-001-as-is-verification.md`
- `docs/team/STATUS.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- TASK-001 至 TASK-006；
- TASK-001 至 TASK-005 对应 ANALYSIS 和 HANDOFF；
- 当前 Git branch、HEAD、index、worktree、untracked 文件状态；
- REVIEW-001 与五份报告引用的源文件、测试、配置、脚本和数据产物；
- Git 与 SHA-256 等内容哈希工具。

## 10. Required Execution Steps

1. 在不修改工作树内容的前提下，重新记录 branch、HEAD、exact tag、`git status --porcelain` 和文件数量。
2. 保存 capture 时点的完整文件清单，区分 tracked、modified、added、deleted、untracked、ignored-but-referenced artifact。
3. 建立“报告承重证据 -> 文件/产物”清单，至少覆盖五份 Analysis/Handoff 与 REVIEW-001 直接引用项。
4. 选择一种允许的冻结方式，并说明选择理由。
5. 若使用 Git commit：
   - 使用独立 baseline/review branch 或 worktree；
   - 将所有相关 dirty 与 untracked 内容纳入 commit，或对无法提交的引用产物建立独立内容哈希 manifest；
   - 记录 commit SHA、tree SHA、parent SHA 和 branch/ref；
   - 验证该 commit 可在 clean worktree 中检出。
6. 若使用 patch/archive：
   - 必须包含完整新增文件、修改文件和删除信息；
   - 对 archive/patch 计算 SHA-256；
   - 对内部文件生成逐文件 SHA-256 manifest；
   - 记录恢复命令和恢复后的校验步骤。
7. 对 active index 等被引用数据产物，记录 pointer、manifest hash、目录/文件 hash；若完整归档不可行，明确它们是外部内容寻址依赖而非 Git tree 的一部分。
8. 验证冻结结果：在独立 clean worktree/临时恢复目录中，重新计算 tree 或 manifest hash，并确认与记录一致。
9. 记录所有排除项；任何被报告用于结论但未冻结或未哈希的项，都必须导致 TASK-007 保持 blocked。
10. 生成供 TASK-008、TASK-009、TASK-010 使用的唯一 Baseline ID 和 Handoff。
11. 不修改 `STATUS.md` 之外的治理状态；完成结果交给 Project Manager 后另行更新状态。

## 11. Required Evidence

必须提供：

- branch、HEAD、tree、parent、tag；
- worktree capture 状态；
- 冻结方式；
- baseline commit SHA 或 archive/patch SHA-256；
- 完整文件 manifest 及逐文件内容哈希；
- untracked 与删除文件的处理证据；
- 被引用 active index/data artifacts 的内容身份；
- clean checkout/restore 验证命令和结果；
- 冻结前后文件内容未被任务 Owner 改写的证据；
- 明确的外部依赖/排除项与 `unknown`。

证据不得仅为截图、文件名或 HEAD 字符串。

## 12. Expected Output Files and Artifacts

本任务允许产生：

- Analysis：`docs/team/ANALYSIS/TASK-007-freeze-e001-baseline.md`
- Handoff：`docs/team/HANDOFFS/TASK-007-freeze-e001-baseline-handoff.md`
- Baseline manifest：`docs/team/BASELINES/E-001-baseline-manifest.md`
- 以下二选一的不可变产物：
  - 一个可检出的独立 baseline/review commit；或
  - 一个完整 patch/archive 及其 SHA-256 文件。

若采用 Git commit，Handoff 必须记录 commit SHA 和 tree SHA；若采用 archive/patch，Handoff 必须记录绝对/可访问位置、文件名、大小和 SHA-256。

除上述文档、不可变快照产物和必要 Git ref/commit 外，不得写入其他项目文件。

## 13. Acceptance Criteria

- 存在一个唯一 Baseline ID；
- Baseline ID 能解析到不可变 commit/tree 或内容寻址 archive/patch；
- 从 clean worktree 或恢复目录能够复现并通过 hash 校验；
- dirty worktree 的相关 tracked、untracked、deleted 内容均被处理；
- 五份报告和 REVIEW-001 的承重代码/测试/配置/数据证据均被纳入或内容寻址；
- 没有仅使用 `main@07dda2e...` 代表 dirty 内容；
- 没有通过丢弃、覆盖、格式化或修复业务文件制造基线；
- TASK-008 可以从该基线派生一个独立最小修复 commit；
- TASK-001 至 TASK-005 Owner 可以在报告中引用同一个明确的 Baseline ID；
- QA 可以独立验证 snapshot、manifest 与恢复结果；
- 所有未纳入项均明确记录，且没有遗漏承重证据。

任一承重文件或数据产物无法被唯一标识时，TASK-007 结论必须为 `blocked`，不得宣称完成。

## 14. Handoff Target

- Primary：TASK-008 Owner（从冻结基线派生最小修复）
- Primary：TASK-009 Coordinator 与 TASK-001 至 TASK-005 原 Owner（重绑定证据）
- Verification：QA / Reviewer（TASK-010）

Handoff 必须明确区分：

- `E001-ASIS-SNAPSHOT`：TASK-007 冻结的评审时 As-Is 字节；
- 后续 TASK-008 修复 commit：从该 snapshot 派生，不得覆盖或重写 snapshot。

## 15. Forbidden Actions

- 禁止只记录 dirty worktree 的 HEAD commit；
- 禁止丢弃、stash 后遗漏、reset、clean 或覆盖现有内容来获得“干净”状态；
- 禁止在冻结前执行 TASK-008 修复；
- 禁止修改业务代码、测试语义、配置行为或调查结论；
- 禁止遗漏与报告结论相关的 untracked 文件；
- 禁止只生成不含新增文件的普通 diff；
- 禁止对被引用数据产物只记录路径而不记录内容哈希；
- 禁止把仓库外模型或环境状态写成已冻结事实；
- 禁止替 TASK-001 至 TASK-005 Owner 修改报告；
- 禁止将 baseline freeze 写成 QA approval 或 E-001 accepted。
