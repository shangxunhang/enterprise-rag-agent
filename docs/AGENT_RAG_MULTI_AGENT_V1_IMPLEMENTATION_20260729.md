# 企业建设方案 Agent-RAG Multi-Agent V1 首轮实现说明

日期：2026-07-29
规格基线：用户提供的《企业建设方案 Agent-RAG Multi-Agent V1 架构规格 v1.0》

## 1. 本轮结论

本轮已按规格的 Phase 1 至 Phase 7 建立一套旁路、强类型、可独立验收的
`agent_rag_v1` 架构组件，并保留现有 `SupervisorAgent` 和生产工作流主链。

当前状态必须分层理解：

- 组件实现：已完成首轮。
- 专项契约和行为验收：已通过。
- 接入现有 LangGraph/Supervisor 生产主链：尚未切换。
- 真实模型、真实 RAG、真实 Excel/Word 端到端运行：本轮未执行。

因此，本轮成果是可集成的 V1 架构底座，不等同于已上线的新主链。

## 2. 代码边界

新实现集中在三个旁路包：

```text
backend/apps/enterprise_document/
├── schemas/agent_rag_v1/
├── services/agent_rag_v1/
└── agents/agent_rag_v1/
```

现有 `SharedState`、`SupervisorAgent`、LangGraph Checkpointer 和
`SchemeWriterStageService` 没有被替换。`SupervisorStateAdapter` 只负责从现有
`DocumentPlanSchema` 初始化新的 `MultiAgentSharedStateSchema`，避免出现第二套
Supervisor。

## 3. Phase 交付矩阵

| Phase | 已实现内容 | 主要落点 |
| --- | --- | --- |
| 1 | 强类型 Agent 输入/输出、Tool Result、Workflow Error、Artifact Reference、字段级 Patch、新 Shared State 与五类 Section 子域 | `schemas/agent_rag_v1/` |
| 2 | StateGateway、写权限、状态迁移、Domain Event、不可变 Artifact、Tool 结果存储、原子预算预留、Checkpoint | `services/agent_rag_v1/state_gateway.py` 等 |
| 3 | Router 11 级优先级、Section DAG、并行组聚合、最小恢复入口、影响分析、版本策略、Supervisor 适配 | `workflow_router.py`、`dag_scheduler.py` 等 |
| 4 | Planner/Research/Writer/Verifier 无状态执行壳、强类型校验、授权引用、ToolGateway 权限和幂等边界 | `agents/agent_rag_v1/`、`tool_gateway.py` |
| 5 | Paragraph 流式落盘、稳定 ID、逐段 Claim、Excel 硬事实绑定、预检查熔断、修订 Diff 与越界校验 | `streaming_writer.py`、`claim_pipeline.py` 等 |
| 6 | 跨章节验证、串行冲突修复、TaskStatus、Generation Report、全状态导出、装配校验、最终成品复核、不可变版本发布 | `document_verifier.py`、`document_assembler_v1.py` 等 |
| 7 | Checkpoint 加事件重放、running 调用条件恢复、stale 丢弃、Human Review 最小恢复、draining 停止新调用 | `event_replay_recovery.py`、`running_invocation_recovery.py` 等 |

## 4. 已锁定的关键语义

- Excel/FactSnapshot 是权威事实源；数量、型号、规格、金额冲突在 Paragraph
  预检查阶段阻断。
- Section 是业务执行与恢复单元；Paragraph 是生成、落盘、Claim 抽取和修订原子。
- Agent 只能返回强类型 Payload、Artifact Reference 和字段级 Patch，不能直接覆盖
  Shared State。
- StateGateway 校验 task/run、Snapshot、Execution、Attempt、权限、Schema、状态迁移
  和领域版本；成功提交生成顺序 Domain Event。
- 不同 Section、不同子域独立版本化；同域旧版本写入被拒绝。
- 大文本和工具原始输出通过不可变 Artifact 引用传递。
- 并发启动前原子预留 Task 预算；耗尽后进入 draining，只允许在途结算、读取、
  聚合和导出。
- `partial_success` 和 `failed` 仍输出保留模板骨架、失败占位和用户说明附录的文档，
  同时输出结构化 Generation Report。
- 文档版本不可覆盖；初版、minor 和 major 版本由统一版本策略生成。
- 崩溃恢复使用最新 Checkpoint 加增量事件重放；running 调用只允许查原调用、
  恢复监控、补事件、读取原结果或使用同一幂等键重试。

## 5. 专项验收

执行命令：

```powershell
.venv\Scripts\python.exe -m pytest `
  backend/tests/test_agent_rag_v1_contracts.py `
  backend/tests/test_agent_rag_v1_infrastructure.py `
  backend/tests/test_agent_rag_v1_workflow.py `
  backend/tests/test_agent_rag_v1_agents.py `
  backend/tests/test_agent_rag_v1_writer_claims.py `
  backend/tests/test_agent_rag_v1_document_closure.py `
  backend/tests/test_agent_rag_v1_recovery.py `
  -q -p no:cacheprovider `
  --basetemp .tmp\pytest-agent-rag-v1-phase7
```

结果：

```text
70 passed
```

覆盖范围包括规格第 36 章要求的 Excel 权威性、并行隔离、旧结果拒绝、
Paragraph 硬错误熔断、增量 Claim、越界修订、跨章节冲突、Partial Success、
Failed、Human Review、版本、预算耗尽、崩溃恢复、装配错误和最终正文问题路由。

## 6. 已知边界与下一集成阶段

后续应以小步接入方式完成主链切换，不能直接一次性替换现有工作流：

1. 在现有 Supervisor 创建输入快照后，用 `SupervisorStateAdapter` 建立 V1
   Shared State 和初始 Checkpoint。
2. 先把 Research/Writer 的工具访问切到 `ToolGateway`，保持现有模型与 RAG
   Provider 不变。
3. 把单个低风险 Section 接到 V1 Router、StateGateway 和流式 Writer，做 shadow
   run，对比现有输出和 V1 输出。
4. 接入 Document Verify、Assembler、Generation Report 与版本发布。
5. 最后再扩大到全部 Section DAG，并补真实 Excel、真实材料、真实模型和 Word
   导出的端到端验收。

仓库全量测试收集当前仍存在一个本轮之前就存在的阻塞：

```text
ImportError: cannot import name 'SelfRAGJudge'
from 'rag.judge.rag_quality_judge'
```

该错误经 `test_architecture_hardening.py` 的导入路径触发，不属于本轮
`agent_rag_v1` 变更；本轮没有越权顺手修改。
