# Stage 1 v7.5.1 Final Status Contract

## Fixed defect

A `partial_success` workflow could still print a fabricated `SUB_AGENT_FAILED`
error in `run_demo.py`.

The cause was twofold:

1. The demo renderer treated every status other than `success` as failure.
2. Nested Pydantic enum values were dumped in Python mode, so
   `ExecutionStatus.SUCCESS` was compared as a string and misclassified.

## Contract

- `success` and `partial_success` are successful terminal states.
- Successful terminal states must expose `error = null`.
- Only failed terminal states may synthesize a fallback sub-agent error.
- Nested runtime payloads are serialized with `model_dump(mode="json")`.

## Verification

- 42 tests passed.
- `compileall` passed.
- Enum normalization smoke test passed.
