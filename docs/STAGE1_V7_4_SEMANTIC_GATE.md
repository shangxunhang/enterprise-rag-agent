# Stage 1 v7.4 — Semantic Gate Refactor

## Goal

Replace business chapter-name/keyword hard gates with a layered quality policy:

1. **Deterministic hard gate** — objective runtime facts only.
2. **1.5B semantic gate** — scope, proposal-vs-commitment and severity classification.
3. **Supervisor aggregation** — `success`, `partial_success`, or `failed`.

## Hard failures retained in code

- Model/tool call failure.
- Empty required output.
- Unrecoverable truncation.
- Required citation missing.
- Citation target or Claim-Evidence grounding invalid.
- Unsupported explicit quantities or high-confidence fabricated project commitments.

## Semantic issues

The local model returns structured JSON issues with:

- `issue_type`
- `severity`: `warning | soft_failure | hard_failure`
- `claim`
- `reason`
- `recommended_action`
- `confidence`

Python normalizes the output. Scope, style and length issues can never become hard failures solely because the model says so. Only a narrow allow-list of unsupported quantitative/resource/project-fact claims may become hard, and only above the confidence threshold.

## Status policy

- `success`: all hard checks pass and no semantic soft issues.
- `partial_success`: all hard checks pass, but semantic soft issues or warnings remain.
- `failed`: objective hard check fails or a high-confidence unsupported factual commitment remains after repair.

## Configuration

```env
ENABLE_SEMANTIC_GATE=true
SEMANTIC_GATE_MODEL_NAME=local_qwen2_5_1_5b
```

If the semantic model is unavailable or its JSON cannot be parsed, the runtime falls back conservatively:

- unsupported explicit quantities remain hard failures;
- non-quantified resource commitments become soft failures;
- length excess becomes a warning.

## Expected console output

```text
[SemanticGate] START section=... model=local_qwen2_5_1_5b candidates=... overlong=...
[SemanticGate] END   section=... decision=pass|warn|partial|fail issues=... fallback=False
[SectionValidation] section=... status=partial_success hard_failures=[] warnings=[...]
[HardGate] passed=True failures=[] warnings=[...]
```
