# Online RAG strategy experiments

`online_strategy_matrix_v1.yaml` compares registered online pipeline profiles on one fixed eval set and one fixed index version.

Before a real run:

1. Create `data/eval/rag_strategy_eval_v1.jsonl` from verified gold samples.
2. Replace `dataset_version` and `index_version` placeholders.
3. Keep the eval set and index unchanged across all experiments.
4. Run:

```powershell
D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe `
  scripts\run_strategy_eval.py `
  --experiment-config backend\rag\experiments\online_strategy_matrix_v1.yaml
```

Outputs include per-experiment JSON/JSONL, a comparison CSV, a Markdown report, component versions, profile hashes, dataset hash, seed and baseline deltas.
