# TASK-005 State and Contract Ownership

## 1. Task ID
TASK-005

## 2. Title
核实核心 State / Contract 的创建者、写入者、读取者与事实所有权

## 3. Owner Role
Architect

## 4. Current Status
draft

## 5. Objective
基于 TASK-001 至 TASK-004 已确认的真实调用链，核实以下核心对象在当前实现中的定义位置、创建者、字段写入者、读取者、生命周期、状态转换和事实所有权：

- `ProjectInput`
- `GraphState`
- `RAGEvidenceContract`
- `CitationBinding`
- `ExecutionStatus`
- `Trace`
- `WorkflowBudget`

本任务必须回答：谁创建对象、谁首次赋值、谁可修改哪些字段、谁读取、谁负责校验、哪个对象/字段在冲突时是当前实现中的事实源，以及该事实所有权是否能够被直接代码或运行证据支持。

## 6. Background
E-001 要求所有核心模块、算法、状态和 Contract 都能挂到两条真实功能链上。类型定义或 Schema 声明只能证明结构存在，不能证明其在运行时被创建、写入、读取或作为事实源。

TASK-001 至 TASK-004 分别提供 Agent/Workflow、在线 RAG、离线索引和算法位置证据。本任务负责跨链汇总对象级和字段级事实所有权，但只形成调查 Analysis 与 Handoff；不得直接修改 `ARCHITECTURE.md` 或 `CONTRACTS.md`。最终汇总需在通过 TASK-006 独立核验后另行处理。

## 7. Investigation Scope
对七类指定对象逐项调查：

- 定义文件、类型形态、继承/组合关系和序列化形式；
- 构造函数、Factory、Normalizer、Adapter、Deserializer 或其他真实创建路径；
- 默认值、派生值和外部输入字段来源；
- 首次写入者、后续写入者、字段级 mutation、merge、copy、replace 和清理行为；
- 读取者、校验者、路由判断者、持久化者和序列化者；
- 对象从创建到销毁或返回的生命周期；
- `ExecutionStatus` 的状态集合、转换发起者、合法性约束和终态读取者；
- `Trace` 的事件创建、追加、关联 ID、落盘/输出和消费路径；
- `WorkflowBudget` 的初始值、消耗计算、扣减/更新、超预算判断和终态报告；
- `RAGEvidenceContract` 与 `CitationBinding` 的创建、字段绑定、验证和 Generation/Quality Gate 消费；
- `ProjectInput` 与 `GraphState` 的字段复制、映射、覆盖和事实源冲突；
- 对象在 active、inactive、legacy、experimental 路径中的不同实现；
- 无法确认的对象、字段或所有权标记为 unknown。

“事实所有权”指当前运行实现中对某项事实具有最终权威读取意义的对象或字段，不等同于团队角色、代码目录归属或设计上应该负责的模块。

## 8. Explicit Non-goals
- 不设计新的 State、Contract、Schema 或状态机；
- 不评价当前所有权划分是否合理；
- 不提出字段移动、对象合并、不可变改造、状态机重构或类型重构方案；
- 不重新执行 TASK-001 至 TASK-004 的完整调查；
- 不修改 `ARCHITECTURE.md`、`CONTRACTS.md` 或任何业务代码；
- 不补齐缺失字段、不修复状态错误、不新增校验；
- 不依据类型名、注释、Docstring 或设计文档推断运行时所有权；
- 不把“定义该类型的模块”自动视为事实所有者。

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
- `docs/team/ANALYSIS/TASK-004-algorithm-placement.md`
- `docs/team/HANDOFFS/TASK-004-algorithm-placement-handoff.md`
- 当前 Git branch、commit、tag 和工作区状态；
- 七类对象相关源代码、Schema、配置、测试、trace、日志、JSON/JSONL 样例和序列化产物。

若上游任务结论冲突，必须保留冲突并分别列出证据，不得擅自选择更符合设计预期的一方。

## 10. Required Investigation Steps
1. 记录调查基线，并核对 TASK-001 至 TASK-004 的 branch/commit、入口和链路边界是否一致。
2. 对七类对象进行全仓搜索，分别登记 definition、construction、deserialization、write、read、validation、serialization、persistence 和 test 候选位置。
3. 将每个候选位置挂到 TASK-001 至 TASK-004 已确认调用链；无法挂链的实现不得写为当前主链事实。
4. 找到每个对象的真实创建点，记录创建者、创建函数、输入参数、默认值和返回去向。
5. 建立字段级写入矩阵：对象字段、首次写入者、后续写入者、写入函数、写入条件、值来源和覆盖规则。
6. 建立字段级读取矩阵：读取者、读取函数、读取目的、读取时点和缺失值处理。
7. 追踪对象在函数边界间是同一实例、浅/深复制、重新构造、序列化后恢复，还是普通字典映射；不得按类型注解推断。
8. 核实 `ProjectInput` 到 `GraphState` 的映射、保留、覆盖和丢失字段，确认冲突时实际读取哪一处。
9. 核实 `RAGEvidenceContract` 的创建、字段来源、验证、返回和消费；区分原始检索结果与正式 Evidence Contract。
10. 核实 `CitationBinding` 的创建时机、证据标识与生成内容的绑定方式、验证者和最终消费方。
11. 核实 `ExecutionStatus` 的所有实际状态值、状态转换调用点、转换条件、异常/重试/取消/部分成功路径和终态读取者。
12. 核实 `Trace` 的事件生产者、追加顺序、关联标识、输出位置、持久化边界和读取者；区分运行 trace、调试日志和评测记录。
13. 核实 `WorkflowBudget` 的创建、初始来源、消耗项、更新者、阈值判断、超预算路径和报告位置。
14. 对同名或重复定义分别分类为 active、inactive、legacy、experimental 或 unknown。
15. 判断事实所有权时，必须以当前消费者实际读取的最终值为依据，并记录可能存在的多源、复制滞后或覆盖冲突。
16. 使用现有测试、trace、日志或序列化样例核验至少关键对象的创建、写入和读取顺序。
17. 输出对象级生命周期图、字段级读写矩阵、状态转换表、事实所有权表、重复实现表、冲突和 unknown 清单。
18. 将所有高风险结论、冲突和证据缺口写入 HANDOFF，交由 TASK-006 独立复核。

状态判定口径：

- **active**：对象/实现位于当前真实链并有创建、写入或读取证据；
- **inactive**：定义存在，但当前链不创建、不写入或不读取，或配置关闭；
- **legacy**：有明确旧链/替代证据，当前主链不再使用；
- **experimental**：仅在实验、Demo、Benchmark 或专项测试中使用；
- **unknown**：现有证据不足以判断其生命周期或所有权。

事实所有权判定口径：

- **authoritative**：当前关键消费者直接读取该值，且不存在更晚覆盖或另一个已证实权威源；
- **derived**：由其他事实计算、复制或转换而来，不是原始事实源；
- **replica/cache**：仅为传递、缓存、展示或序列化副本；
- **conflicting**：存在多个写入源或消费者读取不一致，无法形成唯一权威源；
- **unknown**：证据不足。

## 11. Required Evidence
每个对象至少需要：

- 定义文件、类/Schema 名和字段；
- 实际创建文件、类/模块、函数和调用语句；
- 字段级写入者、写入函数、写入条件和值来源；
- 字段级读取者、读取函数和读取目的；
- copy/merge/serialization/persistence 的直接证据；
- 从当前真实入口到创建/读写点的可达路径；
- 现有测试、trace、日志或序列化样例；
- active / inactive / legacy / experimental / unknown 状态；
- authoritative / derived / replica-cache / conflicting / unknown 所有权判定及理由。

`ExecutionStatus` 必须提供“from -> to -> 发起函数 -> 条件 -> 消费方”的转换证据。

`Trace` 必须提供“事件类型/记录 -> 创建者 -> 追加/落盘函数 -> 存储位置 -> 读取者”的证据。

`WorkflowBudget` 必须提供“初始值 -> 消耗项 -> 更新函数 -> 判断函数 -> 超预算结果”的证据。

类型注解、字段声明、默认值定义、注释和测试夹具不能单独证明运行时事实所有权。

## 12. Expected Output File
本任务只允许写以下两个文件：

- Analysis：`docs/team/ANALYSIS/TASK-005-state-contract-ownership.md`
- Handoff：`docs/team/HANDOFFS/TASK-005-state-contract-ownership-handoff.md`

Analysis 文件至少包含：

- 调查基线与上游结论一致性检查；
- 七类对象的定义与真实调用链位置；
- 对象生命周期图；
- 字段级创建/写入/读取矩阵；
- `ProjectInput` 与 `GraphState` 映射和覆盖关系；
- `RAGEvidenceContract` 与 `CitationBinding` 的创建、绑定、验证和消费；
- `ExecutionStatus` 状态转换表；
- `Trace` 生产、持久化和消费链；
- `WorkflowBudget` 创建、消耗和判断链；
- 事实所有权表；
- active / inactive / legacy / experimental / unknown 分类；
- 重复定义、冲突、unknown 和证据缺口。

Handoff 文件至少包含：

- 交给 TASK-006 的逐项待核验结论；
- 每项高风险所有权结论的证据路径；
- 上游任务之间的矛盾及未决项；
- 禁止被汇总进 `ARCHITECTURE.md` / `CONTRACTS.md` 的 unknown 或 conflicting 内容；
- QA 通过后可供后续 Architect 汇总的候选事实清单。

## 13. Acceptance Criteria
- 七类指定对象均有独立、完整的调查结论；
- 每个对象均确认定义、创建、写入、读取、验证、序列化/持久化和生命周期中的适用项；
- 每个主链结论均能挂到 TASK-001 至 TASK-004 的已确认调用边；
- 字段级写入者和读取者有文件、类/模块、函数和直接证据；
- 已区分类型定义者、对象创建者、字段写入者、读取者和事实所有者；
- `ProjectInput` 与 `GraphState` 的复制、覆盖、映射和冲突已核实；
- `ExecutionStatus` 的实际状态与转换均有触发和消费证据；
- `Trace` 与普通日志、评测记录已区分；
- `WorkflowBudget` 的初始、消耗、判断和结果路径已确认；
- 事实所有权按 authoritative、derived、replica/cache、conflicting 或 unknown 分类；
- 重复实现均区分 active、inactive、legacy、experimental 或 unknown；
- 未根据 Schema、命名或设计意图推断运行时所有权；
- 未修改 `ARCHITECTURE.md`、`CONTRACTS.md`、业务代码或其他文件；
- Analysis 与 Handoff 可由 TASK-006 独立复核；
- 除指定 Analysis 和 Handoff 外没有修改任何文件。

## 14. Handoff Target
- Primary：QA / Reviewer（TASK-006）
- Post-verification consumer：Architect，用于后续受控汇总 `ARCHITECTURE.md` 与 `CONTRACTS.md`

## 15. Forbidden Actions
- 禁止修改任何业务代码、测试、配置、Schema、Contract、状态机或数据；
- 禁止直接修改 `docs/team/ARCHITECTURE.md` 或 `docs/team/CONTRACTS.md`；
- 禁止为证明读写关系而新增日志、埋点、测试或临时代码；
- 禁止执行会产生持久业务数据、状态或外部服务副作用的命令；
- 禁止根据类型定义位置、类名、字段名、注释或设计文档推断事实所有权；
- 禁止把对象创建者自动视为全部字段的事实所有者；
- 禁止忽略复制、覆盖、merge、序列化恢复或多写入者造成的冲突；
- 禁止在证据冲突时自行选择符合预期的结论；
- 禁止将 unknown 或 conflicting 内容写成唯一事实；
- 禁止评价所有权设计优劣或提出重构、字段迁移和状态机改造方案；
- 禁止顺手修复发现的问题；
- 禁止修改 `STATUS.md`、任何 TASK 文件及其他任务的 Analysis/Handoff；
- 禁止写入本任务指定 Analysis 与 Handoff 之外的任何文件。
