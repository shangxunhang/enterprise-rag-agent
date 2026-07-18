# Stage 1 v7.5 — Minimal Cross-Domain Hard Gate

## Goal

Close stage 1 around the technical mainline rather than vertical-domain content review:

`ProjectInput -> RAG -> section generation -> citation binding -> runtime integrity gate -> output`

## Hard failures retained

- RAG/tool/model call failure
- Empty required section
- Unrecoverable token-limit truncation
- Missing required section
- Required citation section has no valid binding
- Citation points to a missing chunk or section
- Claim-evidence binding is not verified
- Invalid output structure or incomplete workflow

## Advisory-only checks

- Section length recommendation
- Semantic scope drift
- Resource/fact wording
- Domain-specific content quality
- Semantic judge results

These are recorded as warnings or `partial_success`; they do not fail stage 1.

## Semantic gate

`ENABLE_SEMANTIC_GATE=false` by default. When explicitly enabled, the 1.5B judge runs once per section for observability/data capture only. It does not rewrite content and cannot create a hard failure.

## Truncation recovery

1. Initial section generation.
2. If `finish_reason=length`, regenerate the entire section as a compact draft; do not append a continuation.
3. If the compact draft still hits the token limit, keep only a sufficiently long prefix ending at a complete sentence/list-item boundary.
4. Record `truncation_recovered:complete_sentence_prefix` and return `partial_success`.
5. If no usable complete prefix exists, keep the hard failure.
