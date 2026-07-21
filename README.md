# Enterprise RAG-Agent

企业级 Agent-RAG 模块化单体项目。

当前版本：

```text
stage1-mainline-stability-v7.9.0-20260718
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
pip install -r requirements-step-11-2.txt

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

原生 Agent-RAG 主链已经完成稳定性验收。下一阶段先进行完整架构、Schema、状态流、错误传播和历史代码梳理，再决定 LangGraph 迁移。

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
