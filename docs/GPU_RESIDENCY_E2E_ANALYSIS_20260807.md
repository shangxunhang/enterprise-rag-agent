# GPU Residency E2E Analysis — 2026-08-07

## 1. Purpose

Validate whether the real government-cloud E2E slowdown is caused by GPU memory pressure before the 3B writer is loaded, specifically whether embedding/reranker and other models remain resident on GPU and force Qwen2.5-3B to CPU offload.

## 2. Repository state

- Recorded date: 2026-08-07 +08:00
- Branch: `checkpoint/controlled-architecture-20260728`
- HEAD: `c04e98e80186d2e79810b9ebcd69a990475fbb91`
- Dirty tree entries before analysis: 231
- No production code was modified during this probe.

## 3. E2E run

- Run ID: `gpu_residency_probe_e2e_20260807_2043`
- Runtime root: `E:\rag-agent-runtime\gpu_residency_probe_e2e_20260807_2043`
- Trace: `E:\rag-agent-runtime\gpu_residency_probe_e2e_20260807_2043\runs\gpu_residency_probe_e2e_20260807_2043_trace.jsonl`
- Model usage: `E:\rag-agent-runtime\gpu_residency_probe_e2e_20260807_2043\runs\gpu_residency_probe_e2e_20260807_2043_model_usage.json`
- Stdout: `E:\rag-agent-runtime\gpu_residency_probe_e2e_20260807_2043\console.stdout.log`
- Stderr: `E:\rag-agent-runtime\gpu_residency_probe_e2e_20260807_2043\console.stderr.log`
- `e2e_report.json` was not produced because shutdown raised `TimeoutError: workflow workers did not become idle before shutdown; resource closure was deferred until idle` after the workflow node timeout. This shutdown/reporting failure is separate from the GPU residency finding.

## 4. GPU memory observations

GPU: NVIDIA GeForce RTX 4070 Ti SUPER, total 16376 MiB.

Observed snapshots:

| Stage | GPU used | GPU free | Evidence |
|---|---:|---:|---|
| Before E2E heavyweight model load | ~2431–2442 MiB | ~13622–13633 MiB | `nvidia-smi` baseline |
| Reranker loaded + 1.5B loaded, before 3B | 10340–11095 MiB | 4969–5724 MiB | stdout + `nvidia-smi` |
| Immediately after 3B load | 13981 MiB | 2083 MiB | stdout + `nvidia-smi` |
| Peak observed during generation/repair | 15609 MiB | 455 MiB | `nvidia-smi` |
| After Python process exit | 647 MiB | 15417 MiB | `nvidia-smi` |

The process therefore occupied the dominant share of GPU memory during the E2E.

## 5. Runtime load order and device placement

The real run showed this order:

1. Retrieval starts.
2. ParentChildReranker loads on `cuda`.
3. `local_qwen2_5_1_5b` loads with `device_map=auto` and `hf_device_map={'': 0}` — fully on GPU.
4. Section execution starts.
5. `local_qwen2_5_3b` loads with `device_map=auto`.
6. Because only about 5 GiB remained before the 3B load, the 3B is split across GPU and CPU.

Observed 3B map:

```text
model.embed_tokens -> GPU
lm_head -> GPU
model.layers.0 ~ model.layers.30 -> GPU
model.layers.31 ~ model.layers.35 -> CPU
model.norm -> CPU
```

Stderr also records:

```text
Some parameters are on the meta device because they were offloaded to the cpu.
```

## 6. Why the earlier resources remain resident

### Retrieval resources

`backend/rag/runtime/retrieval_runtime.py` defaults:

```text
embedding_device = cuda
reranker_device = cuda
```

`backend/rag/runtime/resource_pool.py` lazily creates and caches:

- `_embedding_backend`
- `_parent_rerankers`
- `_dense_retriever`

They are retained for the retrieval runtime lifetime and released by `close()`, not between one retrieval call and the following section generation.

### Model Gateway resources

`backend/bootstrap/model_factory.py` configures:

```text
local_1_5b residency_policy = PRIMARY
local_3b   residency_policy = RESIDENT
```

`backend/model_gateway/model_contract.py` defines both `PRIMARY` and `RESIDENT` as lazy-loaded and retained after first use. Only `ON_DEMAND` is released after each invocation.

Therefore, before the 3B writer is loaded, at minimum these GPU consumers can already coexist:

- embedding model
- reranker
- local Qwen 1.5B
- CUDA/runtime allocations
- desktop/other system GPU usage

The 3B writer is then loaded into the remaining memory via `device_map=auto`, which offloads its final layers to CPU.

## 7. Performance evidence from this run

Representative 3B calls:

```text
Section 1 generation                26.679 s
Section 1 citation repair           25.119 s
Section 1 grounded regeneration     51.330 s
Section 1 recovery generation       30.472 s
Section 2 generation                22.943 s
Section 2 citation repair           21.976 s
Section 2 grounded regeneration    130.149 s
Section 2 recovery generation       16.378 s
Section 2 recovery citation repair  16.445 s
Section 2 recovery regeneration     15.262 s
Section 3 generation                93.351 s
Section 4 generation                78.212 s
Section 5 generation                71.992 s
```

The run reproduced the same stable long-latency 3B behavior seen in the previous E2E. It is not an isolated RAG stall.

Model usage before timeout/shutdown:

```text
logical_calls        = 131
provider_attempts    = 130
local 1.5B calls     = 110
local 3B calls       = 20
model latency total  = 820308 ms
```

## 8. Workflow timeout reproduction

`execute_sections`:

```text
start       2026-08-07T12:46:39.987822+00:00
finish      2026-08-07T13:03:20.342010+00:00
latency_ms  1000354
error_code  WORKFLOW_NODE_TIMEOUT
```

The 1000-second node deadline was reproduced. This run reached Section 6 and entered its section generation when `execute_sections` timed out.

Whole workflow latency:

```text
1088323 ms
```

## 9. Root-cause conclusion

The user hypothesis is confirmed, with one important extension:

> The 3B writer is not competing only with embedding and reranker. The 1.5B model is also retained on GPU. Retrieval resources and the 1.5B model remain resident when the 3B writer is first loaded. With only about 5 GiB free at that point, `device_map=auto` offloads Qwen2.5-3B layers 31–35 and `model.norm` to CPU. The resulting mixed GPU/CPU autoregressive generation is the primary performance bottleneck that drives the eight-section serial node into its 1000-second timeout.

The `execute_sections` timeout is the terminal trigger, not the primary source of latency.

## 10. Separate issue exposed by the probe

After the node timeout, shutdown raised:

```text
TimeoutError: workflow workers did not become idle before shutdown; resource closure was deferred until idle
```

This prevented `e2e_report.json` from being emitted for this probe. Trace and model-usage evidence were still persisted. This is a separate shutdown/reporting issue and should not be mixed with the GPU residency root cause.
