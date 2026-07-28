# Enterprise RAG-Agent

企业级 Agent-RAG 模块化单体项目。

当前版本：

```text
stage1-mainline-stability-v7.9.0-20260718
```

## 受控架构运行基线（2026-07-26）

- Python 固定为 3.10.x；`pyproject.toml` 是唯一人工维护的依赖声明，
  `uv.lock` 冻结完整传递依赖。
- LangGraph checkpoint 使用官方 `langgraph-checkpoint-sqlite`；旧自定义
  SQLite 文件必须先运行 `scripts/migrate_legacy_checkpoints.py`。项目只
  显式持有和关闭 connection，不复制官方 checkpoint 协议。
- 人工审阅使用统一的 required/recommended/status/request/decision 协议，
  应用层支持 inspect、同步/流式 resume、reject 和 cancel。
- 首次执行与跨实例恢复都输出 `workflow_stream_event_v1`。每次会话具有
  独立 `stream_session_id`，并携带 `resume_count`、`stream_kind`；
  event ID 可跨 initial/resume/cancel 安全去重。
- 章节并发默认 `SECTION_MAX_CONCURRENCY=1`；只有显式声明无依赖、
  `can_run_in_parallel=true` 且 `requires_global_context=false` 的章节才会
  通过 LangGraph 并发调度。分支只返回声明过的 additive state delta，
  主线程按 `section_order, section_id` 确定性合并。
- LangChain `stream/astream` 是 canonical 最终领域对象兼容接口，不是
  Provider 原生 token stream；异步接口也不代表原生异步 Provider I/O。

推荐安装命令：

```powershell
conda activate enterprise-rag-agent
uv sync --frozen --extra test --extra providers
uv run --frozen --no-sync python scripts/check_environment.py
```

人工审阅与流式命令：

```powershell
python scripts/workflow_control.py inspect --task-id <task> --run-id <run> --output-root data
python scripts/workflow_control.py resume --task-id <task> --run-id <run> --output-root data --decision approved
python scripts/workflow_control.py cancel --task-id <task> --run-id <run> --output-root data --reason "request withdrawn"
python scripts/workflow_control.py stream --run-id <run> --output-root data --user-input "生成企业方案" --allow-demo-defaults
python scripts/workflow_control.py stream-resume --task-id <task> --run-id <run> --output-root data --decision approved
python scripts/workflow_control.py stream-cancel --task-id <task> --run-id <run> --output-root data --reason "request withdrawn"
```

完整说明见
[`docs/CONTROLLED_ARCHITECTURE_COMPLETION_20260726.md`](docs/CONTROLLED_ARCHITECTURE_COMPLETION_20260726.md)。

当前能力
ProjectInput 标准化
Supervisor Agent 与 Workflow 路由
原生 GraphState Workflow Engine
混合检索、Rerank、Parent-Child Retrieval
RAG-Fusion、HyDE、Self-RAG、Corrective RAG
分章节方案生成
引用绑定与证据恢复
Context Manager
Hard Gate 与结构化错误传播
Trace、评测和后训练数据捕获
本地 Qwen2.5 模型接入
项目结构
backend/
  agent/               Agent 与 Workflow Runtime
  application/         主链应用服务
  apps/                企业文档业务场景
  context_manager/     上下文管理
  contracts/           系统接口契约
  data_capture/        运行与训练数据捕获
  eval/                Agent 与 RAG 评测
  model_gateway/       模型网关
  observability/       Trace 与可观测性
  rag/                 RAG 检索主链
  schemas/             公共 Schema
  tests/               单元与回归测试

scripts/               运行、验收、索引和评测脚本
prompts/               Prompt 模板
data/eval/             评测数据
data/examples/         示例输入
docs/                  阶段设计和验收文档
环境准备
conda activate enterprise-rag-agent
uv sync --frozen --extra test --extra providers
uv run --frozen --no-sync python scripts/check_environment.py

复制环境变量示例：

Copy-Item .env.example .env

然后修改 .env 中的本地模型路径。

运行测试
pytest -q
主链闭环验收
python scripts/run_mainline_closure_acceptance.py
运行 Demo
python scripts/run_demo.py
数据说明

以下内容不会提交到 Git：

本地模型权重
Milvus 本地索引
运行 Trace
TaskState
Data Capture 运行产物
真实业务文档
.env 和真实密钥
当前阶段

Agent-RAG 主链已使用 LangGraph 作为顶层业务图、durable checkpoint 与
章节子图运行时；领域协议、质量门禁和产物生命周期仍由应用代码拥有。

架构治理约束

1. Retrieval Access Scope
   tenant_id 与 authorized_kb_ids 是 mandatory scope；file_id/doc_id 只能进一步缩小范围。
   Dense 与 BM25 必须执行同一有效 scope。当前未建设 IAM/RBAC，授权范围由应用边界提供；未来由 AuthContext/PermissionService 生成同一契约。

2. DATA_ROOT
   DATA_ROOT 是运行数据唯一可配置根目录。tasks/runs/captures/eval_outputs/runtime 等目录全部从 DATA_ROOT 派生，不再作为独立环境变量配置。

3. graph_revision
   graph_revision 只表示经过 StateWriteContract 校验并成功提交的业务 GraphStateDelta 序号，不是整个 GraphState 的全局快照版本。未来引入 checkpoint/resume/exact replay 时再评估 BusinessState/RuntimeState 分离。

4. Compatibility State
   context_bundle 是新业务状态的 canonical source。兼容字段只允许 Canonical -> Compatibility 单向 projection；新生产代码禁止直接新增 state.contexts[...] 写入，也禁止用 compatibility 数据反向覆盖 canonical state。

迁移路线：新代码只写 canonical -> 旧代码暂时可读 compatibility -> compatibility 收敛为纯 projection -> 最终删除 legacy aliases。

<!-- DEVSPACE_WRITE_TEST -->
