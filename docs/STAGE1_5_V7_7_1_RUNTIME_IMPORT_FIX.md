# Stage 1.5 v7.7.1 Runtime Import Fix

## Problem

The v7.7 refactor injected the shared monotonic timing abstraction into
`AdaptiveRAGRouter`, but the module imports for `MonotonicTimer`, `Timer`,
`elapsed_ms`, and `TextGenerator` were omitted. Python compilation succeeds
because undefined names inside function bodies are resolved at runtime, so the
FakeRAG test path did not expose the defect. The real RAG path instantiated the
router and failed with `NameError: MonotonicTimer is not defined`.

## Fix

- Add the missing timing and generation port imports.
- Add a runtime construction and routing regression test.
- Bump the runtime version to `stage1-full-decoupling-v7.7.1-20260715`.
