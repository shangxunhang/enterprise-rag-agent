# TASK-001 Online Agent / Workflow Chain

## 1. Task ID
TASK-001

## 2. Title
还原 Query 到 `SchemeGenerationUseCase` 的 Agent / Workflow 真实调用链

## 3. Owner Role
Agent/Workflow Engineer

## 4. Current Status
draft

## 5. Objective
基于当前基线代码与可复核运行证据，还原用户 Query 从真实可执行入口进入系统后，经过 Agent、Supervisor、Workflow、Application Service 等组件，直到 `SchemeGenerationUseCase` 被实际创建或调用并返回的完整 As-Is 调用链。

本任务必须回答：

- Query 从哪个真实入口进入；
- 每一条实际调用边的调用者、被调用者及函数；
- `SchemeGenerationUseCase` 的真实调用点、输入来源和返回去向；
- Agent / Workflow 组件在主链中的实际职责、分支条件、失败路径和状态变化；
- 哪些候选实现属于 active、inactive、legacy、experimental；
- 哪些事实因证据不足只能标记为 unknown。

## 6. Background
E-001 的目标是重建当前 Agent-RAG 系统的真实认知，而不是依据设计意图、目录结构或命名推测架构。在线生成链的上半段定义为：

`Query -> Agent / Workflow -> SchemeGenerationUseCase`

TASK-002 将从本任务确认的 `SchemeGenerationUseCase` 实际调用点继续追踪在线 RAG 链，因此本任务必须给出可直接交接的准确边界、参数和运行上下文。

## 7. Investigation Scope
包含：

- 当前可执行的 CLI、API、Demo、测试入口或其他 Query 接收入口；
- Query 的解析、规范化、封装及任务对象创建过程；
- Application Service、Use Case、Supervisor、Workflow Engine、Workflow Executor、Agent 等候选组件的真实调用关系；
- 同步、异步、生成器、回调或事件驱动调用方式；
- 路由、条件分支、重试、降级、取消、异常传播和结果回传；
- 与主链相关的配置开关、依赖注入、工厂注册和运行时选择；
- `SchemeGenerationUseCase` 实际调用前后的输入、输出及状态边界；
- 同名、旧版、示例、测试或未接入实现的状态分类。

调查起点：能够被证明确实接收 Query 的当前执行入口。

调查终点：`SchemeGenerationUseCase` 的实际调用完成，且其返回值或控制权去向已经确认。不得继续展开 Use Case 内部 RAG 实现；该部分属于 TASK-002。

## 8. Explicit Non-goals
- 不执行 TASK-002 的在线 RAG 内部调查；
- 不评价现有 Agent / Workflow 架构是否优雅、合理或先进；
- 不提出重构、迁移、合并、拆分或新增 Agent 的方案；
- 不修改业务代码、测试代码、配置或依赖；
- 不修复调查中发现的问题；
- 不以目录名、类名、函数名、注释、README 或历史聊天记录代替运行事实；
- 不更新 `ARCHITECTURE.md`、`CONTRACTS.md`、`STATUS.md` 或其他任务文件。

## 9. Required Inputs
- `docs/team/PROJECT.md`
- `docs/team/ORGANIZATION.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- `docs/team/STATUS.md`
- 当前 Git branch、commit、tag 与工作区状态；
- 当前仓库源代码、配置、依赖注入/注册信息、现有测试和可执行脚本；
- 可用的现有日志、trace、task JSONL、运行输出或测试报告；
- TASK-002 所需的交接边界定义。

## 10. Required Investigation Steps
1. 记录调查基线：branch、commit、tag、工作区是否干净、可执行测试基线；无法取得的项标记为 unknown。
2. 全仓搜索 `SchemeGenerationUseCase` 的定义、构造、注入、调用和测试引用；所有搜索结果先登记为 candidate，不得直接判定为主链。
3. 搜索所有可能接收 Query 的入口，并通过入口注册、启动命令、现有测试或运行证据确认当前真实入口。
4. 从已确认入口逐调用边向下追踪，记录文件路径、类/模块、函数、调用语句及参数变化。
5. 对每个 Agent / Supervisor / Workflow 候选组件，核实其是否由已确认调用者实际触发；仅存在定义或实例化不足以证明 active。
6. 记录 Query 在各边界上的数据形态、关键字段来源、默认值、规范化和转换过程。
7. 记录所有可达分支及触发条件，包括配置、任务类型、状态值、异常、重试、降级、取消和提前返回。
8. 确认 `SchemeGenerationUseCase` 的真实调用函数、调用参数、调用方式以及返回值被谁读取。
9. 对重复实现、旧实现、Demo、测试专用实现和未接入实现分类为 active、inactive、legacy、experimental 或 unknown，并为分类提供证据。
10. 使用至少一种独立于静态阅读的证据核验关键主链，例如现有测试、已有 trace/log，或在不产生持久副作用的条件下执行现有入口。
11. 形成调用链图、逐边证据表、分支表、状态分类表和 unknown 清单。
12. 将 TASK-002 的准确起点、调用参数、上下文对象和相关证据写入 HANDOFF 文件。

状态判定口径：

- **active**：从当前真实入口可达，并有调用证据或当前配置下的运行证据；
- **inactive**：实现存在，但当前真实入口不可达，或被当前配置明确关闭；
- **legacy**：有明确证据表明已被旧链使用、已被替代或仅为兼容保留，且当前主链不可达；
- **experimental**：仅在实验、Benchmark、Demo 或专项测试路径使用，未进入当前主链；
- **unknown**：证据不足，无法可靠判定。

## 11. Required Evidence
每项主链结论至少包含以下静态证据：

- 文件路径；
- 类名、模块名或可识别对象名；
- 函数/方法名；
- 直接调用语句、注册关系或依赖注入关系；
- 调用者与被调用者之间的参数对应。

关键主链还必须尽量包含至少一种运行证据：

- 现有测试名称及结果；
- 启动/执行命令及关键输出；
- trace/log/task JSONL 中能够证明调用顺序的记录；
- 可复现的断点、堆栈或调用记录。

证据表必须明确区分：definition、construction、registration、call-site、runtime。只有 definition 或命名相似不能证明实际运行行为。无法获得运行证据时，必须说明限制并标记相应结论置信度或 unknown。

## 12. Expected Output File
本任务只允许写以下两个文件：

- Analysis：`docs/team/ANALYSIS/TASK-001-online-agent-workflow-chain.md`
- Handoff：`docs/team/HANDOFFS/TASK-001-online-agent-workflow-chain-handoff.md`

Analysis 文件至少包含：

- 调查基线；
- 入口判定；
- Query 到 `SchemeGenerationUseCase` 的顺序调用链；
- 逐边证据矩阵；
- 参数/状态转换表；
- 分支、异常、重试和返回路径；
- active / inactive / legacy / experimental / unknown 清单；
- 未解决问题与证据缺口。

Handoff 文件至少包含：

- TASK-002 的唯一已确认起点；
- 调用文件、类、函数和调用语句；
- 传入 `SchemeGenerationUseCase` 的参数与上下文；
- 返回值或后续控制权去向；
- 相关证据定位；
- 尚未确认且不得被下游当作事实的 unknown 项。

## 13. Acceptance Criteria
- 起点是被证明确实接收 Query 的当前入口，不是依据名称选出的候选入口；
- 终点准确落在 `SchemeGenerationUseCase` 的实际调用及返回边界；
- 主链中每一条调用边都有文件、类/模块、函数和直接证据；
- Query 的关键字段或对象转换可追踪，不存在无来源的跳跃；
- 可达分支、异常、重试、降级和提前返回均已登记，无法确认者标记 unknown；
- 所有候选实现均区分 active、inactive、legacy、experimental 或 unknown；
- 静态事实与运行事实分栏记录，没有把定义存在等同于实际调用；
- 未评价架构优劣，未提出重构方案，未修改任何业务代码；
- Analysis 和 Handoff 内容一致，TASK-002 可据此直接开始调查；
- 除指定的 Analysis 和 Handoff 文件外，没有修改任何文件。

## 14. Handoff Target
- Primary：RAG Engineer（TASK-002）
- Secondary：Architect（TASK-005 汇总状态与 Contract 所有权时使用）
- Verification：QA / Reviewer（TASK-006）

## 15. Forbidden Actions
- 禁止修改任何业务代码、测试代码、配置、依赖、数据库结构或索引数据；
- 禁止为证明链路而新增临时代码、埋点、测试或调试分支；
- 禁止执行会写入共享数据库、Milvus、外部服务或持久业务数据的命令；
- 禁止根据目录、类名、注释、文档描述或历史聊天推断运行行为；
- 禁止把“被导入”“被实例化”“存在测试”直接写成“主链已调用”；
- 禁止将 unknown 补写成推测；
- 禁止评价架构优劣或提出任何重构、优化和迁移方案；
- 禁止顺手修复发现的问题；
- 禁止修改 `docs/team/ARCHITECTURE.md`、`docs/team/CONTRACTS.md`、`docs/team/STATUS.md`、任何 TASK 文件及其他任务的 Analysis/Handoff；
- 禁止写入本任务指定 Analysis 与 Handoff 之外的任何文件。
