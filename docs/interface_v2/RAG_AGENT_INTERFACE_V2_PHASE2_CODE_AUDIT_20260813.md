# RAG–Agent Interface V2 Phase 2 Code Audit

日期：2026-08-13  
基线 HEAD：`94b2a5b`  
审计范围：Phase 2 — RAG Request `extra` Closure  

## 结论

**Phase 2 已完成并通过最终复审。Public Request authority、A/B/C/D ownership、typed request → RAG internal projection、caller budget clamp 均已收口；FIX-P2-01 已清除 SchemeEvidenceService 的死 ModelGateway 依赖。**

```text
PHASE 2 IMPLEMENTATION: PASS
PHASE 2 CONTRACT ACCEPTANCE: ACCEPTED
FIX-P2-01: PASS
PHASE 3: APPROVED TO START
```

---

## 1. Public Request Contract — PASS

`backend/schemas/rag.py` 已直接演进现有 `RAGToolInputSchema`，未创建平行 V2 Request Schema。

已建立正式字段：

```text
retrieval_trace_id
retrieval_scope
task_type
evidence_requirements
document_context
max_context_tokens
```

继续复用：

```text
max_context_chars
max_context_items
```

`RAGDocumentContextSchema` 只包含当前 Frozen 的稳定 RAG 文档语义：

```text
project_name
document_title
required_sections
citation_required_sections
```

`project_type / target_documents / target_templates` 未被错误提升进 typed DocumentContext。

---

## 2. Producer → Request — PASS

生产 `RAGToolInputSchema` 构造点当前只有：

```text
backend/apps/enterprise_document/services/scheme_writer/evidence_service.py
```

SchemeEvidenceService 已停止通过 `extra/extra_metadata` 写入 canonical request semantics。

现在直接写：

```text
retrieval_trace_id=
retrieval_scope=
task_type=
evidence_requirements=
document_context=
max_context_tokens=
max_context_chars=
max_context_items=
```

`extra` 仅剩 Frozen C 类 diagnostic metadata：

```text
project_type
target_documents
target_templates
```

未发现 production 第二个 RAGToolInput producer 仍走旧 extra authority。

---

## 3. RequestMapper — PASS

`backend/rag/mapping/request_mapper.py` 已从：

```text
request.extra["extra_metadata"] -> reconstruct canonical semantics
```

改为：

```text
RAGToolInputSchema typed fields
    -> planner_context / runtime payload projection
```

关键 canonical 值：

```text
task_id
run_id
caller_agent
retrieval_trace_id
retrieval_scope
task_type
evidence_requirements
document_context
context requirements
```

都直接来自 typed request。

`request.extra` 只被放入：

```text
planner_context["request_metadata"]
```

而 RAG package 内没有其他生产消费者读取 `request_metadata` 来覆盖 canonical semantics。

因此当前数据方向是：

```text
Public Contract -> RAG Internal Projection
```

而不是：

```text
extra -> Public Contract Reconstruction
```

---

## 4. RAG Consumer Migration — PASS

静态搜索确认 RAG package 内没有：

```text
request.extra.get("retrieval_scope")
request.extra.get("retrieval_trace_id")
```

作为 RAG request canonical reader。

已确认以下直接使用 typed fields：

```text
rag/services/rag_service.py
rag/mapping/request_mapper.py
rag/mapping/result_mapper.py
rag/integrations/langchain/runtime_facade.py
```

FakeRAGService 也不再通过：

```python
{**request.model_dump(), **request.extra}
```

把 extra 覆盖到 canonical request 上，而是只传 typed `request.model_dump(mode="json")`。

---

## 5. Budget Ownership / Clamp — PASS for production path

`ContextRequirements.from_mapping()` 已实现：

```text
effective caller constraint = min(caller requested, RAG internal safe limit)
```

RAG internal owner 保留：

```text
model_context_window
prompt_reserved_tokens
section_token_budget safe ceiling
max_evidence_items safe ceiling
```

Caller 只能通过：

```text
max_context_tokens
max_context_items
max_context_chars
```

缩小限制，不能放大内部上限。

当前正常 RetrievalRuntime → ContextGate → ContextPack 路径中，`ContextPack.max_context_chars / token_budget / max_items` 来自 clamp 后的 ContextRequirements，EvidenceBundle Builder 会优先使用 ContextPack 中的 effective budget，因此正常生产链路没有发现 caller 扩大内部安全预算的问题。

### 兼容路径观察（非 blocker）

ResultMapper 的 legacy fallback 在缺少 authoritative context pack 时仍使用 `invocation.max_context_items` 作为兼容截断值；当前正式 SchemeEvidenceService 路径由于 access scope / typed requirements 会进入 strict prompt contract，不走该 reconstruction 分支，因此本轮不扩张处理。后续移除 legacy fallback 时应一并删除该兼容语义。

---

## 6. B / C / D Ownership — 主体 PASS

### B — RAG Internal

SchemeEvidenceService 已停止向 RAG request 传：

```text
model_context_window
prompt_reserved_tokens
policy_id
coverage_strategy
model_capability_plan
```

`policy_id / coverage_strategy` 仍只存在于 RAG internal policy wrapper/config 语义，未进入 Public RAG Request。

### C — metadata

保留：

```text
project_type
target_documents
target_templates
```

未发现 RAG core consumer 将这些 metadata 当作 authorization / Requirement / policy authority。

### D — Application/Gate

Application producer 已停止把：

```text
claim_risk_level
allow_framework_only
```

写入 Public Requirement / RAG Request。

RAG internal compatibility wrapper 当前仍持有这些字段，是 Phase 3 前现有 `_gate()` 的兼容残留；按 Frozen Plan 本轮不提前拆 Gate。

---

## 7. FIX-P2-01 — PASS

`SchemeEvidenceService` 的死 ModelGateway 依赖已完成清理：

- `SchemeEvidenceService.__init__` 不再接收 `model_gateway`；
- `self.model_gateway` 已删除；
- `SchemeWriterAgent` 不再向 EvidenceService 注入 ModelGateway；
- stale docstring 已改为 typed RAG request + caller constraint / RAG internal safety 的真实职责描述；
- durable-resume 测试已改为从真实 owner `SectionModelService.model_gateway` 获取 facade；
- 全仓扫描 `evidence_service.model_gateway` 为 0 production matches。

真正的 ModelGateway consumer（如 `SectionModelService`、`SemanticClaimClassifier`、`SemanticSectionJudge`）未被误删。

该补丁只完成 Phase 2 B 类 ownership 的结构收尾，没有进入 Phase 3。

---

## 8. `extra_metadata` 残留判定

全仓仍存在 `extra_metadata`，但审计后区分如下：

```text
rag/mapping/request_mapper.py -> RAG internal runtime payload key
rag/runtime/retrieval_runtime.py -> internal runtime input
rag/rag_engine/... -> internal runtime metadata
rag/application/... -> internal run metadata
eval/rag/... -> evaluation-specific runtime
```

这些不再是 `RAGToolInputSchema.extra` 的 Public authority，因此不属于 Phase 2 blocker。

不应因为字段名仍叫 `extra_metadata` 就继续扩大本轮重构。

---

## 9. Phase Scope — PASS

未发现 Phase 3 偷跑：

```text
rag/evidence/governance_v2.py -> apps.enterprise_document.schemas.gate_decision
```

反向依赖仍存在，符合 Phase 3 尚未开始的预期。

未开始：

```text
Canonical Evidence state migration
ToolGateway Evidence/Citation ID closure
Application -> RAG internal import cleanup
Facade __getattr__ closure
RetrievalAction
Memory
ContextCompiler V2
```

---

## 10. 审计测试证据

独立复跑核心 Phase 1/2 + RAG/Stage D/Facade focused suite：

```text
78 passed in 0.61s
```

单独 Phase 2 focused contract：

```text
7 passed in 0.27s
```

`git diff --check`：

```text
exit 0
```

仅存在 CRLF -> LF 提示，无 whitespace error。

`RAGServicePort` 保持：

```python
retrieve(self, request: RAGToolInputSchema) -> EvidenceBundleSchema
```

未改变。

---

## 11. 最终放行

最终复审确认：

1. EvidenceService 中 `model_gateway` 0 production matches；
2. SchemeWriterAgent 不再向 EvidenceService 注入 ModelGateway；
3. Phase 2 focused contract：`7 passed`；
4. 补丁相关扩展测试共 `48 passed / 2 failed`，两个失败分别为既有 current-project profile snapshot/authority 数据漂移与 `.venv` 缺少可选 `pymilvus`，与 FIX-P2-01 无直接因果关系；
5. 三个补丁文件 `py_compile` 通过；
6. `git diff --check` exit 0，仅有 CRLF → LF 提示；
7. Phase 3 未提前实施。

因此：

```text
PHASE 2 ACCEPTED
READY FOR PHASE 3
```
