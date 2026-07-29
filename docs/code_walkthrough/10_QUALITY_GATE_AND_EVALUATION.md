# 10 质量门禁与评测

## 本文范围

区分在线 runtime gate、advisory、hard failure 与离线评测；解释 RAG quality、Self-RAG、证据充分性、citation、高风险事实、文档硬门、semantic、truncation、final review 及指标。

## 当前事实结论

- [PRODUCTION] 文档硬门是 deterministic authoritative gate；semantic gate 在 Stage 1 明确 advisory-only。
- [INTEGRATED] RAG 的 CRAG assessor/evidence sufficiency gate 位于检索链；Self-RAG-lite 位于生成后的 checker plugin。
- [BROKEN] `quality/plugins.py:13` 导入不存在的 `SelfRAGJudge`，导致 `AgentQualityFactory` 和大量生产/RAG测试无法导入。
- [STANDALONE] V1 使用自己的 paragraph precheck、document verifier、assembly validator、final artifact review，不复用生产 quality plugin registry。
- [IMPLEMENTED_ONLY] `backend/eval` 是离线评测，不参与 runtime pass/fail。

## 入口

生产组合：

```text
backend/bootstrap/agent_quality_factory.py:AgentQualityFactory.build()
→ apps/enterprise_document/config/grounded_generation_v1.yaml
→ apps/enterprise_document/quality/registry.py:build_generation_plugin_registry()
```

最终文档门：

```text
apps/enterprise_document/services/document_gate.py:evaluate_scheme_draft() [37-524]
```

离线 RAG：

```text
eval/rag/capture/rag_eval_runner.py:RAGEvalRunner.evaluate_file() [129-322]
eval/rag/strategy/runner.py:StrategyEvalRunner.run() [282-426]
```

## Gate 表

| Gate | 输入 | 输出 | Hard/Soft | 触发位置 | 失败结果 | 生产调用 |
|---|---|---|---|---|---|---|
| RAG quality judge | query/results/context | scores/assessment | soft/route | RAG pipeline | corrective/metadata | [INTEGRATED] |
| CRAG sufficiency | EvidenceAssessment | route | retrieval hard route | after rerank | corrective/insufficient | [INTEGRATED] |
| Self-RAG-lite checker | answer/context/citations | check/action | soft + route | section generation | retrieve_more/rewrite | [BROKEN] |
| evidence gate | active evidence/citation required | route/partial | hard for required evidence | Section subgraph | insufficient section | [INTEGRATED] design |
| citation occurrence | final text/bindings/canonical citations | named checks | hard | section/document finalization | reject | [INTEGRATED] |
| high-risk fact | claims/trusted ledger | issues | hard deterministic | generation/document gate | reject | [INTEGRATED] |
| truncation | text/finish reason/JSON/sentence | TruncationCheck | hard for artifact | generation + document | retry/partial/reject | [INTEGRATED] |
| semantic section | content/project/evidence | SemanticGateResult | advisory in Stage 1 | section generation | warning/repair hint | configurable |
| document hard gate | SchemeDraft + citations/evidence | HardGateResult | hard | top graph | deliverable/rejected | [INTEGRATED] design |
| V1 paragraph precheck | paragraph/claims/facts | issues | hard local | streaming writer | stop before next paragraph | [STANDALONE] |
| V1 document verify | state/claims/facts | conflicts | hard/report | closure | partial/failed report | [STANDALONE] |
| V1 final artifact review | assembly/report | review result | hard review | closure | review failed flag | [STANDALONE] |
| RAG eval | capture/golden | metric report | offline | eval scripts | report only | [IMPLEMENTED_ONLY] |
| strategy eval | dataset/matrix/runtime | experiment report | offline | strategy runner | comparison only | [IMPLEMENTED_ONLY] |

## RAG Quality Judge

`rag/judge/rag_quality_judge.py` 只有 `RagQualityJudge`（class `17`，`evaluate()` `276-517`）。它结合 query/context lexical overlap、retrieval score normalization、keyword hit、parent/doc diversity、citation/answer信息生成质量结果。当前文件没有 `SelfRAGJudge`，这是 import 失败的直接证据。

检索生产组合实际使用 plugin `CRAGEvidenceAssessor` 等，不应把 `RagQualityJudge`、CRAG assessor 和离线 RAGAS proxy 写成同一个对象。

## Self-RAG

`SelfRAGLiteGenerationCheckerPlugin`（`quality/plugins.py:25-128`）按设计包装 `SelfRAGJudge`，输入 query/answer/context/citations/bindings/runtime context，输出 pass/retrieve-more/rewrite 等。因为 backend class 不存在，插件模块当前不可导入。

所以准确状态是：

- 策略/插件代码：[IMPLEMENTED_ONLY]
- Factory 配置接线：[INTEGRATED] 设计
- 当前运行：[BROKEN]
- 不是独立 Agent。

## Evidence 与 Citation Hard Gates

Section evidence gate：要求引用的 section 若 active assessment 不足，直接生成 explicit insufficient section，不允许无依据 writer。

Document gate `evaluate_scheme_draft()` 检查：

- non-empty、required sections；
- not truncated；
- citation required/present；
- canonical citation ID 唯一且 binding 来源 lineage 匹配；
- marker occurrence 与 binding 数量/目标/claim 一一对应；
- required citation sections 有 binding；
- grounding verified；
- high-risk fact support；
- section statuses、evidence sufficiency、artifact integrity；
- semantic issue 作为配置允许的 advisory。

`CitationOccurrenceIntegrityGate` 以 occurrence identity（section/paragraph/offset/citation id）映射 binding，解决相同 marker ID 多次出现时的“一条 binding 重用”漏洞。

## Truncation 与 Semantic Gate

`SectionGenerationService.generate_section()`：

1. model response 后检测 truncation；
2. recovery service 做 compact full retry/压缩；
3. citation insert/bind/repair；
4. semantic judge（可配置）；
5. checker/repair；
6. citation 重建；
7. 若非 marker 正文变化，再跑 semantic recheck；
8. artifact integrity authoritative。

注释明确 Stage 1 semantic gate advisory-only；确定性 high-risk/citation/truncation 检查不能被 semantic model 的“通过”覆盖。

## Standalone 质量闭环

- `ParagraphPrecheckService.check()`：在持久下一段前验证 claim authority、设备/数量等 hard facts。
- `ClaimPipeline`/`IncrementalClaimPipeline`：claim extraction、authority、生命周期与增量复用。
- `DocumentVerifierService.verify()`：跨 section 与 FactSnapshot 冲突。
- `DocumentAssemblyValidator.validate()`：assembly、report、section refs 一致。
- `FinalArtifactReviewService.review()`：正文、占位符、assembly 和 vendor marketing language。
- `TaskStatusCalculator` 聚合 accepted/failed/waiting 状态为 success/partial/failed。

这些服务真实执行，但事实/模型输出来自 Scenario fixtures。

## 离线评测与指标

`RAGEvalRunner` 从 capture JSONL 生成 `RAGEvalResultSchema/ReportSchema`，聚合：

| 指标 | 仓库语义 |
|---|---|
| context precision | 选中 context 中与 reference/keywords 匹配的比例 proxy |
| context recall proxy | reference/期望证据被检索覆盖的 proxy |
| faithfulness proxy | answer claims 与 context/citation 支持的 proxy |
| answer relevance | query 与 answer 的相关性 proxy |
| citation coverage | answer/claim 的 citation 覆盖 |
| overall | 已定义子分数按实现聚合，不是外部标准自动等价 |

`retrieval_eval.py` 另计算 hit@k、recall@k、MRR、context keyword hit；strategy metrics 还有 NDCG、answer keyword hit、citation/result count、latency。名为 proxy 的指标不能宣称是人工真值 precision/faithfulness。

## 完整调用链

```text
[ONLINE]
RAG candidates → rerank → CRAG assessment → corrective gate
→ context/evidence → Section evidence gate
→ model generation → truncation + semantic advisory + Self-RAG checker
→ citation/high-risk deterministic checks
→ document hard gate → deliverable/rejected

[OFFLINE]
capture/golden dataset → eval case matching
→ retrieval/generation proxy metrics
→ sample results → aggregate report/comparison
```

## Schema 流转

`EvidenceAssessment/GenerationCheck/SemanticGate/TruncationCheck/CitationIntegrity/HardGateResult` 都是不同阶段的结果；最终 document status 只能由 hard gate/TaskStatus 聚合生成。离线 `RAGEvalResultSchema` 不回写 runtime GraphState。

## 状态修改

在线 gate 将 checks/issues/status 写 candidate GraphState 或 section metadata；通过顶层 write contract commit。离线 eval 只写报告。V1 quality 通过 StateGateway 写 verification/artifact/task/final domains。

## 失败、重试与恢复

- evidence 不足 → retrieval corrective 或 partial，不等同技术 retry；
- truncation → generation recovery budget；
- Self-RAG/rewrite → section budget；
- deterministic hard gate fail → rejected artifact，不用换模型掩盖；
- semantic invalid JSON → conservative fallback/advisory；
- import missing → 启动/收集硬失败，无 runtime fallback。

## 配置来源

- `apps/enterprise_document/config/grounded_generation_v1.yaml`
- `ENABLE_SEMANTIC_GATE`
- RAG static/gate YAML
- ProjectInput citation/high-risk requirements
- eval config/dataset manifest/experiment matrix
- V1 Scenario + FactSnapshot/Template

## 对应测试

质量相关测试遍布 `stage1/test_advisory_policies.py`、`stage1/test_workflow_integrity.py`、`test_rag_config_driven_quality_plugins.py`、`test_p1_high_risk_fact_and_citation_integrity.py`、`test_citation_binding_regression.py`、`test_rag_strategy_eval_runner.py` 和 V1 writer/document closure。当前 production/RAG quality 测试大多被 `SelfRAGJudge` 阻塞；V1 quality 专项通过。

## 尚未实现或无法确认

- `SelfRAGJudge` 缺失，无法确认预期 backend 的真实算法。
- semantic model gate 是否在某环境开启取决于 env；默认 `.env.example=false`。
- 代理指标不等于真实业务人工验收。

## 常见误解

- advisory semantic issue 不可覆盖 deterministic hard failure。
- CRAG assessor ≠ Self-RAG checker。
- 离线 eval 分数不会自动改变在线路由。

## 推荐下一篇

[11_CONFIGURATION_AND_REGISTRY.md](11_CONFIGURATION_AND_REGISTRY.md)。
