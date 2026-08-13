# RAG Evaluation Foundation 建设方案（面试级收敛版）

> 日期：2026-08-09  
> 项目：Enterprise RAG-Agent  
> 文档性质：建设方案 / 实施边界 / 验收基线  
> 目标：在现有 `backend/eval/rag`、`backend/experiments`、Trace 与 RAG 运行记录基础上，收敛并升级为统一的 RAG Evaluation Foundation。

---

## 0. 一句话定位

本项目不建设“功能繁多的评测平台”，而建设一个**评测维度稳定、指标定义可靠、实验可复现、失败可归因、能长期支撑 RAG 主链算法改进的 Evaluation Foundation**。

核心原则：

> **方法可以不断变化，评价坐标系尽量稳定。**

Adaptive-RAG、C-RAG、Self-RAG、HyDE、Multi-Query、Context Engineering、Embedding/Reranker/LLM 替换，都应进入同一套核心评测坐标系比较，而不是每引入一篇论文就重新定义一套评测体系。

Evaluation Foundation 的运行时定位：

> **Evaluation is observational, not authoritative.**

它可以评价一次已完成 Run，但不能修改当前 Run 的状态、策略、路由、Evidence、Writer 输出、Verifier 结论或 Hard Gate 结果。

Evaluation Foundation 的研发定位：

> **Evaluation 不拥有 Runtime Authority，但拥有 Engineering Evidence Authority。**

任何主链改进想进入新 Baseline，都应通过固定 Dataset 和统一 Core Metrics 证明其收益。

---

# 1. 建设背景

当前项目已经存在多套与评测相关的实现，并非从零开始：

- `backend/eval/rag/strategy/runner.py`
  - 已有 `StrategyEvalRunner`
  - 支持实验矩阵
  - 支持数据集校验
  - 支持指标注册
  - 支持逐样本失败隔离
  - 支持 baseline comparison
  - 支持 warmup / seed / reproducibility 信息

- `backend/eval/rag/strategy/metrics.py`
  - 已有 `MetricRegistry`
  - 已有 `MetricDefinition`
  - 已有指标方向定义
  - 已有黄金标签要求校验
  - 已实现 Hit@K、Recall@K、MRR、NDCG、关键词覆盖、延迟等

- `backend/eval/rag/engine/ragas_style_eval.py`
  - 已有本地确定性 RAGAS 风格代理评测
  - 已覆盖 Context Precision / Context Recall / Faithfulness / Answer Relevance / Citation 等代理维度
  - 当前明确不是官方 RAGAS、不是语义 Judge，只能解释为低成本 proxy

- `backend/schemas/eval_schema.py`
  - 已有严格 `RAGEvalSampleSchema`
  - 已有端到端 `EvalSampleSchema`
  - 已有 `EvalResultSchema`

- `backend/experiments/manifest.py`
  - 已有 Experiment Run Manifest
  - 已记录 code、model、prompt、index、embedding、dataset、evaluation 等可复现信息

- `docs/code_walkthrough/10_QUALITY_GATE_AND_EVALUATION.md`
  - 已明确 runtime gate 与 offline evaluation 的职责边界
  - 已明确离线 Eval 不参与 runtime pass/fail

因此本轮不重新开发“第二套评测平台”，而是把现有能力收敛成一个稳定统一的 Evaluation Foundation。

---

# 2. 建设目标

## 2.1 第一目标：面试技术资产

Evaluation Foundation 必须能够支撑以下面试叙事：

1. RAG 算法改造不是凭单次 Demo 或主观判断。
2. 所有改造使用固定 Dataset、统一指标和可复现实验配置。
3. Retrieval、Evidence、Grounded Generation、Task Quality、Efficiency、Robustness 分层评价。
4. 最终质量下降时可以通过 Trace 定位 Failure Stage。
5. Adaptive/C-RAG/Self-RAG 等论文机制使用不同实现，但进入相同 Core Metrics 比较。
6. 算法特有指标只作为 Diagnostic Metrics，不污染长期评测坐标系。
7. Badcase 会沉淀为 Regression Set，形成真实优化闭环。

面试时重点不是“用了 RAGAS”，而是：

> 在 RAGAS 基础上构建了方法无关的分层评测坐标系，并通过 Trace、Experiment Manifest、Baseline/Candidate Benchmark 和 Badcase Regression 建立可复现的 RAG 算法实验闭环。

---

## 2.2 第二目标：成为主链改进基座

后续所有重大 RAG/Agentic-RAG 主链改进，必须能够在该框架上完成：

```text
问题发现
  ↓
Evaluation / Badcase 定位
  ↓
提出 Hypothesis
  ↓
冻结 Baseline
  ↓
实现 Candidate
  ↓
固定 Dataset Benchmark
  ↓
Core Metrics 对比
  ↓
Diagnostic Metrics 解释机制
  ↓
保留 / 放弃 Candidate
```

典型改造包括：

- Query Rewrite
- Multi-Query
- HyDE
- Dense / BM25 / Hybrid / RRF 调整
- Parent-Child 调整
- Reranker 调整
- Top-K / Context Packing 调整
- Adaptive Router
- C-RAG-like Evidence Evaluator
- Self-RAG-like Generation Fallback
- ContextCompiler
- Embedding 替换
- Reranker 替换
- LLM 替换

---

## 2.3 第三目标：不无限扩展

本项目只建设“实验方法论完整”的基座，不建设“评测产品平台”。

明确排除：

- Web Dashboard
- 多租户评测 SaaS
- Kafka 实时评测流水线
- 分布式 Eval Scheduler
- 自动超参数搜索
- AutoML
- 自动部署最佳 Candidate
- 自动生产回滚
- 大型人工标注平台
- Dataset 数据湖
- 模型排行榜网站
- 复杂权限系统
- 在线 Evaluation 干预生产主链
- 为每篇论文开发独立 Eval Runner

---

# 3. 三套质量系统必须彻底分离

项目中存在三类不同机制，不能继续混称“测试”。

## 3.1 Software Correctness Test

负责：

- pytest
- unit
- integration
- recovery
- idempotency
- checkpoint
- graph route
- schema contract

回答：

> 代码有没有写坏？

不属于本建设方案核心范围。

---

## 3.2 Production Quality Control

包括：

- CRAG Evidence Assessment / Sufficiency Gate
- Verifier
- Citation/Fact deterministic gate
- Hard Gate

回答：

> 当前 Run 是否允许继续、是否允许发布？

属于生产主链。

---

## 3.3 RAG Evaluation Foundation

负责：

- post-run evaluation
- offline benchmark
- baseline/candidate compare
- metric calculation
- failure attribution
- badcase/regression

回答：

> 这次 Run 做得怎么样？哪一层失败？新算法真的比旧算法好吗？

Evaluation 不回写当前业务 GraphState。

---

# 4. 运行模型

Evaluation Foundation 只有两个正式入口。

## 4.1 Post-Run Evaluation

正常业务任务结束后：

```text
Agent-RAG Mainline
  ↓
Artifact / Rejected Result
  ↓
RunCompleted
  ↓
Evaluation Input Adapter
  ↓
Evaluation Core
  ↓
Evaluation Report
  ↓
Badcase Candidate
```

原则：

- 当前业务 Run 已经结束
- Eval 只读取运行产物
- 不重新执行主链决策
- 不触发 Writer rewrite
- 不触发 Research retrieve-more
- 不修改 Router action
- 不修改 Hard Gate

V1 可先通过同步/显式调用完成，不要求引入消息队列或异步平台。

---

## 4.2 Offline Benchmark

用于算法实验：

```text
Fixed Dataset
   ↓
Baseline
   ↓
Candidate
   ↓
Same Evaluation Core
   ↓
Compare Report
```

同一评测核心，同时服务 Post-Run 和 Benchmark；不建设两套 Metric Engine。

---

# 5. Core Metrics：长期稳定评测坐标系

Evaluation Foundation 的核心不是指标数量，而是以下六个评测维度必须做牢。

---

## 5.1 Retrieval Quality

核心问题：

> 应该找到的信息是否找到，并且排序是否合理？

V1 核心指标：

- `hit_at_k`
- `recall_at_k`
- `mrr`
- `ndcg_at_k`
- `context_precision` / 兼容 proxy

说明：

- 继续复用现有 `backend/eval/rag/strategy/metrics.py`
- 不重复实现已有 IR 指标
- Gold 优先使用稳定 doc/parent/child identity
- 关键词 proxy 只能辅助，不替代 Gold ID

V1 不无限增加 Precision@K、MAP、R-Precision 等同类指标，除非后续真实实验明确需要。

---

## 5.2 Evidence Quality

这是当前项目相较普通 RAGAS 最需要加强的核心层。

核心问题：

> 找到的证据是否足以完成当前 Evidence Need？

V1/V2 稳定核心指标：

- `evidence_requirement_coverage`
- `evidence_precision`
- `missing_evidence_detection`
- `conflict_detection`

基础语义必须区分两套 Requirement：

```text
Gold Requirement
= Evaluation Dataset / human-confirmed evaluation truth

Runtime Requirement
= Planner 在当前业务 Run 中产生的 EvidenceRequirement
```

二者不能互相替代，也不能用 Runtime Requirement 反推 Gold。

正式 Benchmark / 有 Gold 的 Post-Run Evaluation 应按 Gold Requirement 评价：

```text
Gold Requirement
  ↓
EvidencePackage
  ↓
covered / missing / conflicted
```

同时保留 Runtime Requirement，用于判断 Planner 是否发生 requirement omission。

示例：

```text
Gold Required:
A 计算资源
B 网络架构
C 安全能力

Planner Runtime Required:
A 计算资源
B 网络架构

EvidencePackage:
A covered
B covered

Gold Evidence Requirement Coverage = 2 / 3
```

不能因为 Planner 漏掉 C，就把 Coverage 错算成 `2 / 2 = 100%`。

对于没有 Gold Requirement 的普通生产 Post-Run，只能计算并明确标记 `runtime_requirement_coverage` 等诊断结果，不能伪装成 Gold-based Core Metric。

要求：

- Gold Coverage 必须能追到具体 Gold Requirement ID
- Runtime Requirement 必须保留 Planner 产生的 requirement identity
- 不允许只用“LLM 给完整性打 1~5 分”作为最终事实源
- LLM Judge 可以参与语义匹配，但输出必须保留 requirement/evidence 对应关系

---

## 5.3 Grounded Generation Quality

核心问题：

> Writer 是否正确使用 Fact 和 Evidence，而不是只生成了看起来合理的文本？

核心指标：

- `faithfulness`
- `unsupported_claim_rate`
- `fact_accuracy`
- `citation_correctness`

Evaluation 需要组合：

```text
RAGAS / semantic faithfulness
+
Claim → Evidence Binding
+
Claim → FactSnapshot
+
Citation → Evidence
```

示例：

```text
FactSnapshot: server_count = 16
Evidence:      server_count = 16
Writer Claim:  server_count = 20
```

归因必须能够明确：

```text
Retrieval PASS
Evidence PASS
Generation FAIL
```

不能只得到 `faithfulness = 0.7` 而无法继续定位。

---

## 5.4 Task Quality

核心问题：

> 内容即使完全有依据，最终是否真正完成了业务任务？

V1 只保留少量高价值指标：

- `required_point_coverage`
- `technical_quality`

边界：

- 不建设大量重叠的主观 Judge
- Technical Quality 必须有稳定 rubric/version
- Required Point Coverage 优先采用结构化 requirement 对应，而不是纯主观总分

---

## 5.5 Efficiency

核心问题：

> 为获得当前质量，系统付出了多少资源？

核心指标：

- `latency_ms`
- `token_usage`
- `tool_calls`
- `research_rounds`
- `cost`（当 API 成本可可靠记录后启用）

作用：

算法选型必须考虑：

```text
Quality Gain
vs
Latency / Token / Tool / Cost Delta
```

不能只凭准确率单目标决策。

---

## 5.6 Robustness

V1 只做最小但有价值的两项：

- `badcase_pass_rate`
- `regression_pass_rate`

回答：

> 新算法平均分提高后，是否重新打坏历史已修复问题？

不建设复杂对抗攻击或通用安全鲁棒性平台。

---

# 6. Core Metrics 与 Diagnostic Metrics 分离

这是防止评测框架跟论文数量无限膨胀的关键设计。

## 6.1 Core Metrics

长期稳定：

```text
Retrieval
Evidence
Grounded Generation
Task Quality
Efficiency
Robustness
```

任何算法都必须接受这一坐标系评价。

---

## 6.2 Diagnostic Metrics

只用于解释某个方法内部机制是否工作。

### Adaptive Router 示例

- router decision accuracy
- wrong-stop rate
- no-gain action rate
- strategy distribution
- evidence gain after action

### C-RAG-like 示例

- evidence classification accuracy
- correction trigger accuracy
- correction success rate
- evidence gain after correction

### Self-RAG-like 示例

- trigger rate / trigger accuracy
- rewrite success rate
- candidate improvement delta
- no-gain reflection rate

规则：

> Diagnostic Metric 可以随着算法启用/退役而变化；Core Metric 不随论文变化。

禁止：

- `AdaptiveEvalRunner`
- `CRAGEvalRunner`
- `SelfRAGEvalRunner`

各自发展成独立评测体系。

---

# 7. RAGAS 二开定位

本项目不修改 RAGAS 内核，不把 RAGAS 当作整个 Evaluation Foundation。

目标架构：

```text
Evaluation Foundation
      │
      ├── Retrieval Metrics
      ├── RAGAS Adapter
      ├── Evidence Metrics
      ├── Grounding Metrics
      ├── Task Quality Metrics
      ├── Efficiency Metrics
      └── Diagnostic Metrics
```

当前仓库 `ragas_style_eval.py` 是低成本 proxy，应明确保留以下语义：

- proxy ≠ official RAGAS
- proxy ≠ human truth
- proxy ≠ semantic judge

后续演进建议：

1. 保留 deterministic proxy，作为 cheap regression。
2. 新增正式 `RagasMetricAdapter` 或统一 SemanticMetricAdapter。
3. Deep Eval 时允许接官方 RAGAS / LLM Judge。
4. 所有结果统一进入相同 `MetricResult` Contract。

因此：

> 低成本 proxy 与高成本 semantic metric 是同一个评测维度的不同计算器，不是两套评价体系。

---

# 8. Dataset 设计

不建设 Dataset Platform，只建设版本明确、标签明确、可重放的 Golden Dataset。

## 8.1 复用现有 Schema

优先复用：

- `RAGEvalSampleSchema`
- `EvalSampleSchema`
- `GoldenDatasetManifest`

现有 `RAGEvalSampleSchema` 已有：

- sample_id
- query
- reference_answer
- expected_doc_ids
- expected_parent_chunk_ids
- expected_child_chunk_ids
- expected_keywords
- answer_keywords
- golden_set_id
- golden_set_version
- source_refs
- metadata

不应重新创建平行的 RetrievalSampleSchema。

---

## 8.2 需要补强的标签语义

后续按兼容方式增加或扩展：

### Evidence labels

- gold evidence requirements
- expected covered requirements
- expected missing requirements
- conflict pairs / conflict expectation

运行时 Planner 产生的 `runtime evidence requirements` 属于 Evaluation Input 的实际执行数据，不是 Golden Dataset 的 Gold Requirement。

### Grounding labels

- required claims / required points
- forbidden or unsupported claims
- authoritative fact refs
- citation expectation

### Scenario metadata

- domain
- difficulty
- query_type
- badcase_type
- source scenario

要求：

- 新字段必须支持旧 Dataset 兼容
- 不为每种算法新建一份完全不同 Schema
- 特有标签尽量放可扩展结构，稳定后再升级正式字段

---

# 9. Evaluation Input Adapter

该层是 Evaluation 与生产主链解耦的关键。

禁止直接让 Metric 读取 GraphState、Agent 私有对象或某个具体 Runner 的内部结构。

统一链路：

```text
Run Artifact
Trace
FactSnapshot
EvidencePackage
Writer Output
Verifier Output
   ↓
EvaluationInputAdapter
   ↓
EvaluationSample / EvaluationContext
```

建议统一 Evaluation Input 至少表达：

```text
Identity
├── run_id
├── task_id
├── section_id / paragraph_id
└── experiment_id

Task
├── query / requirement
├── section plan
├── gold evidence requirements（when available）
└── runtime evidence requirements

Retrieval
├── query variants
├── retrieval actions
├── ranked results
├── selected context
└── index lineage

Evidence
├── evidence package
├── accepted evidence
├── rejected evidence
├── missing requirements
└── conflicts

Facts
├── authoritative facts
└── source refs

Generation
├── answer / paragraph
├── claims
├── claim-evidence bindings
└── citations

Trace
├── router decision
├── research rounds
├── tool calls
├── self-rag actions
├── latency
└── tokens
```

原则：

> Eval 从已落盘事实读取执行结果，不重新猜测或重放当前 Run 的中间决策。

---

# 10. Metric Engine

现有 `MetricRegistry` 方向正确，应升级而不是重写。

## 10.1 保留现有能力

继续保留：

- metric name
- direction
- requires_answer
- required_gold
- pure compute function
- unknown metric rejection
- dataset gold validation

---

## 10.2 目标扩展

统一 Metric 分类：

```text
metrics/
├── retrieval
├── ragas_adapter
├── evidence
├── grounding
├── task_quality
├── efficiency
└── diagnostics
```

逻辑接口保持简单：

```text
MetricDefinition
  ├── name
  ├── dimension
  ├── direction
  ├── required_inputs
  ├── judge_type
  └── compute/evaluate
```

统一结果：

```text
MetricResult
  ├── metric_name
  ├── dimension
  ├── value
  ├── status
  ├── judge/version
  ├── evidence_refs
  └── details
```

不要一开始构造复杂插件框架；现有 Registry 足以作为 V1 基础。

---

# 11. Runner 设计

Runner 只保留两个入口，避免扩张。

## 11.1 PostRunEvaluationRunner

职责：

- 接收已完成 Run identity / artifacts
- 调用 EvaluationInputAdapter
- 计算 Core Metrics
- 可选计算 Deep/Semantic Metrics
- 执行 Failure Attribution
- 生成单 Run Report
- 产生 Badcase Candidate

不得：

- 修改生产状态
- 重新执行生产 Router
- 自动 rewrite
- 自动 retrieve-more

---

## 11.2 StrategyEvalRunner

现有 `StrategyEvalRunner` 继续作为 Benchmark 主入口，不另造一个 BatchBenchmarkRunner。

需要逐步升级其能力，使其不仅比较“静态检索策略”，还能够在兼容前提下比较：

- retrieval policy
- router
- evidence evaluator
- context policy
- generation fallback

但必须控制：

> Runner 负责实验执行与聚合，不实现具体算法。

现有文件顶部的职责定义继续保留。

---

# 12. Experiment Manifest 与可复现性

现有 `backend/experiments/manifest.py` 已经覆盖：

- code snapshot
- model snapshot
- prompt snapshot
- index identity
- embedding identity
- dataset identity
- evaluation identity
- artifacts

因此不创建第二个 Manifest 系统。

后续需要确保 Benchmark 结果至少冻结：

```text
experiment_id
matrix_id
code commit
seed
Dataset version/hash
Eval Set version/hash
Index version/hash
Embedding model/version
Reranker model/version
LLM model/version
Prompt version/hash
Static retrieval spec/version/hash
Context policy version
Router version
CRAG version
Self-RAG version
Metric profile/version
```

原则：

> 半年后必须能回答某个分数到底由哪套代码、数据、索引、模型、Prompt 和策略产生。

---

# 13. Failure Attribution

Failure Attribution 是本 Evaluation Foundation 最值得做深的二开能力之一。

V1 不使用额外 Root Cause Agent，而采用：

> **Trace + 结构化规则 + Metric Evidence**

## 13.1 V1 Failure Taxonomy

严格控制在以下核心类型：

```text
REQUIREMENT_OMISSION
RETRIEVAL_MISS
RETRIEVAL_RANK_ERROR
EVIDENCE_INSUFFICIENT
EVIDENCE_EVAL_ERROR
CONTEXT_SELECTION_ERROR
FACT_CONFLICT
UNSUPPORTED_CLAIM
CITATION_ERROR
TASK_QUALITY_ERROR
```

以后只有真实 Badcase 无法解释时才新增，不预先扩展几十种错误。

---

## 13.2 典型归因链

```text
Final Grounding FAIL
   ↓
Gold Requirement represented in Planner runtime requirement?
   ├── No → REQUIREMENT_OMISSION
   ↓ Yes
Gold/required evidence retrieved?
   ├── No → RETRIEVAL_MISS
   ↓ Yes
Rank / selected context usable?
   ├── No → RETRIEVAL_RANK_ERROR / CONTEXT_SELECTION_ERROR
   ↓ Yes
Evidence Requirement sufficient?
   ├── No → EVIDENCE_INSUFFICIENT
   ↓ Yes
Evidence evaluator decision correct?
   ├── No → EVIDENCE_EVAL_ERROR
   ↓ Yes
Writer claim supported by evidence/fact?
   ├── No → UNSUPPORTED_CLAIM / FACT_CONFLICT
   ↓ Yes
Citation binding correct?
   ├── No → CITATION_ERROR
   ↓ Yes
Task requirement satisfied?
   ├── No → TASK_QUALITY_ERROR
```

归因必须携带：

- sample_id
- run_id
- section/paragraph/claim ref
- related retrieval/evidence refs
- metric values
- trace refs

---

# 14. Badcase / Regression 闭环

最小闭环：

```text
Evaluation FAIL
  ↓
Failure Attribution
  ↓
Badcase Candidate
  ↓
人工确认
  ↓
Regression Set
```

Badcase Record V1 建议字段：

```text
case_id
source_run_id
sample_id
failure_type
affected_section / claim
trace_ref
metric_results
root_cause_summary
status
created_at
resolved_by_experiment_id
```

不建设复杂人工标注 UI。

Regression Set 的价值：

- 每次重大 Candidate 都回归历史 Badcase
- 防止平均指标提升但关键历史错误复发
- 逐步把真实生产失败转化为长期测试资产

---

# 15. Report 设计

报告核心目标：

> 可比较、可解释、可复现。

禁止只输出一个 `RAG Score`。

## 15.1 单 Run Report

建议结构：

```text
identity
reproducibility

retrieval
  hit@k
  recall@k
  mrr
  ndcg
  context precision

evidence
  requirement coverage
  precision
  missing detection
  conflict detection

grounding
  faithfulness
  unsupported claim rate
  fact accuracy
  citation correctness

task_quality
  required point coverage
  technical quality

efficiency
  latency
  tokens
  tool calls
  research rounds

robustness
  badcase/regression status when applicable

failure_attribution
```

---

## 15.2 Benchmark Compare Report

核心输出：

| Metric | Baseline | Candidate | Delta | Direction |
|---|---:|---:|---:|---|
| Recall@10 | ... | ... | ... | maximize |
| Evidence Coverage | ... | ... | ... | maximize |
| Faithfulness | ... | ... | ... | maximize |
| Unsupported Claim Rate | ... | ... | ... | minimize |
| Tool Calls | ... | ... | ... | minimize |
| Latency | ... | ... | ... | minimize |

报告同时列出：

- improved samples
- regressed samples
- unchanged samples
- new badcases
- fixed badcases

不需要 Dashboard，Markdown + JSON + CSV 已足够。

现有 `StrategyEvalRunner` 报告写出能力优先复用。

---

# 16. Baseline / Candidate 实验规范

任何正式算法结论必须满足 Controlled Experiment。

原则：只改变被研究变量。

例如 3B Router：

```text
Same Dataset
Same Index
Same Embedding
Same Reranker
Same Writer
Same LLM generation model
Same Prompt
Same Metric Profile

Variable:
Heuristic Router vs 3B Router
```

主要比较 Core Metrics：

- Evidence Coverage
- Grounding
- Task Quality
- Latency
- Tool Calls

再使用 Diagnostic Metrics 解释：

- Router Accuracy
- Wrong Stop Rate
- No-gain Action Rate

结论顺序必须是：

> Core Metrics 决定是否有业务价值，Diagnostic Metrics 解释为什么。

不能因为 Router Accuracy 提高，就自动判定整个 Candidate 更好。

---

# 17. Evaluation Quality Gate

必须与 Production Hard Gate 分开。

## 17.1 Production Hard Gate

决定：

> 当前业务 Artifact 能否发布。

属于主链。

## 17.2 Evaluation Quality Gate

决定：

> Candidate 算法是否有足够实验依据升级为新 Baseline。

属于研发/离线实验。

V1 暂不冻结具体阈值，但必须支持：

```text
absolute threshold
baseline delta threshold
must-not-regress metrics
cost/latency budget
regression set pass condition
```

阈值必须通过真实 Baseline 与数据分布校准，不拍脑袋固定。

---

# 18. Trace 与 Evaluation 的关系

Trace 是 Evaluation 的关键事实来源，但 Evaluation 不能存储或依赖私有 chain-of-thought。

可评测 Trace 字段应是结构化结果：

- router action
- reason code
- evidence need
- retrieval query/action
- selected result refs
- evidence assessment
- missing topics
- conflict refs
- context package refs
- generation candidate identity
- self-rag trigger/result
- verifier issue
- tool calls
- latency
- token usage

原则：

> Evaluation 评价可观察决策与结果，不评价私有思维过程。

---

# 19. 与 RAG V2 / Multi-Agent V2 的关系

Evaluation Foundation 不依赖某一种 Agent 架构，但必须能够评价当前 V2 主链。

## 19.1 Adaptive-RAG-like

Core Metrics：

- Retrieval
- Evidence Coverage
- Grounding
- Efficiency

Diagnostic Metrics：

- route/action accuracy
- wrong-stop
- no-gain action
- evidence gain/action

---

## 19.2 C-RAG-like

Core Metrics：

- Evidence Quality
- Grounding
- Efficiency

Diagnostic Metrics：

- evidence classification accuracy
- correction trigger accuracy
- correction gain

---

## 19.3 Self-RAG-like

Core Metrics：

- Grounding
- Task Quality
- Efficiency

Diagnostic Metrics：

- trigger accuracy/rate
- rewrite success
- improvement delta
- no-gain fallback

---

## 19.4 ContextCompiler

Core Metrics：

- Grounding
- Task Quality
- Efficiency

专项观测：

- authoritative fact retention
- evidence retention
- token reduction
- context omission reasons

ContextCompiler 不单独建立一套 Evaluation Framework。

---

# 20. 建设阶段

## Phase E0：Architecture-to-Code Audit

目标：先明确现有能力，禁止直接重写。

审计：

- `backend/eval/rag/strategy/*`
- `backend/eval/rag/engine/*`
- `backend/eval/rag/capture/*`
- `backend/rag/evaluation/*`
- `backend/schemas/eval_schema.py`
- `backend/experiments/manifest.py`
- Trace schema / runtime artifacts

输出分类只允许：

```text
REUSE
UPGRADE
MISSING
LEGACY_COMPAT
STOP_EXPANDING
```

`REMOVE_FROM_MAIN_EVAL_PATH` 不再作为独立状态；根据实际情况归入 `LEGACY_COMPAT` 或 `STOP_EXPANDING`。

验收：

- 明确唯一 canonical Schema
- 明确唯一 Benchmark Runner
- 明确唯一 Metric Registry
- 明确 proxy 与真实 semantic metric 边界
- 不删除仅因架构审美不顺眼的兼容代码

---

## Phase E1：Core Evaluation Contract

目标：冻结评测基座核心契约。

实施：

1. 复用/升级 canonical eval sample schema
2. 定义 EvaluationInput / MetricResult / EvaluationReport 兼容契约
3. MetricRegistry 增加 dimension / judge type 等必要元数据
4. 固定六大 Core Dimension
5. 固定 metric naming/versioning 规则

验收：

- retrieval/evidence/grounding/task/efficiency/robustness 能统一表达
- proxy/semantic 指标不混淆命名
- unknown fields / unknown metrics fail fast

---

## Phase E2：Core Metrics V1

目标：把稳定评测坐标系做实。

实施顺序：

### E2.1 Retrieval

直接复用已有：

- Hit@K
- Recall@K
- MRR
- NDCG

### E2.2 Evidence

新增：

- Evidence Requirement Coverage
- Evidence Precision
- Missing Evidence Detection
- Conflict Detection

### E2.3 Grounding

统一：

- current proxy faithfulness
- claim/evidence support
- fact accuracy
- citation correctness

### E2.4 Task Quality

新增最小：

- Required Point Coverage
- Technical Quality rubric adapter

### E2.5 Efficiency

统一：

- latency
- tokens
- tool calls
- research rounds

### E2.6 Robustness Contract

E2 只冻结第六个 Core Dimension 的 contract 与 metric naming，不制造没有数据基础的空实现。

具体：

```text
badcase_pass_rate
regression_pass_rate
```

依赖 E4 形成 Confirmed Badcase / Regression Set，并在 E5 Benchmark 中正式激活计算。

验收：

- 六个 Core Dimension 都能统一表达
- 每个已激活指标都有明确输入、定义、方向、适用条件、judge/version
- Robustness 在没有 Regression Set 时明确为未激活/不适用，而不是伪造分数
- 核心指标数量保持克制

---

## Phase E3：Post-Run Evaluation

目标：任务结束后自动/显式进入旁路评测，不影响主链。

实施：

```text
RunCompleted
  ↓
EvaluationInputAdapter
  ↓
Core Metric Engine
  ↓
EvaluationReport
```

V1 不要求消息队列；可通过应用服务在 Run closure 后触发。

验收：

- Eval failure 不改变业务 Run status
- Eval failure 不修改 Artifact
- Eval report 明确关联 run_id / task_id / artifacts
- Eval 自身异常可独立记录

---

## Phase E4：Failure Attribution + Badcase

目标：从“知道分数低”升级到“知道哪一层出错”。

实施：

- V1 Failure Taxonomy
- Trace-based attribution rules
- Failure Case → Badcase Candidate → Confirmed Badcase → Regression Set 统一闭环
- Regression Set 入库/文件化

验收：

至少覆盖：

- Requirement omission
- Retrieval miss
- Evidence insufficient
- Context selection error
- Unsupported claim
- Fact conflict
- Citation error

每个归因能够链接到具体 trace/evidence/claim。

---

## Phase E5：Benchmark / Controlled Experiment

目标：把现有 `StrategyEvalRunner` 升级为统一算法实验入口。

实施：

- 固定 Dataset
- Baseline/Candidate
- Experiment Manifest
- Core Metric comparison
- per-case delta
- regression case comparison
- activate `badcase_pass_rate` / `regression_pass_rate`
- quality gate contract

验收：

可以对以下至少一种改造完成完整 A/B：

```text
current heuristic retrieval/router
vs
candidate strategy
```

并输出：

- aggregate delta
- per-case delta
- quality/cost tradeoff
- improved/regressed cases
- reproducibility identity

---

## Phase E6：Algorithm Diagnostic Plugins

目标：仅在对应算法真实进入开发时增加机制诊断。

按需实施：

- Adaptive Router metrics
- C-RAG metrics
- Self-RAG metrics

禁止提前把所有可能论文指标一次性造出来。

---

# 21. 建设优先级

当前项目后续存在 Adaptive Router、C-RAG V2、Self-RAG V2、Context Engineering 等主链改造，因此 Evaluation Foundation 优先级应提升。

推荐顺序：

```text
E0 Audit
  ↓
E1 Contract
  ↓
E2 Core Metrics V1
  ↓
先跑当前系统 Baseline
  ↓
再进入重大主链算法改造
```

E3/E4/E5 可跟随第一轮真实 Baseline 逐步补齐。

关键要求：

> 在大规模修改 Adaptive/C-RAG/Self-RAG 之前，至少应先有可信的 Baseline Dataset + Core Metric Profile + Benchmark Runner。

否则之后无法证明算法改造的收益。

---

# 22. 当前代码的复用 / 升级建议

## 22.1 直接复用

### `backend/eval/rag/strategy/runner.py`

保留：

- StrategyEvalRunner
- matrix execution
- pre-validation
- per-sample isolation
- seed
- warmup
- report aggregation
- baseline comparison

不新建平行 Runner。

### `backend/eval/rag/strategy/metrics.py`

保留：

- MetricRegistry
- MetricDefinition
- IR metrics
- gold requirements
- direction

升级为统一 Core Metric Registry 的基础。

### `backend/schemas/eval_schema.py`

作为 canonical Eval Schema 事实源优先保留。

### `backend/experiments/manifest.py`

作为 reproducibility manifest 继续使用，不建第二套 Experiment Manifest。

---

## 22.2 升级

### `ragas_style_eval.py`

定位调整为：

```text
cheap deterministic RAGAS-compatible proxy
```

后续增加正式 semantic/RAGAS adapter，但不删除 proxy。

### Dataset Schema

补强 Evidence / Claim / Requirement 评测标签。

### Metric Registry

增加：

- dimension
- metric version
- judge type
- required inputs

保持实现轻量。

### Reporting

增加六维分层和 baseline delta，而不是只展示单一 overall。

---

## 22.3 兼容保留，不继续扩展

`backend/eval/rag/capture/*` 与旧 `engine/*` 中仍可作为历史兼容/低成本回归的实现，在 E0 Audit 后决定 canonical path。

原则：

- 不因重复存在就立即大规模删除
- 不继续在 legacy path 新增核心能力
- 新 Core Metrics 只进入 canonical path

---

# 23. 第一版 Golden Dataset 建议

V1 不追求样本数量大，追求标签可靠。

建议组成：

## Normal Cases

覆盖：

- 普通事实查询
- 需要多证据的 section
- 技术方案 generation
- 正常 citation

## Hard Cases

至少包括：

- 历史方案与当前 Excel/Fact 冲突
- Relevant document 被召回但排名低
- Evidence 缺一类关键 requirement
- Evidence 相互冲突
- No-gain 多轮检索
- Writer 生成未经 Evidence 支持的设备/数量
- Citation binding 错误
- Context 超预算导致关键 Fact 被裁剪

原则：

> 一开始可以小，但不能是假 Golden。

所有重要 label 应人工确认或有明确来源。

---

# 24. Baseline 建立要求

Evaluation Foundation V1 完成后，第一件事不是继续开发新功能，而是冻结当前系统 Baseline。

Baseline 至少记录：

- Git commit
- Dataset version/hash
- Index version/hash
- embedding
- reranker
- static retrieval spec
- model
- prompt
- metric profile
- seed

输出：

```text
Baseline Retrieval Report
Baseline Evidence Report
Baseline Grounding Report
Baseline Task Quality Report
Baseline Efficiency Report
Baseline Failure Case List
```

以后每项算法改造必须与该 baseline 或其后继 baseline 比较。

---

# 25. 面试验收标准

Evaluation Foundation 完成后，应能够清楚演示并解释以下内容。

## 25.1 基础架构

能够解释：

- 为什么 Eval 不参与 production mainline
- 为什么 Hard Gate 与 Eval Gate 不同
- 为什么 RAGAS 只是 Metric Adapter
- 为什么 Core Metric 与 Diagnostic Metric 分离

## 25.2 实验可信度

能够展示：

- Dataset version
- Experiment Manifest
- Baseline vs Candidate
- fixed variables / changed variable
- result delta

## 25.3 Failure Attribution

能够拿一个真实 Badcase 解释：

```text
最终错
→ evidence 是否召回
→ evidence 是否足够
→ context 是否携带
→ claim 是否支持
→ root cause
```

## 25.4 算法改造证明

至少完成一个真实 A/B，例如：

```text
Heuristic Router
vs
3B Router
```

或：

```text
single retrieval
vs
C-RAG corrective retrieval
```

要求同时展示质量收益和成本代价。

---

# 26. 防扩张 Gate

以后每个 Evaluation 新需求进入实施前回答四个问题：

1. 它是否增强六个稳定 Core Dimension 中的某一个？
2. 现有 Dataset / Runner / Registry / Manifest 能否复用？
3. 它是不是只服务某一个暂时算法？如果是，能否放 Diagnostic Plugin？
4. 它能否直接提升实验可信度、失败定位或面试技术表达？

如果四个问题都不能明确回答，则默认不进入 Evaluation Foundation 主线。

特别禁止：

> 因为“企业级平台一般有”就加入功能。

本项目的企业级价值来自：

- 指标定义可靠
- 过程可追溯
- 数据版本可靠
- 实验可复现
- 失败可解释

而不是 UI、租户、调度器数量。

---

# 27. 最终目标架构

```text
                         RAG-Agent Mainline
                                │
                         Run Artifacts / Trace
                                │
                                ▼
                    Evaluation Input Adapter
                                │
                                ▼
                     Evaluation Foundation
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
      Core Metrics       Failure Attribution       Dataset
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                ▼
                       Evaluation Report
                                │
                  ┌─────────────┴─────────────┐
                  ▼                           ▼
              Badcase                  Benchmark Compare
                  │                           │
                  ▼                           ▼
           Regression Set              Engineering Decision
```

Core Metrics：

```text
Retrieval
Evidence
Grounded Generation
Task Quality
Efficiency
Robustness
```

Optional Diagnostics：

```text
Adaptive Router
C-RAG
Self-RAG
Future Algorithms
```

---

# 28. 最终冻结结论

1. Evaluation Foundation 首先是面试级技术资产，也是整个 RAG-Agent 算法研发基座。
2. Evaluation 不参与当前业务 Run 的主链决策。
3. 当前 Run 结束后可以执行 Post-Run Evaluation。
4. 算法实验通过现有 StrategyEvalRunner 及其升级能力做 Baseline/Candidate Benchmark。
5. 核心不是建设很多平台功能，而是把六个评测维度做牢。
6. Core Metrics 必须长期稳定，Diagnostic Metrics 随算法按需扩展。
7. RAGAS 是语义指标底座/Adapter，而不是整个评测框架。
8. Trace 用于 Failure Attribution，不存储或评估私有 chain-of-thought。
9. Dataset、Metric、Experiment Manifest、Report 都必须版本化、可追溯、可复现。
10. Badcase 必须逐步沉淀成 Regression Set。
11. 后续 Adaptive-RAG、C-RAG、Self-RAG、Context 等重大改造，应先建立 Baseline，再用同一评测坐标系证明收益。
12. 不建设 Dashboard、多租户、分布式调度、AutoML 等与当前核心目标无关的平台能力。

---

# 29. 下一步实施入口

`E0 — Evaluation Architecture-to-Code Audit` 已于 2026-08-09 完成。

后续只从已确认的 `UPGRADE` / `MISSING` 进入有限实施，不重新打开已冻结的 Evaluation 架构决策。

实施前置顺序保持：

```text
E1 Contract
↓
E2 Core Metrics V1 + Robustness Contract
↓
冻结当前可信 Baseline
↓
重大主链算法 Candidate
↓
E3/E4/E5 逐步闭合 Post-Run / Attribution / Regression / Quality Gate
```

正式 RAGAS Adapter 仍属于后续 Evaluation implementation choice，不因其尚未实现就重新定义为当前架构 Gap。

这样可以保证本 Evaluation Foundation 建设建立在当前真实代码上，而不是重新设计一个理论上漂亮、实际上重复的第二套系统。
