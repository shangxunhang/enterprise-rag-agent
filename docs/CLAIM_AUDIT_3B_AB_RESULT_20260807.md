# Claim Audit 3B Semantic Classification A/B Result — 2026-08-07

## 1. Scope

This experiment evaluates the proposal:

- Writer / generation: local Qwen2.5-1.5B
- Claim semantic classification: local Qwen2.5-3B
- Final allow / forbid enforcement: deterministic rules

The experiment intentionally does **not** rerun retrieval or generation. It reuses the exact eight persisted sections and exact Writer Gate bindings from the real E2E run `gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130`. Therefore the independent variable is claim classification, not RAG or Writer output.

This is an A/B correctness experiment. Production `WriterClaimAuditor` / `WriterArtifactIntegrity` has **not** yet been replaced by the 3B semantic classifier.

## 2. Repository identity

- branch: `checkpoint/controlled-architecture-20260728`
- HEAD: `c04e98e80186d2e79810b9ebcd69a990475fbb91`
- dirty tree at final inspection: 237 entries
- no reset / clean / branch switch / commit performed

Relevant current changes:

- `backend/bootstrap/model_factory.py`
  - `SECTION_GENERATION`: `local_1_5b -> local_3b -> deepseek`
  - `REPAIR`: `local_1_5b -> local_3b -> deepseek`
  - `SEMANTIC_GATE`: `local_3b -> local_1_5b -> deepseek`
- `scripts/run_claim_audit_3b_ab.py`
  - persisted-checkpoint A/B runner
  - 3B structured ClaimType classification
  - fail-closed missing / low-confidence behavior
  - batch split and retry for malformed / incomplete structured output
  - structural Markdown label pre-filter

## 3. Source evidence

Source real E2E:

`E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130`

Source checkpoint:

`E:\rag-agent-runtime\gov_cloud_1_5b_writer_retryfix_e2e_20260807_2130\runs\langgraph_checkpoints.sqlite3`

Final strict A/B:

`E:\rag-agent-runtime\claim_audit_3b_ab_final2_20260807_2350`

Primary report:

`E:\rag-agent-runtime\claim_audit_3b_ab_final2_20260807_2350\claim_audit_3b_ab_report.json`

Console logs:

- `E:\rag-agent-runtime\claim_audit_3b_ab_final2_20260807_2350\console.stdout.log`
- `E:\rag-agent-runtime\claim_audit_3b_ab_final2_20260807_2350\console.stderr.log`

Focused security-section strict probe:

`E:\rag-agent-runtime\claim_audit_3b_ab_section7_strict_20260807_2335\claim_audit_3b_ab_report.json`

## 4. Model routing / runtime evidence

Runtime selection for semantic classification:

- selected model: `local_qwen2_5_3b`
- local path: `D:\models\huggingface\llm\Qwen2.5-3B-Instruct`
- requested device: CUDA
- observed `hf_device_map={'': 0}` in isolated audit runs

The isolated A/B therefore kept the 3B model fully on GPU and did not reproduce the earlier 3B Writer CPU-offload condition caused by simultaneous retrieval-model / 1.5B residency.

Final strict A/B model use:

- model calls: 96
- model latency total: 184,497 ms
- models used: only `local_qwen2_5_3b`

## 5. Baseline vs 3B semantic classification

### Baseline deterministic Claim Audit

Across eight persisted sections:

- total violations: 182
- `unclassified_substantive_claim`: 146
- `forbidden_claim_type_generated`: 30
- `claim_type_not_allowed`: 6
- Claim Audit passed sections: 0 / 8

### Final strict 3B semantic A/B

With the same section text and same Gate permissions:

- total violations: 25
- `semantic_unclassified_substantive_claim`: 10
- `forbidden_claim_type_generated`: 14
- `claim_type_not_allowed`: 1
- semantic Claim Audit passed sections: 0 / 8

The important change is not that all sections passed. They did not. The important change is that the large fail-closed classification noise collapsed:

- unclassified: 146 -> 10
- total violations: 182 -> 25

The remaining failures are now much closer to actual business-policy violations or genuine taxonomy / semantic-coverage gaps.

## 6. Per-section result

| Section | Baseline violations | 3B strict violations | Baseline unclassified | 3B unclassified | Main remaining issue |
|---|---:|---:|---:|---:|---|
| 项目背景 | 5 | 1 | 0 | 0 | policy requirement not allowed + upstream human review |
| 建设目标 | 20 | 2 | 14 | 0 | forbidden policy / project schedule claims |
| 总体架构 | 17 | 1 | 14 | 1 | one unresolved semantic/taxonomy classification |
| 计算资源建设方案 | 34 | 4 | 32 | 4 | unresolved semantic/taxonomy classifications |
| 存储资源建设方案 | 23 | 5 | 22 | 5 | unresolved semantic/taxonomy classifications |
| 网络资源建设方案 | 31 | 7 | 20 | 0 | real forbidden project facts/configuration/schedule |
| 安全设计 | 17 | 3 | 12 | 0 | real forbidden configuration / quantity |
| 实施与验收建议 | 35 | 2 | 32 | 0 | real forbidden project fact / technical specification + upstream human review |

## 7. Confirmed deterministic-regex false positives

Examples where the old regex classifier produced materially misleading ClaimTypes and the 3B classifier corrected them:

1. `本项目旨在……制定一套全面且高效的政务云建设方案。`
   - deterministic regex: `current_project_quantity`
   - 3B semantic: `general_method`
   - root cause: ordinary phrase `一套方案` was treated as a project resource quantity.

2. A sentence saying the document will cover `投资概算` as one chapter/content area.
   - deterministic regex: `current_project_cost`
   - 3B semantic: `general_method`
   - root cause: mentioning the topic `投资概算` was treated as an actual project cost claim.

3. Large numbers of ordinary architecture / method / descriptive sentences previously became `unclassified_substantive_claim` solely because no regex matched them.

Conclusion: the deterministic regex classifier is too narrow and lexically brittle to serve as the sole natural-language ClaimType classifier for real long-form output.

## 8. Confirmed true violations retained by 3B + deterministic policy

The 3B experiment did **not** simply loosen the Gate. Known Writer overreach remained visible after semantic classification and deterministic allow/forbid enforcement.

### Network section

Confirmed forbidden examples include:

- `选用华为 NE40E-X 系列路由器...` -> `current_project_configuration`
- `新增互联网出口带宽至 1Gbps...` -> current-project factual claim, forbidden under this Gate
- setting dual-line access as an already-decided project design -> current-project factual claim
- `利用 Nginx 和 HAProxy 实现...` as an already-decided project implementation -> current-project factual claim
- `项目启动后三个月内完成...` -> `current_project_schedule`
- physical installation / logical configuration asserted as the project design -> `current_project_configuration`

### Security section

After strengthening the semantic contract, the focused strict probe correctly retained:

- `我们将选用华为公司的高端路由器和交换机...` -> `current_project_configuration`
- `我们将配备多台高性能防火墙...` -> `current_project_configuration` + `current_project_quantity`

These claims remain forbidden by the frozen Writer Gate.

### Implementation / acceptance section

Examples retained as forbidden include:

- asserting all predetermined functional modules operate normally as a project fact
- `RTO <= 3 分钟 / RPO <= 1 小时` without current-project evidence -> forbidden technical specification under the frozen Gate

## 9. Important 3B stability finding

A naive “replace regex with 3B” implementation is **not acceptable**.

During an early security-section probe, the same sentence `我们将选用华为公司的高端路由器和交换机` was classified once as `current_project_configuration`, but in a later weak-prompt retry was classified as `general_method`.

The experiment then strengthened the semantic contract with explicit rules:

- `本项目 / 我们将 / 将 / 拟 / 计划` + `采用 / 选用 / 配置 / 部署 / 安装 / 配备 / 接入` is a current-project commitment, not a general method.
- `我们将选用华为路由器` must be `current_project_configuration`.
- `我们将配备多台防火墙` must include `current_project_configuration`, and may also include `current_project_quantity`.
- only suggestion/reference language such as `建议 / 可采用 / 可考虑 / 通常 / 参考 / 应根据实际情况` can normally be treated as non-committed `general_method` / `typical_architecture`.

The structured-output protocol was also made fail-closed:

- missing confidence is not accepted as success
- malformed / missing batch results are split and retried
- unresolved items remain semantic-unclassified rather than silently passing

Therefore the tested architecture is **3B semantic classification under an explicit policy contract + deterministic enforcement**, not “3B says PASS/FAIL”.

## 10. Remaining 10 semantic-unclassified sentences

The final strict run still contains 10 fail-closed semantic-unclassified items.

They are concentrated in sections 3-5 and include two classes:

1. generic descriptive statements that do not map cleanly to the current ClaimType taxonomy, e.g. descriptions of user/department responsibilities or system coverage;
2. template-like / structural descriptions such as “简要描述……情况” that should likely map to `template_structure`, but the current semantic taxonomy/prompt did not classify consistently.

This is now a much smaller problem than the original 146 regex-unclassified items, but it still blocks fail-closed Claim Audit passage. It should be resolved by tightening ClaimType taxonomy / definitions and structural-text handling, not by treating empty classification as PASS.

## 11. Upstream human-review blockers independent of Claim classifier

Two sections already carry `gate.human_review_required=true` before post-generation Claim Audit:

### Section 1 项目背景

- delivery mode: `qualified`
- reason: `fact_status_requires_qualification`
- required qualifier: `该信息尚未确认`
- human review required: true

### Section 8 实施与验收建议

- delivery mode: `qualified`
- reason: `fact_status_requires_qualification`
- required qualifier: `该信息尚未确认`
- human review required: true

Therefore changing Claim classification alone cannot make the full document formally deliverable. These upstream Evidence Governance / fact-status decisions must also be examined.

## 12. Independent citation blocker remains

The source E2E also had an independent citation failure:

- `安全设计` had zero final citation bindings
- it is a citation-required section

This is outside the ClaimType classifier experiment and still needs a separate fix.

## 13. Regression evidence

Existing Stage E regression suite after the routing / experiment changes:

```text
44 passed in 1.02s
```

Command:

```text
python -m pytest -q \
  backend/tests/test_stage_e_claim_classifier_fail_closed.py \
  backend/tests/test_stage_e_writer_governance.py \
  backend/tests/test_stage_e_claim_audit_integrity.py
```

The current production deterministic Stage E contracts still pass; the 3B semantic A/B has not yet replaced production audit recomputation.

## 14. Conclusion

Measured evidence supports the user's proposed model specialization **with an important refinement**:

```text
1.5B Writer
  -> 3B semantic ClaimType classifier
  -> deterministic allowed / forbidden enforcement
  -> deterministic citation / evidence / release gates
```

It is materially better than regex-only semantic classification, because it reduced the major false-positive/unclassified burden while retaining known true Writer overreach.

However, a plain “3B decides the Gate” design is not safe enough. Production integration must preserve:

- explicit ClaimType semantic contract
- fail-closed malformed / missing classifications
- high-confidence hard constraints for obvious current-project commitments
- deterministic final policy decision
- persisted classifier/model/prompt/version lineage
- integrity validation compatible with `WriterArtifactIntegrity`

Next correctness blockers after this experiment are:

1. resolve the remaining 10 semantic/taxonomy gaps;
2. add a targeted repair loop driven by 3B Claim violations so the 1.5B Writer can rewrite forbidden claims into allowed/qualified language;
3. separately fix `安全设计` citation binding;
4. inspect why sections 1 and 8 are upstream `human_review_required=true` and decide whether that is correct evidence governance for this E2E fixture;
5. only after those are closed, integrate semantic audit into the live mainline and rerun the same real E2E to Document Hard Gate / Release Gate.
