# TASK-004 Algorithm Placement Map

## 1. Task ID
TASK-004

## 2. Title
核实 RAG 算法的实际位置、启用状态与调用条件

## 3. Owner Role
Algorithm Lead

## 4. Current Status
draft

## 5. Objective
基于 TASK-001、TASK-002、TASK-003 的真实调用链和当前代码/运行证据，逐项核实 Hybrid、RAG-Fusion、HyDE、CRAG、Self-RAG、Adaptive-RAG、Lost-in-the-Middle 等算法在当前系统中的实际实现位置、调用阶段、启用状态、触发条件、输入输出和后续消费方。

本任务不是判断仓库里是否存在同名代码，而是判断每个算法是否进入当前真实功能链、在什么条件下进入、实际执行了哪些步骤，以及无法确认的部分是什么。

## 6. Background
项目历史上可能同时保留主链实现、旧版实现、实验实现、评测实现和未接入候选代码。算法名称、文件位置、类名、注释和 README 均不能独立证明算法已启用。

本任务必须以 TASK-002 的在线 RAG 链和 TASK-003 的离线索引链为骨架，将每个算法挂到已证实调用边上。若算法无法挂到真实链路，必须标为 inactive、legacy、experimental 或 unknown，不得为了形成完整算法图而推断。

## 7. Investigation Scope
必须逐项调查：

- Hybrid Retrieval；
- RAG-Fusion；
- HyDE；
- CRAG；
- Self-RAG；
- Adaptive-RAG；
- Lost-in-the-Middle 相关处理或缓解机制。

对每项算法调查：

- 定义、配置、构造、注册、调用和测试候选位置；
- 所属阶段：Query 预处理、路由、召回、融合、重排、相关性判断、补检索、Evidence 组装、上下文排序、Generation 后检查或离线索引；
- 从真实入口到该实现的完整可达路径；
- 当前配置、任务类型、阈值、状态或数据条件；
- 实际输入、输出及下游消费方；
- 是否存在多套实现及其关系；
- 运行时是否执行，执行次数和关键结果；
- active、inactive、legacy、experimental 或 unknown 状态；
- 实现名称与标准算法概念不完全一致时，实际执行行为是什么。

允许登记其他在真实链中发现的算法性处理，但不得扩展为新的设计任务。

## 8. Explicit Non-goals
- 不评价算法效果、先进性、论文一致性或业务价值；
- 不进行指标对比、重新评测、调参或模型更换；
- 不建议启用、关闭、新增、删除或替换算法；
- 不提出 RAG 流程重构或算法编排方案；
- 不重新执行 TASK-001、TASK-002、TASK-003 的完整链路调查；
- 不修改代码、配置、Prompt、模型、测试、索引或数据；
- 不把算法类名、函数名或注释直接解释为算法行为；
- 不更新 `ARCHITECTURE.md`、`CONTRACTS.md`、`STATUS.md` 或其他任务文件。

## 9. Required Inputs
- `docs/team/PROJECT.md`
- `docs/team/ORGANIZATION.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- `docs/team/STATUS.md`
- `docs/team/ANALYSIS/TASK-001-online-agent-workflow-chain.md`
- `docs/team/HANDOFFS/TASK-001-online-agent-workflow-chain-handoff.md`
- `docs/team/ANALYSIS/TASK-002-online-rag-chain.md`
- `docs/team/HANDOFFS/TASK-002-online-rag-chain-handoff.md`
- `docs/team/ANALYSIS/TASK-003-offline-index-chain.md`
- `docs/team/HANDOFFS/TASK-003-offline-index-chain-handoff.md`
- 当前 Git branch、commit、tag 和工作区状态；
- 算法相关源代码、配置、Prompt、测试、trace、日志和评测产物。

若上游 Analysis/Handoff 尚未完成，允许先建立 candidate 清单，但不得给出 active 结论；阻塞项必须标记 unknown 或 pending evidence。

## 10. Required Investigation Steps
1. 记录调查基线，并核对 TASK-001 至 TASK-003 的 branch/commit 是否一致；不一致时明确登记差异。
2. 从 TASK-002、TASK-003 已确认调用链提取所有算法候选调用点，建立“链路阶段 -> 调用边 -> 候选算法”索引。
3. 对七类指定算法分别进行全仓搜索，登记 definition、construction、registration、configuration、call-site、test、runtime evidence；搜索命中本身不构成状态结论。
4. 为每项算法写出最小可验证行为定义，即必须观察到哪些实际步骤才能认为该算法在运行；不得仅按命名匹配。
5. 从已确认真实入口验证到算法实现的可达调用路径，记录所有中间调用边和触发条件。
6. 核实配置开关、默认值、环境变量、任务类型、阈值、状态值和数据条件的实际来源及优先级。
7. 追踪算法输入、内部关键处理、输出及下游消费；若输出未被消费，不得判定其对当前主链生效。
8. 对同一算法的多套实现分别编号，确认它们是主链、替代实现、旧实现、实验实现还是无法判断。
9. 使用现有测试、trace、日志或无持久副作用的现有入口核验关键算法是否执行及执行条件。
10. 对 Lost-in-the-Middle，不得只搜索同名字符串；需核实是否实际存在上下文重排、首尾增强、分段生成或其他明确缓解行为，并准确描述行为，不强行贴标签。
11. 对 CRAG、Self-RAG、Adaptive-RAG，不得因存在 relevance、reflection、routing 等通用步骤就自动认定；必须证明完整或实际实现的具体子流程。
12. 对 Hybrid、RAG-Fusion、HyDE，必须分别确认召回源、查询生成、融合公式/排序和结果消费，而非只确认函数被调用。
13. 为每项算法给出状态、证据强度、调用条件和 unknown 清单；有矛盾证据时并列记录，不自行消解。
14. 输出算法位置矩阵、调用条件表、多实现关系表、运行证据表和证据缺口。
15. 将确定的算法调用边、状态依赖和所读写 Contract/State 字段交接给 TASK-005。

状态判定口径：

- **active**：能够挂到当前真实主链，当前配置/条件下可达，且存在直接调用和运行或等价强证据；
- **inactive**：实现存在，但当前真实主链不可达，或当前配置明确关闭，或输出未被主链消费；
- **legacy**：有明确证据表明属于旧链、兼容链或已被替代实现，当前主链不可达；
- **experimental**：仅在实验、Benchmark、Demo、Notebook 或专项测试路径中运行；
- **unknown**：可达性、配置、运行行为或输出消费证据不足。

状态必须针对“具体实现 + 当前基线 + 具体调用条件”给出，禁止对算法名称做脱离实现的全局笼统判断。

## 11. Required Evidence
每项算法至少需要以下证据项：

- 实现文件路径；
- 类名、模块名和函数/方法名；
- 真实调用者与直接调用语句；
- 从 TASK-002/TASK-003 主链到该调用点的路径；
- 配置键、默认值、覆盖来源和当前值；
- 触发条件；
- 输入、关键处理和输出；
- 输出的实际读取者/消费方；
- 测试、trace、日志或运行记录；
- 状态分类及分类理由。

证据必须区分：

- 代码存在；
- 对象被构造；
- 实现被注册；
- 调用点可达；
- 条件实际满足；
- 算法实际执行；
- 输出实际生效。

只有最后三项均有充分证据时，才能稳定判定为当前条件下 active；否则应根据事实标为 inactive、legacy、experimental 或 unknown。

## 12. Expected Output File
本任务只允许写以下两个文件：

- Analysis：`docs/team/ANALYSIS/TASK-004-algorithm-placement.md`
- Handoff：`docs/team/HANDOFFS/TASK-004-algorithm-placement-handoff.md`

Analysis 文件至少包含：

- 调查基线及上游链路一致性检查；
- 七类指定算法的逐项结论；
- 算法位置矩阵：算法/实现 -> 链路阶段 -> 调用者 -> 被调用者；
- 启用条件矩阵：配置、任务类型、阈值、状态和数据条件；
- 输入、处理、输出和消费方；
- 多套实现关系；
- active / inactive / legacy / experimental / unknown 状态；
- 静态证据与运行证据；
- 命名与实际行为差异；
- 冲突、unknown 和证据缺口。

Handoff 文件至少包含：

- 交给 TASK-005 的算法实现所读取和写入的 State/Contract 字段；
- 算法路由、状态切换、预算消耗和 trace 写入线索；
- 每项算法的最终状态及证据定位；
- QA / Reviewer 需要重点复核的高风险结论和矛盾点。

## 13. Acceptance Criteria
- Hybrid、RAG-Fusion、HyDE、CRAG、Self-RAG、Adaptive-RAG、Lost-in-the-Middle 七项均有独立调查结论；
- 每项结论绑定到具体实现、当前基线和调用条件，而非只写算法名称；
- active 结论均能挂到 TASK-002 或 TASK-003 的已确认调用链；
- 每项状态均有文件、类/模块、函数、调用边、配置和输出消费证据；
- 已区分代码存在、可达、条件满足、实际执行和输出生效；
- 多套实现分别分类，没有合并为模糊结论；
- Lost-in-the-Middle 依据实际行为核实，没有强行按名称判断；
- 实现不完整或概念不完全对应时，准确描述实际行为并标记范围；
- 所有候选均区分 active、inactive、legacy、experimental 或 unknown；
- 未评价算法优劣，未进行调参、重构或新增算法建议；
- 未修改代码、配置、测试、模型、索引或数据；
- Analysis 与 Handoff 可供 TASK-005 和 TASK-006 直接复核；
- 除指定 Analysis 和 Handoff 外没有修改任何文件。

## 14. Handoff Target
- Primary：Architect（TASK-005）
- Verification：QA / Reviewer（TASK-006）

## 15. Forbidden Actions
- 禁止根据算法名、文件名、类名、函数名、注释或 README 直接判断算法已实现或启用；
- 禁止把对象构造、注册或测试覆盖直接写成当前主链 active；
- 禁止因出现通用步骤而强行贴 CRAG、Self-RAG、Adaptive-RAG 或 Lost-in-the-Middle 标签；
- 禁止修改业务代码、配置、Prompt、模型、测试、索引或数据；
- 禁止新增实验、评测、日志、埋点或临时代码；
- 禁止执行会产生持久业务数据、索引写入或外部服务副作用的命令；
- 禁止将 unknown 补写成推测；
- 禁止评价算法和架构优劣，禁止提出重构、调参、新增或替换方案；
- 禁止顺手修复发现的问题；
- 禁止修改 `ARCHITECTURE.md`、`CONTRACTS.md`、`STATUS.md`、任何 TASK 文件及其他任务的 Analysis/Handoff；
- 禁止写入本任务指定 Analysis 与 Handoff 之外的任何文件。
