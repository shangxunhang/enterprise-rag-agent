# Project Status

## Current Epic

- Epic: `E-001` 当前系统认知重建
- Current Status: `blocked`
- Blocking Review: `REVIEW-001`
- Review Date: `2026-07-26`
- Review Final Conclusion: `blocked`
- Acceptance State: TASK-001 至 TASK-005 的 As-Is 结论尚不得提升为 accepted 架构或 Contract 事实。

## Current Repository State

- Repository: `D:\MyCode\rag-agent`
- Current Branch: `main`
- Current HEAD Commit: `07dda2e80645cac67f8b5f5e30002d21c2ab01eb`
- Current Worktree: `dirty`
- REVIEW-001 Capture Changed/Untracked Entry Count: `82`
- Post-plan Current Changed/Untracked Entry Count: `86`
- Exact Tag: `none / unknown`
- Immutable E-001 Baseline: `pending TASK-007`

`HEAD` 只标识已提交内容，不能代表当前 82 项 dirty worktree 的实际字节。任何 TASK-001 至 TASK-005 的证据重绑定，都必须等待 TASK-007 产出不可变、可复现的基线标识。

## REVIEW-001 Disposition

`docs/team/REVIEWS/REVIEW-001-as-is-verification.md` 已完成独立核验，最终结论为：

```text
blocked
```

已独立完成的两个非重叠测试批次共计 `116 passed`。但不可变基线缺失，以及高风险测试文件无法独立收集，阻止 E-001 通过当前 Gate。

## Registered Blockers and Major Issues

| ID | Severity | Affected Scope | Current Finding | Required Remediation | Owner / Route | Status |
|---|---|---|---|---|---|---|
| `B-001` | blocker | TASK-001 至 TASK-005 | 调查基线为 dirty worktree，只有 HEAD commit，没有能够标识未提交字节的不可变标识 | TASK-007 冻结完整、不可变、可复现基线；TASK-009 要求所有原负责人重绑定证据并重跑承重验证 | AI Project Manager；原 TASK-001 至 TASK-005 Owner | open |
| `B-002` | blocker | TASK-005 / `QA-005-08` | `backend/tests/test_p1_security_durability_identity_closure.py` 独立收集时发生循环导入 | TASK-008 仅修复该导入/收集问题；之后 TASK-005 Owner 才能完成高风险测试验证 | Agent/Workflow Engineer（Workflow/Package/Test Owner） | open |
| `M-001` | major | TASK-002、TASK-005 | `LegacyEvidenceAdapter` 分类矛盾；当前主链 active 证据不足 | TASK-009 统一为 `legacy/inactive for confirmed current repository mainline`，除非补充直接当前调用证据 | RAG Engineer、Architect | open |
| `M-002` | major | TASK-005 Trace | 同进程并发写入时，sequence 分配与 append 非原子，可能产生文件顺序 `2,1` 并违反 validator 连续顺序要求 | E-001 内仅登记 Finding、收窄 Trace 事实所有权条件并建立后续实现候选；不得顺手修复 | Architect；后续 Trace Runtime Owner | open / fix deferred |
| `M-003` | major | TASK-001、TASK-002、TASK-004，且影响 TASK-005 表述 | “真实生产主链”和无条件 `active` 超出已验证边界；当前仅确认仓库主链、受控运行及资源条件 | TASK-009 将表述统一为 repository-mainline / production-intended，并保留 controlled-runtime、resource-conditional、real-production-unknown 限定 | 原 TASK-001、002、004、005 Owner | open |

## Additional REVIEW-001 Report Revisions

TASK-009 还必须吸收 REVIEW-001 的全部报告修订项，包括：

- TASK-002 将“Evidence 唯一 canonical 创建点”收窄为“real-RAG canonical success path 创建点”，并列出 fake/legacy 替代创建路径及其正确状态；
- TASK-003 将历史入口改为“唯一仓库内可归因的高置信报告生产入口”，保留原始命令、历史 commit 和签名生产者为 `unknown`；
- 所有报告使用同一个不可变基线标识，且不得以 dirty HEAD 代替；
- TASK-005 在 TASK-008 完成后重新执行独立高风险测试验证。

## Remediation Tasks

| Task | Title | Owner | Current Status | Depends On |
|---|---|---|---|---|
| `TASK-007` | Freeze E-001 Baseline | AI Project Manager / Workflow Coordinator | draft | none |
| `TASK-008` | Fix Security Test Import Cycle | Agent/Workflow Engineer（Workflow/Package/Test Owner） | draft / blocked by TASK-007 | TASK-007 |
| `TASK-009` | Revise E-001 Analysis Reports | AI Project Manager / Workflow Coordinator（协调）；原 TASK-001 至 TASK-005 Owner（分别执行） | draft / blocked by TASK-007 | TASK-007；TASK-005 高风险验证另依赖 TASK-008 |
| `TASK-010` | E-001 QA Reverification | QA / Reviewer | draft / blocked | TASK-007、TASK-008、TASK-009 |

## Dependency and Execution Order

```text
TASK-007 Freeze immutable reviewed snapshot
    |
    +--> TASK-008 Minimal import-cycle correction
    |
    +--> TASK-009 report revision lanes
            |- TASK-001 Owner revision
            |- TASK-002 Owner revision
            |- TASK-003 Owner revision
            |- TASK-004 Owner revision
            `- TASK-005 Owner revision
                   `-- high-risk verification waits for TASK-008

TASK-007 + TASK-008 + TASK-009
    `--> TASK-010 independent QA reverification
```

TASK-009 可以在 TASK-007 后启动。TASK-001 至 TASK-004 的文字与证据重绑定可与 TASK-008 并行；TASK-005 不得在 TASK-008 完成前声称高风险测试验证完成。

## Follow-up Implementation Candidate Outside E-001

- Candidate ID: `TASK-CANDIDATE-011-trace-recorder-atomic-sequence-append`
- Candidate Owner: Agent/Workflow Engineer / Trace Runtime Owner
- Candidate Scope: 使同进程并发 writer 的 event sequence 分配与 JSONL append 形成可验证的原子顺序，并补充并发与 validator 测试。
- Current State: backlog candidate only；尚未创建正式实施任务。
- E-001 Rule: TASK-009 只需在 TASK-005 Analysis/Handoff 中准确记录 M-002、限定 Trace authority 条件并引用该候选；禁止在 E-001 整改中修改 Trace runtime。

## Roles Required to Re-enter Work

- AI Project Manager / Workflow Coordinator：执行 TASK-007，协调 TASK-009，维护依赖和 Gate；
- Agent/Workflow Engineer：执行 TASK-008，并以 TASK-001 原负责人身份修订自己的 Analysis/Handoff；
- RAG Engineer：修订 TASK-002 Analysis/Handoff；
- Data Engineer：修订 TASK-003 Analysis/Handoff；
- Algorithm Lead：修订 TASK-004 Analysis/Handoff；
- Architect：修订 TASK-005 Analysis/Handoff，并在 TASK-008 后完成高风险验证记录；
- QA / Reviewer：仅在 TASK-007、TASK-008、TASK-009 全部完成后执行 TASK-010。

## Current Restrictions

- 当前 Project Manager 只制定和记录整改计划，不修改业务代码，不替原负责人改写调查报告；
- TASK-007 不得通过修改业务内容制造“干净基线”；
- TASK-008 只允许解决指定测试文件的循环导入/独立收集问题；
- TASK-009 每个原负责人只能修改自己原有的 Analysis/Handoff 与被明确分配的整改记录；
- M-002 本轮只登记、收窄声明和创建候选，不得修复；
- QA 不得替调查 Owner 修改报告；发现问题只能给出 `changes_requested` 或 `blocked`。

## Next Gate

**整改完成并通过 QA 复核**
