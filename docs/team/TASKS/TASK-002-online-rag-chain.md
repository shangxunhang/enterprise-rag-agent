# TASK-002 Online RAG Chain

## 1. Task ID
TASK-002

## 2. Title
从 `SchemeGenerationUseCase` 实际调用点还原在线 RAG 到 Evidence 的真实调用链

## 3. Owner Role
RAG Engineer

## 4. Current Status
draft

## 5. Objective
从 TASK-001 已确认的 `SchemeGenerationUseCase` 实际调用点出发，还原当前在线请求如何形成检索请求、调用检索组件、访问索引或外部知识源、完成候选合并与处理，并最终形成 Evidence 的真实 As-Is 调用链。

本任务必须回答：

- `SchemeGenerationUseCase` 在何处、以何种参数触发在线 RAG；
- Query 如何被改写、拆分、扩展或封装成检索输入；
- 稠密检索、关键词检索、向量数据库、重排、过滤、去重、父子块回填等步骤是否真实发生；
- 检索结果如何转换为 Evidence；
- Evidence 的创建点、关键字段来源、返回路径和消费方；
- 每个候选实现的 active、inactive、legacy、experimental 或 unknown 状态。

## 6. Background
项目在线生成链定义为：

`Query -> Agent / Workflow -> RAG -> Evidence -> Generation`

TASK-001 负责确认 Query 到 `SchemeGenerationUseCase` 的真实边界。本任务只接受 TASK-001 交接文件中被证实的调用点作为起点，不得自行依据类名选择另一个入口。TASK-004 将基于本任务的逐调用边证据判断 Hybrid、RAG-Fusion、HyDE、CRAG、Self-RAG、Adaptive-RAG、Lost-in-the-Middle 等算法的实际位置和启用条件；TASK-005 将进一步核实 Evidence 与相关 Contract 的事实所有权。

## 7. Investigation Scope
包含：

- `SchemeGenerationUseCase` 内部或其直接协作者触发 RAG 的实际调用点；
- 检索请求对象的创建、字段来源、默认值及转换；
- Query rewrite、query decomposition、multi-query、fusion、HyDE 等候选预检索处理的实际调用边；
- 稠密、稀疏、关键词、全文、图或其他检索后端的真实调用；
- Milvus collection、索引、分区、过滤表达式、top-k 和连接参数的运行时来源；
- 多路候选的合并、打分、归一化、去重、过滤、重排和截断；
- parent-child、上下文扩展、原文回填或 metadata enrichment；
- 相关性判断、检索质量门、补检索、重试、降级和空结果处理；
- 原始检索命中到 Evidence 的组装过程；
- Evidence 返回给 Generation 或上游调用者的实际路径；
- 同名、旧版、Demo、测试或未接入 RAG 实现的状态分类。

调查起点：TASK-001 Handoff 中确认的 `SchemeGenerationUseCase` 实际调用边界。

调查终点：Evidence 被创建并返回给明确消费方。Generation 的提示词构造、正文生成和质量门不属于本任务。

## 8. Explicit Non-goals
- 不重新调查 Query 到 `SchemeGenerationUseCase` 的 Agent / Workflow 上游链；
- 不调查离线文档清洗、Chunk、Embedding 和入库链；该部分属于 TASK-003；
- 不对算法名称、效果和优劣做最终判定；算法位置总表属于 TASK-004；
- 不核定 Contract 的全局事实所有权；该部分属于 TASK-005；
- 不评价 RAG 架构、召回质量或参数是否合理；
- 不提出新增算法、调参、重构、缓存或性能优化方案；
- 不修改代码、配置、索引、Milvus 数据或测试；
- 不更新架构汇总文件和其他任务文件。

## 9. Required Inputs
- `docs/team/PROJECT.md`
- `docs/team/ORGANIZATION.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- `docs/team/STATUS.md`
- `docs/team/HANDOFFS/TASK-001-online-agent-workflow-chain-handoff.md`
- 当前 Git branch、commit、tag 和工作区状态；
- RAG 相关源代码、配置、依赖注入/注册、现有测试和可执行入口；
- 当前可用的 trace、日志、检索结果、评测输出或调用堆栈；
- 可读取但不得改写的 Milvus schema、collection 信息及本地索引元数据。

若 TASK-001 Handoff 不存在、起点不唯一或关键参数为 unknown，必须先在本任务 Analysis 中登记阻塞，不得自行补造入口事实。

## 10. Required Investigation Steps
1. 记录调查基线，并逐项核对 TASK-001 Handoff 的起点、调用函数、参数和上下文是否能在当前代码中复现。
2. 从已确认的 `SchemeGenerationUseCase` 调用点向下搜索直接调用边；所有名称匹配结果先标为 candidate。
3. 找到在线 RAG 的实际入口函数，记录调用者、参数对象、配置来源和返回类型。
4. 逐边追踪检索输入的形成过程，包括 Query 原文、改写结果、子查询、假设文档、过滤条件、top-k 和租户/任务上下文。
5. 逐一核实每个检索后端是否在当前入口下可达，记录实际客户端、collection/index、调用方法和返回结构。
6. 追踪多路召回的合并、融合、去重、打分、归一化、重排、阈值过滤和截断顺序；不得仅凭函数名认定算法。
7. 追踪候选 Chunk、Parent Chunk、Document 或其他上下文对象的回填与 metadata 变化。
8. 记录相关性判断、补检索、重试、fallback、空结果、异常和超预算路径及触发条件。
9. 找到 Evidence 的实际创建点，逐字段追踪其值来自哪个上游对象、函数或默认值。
10. 确认 Evidence 的返回值被谁读取、如何传递到后续 Generation，并记录边界，不继续展开生成逻辑。
11. 对所有候选 RAG 实现和后端分类为 active、inactive、legacy、experimental 或 unknown，并提供可核验证据。
12. 至少使用一种运行证据核验关键检索路径；不得通过写入生产/共享索引来制造证据。
13. 输出顺序调用链、逐边证据表、检索阶段表、数据形态变化表、分支表、状态分类表和 unknown 清单。
14. 将算法候选及调用条件交接给 TASK-004，将 Evidence 创建与字段来源交接给 TASK-005。

状态判定口径：

- **active**：从 TASK-001 确认入口可达，并有当前配置或运行证据；
- **inactive**：实现存在，但当前入口不可达，或当前配置明确禁用；
- **legacy**：有明确历史/替代证据，当前主链不可达；
- **experimental**：只在实验、Demo、Benchmark 或专项测试中使用；
- **unknown**：无法以现有证据可靠判断。

## 11. Required Evidence
每一条关键结论必须提供：

- 文件路径；
- 类名、模块名或对象名；
- 函数/方法名；
- 直接调用语句或注册/注入关系；
- 输入参数与返回值对应；
- 相关配置键及其实际取值来源；
- 若涉及 Milvus，提供 collection、字段、过滤、top-k 或调用参数的代码/运行证据。

关键链路还必须尽量提供至少一种运行证据：

- 现有测试及结果；
- 不产生持久副作用的现有执行命令及输出；
- trace/log 中的检索阶段、候选数量、rerank 或 Evidence 记录；
- 可复现调用堆栈或断点记录。

Evidence 字段必须采用“字段 -> 写入函数 -> 上游来源 -> 消费函数”的证据链。仅有类型定义、注释或变量命名不足以证明实际赋值与使用。

## 12. Expected Output File
本任务只允许写以下两个文件：

- Analysis：`docs/team/ANALYSIS/TASK-002-online-rag-chain.md`
- Handoff：`docs/team/HANDOFFS/TASK-002-online-rag-chain-handoff.md`

Analysis 文件至少包含：

- 调查基线与 TASK-001 起点核验；
- `SchemeGenerationUseCase` 到 Evidence 的顺序调用链；
- 逐边证据矩阵；
- 各检索阶段输入/输出及数量变化；
- 检索后端、collection/index 和配置来源；
- 分支、异常、重试、降级和空结果路径；
- Evidence 字段来源与返回消费方；
- active / inactive / legacy / experimental / unknown 清单；
- 未解决问题与证据缺口。

Handoff 文件至少包含：

- 交给 TASK-004 的算法候选、实际调用点、触发条件和证据；
- 交给 TASK-005 的 Evidence 创建点、写入者、读取者、字段来源和生命周期线索；
- 对 TASK-003 有帮助的在线 collection、字段和 metadata 读取要求，但不得反向推断离线写入链；
- 所有尚未确认的 unknown 项。

## 13. Acceptance Criteria
- 起点与 TASK-001 Handoff 完全对应；若不一致，已明确登记而非自行改写事实；
- 终点准确落在 Evidence 创建与返回给明确消费方的边界；
- 在线 RAG 每一条主链调用边均有文件、类/模块、函数和直接证据；
- 检索请求、候选结果和 Evidence 之间的数据变化可逐步追踪；
- 实际检索后端、Milvus collection/index、过滤和 top-k 均有来源证据，无法确认者标记 unknown；
- 合并、融合、去重、重排、过滤、回填和截断的真实顺序已确认；
- 分支、重试、降级、空结果和异常路径已登记；
- 所有实现均区分 active、inactive、legacy、experimental 或 unknown；
- 未把算法类名等同于算法已启用，未评价算法效果；
- 未修改业务代码、配置、测试或索引数据；
- Analysis 与 Handoff 内容一致，可供 TASK-004、TASK-005 和 TASK-006 直接复核；
- 除指定 Analysis 和 Handoff 外没有修改任何文件。

## 14. Handoff Target
- Primary：Algorithm Lead（TASK-004）
- Secondary：Architect（TASK-005）
- Verification：QA / Reviewer（TASK-006）

## 15. Forbidden Actions
- 禁止自行选择或假定 `SchemeGenerationUseCase` 起点；
- 禁止修改业务代码、测试、配置、模型、Milvus schema、collection 或索引数据；
- 禁止执行会写入共享数据库、向量库、缓存或外部服务的命令；
- 禁止为制造运行证据而新增日志、埋点、测试或临时代码；
- 禁止根据目录、类名、函数名、注释或文档描述推断真实调用；
- 禁止把实现存在、导入、注册或实例化直接判定为 active；
- 禁止将 unknown 补写为推测结论；
- 禁止评价架构和算法优劣，禁止提出重构、调参或新增算法方案；
- 禁止顺手修复检索、Evidence 或索引问题；
- 禁止修改 `ARCHITECTURE.md`、`CONTRACTS.md`、`STATUS.md`、任何 TASK 文件及其他任务的 Analysis/Handoff；
- 禁止写入本任务指定 Analysis 与 Handoff 之外的任何文件。
