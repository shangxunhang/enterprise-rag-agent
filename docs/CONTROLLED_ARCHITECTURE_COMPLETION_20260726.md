# 受控架构完善说明（2026-07-26）

本文说明依赖、checkpoint、人工审阅、流式执行、LangChain 适配、真实
Provider 验收与安全章节并发的当前生产边界。

## 1. 可复现环境

项目要求 Python 3.10.x。`pyproject.toml` 是唯一人工维护的项目与
extras 依赖声明，`uv.lock` 冻结完整传递依赖。核心组合为：

| 组件 | 版本 |
| --- | --- |
| Python | 3.10.x |
| langgraph | 1.2.9 |
| langgraph-checkpoint | 4.1.1 |
| langgraph-checkpoint-sqlite | 3.1.0 |
| langchain-core | 1.4.9 |
| langchain-openai | 1.3.5 |
| pydantic | 2.13.4 |
| pytest | 9.1.1 |

安装与检查：

```powershell
conda activate enterprise-rag-agent
uv lock --check
uv sync --frozen --extra test --extra providers
uv run --frozen --no-sync python scripts/check_environment.py
uv run --frozen --no-sync python -m pip check
```

CI 和普通应用 extras 不安装 `local-model`，因此不会触碰宿主 CUDA
PyTorch。Milvus 与 sentence-transformers 的声明分别位于 `local-rag`
和 `local-model`；CUDA PyTorch 继续作为宿主运行时前置条件，由环境检查
验证固定版本和 `torch.cuda.is_available()`。不要用普通 uv 同步命令在
宿主 Conda 环境内替换已经验证的 CUDA wheel。

## 2. 官方 SQLite checkpoint

生产组合根创建 `SQLiteCheckpointResource`，其中 saver 仍是官方
`SqliteSaver`。资源对象只拥有 connection/path/close 生命周期，不代理或
复制任何数据库方法。序列化器禁用 pickle fallback，checkpoint 的表结构、
历史、pending writes 和恢复语义均由官方包负责。
`WorkflowNamespaceCheckpointSaver` 只保留本项目的 workflow
id/version namespace 约束。

`SupervisorAgent.close()` 等待 workflow worker idle 后，按
checkpoint connection、RAG、ModelGateway 的顺序释放资源。`close()` 可
重复调用；`weakref.finalize()` 只用于异常兜底。每次 run/inspect/resume/
cancel（包括异常路径）都在应用层 `finally` 中关闭 Supervisor，所以
Windows 文件句柄不依赖 GC 回收。

旧自定义 SQLite 表结构不会被自动覆盖。启动时检测到旧结构会 fail fast，
并提示执行：

```powershell
python scripts/migrate_legacy_checkpoints.py `
  --database <output-root>\<task-id>\runs\langgraph_checkpoints.sqlite3
```

迁移工具把 checkpoint 与 pending writes 重放到新官方数据库，核对数量后
原子替换，并保留带 UTC 时间戳的 `legacy-backup` 文件。迁移前后均不会
启用任意对象反序列化。

## 3. 统一人工审阅协议

`AgentResultSchema` 的规范字段为：

- `human_review_required`
- `human_review_recommended`
- `human_review_status`
- `human_review_request`
- `human_review_decision`

状态为 `not_required | pending | approved | rejected | cancelled`。
`need_human_review` 仅作为兼容投影存在，不再由 Supervisor 无条件写成
true。

应用层接口：

- `inspect_run()`：只读取原 task/run 的 checkpoint 与 interrupt。
- `resume_run(decision=approved|rejected)`：恢复原 run，不创建新 task。
- `cancel_run()`：通过同一 durable graph 写入 cancelled/rejected artifact。
- `stream_resume_run(decision=approved|rejected)`：从原 interrupt 输出恢复事件。
- `stream_cancel_run()`：从原 interrupt 输出取消事件。

恢复前会同时校验 task state、checkpoint GraphState、workflow id/version
和 task_id/run_id。只有 paused 且存在 durable interrupt 的任务可继续。

## 4. 稳定流式协议

`EnterpriseDocumentWorkflowEngine.stream_events()` 消费 LangGraph 的
`tasks`、`updates`、`checkpoints` 与 `custom` stream modes，并映射到
`workflow_stream_event_v1`。原始 LangGraph event 不跨越引擎边界。

每次调用生成独立 `stream_session_id`；事件同时携带 `resume_count` 和
`stream_kind=initial|resume|cancel`。单 session 的 `sequence` 从 1 开始
严格递增，event ID 形如：

```text
run_id:resume_count:stream_session_id:sequence:event_type
```

因此同一 run 跨恢复不会重复。事件还包含 task/run/workflow identity、
node/section、状态、进度、结构化 payload 和结构化 error。覆盖：

- workflow/node/checkpoint 生命周期
- retrieval 生命周期
- section started/generated/repaired/completed
- human review required/resumed
- artifact persisted
- workflow completed/failed/cancelled

Supervisor 的最后事件携带 `AgentResultSchema`，应用层最后事件携带与同步
入口同级的结构化 summary。恢复运行会把暂停前已持久化 usage 作为只读
基线，只累加新会话实际产生的调用。事件来自 LangGraph stream writer，
不解析 stdout。

## 5. LangChain 标准接口

ModelGateway 与 RAG facade 显式实现：

- `invoke` / `ainvoke`
- `stream` / `astream`
- `batch` / `abatch`
- callback、tags、metadata 与 `max_concurrency` 传播

每个逻辑输入只进入底层 domain port 一次；fallback、错误归一化、trace
和 usage ledger 仍由原有 ModelGateway/RAG 边界负责。batch 保持输入顺序。
能力对象明确声明 `stream_final_response=true`、
`token_streaming=false`、`native_async_provider=false`。模型 stream 只产生
一个最终 `ModelResponseSchema`，RAG stream 只产生一个最终
`EvidenceBundleSchema`。这与工作流事件流是三种不同能力：

- 工作流事件流：节点、章节、checkpoint、HITL 和终态事件。
- canonical final-response stream：统一 Runnable 兼容层的一次最终领域对象。
- Provider token stream：本版本未实现。

`ainvoke/astream` 是 LangChain Runnable async compatibility，不承诺原生
异步 Provider I/O。

## 6. 真实 Provider 验收

默认测试不会访问网络。只有显式配置以下变量才执行：

```powershell
$env:REAL_PROVIDER_ACCEPTANCE = "1"
$env:MODEL_PROVIDER = "openai_compatible"
$env:MODEL_API_KEY = "<secret>"
$env:MODEL_BASE_URL = "https://..."
$env:MODEL_NAME = "<provider-model>"
python scripts/run_real_provider_acceptance.py `
  --output-dir data/real_provider_acceptance
```

脚本验证真实同步、异步兼容与 canonical final-response stream、精确
`REAL_PROVIDER_ACCEPTANCE_OK` marker、正 token usage、Provider 原始 metadata、
callbacks、trace 完整闭合、timeout/max_retries 配置、可用性 fallback、
逻辑调用与 Provider attempt 计数，以及错误归一化。
`provider_specific_fields_status` 只报告 `preserved`、
`not_returned_by_provider` 或 `unsupported`，不会把未返回的 reasoning 字段
伪报为已验证。输出、异常和 trace 不包含 API key 或 Authorization header。

默认测试还会启动仅绑定 `127.0.0.1` 的临时 OpenAI-compatible HTTP server，
经真实 `ChatOpenAI -> LangChain adapter -> ModelGateway` 路径确定性覆盖正常
响应、401、429、500、timeout、非法 JSON 和缺少 choices；该测试不访问公网。

## 7. 安全章节并发

默认 `SECTION_MAX_CONCURRENCY=1`，行为与原顺序执行一致。并发还要求
`SectionPlanSchema` 显式满足：

- `can_run_in_parallel=true`
- `requires_global_context=false`
- `depends_on=[]`
- 同一 batch 的 `execution_group` 相同

依赖未知、自依赖、依赖环和不安全并发声明会在 schema 边界拒绝。实际
fan-out 使用 LangGraph `StateGraph`，没有手写 asyncio/thread pool。
临时 fan-out graph 明确不继承父 checkpointer；每个 section 子图仍使用
独立 durable namespace。

并发章节使用隔离 GraphState 与 citation registry，并返回
`ParallelSectionExecutionEnvelopeSchema(result, state_delta)`。delta 只允许
append-only warnings/errors/runtime diagnostics、generated IDs/outputs、
计数器增量与无冲突的 section-scoped metadata。current section、final result、
task/tenant/workflow identity 和其他全局字段一旦变化就 fail fast。

fan-in 按 `section_order, section_id` 确定性合并，重新分配引用、重建 marker
occurrence/binding，并按文档顺序更新 shared state。批次预算在 fan-out 前由
锁保护的 allocator 原子切片，不能超额预留。任一技术分支失败时整批不提交；
取消信号阻止新分支启动并撤销待提交副作用；timeout/cancel 继续进入既有结构化
失败协议。最终 assembly、hard gate、deliverable/rejected 生命周期保持原协议。

## 8. 测试层级

```powershell
# 环境与依赖
uv lock --check
uv sync --frozen --extra test --extra providers
uv run --frozen --no-sync python scripts/check_environment.py
uv run --frozen --no-sync python -m pip check

# 定向架构回归
python -m pytest backend/tests/test_controlled_architecture_completion.py -q
python -m pytest backend/tests/test_phase4_durable_resume_hitl.py -q

# 全量（真实 Provider 默认 skip）
python -m pytest backend/tests -q

# 显式真实 Provider
python -m pytest backend/tests/test_real_provider_acceptance.py -q -m real_provider
```

验收结论必须分层表达：

- 代码完成：实现与契约已落库。
- 测试通过：本地 fake/stub/SQLite/并发回归通过。
- 真实环境通过：仅在显式 Provider、Milvus、模型/CUDA 环境实际运行后声明。
- 暂未验证：缺少凭据、服务或硬件时必须明确列出，不能用 fake 结果替代。
