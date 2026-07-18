# RAG strategy evaluation data

The formal file expected by the example experiment matrix is:

```text
data/eval/rag_strategy_eval_v1.jsonl
```

Do not rename the `.example.jsonl` file into the formal eval set without replacing every placeholder with reviewed gold labels. Using one strategy's retrieved results as gold would contaminate the comparison.

Each line uses `rag_strategy_eval_sample_v1` and should identify at least one verified expected document, parent chunk, child chunk, context keyword, or answer keyword.
