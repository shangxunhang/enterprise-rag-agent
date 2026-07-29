# 生产主链基线恢复记录（第 0 轮）

日期：2026-07-30

## 范围与结论

本轮只恢复生产链使用的 Self-RAG 生成支持性检查接口，并验证既有
Supervisor / LangGraph 主链。未修改或接入 Multi-Agent V1、Standalone
Runtime，也未重写现有 LangGraph。

接口漂移由提交
`c7171b7c84d23565ef97453a7468b348dfc74236`
（`feat: harden durable LangGraph workflow runtime`，2026-07-28）引入：
该提交将 `backend/rag/judge/rag_quality_judge.py` 改为仅提供检索证据质量
判断的 `RagQualityJudge`，删除了 `SelfRAGJudge` 及其结果类型，但
`backend/apps/enterprise_document/quality/plugins.py` 仍从原模块导入并实例化
`SelfRAGJudge`。

采用方案 A：在独立模块 `backend/rag/judge/self_rag_judge.py` 恢复
Self-RAG 生成支持性检查，插件只更新导入路径；现有 `RagQualityJudge`
保持不变。

## 恢复的接口契约

1. 构造参数：`llm_generator`、`use_llm`、`generation_params`、
   `fallback_to_deterministic`、`noise_terms`、`timer`。
2. 公开方法：`check_answer(*, query, answer, context, citations=None)`。
3. 输入：用户问题、生成文本、证据上下文和可选引用。
4. 输出：`SelfRAGJudgeResult`，包含 `enabled`、`method`、
   `is_supported`、`faithfulness_label`、`answer_relevance_label`、
   `need_rewrite`、`need_retrieve_more`、`unsupported_claims`、
   `problems`、`score`、`latency_ms`、`raw_output`、`metadata`，
   并通过 `to_dict()` 输出同名字段。
5. 模型依赖：可选 `TextGenerator`；无模型时确定性判断，模型调用失败时按
   原契约确定性降级；预算超限和工作流取消继续向上传播。
6. 调用方：`SelfRAGLiteGenerationCheckerPlugin`；其生产消费者会读取
   `is_supported`、`need_rewrite`、`need_retrieve_more` 和 `score`。
7. 相关既有测试位于
   `backend/tests/test_day2_architecture_closure.py`、
   `backend/tests/test_rag_config_driven_quality_plugins.py`、
   `backend/tests/test_rag_config_driven_repair_strategy.py` 及主链验收测试。
8. 仓库中没有完整等价替代：`RagQualityJudge` 判断检索结果质量，
   `faithfulness_proxy` 仅用于离线评估，其他语义或引用门禁也不具备旧输出契约。

不能使用 `SelfRAGJudge = RagQualityJudge` 类名别名：两者职责、构造参数、
输入和输出均不同；别名既不能提供 `check_answer()`，也会把检索质量与生成
支持性检查重新耦合。

## 验证结果

- 顶层生产图导入：
  `.venv\Scripts\python.exe -c "from apps.enterprise_document.workflows.top_level_business_graph import EnterpriseDocumentWorkflowEngine; print('TOP_LEVEL_IMPORT_OK')"`
  返回 `TOP_LEVEL_IMPORT_OK`。
- 新增契约回归：
  `backend/tests/test_self_rag_judge_baseline_recovery.py`
  返回 `6 passed`。
- 指定的五组生产主链测试在仓库内独立临时目录重跑，返回
  `39 passed in 57.62s`。
- 直接无参数执行 `.venv\Scripts\python.exe scripts/run_demo.py` 时，
  非交互终端在读取用户输入处返回 `EOFError`。
- 使用 `--user-input` 和默认输出根执行时，已有
  `data\runs\langgraph_checkpoints.sqlite3` 是旧版自定义 schema，程序按保护
  逻辑拒绝直接复用，未迁移或删除该数据库。
- 使用全新 `.tmp` 输出根、真实运行模式（未使用 `--fake-runtime`）执行后，
  确认进入现有 Supervisor 与顶层 LangGraph；工作流在检索节点因独立的
  `ParentChildChunkResult` 导入问题失败。
- 两条既有 Self-RAG 兼容测试的定向运行同样被
  `ParentChildChunkResult` 在测试收集阶段阻断，未进入测试体。

首次按原命令运行五组主链测试时，系统 pytest 临时目录
`C:\Users\Administrator\AppData\Local\Temp\pytest-of-Administrator`
权限异常，结果为 `18 passed, 21 errors`；改用仓库内 `--basetemp` 后
全部 39 条通过，错误不属于产品断言失败。

## Baseline evidence

| 项目 | 记录 |
| --- | --- |
| 主链入口 | `.venv\Scripts\python.exe scripts/run_demo.py --user-input "请生成企业知识库与RAG平台建设方案" --run-id baseline_recovery_fresh_20260730 --output-root .tmp\mainline-baseline-recovery-20260730` |
| 运行模式 | `real`，未使用 fake runtime |
| task_id | `task_baseline_recovery_fresh_20260730` |
| run_id | `baseline_recovery_fresh_20260730` |
| workflow | `scheme_generation_v2` / `v2.0` |
| checkpoint thread_id | `baseline_recovery_fresh_20260730` |
| workflow namespace | `scheme_generation_v2:v2.0` |
| 最终状态 | `failed` |
| 节点顺序 | `normalize_project_input` → `plan_document` → `retrieve_document_evidence` → `finalize_workflow_failure` |
| checkpoint | `D:\MyCode\rag-agent\.tmp\mainline-baseline-recovery-20260730\runs\langgraph_checkpoints.sqlite3` |
| trace | `D:\MyCode\rag-agent\.tmp\mainline-baseline-recovery-20260730\runs\baseline_recovery_fresh_20260730_trace.jsonl` |
| e2e report | `D:\MyCode\rag-agent\.tmp\mainline-baseline-recovery-20260730\runs\baseline_recovery_fresh_20260730_e2e_report.json` |
| 最终 artifact | 无；失败发生在检索节点，未生成 draft、rejected artifact 或 final deliverable |
| 测试通过数量 | 本轮新增 6 条；指定生产主链 39 条 |
| 尚存告警 | 检索端口导入 `ParentChildChunkResult` 失败；默认 checkpoint DB 为旧 schema；系统 pytest 临时目录权限异常；部分 prompt id 使用弃用重定向 |

运行产生的 checkpoint、trace、报告及其他 `.tmp` 文件仅作为本地证据，
不纳入版本控制。
