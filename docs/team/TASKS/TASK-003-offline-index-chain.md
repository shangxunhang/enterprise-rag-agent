# TASK-003 Offline Index Chain

## 1. Task ID
TASK-003

## 2. Title
还原原始数据或 `CleanedTextUnit` 到 Chunk、Embedding、Milvus 的离线索引真实调用链

## 3. Owner Role
Data Engineer

## 4. Current Status
draft

## 5. Objective
基于当前代码基线与可复核证据，还原离线数据从真实入口进入后，经过读取/清洗边界、Chunk 生成、Embedding 计算、记录组装并写入 Milvus 的完整 As-Is 索引链。

本任务必须回答：

- 当前离线索引链的真实可执行入口是什么；
- 实际支持的输入是原始文档、`CleanedTextUnit`，还是其他对象；
- Chunk 如何创建、切分、关联父子关系并获得 ID/metadata；
- Embedding 模型、调用方式、批次和向量维度从何处确定；
- Milvus collection、schema、字段映射、写入方式和重复写入语义是什么；
- 错误、跳过、重试、断点续传或部分成功如何处理；
- 哪些实现属于 active、inactive、legacy、experimental 或 unknown。

## 6. Background
项目离线索引链定义为：

`Raw Document / CleanedTextUnit -> Chunk -> Embedding -> Vector Database`

在线 RAG 能否正确检索取决于离线链实际写入的数据形态，但本任务不得根据在线读取代码反向假定离线写入行为。TASK-002 可提供当前在线读取的 collection、字段和 metadata 需求作为核对线索；TASK-004 将依据本任务证据确认离线阶段是否存在算法性处理；TASK-005 将使用本任务结果核实相关状态与 Contract 的创建和写入责任。

## 7. Investigation Scope
包含：

- 当前可执行的离线 CLI、脚本、Job、Pipeline、服务入口或测试入口；
- 原始文件、解析产物、清洗产物和 `CleanedTextUnit` 等候选输入的真实使用状态；
- 文档发现、读取、解析、过滤、清洗边界，但只追踪与索引链直接相关的调用；
- Chunk 策略、长度/重叠参数、父子块关系、顺序和边界处理；
- Chunk ID、Document ID、Parent ID、source、page、section 等 metadata 的创建来源；
- Embedding 客户端、模型路径/名称、设备、批次、归一化、维度及异常处理；
- Milvus 连接、database、collection、schema、索引配置和字段映射；
- insert、upsert、delete-before-insert、flush、load 等实际调用及条件；
- 批处理、重复数据、幂等性、断点、重试、失败记录和统计输出；
- 中间 JSON/JSONL/缓存/数据库产物是否为真实链路节点；
- 同名、旧版、Demo、实验和未接入实现的状态分类。

调查起点：被证明确实启动当前索引流程的入口，以及该入口实际接收的最早输入对象。

调查终点：向 Milvus 发出实际写入并确认写入结果处理完成。在线查询和 Evidence 构造不属于本任务。

## 8. Explicit Non-goals
- 不调查在线 Query、Agent、Workflow、RAG 检索和 Evidence 生成；
- 不评价 Chunk、Embedding 或 Milvus 方案是否合理；
- 不比较模型质量、召回效果或切分策略优劣；
- 不提出数据治理、Schema 重构、增量索引、缓存或性能优化方案；
- 不重新清洗或重新索引真实数据；
- 不写入、删除或迁移 Milvus 数据；
- 不修改业务代码、配置、脚本、测试或依赖；
- 不更新架构汇总文件和其他任务文件。

## 9. Required Inputs
- `docs/team/PROJECT.md`
- `docs/team/ORGANIZATION.md`
- `docs/team/WORKFLOW.md`
- `docs/team/POLICIES.md`
- `docs/team/STATUS.md`
- 当前 Git branch、commit、tag 和工作区状态；
- 离线索引相关源代码、配置、脚本、依赖注入、现有测试和运行日志；
- 可读取的样例输入、已有中间产物和索引统计；
- 可读取但不得更改的 Milvus schema、collection 元数据和记录样例；
- 若已完成，`docs/team/HANDOFFS/TASK-002-online-rag-chain-handoff.md` 中关于在线字段读取的核对线索。

## 10. Required Investigation Steps
1. 记录 branch、commit、tag、工作区状态及现有离线测试/运行基线；缺失项标记 unknown。
2. 搜索所有 Chunk、Embedding、Milvus 写入和 `CleanedTextUnit` 相关定义、调用和脚本；先建立 candidate 清单。
3. 通过启动命令、脚本调用关系、任务注册、现有测试或日志确认当前真实离线入口，不得以文件名判定。
4. 从入口追踪实际输入来源，确认最早可证实对象是 Raw Document、解析产物、`CleanedTextUnit` 或其他类型。
5. 逐调用边追踪清洗边界到 Chunk 创建，记录切分函数、参数来源、输出数量和边界条件。
6. 逐字段追踪 Chunk 的 ID、文本、父子关系、source、page、section、offset 和其他 metadata 来源。
7. 找到 Embedding 的真实调用点，确认模型、设备、批次、归一化、向量维度和返回结构的来源。
8. 追踪 Chunk/Embedding 如何组装成 Milvus 写入记录，并建立字段映射表。
9. 找到真实 Milvus 客户端调用，确认 database、collection、schema、insert/upsert 等方法及其配置来源。
10. 记录批次、重复处理、幂等语义、flush/load、异常、重试、跳过、部分成功和统计路径。
11. 对已有 JSON/JSONL、缓存或中间数据库，核实它们是主链节点、旁路产物还是实验产物。
12. 对所有候选实现分类为 active、inactive、legacy、experimental 或 unknown，并提供证据。
13. 优先使用已有日志、测试或只读检查形成运行证据；不得通过向共享 Milvus 写入新数据来验证。
14. 输出顺序调用链、逐边证据表、数据形态表、字段映射表、配置表、状态分类和 unknown 清单。
15. 将算法性处理线索交接给 TASK-004，将状态/Contract 创建和写入线索交接给 TASK-005。

状态判定口径：

- **active**：由当前可执行离线入口可达，并有调用或运行证据；
- **inactive**：实现存在，但当前入口不可达或配置关闭；
- **legacy**：有明确替代/历史证据，当前链不再使用；
- **experimental**：仅用于 Demo、实验、Benchmark 或专项测试；
- **unknown**：现有证据不足以判定。

## 11. Required Evidence
每条关键调用边必须包含：

- 文件路径；
- 类名、模块名或脚本名；
- 函数/方法名；
- 直接调用语句；
- 输入类型与输出类型；
- 参数及配置来源；
- 对应的数量、字段或状态变化。

关键事实还必须尽量提供至少一种非命名证据：

- 现有测试名称及结果；
- 现有离线运行日志或统计输出；
- 已生成中间文件的字段样例与其生产函数对应；
- Milvus 只读 schema/collection/记录样例；
- 可复现但不产生持久写入的调用堆栈。

Milvus 字段映射必须采用“源字段 -> 转换函数 -> 写入字段 -> 在线读取线索”的形式记录。在线读取线索只能用于一致性核对，不能证明离线写入事实。

## 12. Expected Output File
本任务只允许写以下两个文件：

- Analysis：`docs/team/ANALYSIS/TASK-003-offline-index-chain.md`
- Handoff：`docs/team/HANDOFFS/TASK-003-offline-index-chain-handoff.md`

Analysis 文件至少包含：

- 调查基线与真实离线入口；
- Raw Document / `CleanedTextUnit` 到 Milvus 的顺序调用链；
- 逐边证据矩阵；
- 各阶段输入/输出类型和数量变化；
- Chunk 参数及字段来源；
- Embedding 模型、设备、批次和维度来源；
- Milvus schema、collection、字段映射和写入语义；
- 错误、重试、跳过、重复和部分成功路径；
- active / inactive / legacy / experimental / unknown 清单；
- 未解决问题与证据缺口。

Handoff 文件至少包含：

- 交给 TASK-004 的离线算法候选、调用点、条件和证据；
- 交给 TASK-005 的对象创建者、字段写入者、状态变化和序列化线索；
- 与 TASK-002 在线读取字段的已证实一致项、冲突项和 unknown 项；
- 不得由下游当作事实的未确认内容。

## 13. Acceptance Criteria
- 真实离线入口由执行、注册、测试或日志证据确认，而非按脚本名推断；
- 最早输入对象与最终 Milvus 写入调用均有明确边界；
- 每一条主链调用边都有文件、类/模块/脚本、函数和直接证据；
- Chunk 的文本、ID、父子关系和关键 metadata 均可追踪来源；
- Embedding 模型、设备、批次、归一化和向量维度均有配置或运行证据；
- Milvus collection、schema、字段映射和写入方法均已确认，无法确认者标记 unknown；
- 重复处理、错误、重试、跳过和部分成功路径已登记；
- 中间产物已区分主链、旁路、legacy 或 experimental；
- 所有候选实现均区分 active、inactive、legacy、experimental 或 unknown；
- 未执行真实重建索引，未修改代码、配置、测试或 Milvus 数据；
- Analysis 与 Handoff 可供 TASK-004、TASK-005 和 TASK-006 直接复核；
- 除指定 Analysis 和 Handoff 外没有修改任何文件。

## 14. Handoff Target
- Primary：Algorithm Lead（TASK-004）
- Secondary：Architect（TASK-005）
- Verification：QA / Reviewer（TASK-006）

## 15. Forbidden Actions
- 禁止修改业务代码、脚本、测试、配置、依赖或数据文件；
- 禁止执行会新增、更新、删除或重建 Milvus collection/index/record 的命令；
- 禁止重新处理真实数据、生成新 Embedding 或覆盖现有中间产物；
- 禁止为证明链路而新增日志、埋点、测试或临时代码；
- 禁止根据目录、类名、脚本名、注释或 README 推断执行行为；
- 禁止由在线读取字段反向假定离线写入实现；
- 禁止把实现存在、导入或实例化直接判定为 active；
- 禁止将 unknown 写成推测结论；
- 禁止评价方案优劣或提出重构、调参和性能优化方案；
- 禁止顺手修复清洗、Chunk、Embedding 或入库问题；
- 禁止修改 `ARCHITECTURE.md`、`CONTRACTS.md`、`STATUS.md`、任何 TASK 文件及其他任务的 Analysis/Handoff；
- 禁止写入本任务指定 Analysis 与 Handoff 之外的任何文件。
