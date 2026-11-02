# 公开工程快照问题登记

维护日期：2026-11-02。遵循仓库问题记录规则：保留客观发现、影响、根因、处置与验证边界。本文是公开技术索引，不替代本地历史 Issue Register，也不变更任何业务冻结 authority。

本地 authority 路径作为证据标识列出；未随公开快照分发的材料不承诺能够在线访问。下列记录不包含真实业务正文、凭据或 Provider 原始回包。已有问题的发现日期按原报告保留；本次整理只维护公开说明，不重新运行原实验。

本次公开范围已由用户于 2026-11-02 明确批准：真实目录、原始模型记录和历史数据
留本地；发布当前工程和测试源码，并列出历史回放的外部依赖。下列处置不代表
历史业务质量或现存缺陷得到验收。

## PUB-DOC-001：当前工程状态缺少统一公开入口

| 字段 | 记录 |
| --- | --- |
| 发现时间 / 阶段 | 2026-11-02，GitHub 发布准备 |
| 发现位置 | 原 README 与分散的阶段、验收和回归报告 |
| 客观现象 / 证据 | 原 README 主要介绍较早运行基线，未集中说明当前五项阶段状态、质量与 Release 缺口；已直接核对原 README 与历史报告 |
| 影响范围 | 面试展示、工程阅读和复现预期 |
| Severity / 阻塞 | MAJOR；阻塞准确的公开状态说明，本文档补充后解除该文档缺口 |
| 需要用户决策 | 否；用户已明确要求客观整理 README 与工程说明 |
| 根因 | 工程进度保存在多个 dated reports，主入口尚未同步 |
| 解决方案 | 新增本目录技术概览、当前状态与问题索引；仓库 README 链接当前入口；历史报告保留原样 |
| 验证方式 | 文档与源码入口、既有报告逐项对照；发布前再检查仓库内链接 |
| 最终状态 | RESOLVED（公开摘要已形成；不表示业务问题已解决） |

## PUB-DOC-002：旧 R13 与当前 V3 验收范围容易混淆

| 字段 | 记录 |
| --- | --- |
| 发现时间 / 阶段 | 2026-11-02，历史文档整理 |
| 发现位置 | 本地 `docs/post_step7_real_provider_hardening/R13_CLOSURE_HANDOFF.md`、`REAL_PROVIDER_ATTEMPT_R13.md`；后续 Full8/R02 汇总 |
| 客观现象 / 证据 | 旧 R13 具有 BEST_EFFORT Release 和质量 FAIL；后续八章 Writer Run 与 R02 的 DOCUMENT/Release 完成情况不同 |
| 影响范围 | 当前版本质量、真实阶段8与历史 Release 的表述 |
| Severity / 阻塞 | NOTE；若混用版本，会产生不准确的验收声明 |
| 需要用户决策 | 否；保留实际版本和阶段边界即可 |
| 根因 | 多轮 Run 名称相近，但源码、输入合同与完成范围不同 |
| 解决方案 | 在 CURRENT_STATUS 中分别说明旧 R13、2026-10-20 Full8 和 R02；当前阶段8仍未验收 |
| 验证方式 | 直接比较各原始汇总的 source identity、质量、DOCUMENT、RunCompletion 与 Release 状态 |
| 最终状态 | RESOLVED（表述范围已明确，原验收结论未改变） |

## KNOWN-BC33-R01：并行内部图 durability 兼容尚未验收

| 字段 | 记录 |
| --- | --- |
| 发现时间 / 阶段 | 2026-10-28～29，BC33 离线回归与归因；2026-11-02公开汇总 |
| 发现位置 | `backend/apps/enterprise_document/workflows/section_parallel_execution_graph.py` |
| 客观现象 / 证据 | 无 Checkpointer 的 fan-out 图继承父级同步 durability 时，既有依赖路径访问不存在的 checkpoint future；原生任务和隔离内存探针支持同一根因；9条相关失败归为同一家族 |
| 影响范围 | 并行章节调度、真实 Section Checkpoint 与恢复验证；不外推默认串行 Full8 |
| Severity / 阻塞 | MAJOR / P1；阻塞对外承诺已验收的并行能力，不阻塞如实披露限制的源码发布 |
| 需要用户决策 | 候选改动仍须按既有设计审计与实施授权处理；若改变持久化合同或扩大修改范围，需要用户决定 |
| 根因 | 临时调度图与正式持久化图的 durability 配置传播边界不兼容 |
| 方案 / Deferred reason | 当前源码已有局部候选改动；本次仅保存，后续按最小设计验证，不修改 LangGraph 私有字段或全局降级持久化 |
| 验证方式 | 后续需原生并行 Writer、预算拒绝、Section 保存/冷读及恢复回归；当前局部探针不等于完整验收 |
| 最终状态 | OPEN / IMPLEMENTED_UNVERIFIED |
| 本地 Authority | `docs/33_FULL_OFFLINE_TEST_SWEEP_20261028/13_BC33_R01_PARALLEL_DURABILITY_MINIMAL_DESIGN_20261029_R01.md`；同目录 `08_FINAL_ENGINEERING_CLOSEOUT_20261029.md` |

## KNOWN-BC33-R02：多 Attempt 文档级失败可能覆盖首发错误

| 字段 | 记录 |
| --- | --- |
| 发现时间 / 阶段 | 2026-10-28～29，BC33 故障注入与归因；2026-11-02公开汇总 |
| 发现位置 | `services/scheme_writer/stage_service.py` 与 `services/agent_rag_v1/lifecycle_runtime.py`，均位于 `backend/apps/enterprise_document/` |
| 客观现象 / 证据 | 两条测试在多章完成后注入文档级技术故障；失败处理将缺失的触发 Attempt 身份传入要求精确身份的入口，后续生命周期异常覆盖初始诊断 |
| 影响范围 | 特定文档/Run级非取消故障的诊断与合法终态收敛；未证明正文被删除或正常 Run 必然失败 |
| Severity / 阻塞 | MAJOR / 原交付收口P0；阻塞对应故障收尾验收，不阻塞披露现状的源码发布 |
| 需要用户决策 | 是，若变更失败归属/生命周期合同；现有最小设计仍待审计和正式实施授权 |
| 根因 | 文档级故障没有唯一触发 Attempt，调用方与既有 Attempt级失败契约不匹配 |
| 方案 / Deferred reason | 既有方案提出在原复合生命周期转换中显式处理可信文档级失败；本次不实施、不随意选择一个 Attempt，也不保留 RUNNING 伪装修复 |
| 验证方式 | 后续需首发异常保留、Run/Attempt原子终态、重复请求幂等、历史正文保全及读回；发布任务只记录现状 |
| 最终状态 | OPEN / DESIGN_PROPOSAL |
| 本地 Authority | `docs/33_FULL_OFFLINE_TEST_SWEEP_20261028/09_R02_MINIMAL_FAILURE_TERMINALIZATION_DESIGN_20261029_R01.md`；同目录 `08_FINAL_ENGINEERING_CLOSEOUT_20261029.md` |

## KNOWN-BC33-D12：CLI 审查稿与拒绝信息交付尚未闭合

| 字段 | 记录 |
| --- | --- |
| 发现时间 / 阶段 | 2026-10-29，实际CLI出口审计；2026-11-02公开汇总 |
| 发现位置 | `scripts/run_demo.py` 的产物持久化与终态输出 |
| 客观现象 / 证据 | 旧CLI在内部已有审查正文/拒绝清单而正式发布产物为空时，未提供可取出口；既有纯投影探针确认该旧行为；当前源码已有候选导出和路径投影 |
| 影响范围 | Gate拒绝后的同Run审查稿、缺章说明与拒绝信息交付；不代表系统没有生成正文 |
| Severity / 阻塞 | MAJOR / 交付验收问题；阻塞承诺该出口已完整验收 |
| 需要用户决策 | 当前候选按既有设计审计；若改变公开字段、安全边界或交付合同，需要用户决定 |
| 根因 | 既有审查对象与CLI正文导出、终态早返回分支之间缺少完整接线 |
| 方案 / Deferred reason | 保存当前候选，后续验收真实同Run文件、缺章声明、拒绝信息白名单和正式Release优先分支；不把审查稿升级为正式发布 |
| 验证方式 | 后续需受控临时根的实际导出/读回与原生Fake业务组合回归；纯投影或仅新增分支不足以关闭问题 |
| 最终状态 | OPEN / IMPLEMENTED_UNVERIFIED |
| 本地 Authority | `docs/33_FULL_OFFLINE_TEST_SWEEP_20261028/15_BC33_D12_CLI_REVIEW_DELIVERY_MINIMAL_DESIGN_20261029_R01.md`；同目录 `08_FINAL_ENGINEERING_CLOSEOUT_20261029.md` |

## KNOWN-FIXTURE-001：部分历史测试无法在公开克隆中独立复现

| 字段 | 记录 |
| --- | --- |
| 发现时间 / 阶段 | 既有BC33测试与环境归因；2026-11-02发布依赖整理 |
| 发现位置 | 历史原生测试、外置Fixture定位链、可信NTFS根与独立数据库前置条件 |
| 客观现象 / 证据 | 既有失败归因包括冻结源码身份不符、Fixture/API漂移、外部资料和环境缺失；公开范围排除真实运行数据库与业务证据 |
| 影响范围 | 全量历史测试和跨机历史回放；不等于所有便携单元测试都不可运行 |
| Severity / 阻塞 | MAJOR；阻塞“新克隆即可重现全部历史验收”的承诺 |
| 需要用户决策 | 若公开原始资料、迁移历史Fixture或修改冻结身份合同，需要单独决定；仅披露前置条件不需要 |
| 根因 | 历史验收绑定特定来源字节、环境与持久化证据，公开工程快照不分发这些输入 |
| 方案 / Deferred reason | 明确区分便携离线测试和历史验收测试；原始资料留本地，不伪造输入、不降低可信目录守卫 |
| 验证方式 | 本次发布仅执行明确独立的定向检查；历史复现须按原前置条件另行验收 |
| 最终状态 | OPEN / DISCLOSED_LIMITATION |
| 本地 Authority | `docs/33_FULL_OFFLINE_TEST_SWEEP_20261028/08_FINAL_ENGINEERING_CLOSEOUT_20261029.md`；`docs/git_baseline_closure_20261005/README.md` |

## KNOWN-QUALITY-001：质量与当前真实阶段8验收未闭合

| 字段 | 记录 |
| --- | --- |
| 发现时间 / 阶段 | 多轮真实业务验证；2026-11-02状态整理 |
| 发现位置 | 文档质量Gate、DOCUMENT post-write、RunCompletion与Release边界 |
| 客观现象 / 证据 | 已有八章Writer和原生阶段5～7链路；质量FAIL和后续DOCUMENT/Release缺项分别保留，旧R13只证明其旧冻结版本 |
| 影响范围 | 业务质量、正式交付和当前贯通版本阶段8验收 |
| Severity / 阻塞 | MAJOR；阻塞业务质量PASS及当前阶段8完成声明 |
| 需要用户决策 | 需要后续具体测试/费用/运行合同授权；任何质量语义或发布规则变更也须按authority处理 |
| 根因 | 正文生成、领域校验、最终发布是不同证明；当前证据未覆盖完整通过条件，不能归结为单一未经证明的代码根因 |
| 方案 / Deferred reason | 保留原FAIL与未执行边界；根据既有问题和后续授权逐项验证，本次不运行真实E2E或付费模型 |
| 验证方式 | 后续需精确源码/输入/预算/RAG身份下的真实DOCUMENT、RunCompletion、Release及质量证据 |
| 最终状态 | OPEN / NOT_ACCEPTED |
| 本地 Authority | `docs/rc1_full8_real_e2e_acceptance_20261020_r01/01_FULL_E2E_REPORT.md`；`docs/rc1_full8_r02_authorized_execution_20261022_r01/FINAL_RUN_HANDOFF.md` |

## KNOWN-PERF-001：当前完整业务性能与成本尚无统一保证

| 字段 | 记录 |
| --- | --- |
| 发现时间 / 阶段 | 既有Post-write与预算性能工作；2026-11-02公开限制整理 |
| 发现位置 | 预算持久化、Post-write处理、Provider耗时与端到端观测 |
| 客观现象 / 证据 | 已有阶段性观测与离线优化材料；部分时间/CPU/I/O归属、缓存影响和实际账单未完整建立，不能用局部读取次数或token数量替代全链性能/收费 |
| 影响范围 | Full8时延、吞吐、资源用量和成本承诺 |
| Severity / 阻塞 | NOTE / 性能验证债；阻塞未经测量的性能或成本保证 |
| 需要用户决策 | 新真实测量、付费执行或改变优化范围时需要；本次仅披露现状 |
| 根因 | 局部离线实验与完整真实链路的输入、运行范围、缓存和计量口径不同 |
| 方案 / Deferred reason | 后续先冻结输入并测量，再分析和决定优化；本次不扩大性能改造、不增加Provider调用 |
| 验证方式 | 后续独立记录wall-clock、Provider、CPU/I/O、未归属项和真实费用；保留错误/恢复等价性 |
| 最终状态 | OPEN / DEFERRED |
| 本地 Authority | `docs/rc1_full8_postwrite_performance_baseline_20261020_r01/`；`docs/rc1_full8_budget_phase1_read_consolidation_implementation_20261021_r01/` |

源码公开、Git提交或定向检查通过，只关闭本次明确覆盖的维护事项。现存业务、质量、恢复、环境和性能问题继续保持各自状态。
