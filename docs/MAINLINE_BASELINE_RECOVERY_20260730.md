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

## 第 0.5 轮：生产主链成功基线

### 原失败证据与调用链

上轮 trace 的 event sequence 9 是 `rag_finished/failed`，sequence 10 是
`retrieve_document_evidence/failed`，错误类型为 `ImportError`，消息为：

```text
cannot import name 'ParentChildChunkResult'
from 'rag.chunker.ChildParentChunker'
```

该 trace 的 `stack_trace` 字段实际为 `null`。通过同一生产导入路径复现的 traceback
和源码调用关系为：

```text
LangGraph node: retrieve_document_evidence
→ SchemeGenerationStageService.retrieve_document_evidence
→ SchemeEvidenceService.retrieve
→ RAGServicePort
→ TracedRAGService / RAGService
→ RetrievalRuntime.retrieve / initialize
→ ParentChildRuntimeFactory.build
→ rag.registry.default_registrations
→ rag.registry.component_registry
→ rag.ports.__init__
→ rag.ports.chunking
→ rag.chunker.ChildParentChunker（缺少 ParentChildChunkResult）
```

该问题属于代码接口漂移，不是 checkpoint schema、索引缺失、数据输入或可预期业务
失败。`c7171b7` 将 `ChildParentChunker.py` 替换为另一套接口，删除了
`ParentChildChunkResult`、旧构造参数和 `chunk_records()`，但 port、plugin 和测试
仍按原契约调用。仓库中没有等价新类型。

历史契约的定义位置为具体 chunker，字段及构造参数均为
`parents: list[dict]`、`children: list[dict]`。使用方包括 `ChunkerPort`、四种
chunker plugin 和 `OfflineIndexBuilder`；adapter 必须返回可序列化的
`parent_chunk_v1` / `child_chunk_v1`，测试还要求父子 ID 一致、时间确定且重复运行
结果相同。

### 语义修复

- 将 `ParentChildChunkResult` 归属到 `rag.ports.chunking`，并从历史具体模块重新
  导出，避免 port 反向依赖 adapter。
- 为现有 `ChildParentChunker` 补回 plugin 已经依赖的构造参数、
  `chunk_records()` 和带字符偏移的窗口接口；保留现有 `chunk_document(s)`。
- 恢复仍被在线/离线生产模块引用的 chunk、vector-index 和 retrieval-result schema
  兼容契约。没有使用 `Any`、空占位类型或静默 ImportError 降级。
- `c7171b7` 同时删除了在线检索 assessor 使用的 `CRAGJudge`。该能力恢复到独立
  `rag.judge.crag_judge`，旧导入路径只重新导出同一实现；Self-RAG 修复未改写。

修复过程中按真实导入顺序继续暴露并处理了
`RETRIEVAL_RESULT_TEMPLATE`、企业 chunk builder 和 `CRAGJudge` 漂移；这些都发生在
`ParentChildRuntimeFactory.build` 内，属于同一检索节点的直接阻断。

### Provider 边界

真实模式运行
`baseline_success_20260730_05a` 已越过接口漂移，但在 runtime 初始化时因当前
`.venv` 缺少 `sentence_transformers` 失败。环境同时缺少 `torch`、
`transformers` 和 `pymilvus`；active manifest 是真实模型 embedding +
Milvus Lite，因此不能使用 hash query 冒充同一索引的真实召回。

成功 baseline 使用仓库正式支持的 `scripts/run_demo.py --fake-runtime`。替换边界仅为
外部 `FakeRAGService/FakeRAGTool` 和 `fake_llm` provider；仍执行现有 Supervisor、
生产 LangGraph、GraphState、质量门禁、checkpoint、trace 和 artifact 持久化。
未进入 Standalone Runtime 或 Multi-Agent V1。

### 成功 baseline evidence

| 项目 | 记录 |
| --- | --- |
| 入口 | `.venv\Scripts\python.exe scripts/run_demo.py --fake-runtime --user-input "请生成企业知识库与RAG平台建设方案" --run-id baseline_success_20260730_05b --output-root .tmp\mainline-baseline-success-20260730-05b` |
| task_id | `task_baseline_success_20260730_05b` |
| run_id | `baseline_success_20260730_05b` |
| workflow namespace | `scheme_generation_v2:v2.0` |
| checkpoint thread_id | `baseline_success_20260730_05b` |
| 最终状态 | `partial_success`（仓库正式成功类终态） |
| 节点顺序 | `normalize_project_input` → `plan_document` → `retrieve_document_evidence` → `initialize_generation` → `execute_sections` → `assemble_document` → `evaluate_document_gate` → `persist_deliverable` → `finalize_workflow_result` |
| 检索结果 | document retrieval 成功；1 chunk、1 canonical citation |
| sections | 8；3 success、5 partial_success |
| trace | `D:\MyCode\rag-agent\.tmp\mainline-baseline-success-20260730-05b\runs\baseline_success_20260730_05b_trace.jsonl` |
| checkpoint | `D:\MyCode\rag-agent\.tmp\mainline-baseline-success-20260730-05b\runs\langgraph_checkpoints.sqlite3` |
| artifact | `D:\MyCode\rag-agent\.tmp\mainline-baseline-success-20260730-05b\runs\baseline_success_20260730_05b_deliverable.md` |
| e2e report | `D:\MyCode\rag-agent\.tmp\mainline-baseline-success-20260730-05b\runs\baseline_success_20260730_05b_e2e_report.json` |
| trace events | 98 |
| artifact lifecycle | `generated_draft=true`、`deliverable_final_result=true` |

最终报告可读取 8 个 section 的状态、citation count 和 evidence scope；`建设内容`、
`技术方案`、`安全设计` 各绑定 canonical citation `C1`。Hard Gate 通过，五个章节
因确定性 fixture 的 Self-RAG 告警形成 `partial_success`，没有吞掉技术异常或强制
改写终态。

### 验证矩阵

- 顶层生产图 import：`TOP_LEVEL_IMPORT_OK`。
- Self-RAG baseline recovery：`6 passed`。
- 原先被阻断的两条定向测试：
  - `test_quality_checker_exposes_deterministic_primary_without_degradation`
  - `test_self_rag_llm_failure_exposes_degraded_deterministic_fallback`
  - 结果：`2 passed`。
- ParentChild/offline index 全文件：`18 passed`。
- 新增 production retrieval baseline 回归：`4 passed`。
- 检索节点直接相关五个测试文件：`49 passed`。
- 指定五组生产主链测试：`39 passed in 58.32s`。

默认旧 checkpoint 数据库仍由现有 runtime 以明确的 legacy schema 错误拒绝；本轮
未删除、覆盖或迁移它。成功 baseline 使用上述全新 `.tmp` checkpoint。
