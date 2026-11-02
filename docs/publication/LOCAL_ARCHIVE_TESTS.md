# 公开快照的本地归档与测试前置条件

日期：2026-11-02。机器清单见 [LOCAL_ARCHIVE_TESTS.json](LOCAL_ARCHIVE_TESTS.json)。

本快照保留全部正式测试源码，真实历史输入、模型请求/响应、checkpoint、数据库、业务文档与本机 RAG 数据继续在原本地位置保留。保留测试源码不表示公开 clone 可以直接运行全量 `pytest`。本清单记录测试的外部前置条件。隔离发布副本对三个测试文件做了敏感配置/文件名/project identity 外置，并明确缺少原始值时 skip；没有替换原始输入，也不将缺失资源改为成功。原工作区测试字节保持原样。

本清单由源码与静态路径分析形成，扫描 `backend/tests` 下 462 个 Python 文件，解析出 19 个直接 docs 文件依赖，共 85,837,715 字节；这些是覆盖下限。本文生成过程没有运行测试、读取 `.env`、调用模型或启动历史 Run。测试可被收集、fixture 可执行、断言通过及历史验收成立是不同结论。除明确列出的 skip 行为外，不推断缺少资源会自动跳过。

## 公开执行与本地回放的边界

- **requires-history**：原始任务输入、历史收据、业务正文、模型回复、checkpoint、Gold 或冻结运行环境；只能由持有原始授权归档的本地开发者执行对应节点。
- **catalog**：真实文档清单、profile 和解析正文，保持本地；与纯工程 evaluator catalog 分开。
- **native-RAG**：原生冻结索引 manifest，以及该 manifest 动态指向的资源，公开快照不伪造这些输入。
- **technical/synthetic fixture**：小型技术源码、契约或明确合成样例；能否纳入以发布白名单和安全检查为准。历史文件身份仍按原测试要求校验。
- **local-service / native fixture**：数据库、桌面应用、原生驱动、固定 NTFS 保护根或明确启用的验证插件；不等同私有历史数据。

多数已检查的历史文件是在 fixture/setup/测试函数内读取；它们缺失时，模块可能仍可收集，对应节点会在运行阶段失败。本文未独立证明完整 clean-clone collection。混合模块内的合成测试可能可独立执行；不要仅凭模块名、`Fake`、`offline` 或 `native` 字样推断整模块前置条件。

## 19 个直接 docs fixture

下表只写文件名。JSON 为每项保留完整仓库相对路径、源文件大小、引用模块及源码行号，不包含任何真实正文或凭据。最终装配已纳入 DOC-04/DOC-05 两个公开合成文件，其余17个识别到的 docs fixture 继续排除并保留本地。真实历史正文不因测试依赖自动公开。

| ID | 文件名 | 源字节数 | 类型 | 快照 |
|---|---|---:|---|---|
| DOC-01 | `03_D_VERIFICATION_DESIGN_V0_2.md` | 7,999 | technical-fixture | 本地保留 |
| DOC-02 | `nonclock_n_source_blocker_02.json` | 115,820 | requires-history | 本地保留 |
| DOC-03 | `final_campaign_evidence_recorder.py` | 30,785 | explicit-harness | 本地保留 |
| DOC-04 | `01_COVERAGE_MATRIX.json` | 21,290 | synthetic-fixture | 已纳入合成 fixture |
| DOC-05 | `02_SYNTHETIC_FACT_SCENARIOS.json` | 13,333 | synthetic-fixture | 已纳入合成 fixture |
| DOC-06 | `01_CH1_METHOD_CONFIG.json` | 2,854 | technical-fixture | 本地保留 |
| DOC-07 | `real_business_worker.py` | 21,356 | requires-history | 本地保留 |
| DOC-08 | `12_PROFILE_INVENTORY_READONLY.json` | 144,126 | requires-history | 本地保留 |
| DOC-09 | `settlement_oracle_a.py` | 4,596 | technical-fixture | 本地保留 |
| DOC-10 | `before_config.py` | 26,425 | technical-fixture | 本地保留 |
| DOC-11 | `EXACT_VALIDATE_DOCUMENT_ASSEMBLY_PREDECESSOR.json` | 76,462,510 | requires-history | 本地保留 |
| DOC-12 | `PLAN.json` | 3,107 | requires-history | 本地保留 |
| DOC-13 | `PROJECT_INPUT_V3.json` | 2,846 | requires-history | 本地保留 |
| DOC-14 | `REAL_E2E_POLICY.json` | 1,152 | requires-history | 本地保留 |
| DOC-15 | `phase_a_probe.py` | 11,081 | technical-fixture | 本地保留 |
| DOC-16 | `10_HISTORICAL_CANONICAL_FACTS.json` | 8,629,934 | requires-history | 本地保留 |
| DOC-17 | `14_TEST_ROOT.json` | 269 | local-fixture-locator | 本地保留 |
| DOC-18 | `consistency_v1.py.txt` | 42,347 | technical-fixture | 本地保留 |
| DOC-19 | `historical_fixed_input.json` | 295,885 | requires-history | 本地保留 |

特别边界：DOC-11 是 76.5 MB 的真实 checkpoint 导出，包含业务/context/model 文本，相关测试还要求导出内部 `source_db` 所指向的原始只读 SQLite 数据库；DOC-16 是 8.6 MB 历史 canonical/SQLite capture，包含编码 payload；DOC-19 保存历史原始模型回复。三者不能仅因测试依赖而公开。

DOC-04/DOC-05 是明确的本地合成覆盖矩阵与场景。DOC-09/DOC-15/DOC-18 等代码 fixture 用于 AST 提取或旧算法比较，保留它们不代表重新执行、恢复或认证历史系统。DOC-03 属于显式验证插件依赖，普通 pytest 启动并不自动加载该插件。

## 受上述依赖影响的模块

资源 ID 的完整路径和原因见 JSON。`PUBLIC-EVALUATOR-CATALOG` 已作为纯工程数据保留；该行不表示需要私有归档。

| 模块 | 资源 ID |
|---|---|
| `fpbca_current_verification_case_mapping.py` | DOC-03 |
| `test_ch1_writer_minimal_section_method_20261012.py` | DOC-06 |
| `test_deterministic_review_report_contract_20261017.py` | DOC-16, DOC-17 |
| `test_formal_guard_v2_matrix_20261018.py` | DOC-18, DOC-19 |
| `test_fpbca_bcd_rec_sv_native_measurements.py` | DOC-01 |
| `test_fpbca_budget_graph_native_replays.py` | DOC-02 |
| `test_fpbca_e2e_semantic_convergence_verifier_20261009.py` | HISTORY-CH1-112450 |
| `test_fpbca_e2e_single_section_target_fake_20261009.py` | HISTORY-CH1-112450 |
| `test_fpbca_real_ch1_fce_x14_fact_basis_20261009.py` | HISTORY-FCE-CH1 |
| `test_fpbca_real_e2e_offline_section_contract_20261009.py` | HISTORY-CH1-064923 |
| `test_fpbca_v6_indeterminate_verifier_real_badcase_20261009.py` | HISTORY-CH1-112450 |
| `test_full8_business_coverage_contract_20261028.py` | DOC-04, DOC-05, HISTORY-GOLD |
| `test_full8_fake_business_synth_matrix_20261028.py` | DOC-04 |
| `test_full_chain_eval_core.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_correction_iia_ingress_fanin.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_correction_iia_ir.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_correction_iia_memory.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_correction_iia_native_remaining.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_correction_iia_package.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_correction_iia_references.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_iia_native_pw.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_integration.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_full_chain_eval_runtime_iia_correction.py` | PUBLIC-EVALUATOR-CATALOG |
| `test_historical_fixture_locator.py` | HISTORY-ATTEMPT4 |
| `test_local_verifier_recovery_composition.py` | DOC-09 |
| `test_local_verifier_recovery_integration.py` | HISTORY-ATTEMPT4 |
| `test_post_phase1_full8_launcher.py` | DOC-07, DOC-10, DOC-12, DOC-13, DOC-14, HISTORY-OVERLAY-SOURCE, NATIVE-RAG-FULL8 |
| `test_postwrite_a_minimal_instruction.py` | HISTORY-ISOLATION |
| `test_pwa004_native_full8_fake_e2e.py` | DOC-07 |
| `test_rag_lost_in_middle_priority_admission.py` | DOC-15 |
| `test_rc1_ch1_consistency_correction_v2_guards.py` | DOC-08 |
| `test_sdc_stage2_guard_v2_identity_20261018.py` | DOC-18 |
| `test_stage_b_document_catalog.py` | PRIVATE-DOCUMENT-CATALOG |
| `test_stage_c_profile_aware_indexing.py` | PRIVATE-DOCUMENT-CATALOG |
| `test_stage_c_profile_lineage.py` | PRIVATE-DOCUMENT-CATALOG |
| `test_writer_review_copy_snapshot_set_order_20261020.py` | DOC-11 |

重要的节点范围：

- `test_local_verifier_recovery_integration.py` 的两个原始 Attempt4 节点依赖外部八文件归档；同模块其余新建合成 owner 测试不能因此一概归为不可运行。
- `test_historical_fixture_locator.py` 的 canonical 原始八文件节点依赖归档，其 synthetic refusal/relocation controls 是另一层证据。
- `test_local_verifier_recovery_composition.py` 使用 `phase1_settlement_a` 的节点需要 DOC-09；前面的本地合成 composition 测试不都需要该文件。
- `test_rag_lost_in_middle_priority_admission.py` 的 D15 原 probe 输入比较需要 DOC-15；多数其他 admission 测试由当前测试源码直接创建合成输入。
- `test_stage_c_profile_aware_indexing.py` 的真实 catalog 投影节点，以及 `test_stage_c_profile_lineage.py` 的真实 profile 节点需要真实 catalog/source；其他节点存在纯合成 fixture。
- `test_full8_business_coverage_contract_20261028.py` 只有 Gold provenance 节点在 Gold 根不存在时按原实现 skip；其矩阵/合成节点仍需要 DOC-04/DOC-05。
- `test_post_phase1_full8_launcher.py` 是混合模块：历史 task/policy/plan、旧源码、某些原生 RAG manifest、外部 helper 各由不同节点使用。全文件不能作为无历史数据的默认 smoke suite。

Attempt4 资源由仓库内 pinned closure receipt、artifact manifest、外部 move receipt 和八个原始成员共同约束。`RAG_AGENT_BASELINE_SOURCE_MIRROR_ROOT` 只允许按原契约迁移物理镜像位置，不能改变原始字节或采用替代 fixture。

真实文档 catalog 的具体资源为：

- `data/parsed/document_full_v1/document_manifest.jsonl`
- `data/catalog/document_profile_seeds_v1.jsonl`
- `data/catalog/document_profiles_v1.jsonl`
- `data/catalog/document_profile_catalog_audit_v1.json`
- `data/raw/jsonl/cleaned_text_unit_v1_batch_20260803.jsonl`

`data/eval/full_chain/unit_edge_catalog.json` 是 evaluator 的纯工程单位/边/用例 catalog。`load_catalog()`/`observe()` 默认读取它，需要随公开工程保留，不能与真实文档 catalog 一起排除。其他 reference/badcase 文件仍以最终安全白名单为准。

## 隔离发布副本的三个隐私外置测试

原始值继续留在本地；仅下列环境变量名进入文档，不写入真实值或示例替代值：

| 变量 | 原始本地输入 |
|---|---|
| `RAG_LOCAL_WORD_FULL_V2_CONFIG` | 原始本地 `document_ingestion/configs/word_full_v2.document-config.json` 的绝对路径；该真实配置未进入公开树 |
| `RAG_LOCAL_HISTORICAL_SCHEME_FILENAME` | 上述配置中对应历史参考文件的原始文件名 |
| `RAG_LOCAL_HISTORICAL_PROJECT_ID` | 原始历史 project identity；不得生成替代值 |

- `backend/tests/test_document_ingestion_text_normalizer.py`：`test_word_full_v2_config_excludes_gold_reference_from_index`。
- `backend/tests/test_stage_b_document_catalog.py`：`test_current_project_scope_is_limited_to_local_project`, `test_runtime_whitelist_keeps_matching_project_sources_and_all_reference_roles`。
- `backend/tests/test_stage_c_profile_lineage.py`：`test_milvus_extra_json_preserves_canonical_profile_and_chunking`。

Normalizer 的历史配置节点缺少 config/filename 输入时明确 skip；identity helper 缺原始 project identity 时也明确 skip。Stage B/C 的部分节点会先读取私有 catalog/source，资源缺失时可能在到达 identity helper 前失败；不能宣称它们统一自动 skip。

这三个隔离副本文件保留原有质量/索引排除断言，敏感字面量改由原始本地输入提供。原工作区文件不改写。纯文本 Normalizer 的两个节点不依赖这些环境变量，可在公开快照直接回归。所需依赖仍按精确 lock 安装；主机 uv 版本不符时，本次根验证使用 `uvx --from uv==0.11.32 uv sync --frozen` 加 README 所列 extras。安装与实测结果以根验证收据为准。

## 服务、应用与原生 fixture 单列

| 前置条件 | 模块/节点范围 | 缺失时的既有行为 |
|---|---|---|
| 隔离 PostgreSQL + durable-memory extra | `test_step7_durable_memory_postgres.py`，combined/resource-terminal 中使用 `pg_store` 的节点 | `LANE351_TEST_PG_DSN` 缺失时 fixture 明确 skip；不得指向已有业务库 |
| Milvus Lite / pymilvus 原生兼容环境 | `test_milvus_child_chunk_record_compat.py` 原生节点，`test_p1_trust_semantic_payload_closure.py` 的真实 Milvus scope 节点 | 纯记录/schema 节点独立；一项兼容性测试有原有 Windows skip，不能假定所有缺驱动场景均 skip |
| Windows + Microsoft Word COM + pywin32 | `test_document_ingestion_word_parser.py` 的 `word_com` 节点 | 仅 Windows 且 `RUN_WORD_COM_TESTS=1` 时执行；其余 parser 节点使用受控 fixture |
| 测试自建 loopback HTTP server | `test_provider_protocol_hardening.py` | 需允许临时绑定 127.0.0.1；这是本地协议 fixture，无远程模型请求 |
| 真实 Provider 明确 opt-in | `test_real_provider_acceptance.py` | 原有 `real_provider` marker 和 `REAL_PROVIDER_ACCEPTANCE` skip 保留；本次发布验证不启用 |
| 新建受保护 NTFS E: 根 | `test_formal_guard_v2_isolation_native_20261018.py`、`test_formal_guard_v2_scopes_native_20261018.py` | 需 `GUARD_V2_NATIVE_ROOT`/`GUARD_V2_EVIDENCE_DIR` 与精确路径准入，缺失通常失败，未自动 skip |
| 新建受保护 report 根 | `test_deterministic_review_report_native_20261017.py` | 缺 `RPT_TEST_ROOT` 或 `RPT_EVIDENCE_DIR` 时原实现明确 skip |
| NTFS ACL/native handle 语义 | Writer Review Copy C3/volume-root/compat 测试 | 平台、pywin32 和目录准入需逐节点满足；不宣称跨平台全量可运行 |
| 显式历史 verification mapping 插件 | `fpbca_current_verification_case_mapping.py` | 默认不加载；显式加载时需 gate/hash、原 recorder 与引用 authority，缺失在 pytest_configure 拒绝 |

这些前置条件只说明如何理解原测试，未授权启动服务、付费模型、完整 E2E 或创建新的历史验收结论。

## 额外公开轻量回归建议

下面组合侧重 SDK 输出参数、evaluator scope、Writer 指令投影与严格引用字段，使用当前源码里的合成 fixture。SDK wire 模块以 MockTransport 并禁止 socket/DNS，路由配置内的本地模型路径仅作为受控常量，不加载真实模型权重。组合预计为几十秒量级，尚未由本清单生成者实测；以最终验证收据为准。

在按 README 安装的 Python 3.10 环境，从快照根执行：

```powershell
$env:RAG_CONFIG_SOURCE_POLICY = 'process_env_defaults_v1'
$env:REAL_PROVIDER_ACCEPTANCE = '0'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$publicationTestNodes = @(
  'backend/tests/test_post_step7_deepseek_output_binding.py'
  'backend/tests/test_stage3_g2_g5_reference_semantics_20261018.py'
  'backend/tests/test_multi_agent_v3_writer_input_boundary.py::test_r1_task_semantics_are_explicitly_projected'
  'backend/tests/test_multi_agent_v3_writer_input_boundary.py::test_r1_language_tone_and_section_boundaries_are_not_lost'
  'backend/tests/test_bca014_verifier_reply_ref_normalization.py::test_duplicate_refs_are_not_deduplicated_and_remain_strictly_rejected'
  'backend/tests/test_document_ingestion_text_normalizer.py::test_word_caption_separator_and_controls_are_normalized'
  'backend/tests/test_document_ingestion_text_normalizer.py::test_word_cell_markers_and_zero_width_characters_are_removed'
)
python -B -m pytest -q -p no:cacheprovider -m 'not real_provider and not word_com' @publicationTestNodes
```

必须在首次导入 `core.config` 前设置进程配置策略；它使应用不读取 `.env`。不要读取、复制、删除或改名本机 `.env` 来完成验证。运行上述组合时的实际结果与本清单的静态分析应分别记录。

## 覆盖与维护

静态解析不能穷尽动态函数生成路径、导入的 resource map、envelope 内的指针、历史 `source_db` 或 `preserved_path` 闭包。19 个 docs fixture 和模块列表均为已识别下限，不能据此证明其他测试均不依赖本地资源。

保留原断言与原历史身份。仅上述三个发布副本测试进行已记录的隐私外置和明确 skip；其他历史资源缺失不自动改为 skip。缺少原始输入、平台、服务或授权时应诚实记录 NOT_RUN / SKIPPED / BLOCKED；不能补造历史输入、将所有缺失变成 skip，或以合成回归替代真实业务质量与正式 Release 验收。后续若要将历史测试改造成完整公开自包含套件，应另行冻结测试语义与合成 fixture 方案。
