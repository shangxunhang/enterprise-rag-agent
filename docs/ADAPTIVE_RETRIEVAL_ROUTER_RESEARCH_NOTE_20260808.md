# Adaptive Retrieval Router 研究备忘（2026-08-08）

## 状态

本文只记录候选设计思想，**当前不实施、不修改生产主链**。待重新读完 Adaptive-RAG、CRAG、Self-RAG 三篇论文并完成职责边界审计后，再决定是否进入实现。

## 1. 当前实现事实

当前 `AdaptiveRetrievalPlanner` 不是 Adaptive-RAG 原论文的严格复现。

现有逻辑是 heuristic-based query transform routing：

```text
Query
→ 确定性 if/elif 规则
→ identity / multi_query / hyde
```

主要规则包括：

- 短查询、歧义查询、多方面查询、正式文档类任务倾向 `multi_query`；
- 原理、机制、架构等抽象查询倾向 `hyde`；
- 其他查询使用 `identity`。

因此当前更准确的名称是：

> Adaptive Query Transformation Planner

而不是 Adaptive-RAG strict reproduction。

## 2. 与原论文的关键差异

Adaptive-RAG 原论文的核心思想是：

```text
Query
→ Query Complexity Classifier
→ No Retrieval / Single-step Retrieval / Multi-step Retrieval
```

论文 adapt 的是 **retrieval computation / QA strategy complexity**，而当前项目 adapt 的是 **query transformation mode**。

企业文档生成场景与 open-domain QA 不完全一致，尤其存在 evidence grounding、citation、可追溯性要求，因此不应机械照搬论文的 `No Retrieval / Single-step / Multi-step` 三档。

项目后续应明确表述为：

> 复现论文核心思想并做业务化 adaptation，而不是声称完整复现论文实现。

## 3. 候选改造方向

把当前 `AdaptiveRetrievalPlanner` 的职责从“Query Transform 选择”提升为：

> Query-conditioned Retrieval Strategy Routing

即根据 Query 和任务上下文直接选择检索策略，而不是先显式预测 simple / complex。

候选策略空间第一阶段保持克制，不动态切换底层索引结构：

```text
DIRECT / NO_RETRIEVAL
SINGLE_RETRIEVAL
EXPANDED_RETRIEVAL
ITERATIVE_RETRIEVAL
```

其中 `identity / multi_query / hyde` 下沉为具体 retrieval strategy 内部的 query transformation 参数，而不再作为 Adaptive 层的全部输出。

第一阶段仍固定现有强 baseline：

```text
Dense + BM25
→ RRF
→ Parent-Child
→ Rerank
```

Adaptive 层优先只控制：

- 是否需要检索；
- 单次还是多轮检索；
- 是否需要 query expansion；
- 允许的检索/纠错预算。

不要为了“Adaptive”无限扩展到 HNSW / IVF / BM25 / Dense 等底层索引动态切换。

## 4. 3B Teacher / Shadow Router 思路

下一步若实施，不直接用 3B 替换 heuristic，而采用 Shadow Router：

```text
Query
├─ Heuristic Router → 当前真实执行策略
└─ Qwen2.5-3B Router → shadow strategy，仅记录，不控制主链
```

目的：先验证 3B 路由是否真的优于确定性规则，避免未经验证就替换生产逻辑。

建议新增稳定 Model Role：

```text
RETRIEVAL_STRATEGY_ROUTING
```

3B Router 输出至少包含：

```text
selected_strategy
confidence
reason
candidate_strategies（可选）
```

## 5. Trace / 数据闭环

现有 Trace 已经能够记录：

- `retrieval_plan`；
- `query_transform_mode`；
- heuristic `reason` / `signals`；
- rewritten queries / HyDE query；
- C-RAG evidence assessment；
- corrective retrieval；
- retrieval result / context / citation；
- model latency / token usage；
- Self-RAG 相关信号。

但这些数据目前是“运行观测数据”，还不是可直接训练 Router 的监督数据。

后续需要增加两类稳定数据：

### 5.1 Router Decision

```text
query
query_context
baseline_router_type
baseline_strategy
baseline_reason
baseline_signals
teacher_model
teacher_strategy
teacher_confidence
teacher_reason
strategy_disagreement
```

### 5.2 Strategy Outcome

同一个 Query 对不同策略分别记录：

```text
strategy
retrieval_metrics
crag_metrics
generation_metrics
self_rag_metrics
latency_ms
token_usage
cost
```

然后离线计算：

```text
optimal_strategy
label_source
label_confidence
```

## 6. Ground Truth 原则

**3B Teacher 的判断不能直接等于 Ground Truth。**

正确闭环：

```text
3B Teacher 建议
+
同一 Query 多策略实际运行结果
+
Retrieval / CRAG / Self-RAG / Cost Eval
↓
经验最优策略标签
```

否则只能得到“模仿 3B”的蒸馏数据，而不能证明训练出的 Router 能提升真实 retrieval utility。

## 7. 现有项目基础设施判断

当前项目已经具备大部分基础：

- Model Gateway 已注册 Qwen2.5-3B；
- `RetrievalPlannerPort` 已存在，可替换 Planner 实现；
- identity / multi-query / HyDE 已实现；
- `retrieval_plan_overrides` 支持实验强制策略；
- `StrategyEvalRunner` 已支持多策略矩阵对照；
- 已有 Hit@K、Recall@K、MRR、NDCG、context keyword hit、latency 等 retrieval 指标；
- C-RAG 已输出 evidence quality / correction 信号；
- Self-RAG 已输出 supported / rewrite / retrieve-more 信号；
- Trace v2、RAG run record、DataCapture v2、training_ready / eval_ready 生命周期均已存在。

主要缺口不是新架构，而是数据闭环胶水层：

1. 3B Shadow Router；
2. Router Decision 稳定记录；
3. 多策略 Strategy Outcome 聚合；
4. empirical best-strategy label 生成；
5. Router Dataset Builder；
6. 后续轻量 Router 微调和 A/B。

## 8. 候选演进叙事

```text
V1  Heuristic Router
    ↓
    写死规则 + Trace

V2  3B Shadow Router
    ↓
    Rule vs 3B disagreement

V3  Counterfactual Strategy Evaluation
    ↓
    identity / multi-query / HyDE / 后续 iterative 多策略实际执行

V4  Router Dataset
    ↓
    empirical optimal-strategy labels

V5  Fine-tuned Lightweight Router
    ↓
    与 heuristic / 3B prompt router 做 A/B
```

## 9. 面试叙事原则

不能说：

> “我完整复现了 Adaptive-RAG。”

应说：

> “我复现了 Adaptive-RAG 根据 Query 动态分配 retrieval computation 的核心思想，但企业文档生成要求 evidence grounding 和 citation，因此没有机械照搬原论文的 routing space。我把它适配成 query-conditioned retrieval strategy routing，并通过 Trace、counterfactual strategy evaluation 和后续 Router fine-tuning 验证这种 adaptation 是否成立。”

## 10. 当前冻结结论

- **暂不改代码。**
- 当前 heuristic 继续作为 production baseline。
- 继续阅读 CRAG、Self-RAG 原论文。
- 三篇论文读完后统一审计：论文机制、当前实现、业务 adaptation、职责重叠和数据闭环。
- 只有完成上述审计后，才决定是否实施 Shadow Router 和后训练闭环。
