# RAG 质量插件迁移：当前职责边界

> 本文已按当前架构重写，不再描述早期兼容接口。

## 1. 当前数据流

```text
Parent Rerank
├─ current_reranked ───────────────────────────────┐
└─ EvidenceAssessor → EvidenceAssessment           │
                       ↓                           │
              CorrectiveRetrievalGate              │
                       ↓ insufficient + budget     │
              CorrectiveQueryPlanner               │
                       ↓                           │
              Additional Retrieval                 │
                       ↓                           │
                 Merge + Rerank ───────────────────┘
                       ↓
                  ContextGate
                       ↓
                  ContextPacker
```

Pipeline 始终持有证据集合。质量评估与证据是并行信息，评估对象不能成为新的证据容器。

## 2. 插件职责

### EvidenceAssessor

输入 rerank 后的证据，只输出：

- `sufficient`；
- `confidence`；
- `reason`；
- `item_judgements`；
- `report` 与审计元数据。

它不能改变证据数量、顺序、内容、rank 或 metadata，也不能规划纠错查询。

### CorrectiveRetrievalGate

只根据评估结果、纠错预算和已完成轮次决定是否开启下一轮纠错。

### CorrectiveQueryPlanner

只在 Gate 开启后生成补检索查询，不能提前决定是否纠错。

### ContextGate / ContextPacker

负责决定证据是否可以进入上下文，以及在 token/字符预算内如何打包。未来如果需要独立的证据选择策略，应在这里增加 `EvidenceSelector`，不能把选择权放回 Assessor。

## 3. 配置边界

静态检索配置分别声明：

```text
evidence_assessor
corrective_retrieval_gate
corrective_query_planner
context_gates
context_packers
```

Assessor 配置只包含评估参数，例如判断条数、模型开关、置信度阈值和最少相关证据数，不包含过滤或排序策略。

## 4. 契约验收

- `EvidenceAssessment` 类型没有证据集合字段；
- Assessor 调用前后，输入证据数量、顺序与深层内容完全一致；
- 纠错循环显式返回 `final_reranked`、`final_assessment` 和 trace；
- `RetrievalStageResult.results` 来自 `final_reranked`；
- 每轮追加检索后必须重新 rerank、重新评估；
- Trace 能独立展示证据数量与评估标签，但评估不会反写证据。
