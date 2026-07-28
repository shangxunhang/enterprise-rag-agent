# TASK-008 Fix Security Test Import Cycle

## 1. Task ID

`TASK-008`

## 2. Title

修复高风险安全/持久化/身份测试的独立收集循环导入

## 3. Owner Role

Agent/Workflow Engineer（Workflow/Package/Test Owner）

## 4. Current Status

`draft`

启动前置条件：`TASK-007` 已完成，并提供不可变 `E001-ASIS-SNAPSHOT`。

## 5. Objective

以 TASK-007 冻结基线为父基线，做最小、独立、可审查的修复，使以下测试文件能够在全新 Python 进程中独立完成 collection，并执行其原有高风险断言：

```text
backend/tests/test_p1_security_durability_identity_closure.py
```

本任务只解决 REVIEW-001 `B-002` 指定的循环导入问题，不得借机重构其他业务链、改变测试意图或处理其他 Finding。

## 6. Background

REVIEW-001 和 TASK-005 均复现了独立收集失败：

```text
test_p1_security_durability_identity_closure.py
  -> document_gate
  -> citation_integrity_service
  -> scheme_writer.__init__
  -> SchemeGenerationUseCase
  -> SchemeGenerationStageService
  -> document_gate (partially initialized)
```

异常：

```text
ImportError: cannot import name 'evaluate_scheme_draft' from partially initialized module
'apps.enterprise_document.services.document_gate'
```

该测试文件用于验证 security、durability 和 identity invariants。聚合测试顺序可能掩盖问题，但不能替代 fresh-process 独立 collection。

## 7. Investigation / Implementation Scope

允许调查和修改的范围仅限：

- `backend/tests/test_p1_security_durability_identity_closure.py` 的导入/收集行为；
- 已知循环链中直接参与 package initialization 或 top-level import 的模块：
  - `backend/apps/enterprise_document/services/document_gate.py`
  - `backend/apps/enterprise_document/services/scheme_writer/citation_integrity_service.py`
  - `backend/apps/enterprise_document/services/scheme_writer/__init__.py`
  - `backend/apps/enterprise_document/services/scheme_writer/use_case.py`
  - `backend/apps/enterprise_document/services/scheme_writer/stage_service.py`
- 为证明修复有效所需的最小 import-order probe 和目标测试运行；
- 与修复直接相关的最小回归测试。

实际修改文件必须是上述范围中的最小子集。列入 scope 不等于允许全部修改。

允许的修复类型包括：

- 移除 package `__init__` 中非必要 eager import；
- 将仅在运行时需要的依赖改为局部/延迟导入；
- 将纯类型依赖移入 `TYPE_CHECKING`；
- 以不改变公开行为的方式打断 package initialization cycle；
- 调整测试导入方式，但不得弱化、跳过或绕过被测生产对象。

## 8. Explicit Non-goals

- 不重构 document gate、citation、SchemeGenerationUseCase 或 StageService 的业务职责；
- 不修改 RAG、GraphState、Trace、WorkflowBudget、checkpoint、identity 或 security 业务逻辑；
- 不解决 M-002 Trace 并发顺序问题；
- 不修复其他测试、其他 import cycle 或全仓 package layout；
- 不改写 TASK-001 至 TASK-005 Analysis/Handoff；
- 不新增功能或更换框架；
- 不通过删除断言、skip、xfail、改名或排除测试来获得通过；
- 不将测试能在聚合顺序中运行视为独立 collection 已修复。

## 9. Required Inputs

- `docs/team/REVIEWS/REVIEW-001-as-is-verification.md`
- `docs/team/STATUS.md`
- `docs/team/TASKS/TASK-007-freeze-e001-baseline.md`
- TASK-007 Analysis、Handoff、Baseline ID 和恢复验证记录；
- TASK-005 Analysis/Handoff 中 `Finding-005-07` 与 `QA-005-08`；
- 指定测试文件和已知导入链涉及的源码；
- 当前项目 Python/pytest 运行环境。

## 10. Required Investigation and Implementation Steps

1. 从 TASK-007 的不可变 snapshot 创建独立修复 branch/worktree；不得直接改写 snapshot。
2. 在 fresh Python process 中复现目标测试独立 collection failure，并记录完整 traceback 和 import chain。
3. 分别检查 package `__init__` eager import、document gate 与 stage service 的 top-level import，定位形成闭环的最小边。
4. 选择最小修复点；记录为什么该点能打断 cycle，且不改变业务调用边和公开 Contract。
5. 实施最小修改。不得同时格式化、重命名或整理无关代码。
6. 在 fresh process 中运行：

```text
./.venv/Scripts/python.exe -m pytest -q backend/tests/test_p1_security_durability_identity_closure.py
```

Windows 路径不适用时，使用当前环境等价 Python 命令，但必须记录精确命令。
7. 确认测试不是被 skip/xfail/deselect，并记录 collected test count、passed/failed count。
8. 执行至少两个独立 import-order probe：
   - 先导入 `document_gate`；
   - 先导入 `scheme_writer`/`stage_service` 相关入口；
   两者均不得出现 partially initialized module 异常。
9. 运行与改动模块直接相关的最小现有回归测试；不得以全仓无关失败扩大整改范围。
10. 生成独立修复 commit，记录 parent 为 TASK-007 snapshot（或明确内容等价父基线）、commit SHA、tree SHA 和 diff。
11. 将测试结果和最小差异交接给 TASK-005 Owner 与 TASK-010 QA。
12. 若 collection 修复后暴露断言失败，不得修改断言规避；记录为 `blocked` 或 `changes_requested`，由 QA/Owner判断下一步。

## 11. Required Evidence

必须提供：

- TASK-007 Baseline ID 与修复 parent；
- 修复前 fresh-process collection traceback；
- 循环导入的直接代码边；
- 精确修改文件和 diff；
- 修复后 fresh-process collection 与 test execution 输出；
- collected/pass/fail/skip/xfail 统计；
- 两种 import-order probe 输出；
- 相关最小回归测试结果；
- 修复 commit SHA/tree SHA；
- 未修改其他业务链的 diff/stat 证明。

“聚合 suite 通过”不能替代目标文件独立执行证据。

## 12. Expected Output Files and Code Change

本任务允许写入：

- 为打断已知 cycle 所需的最小代码修改，且仅限第 7 节范围中的最小子集；
- Analysis：`docs/team/ANALYSIS/TASK-008-fix-security-test-import-cycle.md`
- Handoff：`docs/team/HANDOFFS/TASK-008-fix-security-test-import-cycle-handoff.md`
- 一个独立、可审查的修复 commit。

不得修改 TASK-001 至 TASK-005 的 Analysis/Handoff、`ARCHITECTURE.md`、`CONTRACTS.md` 或 M-002 Trace runtime。

## 13. Acceptance Criteria

- 修复基于 TASK-007 不可变 snapshot，而不是未标识 dirty worktree；
- 目标测试文件在 fresh Python process 中独立 collection 成功；
- 原有测试均被实际收集和执行，没有 skip、xfail、删除、改名或弱化；
- 目标测试全部通过；若未全部通过，TASK-008 不得标记完成；
- 已知两种 import order 均不再触发 partially initialized module；
- 修改范围是打断该 cycle 的最小必要集合；
- 未改变 document gate、citation、UseCase、StageService 的业务语义和调用链；
- 未修改其他业务模块、算法、Trace 或调查报告；
- 修复以独立 commit 标识，QA 可从 TASK-007 snapshot 重放；
- TASK-005 Owner 能据此重新执行并记录 `QA-005-08` 高风险验证。

## 14. Handoff Target

- Primary：Architect / TASK-005 Owner，用于完成高风险测试验证记录；
- Verification：QA / Reviewer / TASK-010；
- Coordination：AI Project Manager / Workflow Coordinator。

Handoff 必须明确：

- `E001-ASIS-SNAPSHOT` 没有被重写；
- TASK-008 commit 是独立派生修复；
- 哪些验证属于修复后 baseline，不能反向声称冻结前 snapshot 已通过该测试。

## 15. Forbidden Actions

- 禁止在 TASK-007 完成前修改任何相关代码；
- 禁止重写、force-update 或覆盖冻结 baseline commit/archive；
- 禁止扩大到其他 import cycle、package cleanup 或架构重构；
- 禁止修改其他业务链；
- 禁止删除、skip、xfail、deselect 或弱化目标测试；
- 禁止通过预导入模块、依赖测试顺序或单进程缓存掩盖问题；
- 禁止修复 M-002 Trace 并发问题；
- 禁止替 TASK-005 Owner 修改其 Analysis/Handoff；
- 禁止把 collection success 写成所有 security/durability/identity 事实已自动成立；
- 禁止提交混入格式化、重命名或无关改动的修复 commit。
