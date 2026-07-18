# RAG 插件化迁移 Step 5：EvidenceGrader 与 Self-RAG Checker

## 1. 本阶段目标

将现有 C-RAG-lite 与 Self-RAG-lite 从 `ParentChildRAGEngine` 中的固定对象构造，迁移为配置驱动插件：

```text
Reranked Parent Candidates
→ EvidenceGrader
→ ContextPacker
→ Generation
→ GenerationChecker
```

职责严格分离：

- `evidence_grader`：判断检索证据质量，可执行保留、降级排序和过滤；
- `generation_checker`：判断答案是否忠实、相关，输出是否需要重写或补检索；
- `repair_strategy`：仍是后续阶段，不在本阶段执行答案重写或重新检索；
- Agent 的 `CitationLinker`、章节校验和 `DocumentHardGate` 不在本阶段修改。

## 2. 配置 Schema

Schema 升级为：

```text
online_rag_pipeline_config_v4
```

`evidence_grader` 与 `generation_checker` 变为必需、显式且启用的组件。禁用质量能力不再使用 `null`，而是选择 no-op 插件：

```yaml
evidence_grader:
  name: noop_evidence
  version: v1
  enabled: true
  params: {}

generation_checker:
  name: noop_generation
  version: v1
  enabled: true
  params: {}
```

这样每一份 Profile 都能完整描述真实运行栈，不再依赖 Profile 外的布尔开关补全行为。

## 3. 新增插件

### 3.1 EvidenceGrader

```text
crag_lite@v1
noop_evidence@v1
```

`crag_lite@v1` 封装原有 `CRAGJudge`，配置参数包括：

```yaml
params:
  max_judge_chunks: 8
  drop_irrelevant: true
  keep_at_least: 1
  use_llm: true
  fallback_to_deterministic: true
```

输出继续保留原有：

- `c_rag_judgement`；
- `relevance_label`；
- `decision`；
- `score`；
- `corrective_action`；
- 过滤后连续 rank；
- Parent-Child 候选字段和 Citation 所需元数据。

### 3.2 GenerationChecker

```text
self_rag_lite@v1
noop_generation@v1
```

`self_rag_lite@v1` 封装原有 `SelfRAGJudge`，输出继续使用兼容的 `self_rag` 结构：

- `is_supported`；
- `faithfulness_label`；
- `answer_relevance_label`；
- `need_rewrite`；
- `need_retrieve_more`；
- `unsupported_claims`；
- `problems`；
- `score`。

本阶段只输出检查结果，不自动修改答案。

## 4. 新增 Profile

```text
c_rag_v1.yaml
self_rag_v1.yaml
c_rag_self_rag_v1.yaml
```

普通 `hybrid/rag_fusion/hyde` Profile 显式选择两个 no-op 插件，因此原有主链路行为保持不变。

## 5. 兼容字段

以下旧请求字段暂时保留：

```text
enable_crag
enable_self_rag
crag_max_judge_chunks
crag_drop_irrelevant
retrieval_strategy=c_rag/self_rag/...
```

在配置驱动运行时，这些字段不会覆盖 Profile，Trace 中记录：

```text
legacy_quality_overrides:
  ignored_by_configured_quality_plugins: true
```

Adaptive-RAG Router 目前仍保留兼容路由输出，但不能动态替换已构建的质量插件；Adaptive Router 本身将在后续单独插件化。

## 6. Trace 与审计

`pipeline_components` 新增：

```text
evidence_grader
generation_checker
```

运行 Trace 新增：

```text
configured_evidence_grader
configured_generation_checker
legacy_quality_overrides
```

EvidenceGrader 执行记录包括：

```text
input_count
output_count
max_judge_chunks
drop_irrelevant
keep_at_least
use_llm
fallback_to_deterministic
report_method
```

GenerationChecker 执行记录包括：

```text
enabled
mode
use_llm
llm_available
fallback_to_deterministic
legacy_enable_self_rag
legacy_flag_ignored
```

## 7. 测试结果

专项测试：

```text
47 passed
```

不携带用户真实 Parent-Child 数据文件的完整分享环境：

```text
132 passed, 1 deselected
```

被排除项仍依赖：

```text
data/processed/parent_child_chunks/child_chunks.jsonl
```

## 8. 本阶段验收标准

- YAML 可切换 no-op 与 C-RAG EvidenceGrader；
- YAML 可切换 no-op 与 Self-RAG GenerationChecker；
- Pipeline 和 Engine 不直接构造 `CRAGJudge` 或 `SelfRAGJudge`；
- 旧质量布尔参数不能覆盖 Profile；
- EvidenceGrader 过滤后 Parent-Child 与 Citation 元数据不丢失；
- 普通 Profile 行为不变；
- 插件名称、版本、参数和执行结果进入 Trace；
- CitationLinker、HardGate 和 Repair 逻辑无本阶段改动。
