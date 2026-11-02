# Enterprise RAG-Agent / Multi-Agent V3

面向企业长文档生成的 Python 工程。项目以 LangGraph 调度顶层业务图和章节子图，
将检索授权、证据绑定、章节生成、Post-write 验证、质量 Gate 与产物生命周期放在独立领域模块中。

## 当前状态（2026-11-02）

| 范围 | 当前结论 |
| --- | --- |
| 八章真实业务 Writer | 已运行；属于历史冻结 Run 的事实，不表示任意输入均可成功 |
| Post-write 第5～7阶段 | 已完成原生闭环；技术闭环与业务质量验收分别判断 |
| 文档质量 Gate | 尚未通过 |
| 当前 V3 第8阶段真实 RunCompletion / Release | 尚未验收 |
| 恢复、质量、性能与交付问题 | 仍有后续计划和开放问题 |

历史 R13 在其冻结源码上曾记录 BEST_EFFORT Release，同时质量 FAIL；
这份历史结果不代表当前源码的正式 Release 验收。项目目前适合展示工程设计、
证据链和问题治理，尚不能宣称已经完成生产质量验收。

## 业务链路与代码入口

调用方提供 ProjectInput；可信应用边界通过独立渠道提供 RetrievalAccessScope。
Supervisor 与业务图组织 FactSnapshot、SectionPlan、Research 和 Writer，
Post-write 执行引用、Claim Support、Faithfulness 和 Consistency 检查。
Gate、RunCompletion、Release 保留各自证据与状态，运行成功不替代质量通过。

| 路径 | 职责 |
| --- | --- |
| `backend/application/` | 主链应用服务与终态视图 |
| `backend/agent/` | Supervisor 与图执行运行时 |
| `backend/apps/enterprise_document/` | 企业文档领域、章节工作流和 Post-write |
| `backend/rag/` | 授权范围、检索、证据与上下文打包 |
| `backend/model_gateway/` | 模型调用边界 |
| `backend/tests/` | 单元、集成、恢复和历史回放测试 |
| `scripts/run_pipeline.py` | 显式 ProjectInput 的主链入口 |
| `scripts/workflow_control.py` | inspect、resume、reject、cancel 和流式操作 |
| `source_bootstrap.py` | 源码导入来源约束；运行脚本和 pytest 均需要 |

## 安装与离线检查

使用 Python 3.10.x 和 `pyproject.toml` 指定的 uv 版本。
依赖声明由 `pyproject.toml` 维护，传递依赖由 `uv.lock` 冻结。
项目固定 uv `0.11.32`；若主机版本不同，可用
`uvx --from uv==0.11.32 uv <command>` 独立运行该版本。

```powershell
uv sync --frozen --extra test --extra providers
uv run --frozen --no-sync python scripts/check_environment.py

$env:RAG_CONFIG_SOURCE_POLICY = 'process_env_defaults_v1'
$env:REAL_PROVIDER_ACCEPTANCE = '0'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
uv run --frozen --no-sync python -B -m pytest -q -p no:cacheprovider `
  backend/tests/test_src_rev_04_citation_association.py `
  backend/tests/test_rag_config_driven_context_packer.py `
  backend/tests/test_fpbca_bcd_source_bootstrap.py `
  backend/tests/test_multi_agent_v3_artifact_runtime.py `
  backend/tests/test_multi_agent_v3_fact_snapshot_runtime.py `
  backend/tests/test_multi_agent_v3_section_plan_runtime.py `
  backend/tests/test_multi_agent_v3_budget_runtime.py `
  backend/tests/test_document_ingestion_text_normalizer.py
```

Windows 上涉及 Word COM / Writer Review Copy 的能力另需
`uv sync --frozen --extra test --extra providers --extra document-ingestion`。
本地向量库、本地模型和 PostgreSQL 分别使用 `local-rag`、`local-model`、
`durable-memory` 可选依赖，并需自行准备对应服务和数据。

这组离线检查只验证选定契约。部分历史回放测试依赖本地冻结归档、真实运行
checkpoint 或 Provider 原始记录；公开 clone 不保证全套 `pytest -q` 可独立复现。
本次发布未重新执行完整 E2E、付费 Provider 或正式 Release。
本地归档依赖逐项列于 [测试依赖清单](docs/publication/LOCAL_ARCHIVE_TESTS.md)。
CI 只执行上述公开离线子集；正常的归档缺失跳过不代表历史回放通过。

## 启动入口

准备 ProjectInput JSON、可信授权范围 JSON、检索数据和模型配置后使用：

```powershell
uv run --frozen --no-sync python scripts/run_pipeline.py `
  --project-input-file <project-input.json> `
  --retrieval-access-scope-file <trusted-scope.json> `
  --output-root <local-output-root>
```

上面的尖括号是需替换的路径占位符。授权范围必须由可信应用边界提供，
不能从用户 ProjectInput 推导。运行入口可能调用实际配置的模型；模型、预算
和数据授权应在实际运行前独立配置。配置模板为 `.env.example`；真实配置和密钥仅保留本地。

## 证据与已知限制

历史离线工程收口报告记录 8,360 PASS、212 FAIL、287 ERROR、23 SKIP、
2 TIMEOUT；这是历史统计，并非本次发布的新测试成绩。
已记录的并行持久化、多 Attempt 故障终态、CLI 交付接线问题仍按原计划处理。
本次仓库维护不修改这些业务行为，也不更改历史审计结论。

运行数据库、checkpoint、原始 Provider 请求/响应、预算完整账本、诊断 JSONL、
模型权重、企业原始资料和重复保存清单应保留在本地归档，不进入公开版本。
工程测试与历史数据回放的边界必须在发布清单中逐项说明。

工程问题记录遵守 `AGENTS.md` 和
`docs/PROJECT_ISSUE_RECORDING_RULE_20260815.md`。

继续阅读：[技术概览](docs/publication/TECHNICAL_OVERVIEW.md)、
[当前验收边界](docs/publication/CURRENT_STATUS.md)、
[问题登记](docs/publication/ISSUE_REGISTER.md)、
[公开快照验证记录](docs/publication/VALIDATION.md)。
