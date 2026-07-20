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

<!-- DEVSPACE_WRITE_TEST -->
