# Stage 1.5 Full Decoupling v7.7

## 1. Scope

This refactor turns the stable v7.6 mainline into a modular monolith without
changing the business algorithms, prompts, public schemas, status semantics or
CLI contracts.

Runtime version:

```text
stage1-full-decoupling-v7.7-20260715
```

The refactor starts from the real-environment v7.6 baseline that already passed
RAG, local Qwen, citation, truncation-recovery and HardGate execution.

## 2. Non-goals

The following are deliberately not introduced:

- LangGraph;
- section-level retrieval;
- new Prompt or retrieval algorithms;
- Graph RAG / Adaptive-RAG / CRAG / Self-RAG changes;
- DeepSeek integration;
- microservices;
- Redis, Kafka or FastAPI;
- public Schema-field changes.

## 3. Decoupled areas

### 3.1 SchemeWriterAgent

`SchemeWriterAgent` remains a thin workflow adapter. Generation behavior lives
under `apps/enterprise_document/services/scheme_writer/`:

- input service;
- evidence service;
- prompt service;
- model and recovery service;
- citation service;
- advisory service;
- section-generation service;
- document-planning service;
- capture service;
- document-generation use case.

The compatibility facade temporarily preserves the previous private-method
surface for tests and downstream subclasses.

### 3.2 ProjectInputNormalizerAgent

The agent now delegates to:

- `ProjectInputReader`;
- project summary service;
- table-analysis builder;
- structured-fact extractor;
- `ProjectInputNormalizationUseCase`.

### 3.3 SupervisorAgent

Routing, context creation, task lifecycle, trace and workflow coordination are
separate services:

- `WorkflowCatalog` / `WorkflowRouter`;
- `ContextBundleFactory`;
- `TaskLifecycleService`;
- `WorkflowTraceService`;
- `SupervisorService`.

### 3.4 WorkflowExecutor

The executor delegates steps through `WorkflowStepDispatcher`. Agent execution
and unsupported-step handling are isolated handlers. Additional Tool, Model,
Rule or Export handlers can be registered without changing the executor.

### 3.5 SharedState access

Application code uses `SharedStateReader` and `SharedStateWriter` for canonical
state mutations. The adapters keep the temporary compatibility views in sync
with `ContextBundle`.

### 3.6 Mainline runtime

`scripts/mainline_runtime.py` is now a compatibility facade. Runtime assembly is
split into:

- application services: project input, task, workspace and mainline execution;
- bootstrap factories: observability, model, RAG and supervisor composition;
- runtime options: the only environment-variable access point for mainline
  feature selection.

### 3.7 RAG runtime

`LegacyRAGService` is decomposed into request, backend, evidence and result
adapters. `RAGTool` delegates component construction to a runtime factory and
execution to a runner. `ParentChildRAGEngine` coordinates isolated retrieval,
generation and run-record services.

Heavy vector/ML imports are lazy behind `ParentChildRuntimeFactory`, so importing
application contracts does not require the full Milvus/Transformers runtime.

### 3.8 RAG Schema boundary

Canonical public contracts remain in `backend/schemas/rag.py`. Historical RAG
DTO/builders now live in `backend/rag/legacy/schema/`. The old
`backend/rag/schema/` import paths are compatibility exports only.

### 3.9 Duplicate implementations

- canonical Tool contract: `backend/contracts/base_tool.py`;
- legacy dict Tool contract: `backend/rag/legacy/tool_contract.py`;
- duplicated reader factory path converted to a compatibility export;
- two fixed-size chunker paths now share `fixed_size_core.py`, while preserving
  their historical metadata styles;
- common RAG coercion/path/presentation helpers are centralized.

### 3.10 Model Gateway

The gateway is a facade over:

- `ModelRegistry`;
- `ModelRouter`;
- `ModelInvoker`;
- `ModelCallObserver`.

Local Qwen is separated into chat formatting, Hugging Face runtime and client
adapter.

### 3.11 Cross-cutting runtime ports

Runtime code depends on protocols instead of JSONL implementations:

- `TraceSink`;
- `DataCaptureSink`;
- `TaskStateManager`;
- `Clock` / `Timer`;
- `IdGenerator`;
- `ErrorFactory`;
- RAG generator, capture, retriever, reranker, context and prompt ports.

JSONL trace, capture and task-state adapters accept deterministic clock and ID
implementations for tests.

### 3.12 SchemeWriter Schema modules

The former monolithic schema file is split into:

- `generation.py`;
- `planning.py`;
- `evaluation.py`;
- `document.py`;
- `output.py`.

`scheme_writer_schema.py` remains a compatibility re-export, so current imports
and serialized field semantics do not change.

### 3.13 Tests

The previous 1,257-line Stage-1 test file is split into:

- contract tests;
- workflow-integrity tests;
- generation-recovery tests;
- advisory-policy tests.

Architecture-contract tests additionally verify:

- old/new schema identity;
- centralized state mutation;
- injectable clock and IDs;
- Model Gateway composition;
- workflow step dispatch;
- fixed chunker compatibility;
- no runtime dependency on the `eval` package.

## 4. Dependency direction

Target direction:

```text
scripts / API adapters
    -> application use cases
        -> domain schemas and ports
            <- infrastructure adapters
```

Important corrections:

- runtime enterprise-document code no longer imports `eval`;
- runtime RAG metrics live under `rag/evaluation`, while the previous Eval path
  is a compatibility export;
- heavy model/vector dependencies stay behind bootstrap/runtime factories;
- agents no longer own transformation, retrieval, generation or persistence
  implementations.

## 5. Compatibility guarantees

The following remain stable:

- `run_demo.py`, `run_pipeline.py` and `mainline_runtime.py` public entry points;
- Agent, Tool, Model, Task, RAG and SchemeWriter public Schema fields;
- workflow status and structured-error semantics;
- Prompt IDs and Prompt text;
- retrieval, rerank, citation, recovery and HardGate algorithms;
- trace, capture and task-state JSONL payload structure;
- historical RAG and SchemeWriter import paths.

## 6. Validation

Automated validation:

```text
52 passed
python -m compileall -q backend scripts: passed
run_pipeline.py --help: passed
core import smoke: passed without pymilvus installed
```

Fake end-to-end validation:

```text
ProjectInputNormalizerAgent: success
SchemeWriterAgent: success
RAG: FakeRAGTool
Model: FakeLLMClient
2 required sections generated
HardGate: passed
final status: success
final error: null
Trace/DataCapture/TaskState: written
```

Real Qwen + Milvus validation must be rerun in the Windows inference environment
before tagging this version as the new real-runtime baseline.

## 7. Next boundary

After the real v7.7 regression passes, the modular-monolith code can be frozen.
Only then should Stage 2 map these stable use cases and ports into LangGraph
nodes and checkpoints.
