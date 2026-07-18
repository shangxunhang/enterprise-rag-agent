# RAG Plugin Migration Step 6

## Scope

Step 6 moves Self-RAG from the temporary standalone RAG answer position to the
Agent final-section path:

```text
section generation
→ deterministic CitationLinker
→ existing citation repair / grounded regeneration
→ configured GenerationChecker
→ configured RepairStrategy
→ remove untrusted markers
→ rebuild CitationBinding
→ GenerationChecker recheck
→ section validation / HardGate
```

## Configuration

The online profile schema is now `online_rag_pipeline_config_v5` and requires
an enabled `repair_strategy`.

Built-in strategies:

- `noop_repair@v1`
- `local_rewrite@v1`

Normal profiles select `noop_generation + noop_repair`. `self_rag_v1` and
`c_rag_self_rag_v1` select `self_rag_lite + local_rewrite`.

## Safety properties

A repaired section is accepted only when:

1. the candidate is non-empty;
2. it is not truncated;
3. required citations can be rebuilt as supported bindings;
4. the configured checker passes the rewritten candidate.

Model-emitted citation markers are discarded before binding. A repair failure
or a request for more retrieval is recorded as an advisory warning rather than
silently forcing a successful document.

## Compatibility

Legacy `enable_agent_self_rag` is retained only as audit metadata. The selected
profile is the source of truth.

The standalone RAG generation path still supports the same checker and repair
ports, but `run_demo` uses `generate_answer=False`; therefore Agent-level final
section checking is the production path for SchemeWriter.
