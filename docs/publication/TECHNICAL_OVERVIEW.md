# Multi-Agent V3 技术概览

日期：2026-11-02。本文根据当前源码和既有工程报告编写，面向代码阅读与工程复现，不构成新的架构审计或真实业务验收。

## 主业务链路

项目围绕企业方案文档生成组织多 Agent 与 RAG：接收结构化项目输入，生成章节计划，检索适用知识，形成正文与引用，再执行章节和文档级校验。质量判断、可交付产物、技术执行结果和 Release 分别保留状态。

```text
ProjectInput
  → MainlineApplicationService
  → SupervisorAgent / 顶层 LangGraph
  → 章节计划与依赖调度
  → SectionExecutionGraph
      Planner → Research / RAG → Writer → Claim / Support / Verifier
  → Post-write 校验与 Control
  → 文档组装、RunCompletion 与 Release 边界
```

箭头表示调用和处理关系，不表示每次执行都能完成全部阶段。预算不足、来源不适用、硬依赖未满足、模型返回不合法或技术故障都可能在相应边界停止。

| 层次 | 职责 | 代码入口 |
| --- | --- | --- |
| 应用与编排 | 接收任务、组织运行、控制恢复与终态投影 | [mainline_service.py](../../backend/application/mainline_service.py)、[supervisor_agent.py](../../backend/agent/supervisor_agent.py) |
| 业务图 | 顶层阶段、章节执行、条件路由与依赖关系 | [top_level_business_graph.py](../../backend/apps/enterprise_document/workflows/top_level_business_graph.py)、[section_execution_graph.py](../../backend/apps/enterprise_document/workflows/section_execution_graph.py) |
| 业务 Agent | Planner 计划、Research 检索、Writer 正文生成、Verifier 验证；跨调用状态由运行时管理 | [agents/agent_rag_v1](../../backend/apps/enterprise_document/agents/agent_rag_v1/) |
| 领域运行时 | Fact、Artifact、Claim、预算、生命周期、校验回执与恢复 | [services/agent_rag_v1](../../backend/apps/enterprise_document/services/agent_rag_v1/) |
| 检索与模型 | 检索范围、候选恢复与重排、上下文打包、模型请求与错误传播 | [backend/rag](../../backend/rag/)、[backend/model_gateway](../../backend/model_gateway/) |

## 事实、证据与验证

结构化项目事实和检索所得证据具有不同来源与适用范围。历史技术参考可以帮助组织内容；它不能自动成为当前项目的已确认事实。Writer 获得受控上下文，引用绑定保存正文与来源之间的身份关系。

Claim、Support 和 VerificationResult 分别承载主张、支持关系和验证结果。存在引用、绑定完成或文件成功落盘，都不能单独证明主张被支持。跨章节使用还受当前性、依赖与验证状态约束。

Post-write 链包含引用 Grounding、Faithfulness、Consistency 和 Requirement Adherence 等校验，并由既有 Gate/Control 决定后续处置。正文、校验结果、技术失败和发布状态保留各自含义；质量 FAIL 不会被“八章均有正文”覆盖。

## RAG 与模型调用边界

RAG 支持配置驱动的查询处理、稀疏/稠密检索、Parent-Child 恢复、重排和上下文筛选。具体组件取决于所选配置及本地资源，不能将源码中存在的插件视为当前运行全部启用。

检索请求包含租户和知识库授权范围；文件或文档范围只能继续缩小检索集合。当前工程未提供完整 IAM/RBAC 产品，调用边界需要提供有效授权范围。

模型调用通过既有 Gateway、客户端和预算运行时执行，保留结构化错误、调用身份与使用量记录。Provider 配置、超时、重试、模型能力和本地资源需要按实际执行合同配置。公开示例不携带凭据，也不保证某个历史 Provider 名称或环境在其他机器上仍可直接使用。

## 持久化、恢复与输出

LangGraph 承担业务图和 Checkpoint 调度；应用运行时拥有领域状态、Artifact、Run/Attempt 生命周期、幂等和最终输出约束。恢复需要精确的任务身份、源码/配置兼容性与可用的持久化材料，不能仅凭相同 HEAD 或产物目录存在推定可恢复。

章节并行有独立约束；当前并行 durability 候选改动尚未完成完整原生验收。默认串行路径和并行能力应分别验证，详见 [当前状态](CURRENT_STATUS.md)。

正式 Release、BEST_EFFORT 产物、未验收审查稿、质量报告与公开终态投影属于不同出口。CLI 导出和故障后的产物可取性仍有已登记的验收缺口，不应承诺所有失败路径都能完整交付正文与审查信息。

## 工程阅读与复现

1. 按仓库 README 安装冻结依赖，并使用配置示例提供本机路径与资源。
2. 从应用入口、顶层图、章节图读到 Agent 与领域运行时，再阅读相关测试。
3. 先运行明确不依赖网络、真实 Provider、Word COM 或历史数据的定向测试。
4. 历史数据相关测试需要维护者另行提供合法、兼容的 Fixture；缺失时应明确跳过或报告前置条件，不能编造历史回执。

完整质量、恢复、性能和 Release 的证据范围见 [当前状态](CURRENT_STATUS.md)；尚未闭合的问题见 [问题登记](ISSUE_REGISTER.md)。
