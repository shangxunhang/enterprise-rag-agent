"""Regression contracts for the shared RAG / ModelGateway runtime."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from bootstrap.rag_service_factory import RAGServiceFactory
from bootstrap.runtime_options import RuntimeOptions
from core.runtime.execution_control import WorkflowExecutionCancelled
from model_gateway.integrations.text_generator import ModelGatewayTextGenerator
from rag.mapping.request_mapper import RAGRequestMapper
from rag.plugins.corrective_query_planners import SectionGapCorrectiveQueryPlanner
from rag.application.run_record import RAGRunRecordBuilder
from rag.plugins.evidence_assessors import CRAGEvidenceAssessorPlugin
from rag.plugins.query_transformers import HyDEQueryTransformer, MultiQueryTransformer
from rag.ports.quality import EvidenceAssessment
from rag.query.query_expander import QueryExpansionResult
from rag.services.rag_service import ObservedRAGService, RAGService
from schemas.model import ModelRequestSchema, ModelResponseSchema
from schemas.rag import EvidenceBundleSchema, RAGToolInputSchema


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _RecordingGateway:
    def __init__(self, outputs: list[str] | None = None, *, fail: bool = False) -> None:
        self.outputs = list(outputs or [])
        self.fail = fail
        self.requests: list[ModelRequestSchema] = []

    def generate(self, request: ModelRequestSchema) -> ModelResponseSchema:
        self.requests.append(request)
        content = self.outputs.pop(0) if self.outputs else ""
        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name=request.model_name,
            success=not self.fail,
            content=content,
            error_message="gateway unavailable" if self.fail else None,
            created_at=request.created_at,
        )


def _generator(gateway: _RecordingGateway) -> ModelGatewayTextGenerator:
    return ModelGatewayTextGenerator(
        model_gateway=gateway,
        model_name="local_qwen2_5_1_5b",
    )


def _llm_context(generator: ModelGatewayTextGenerator) -> dict:
    params = {
        "temperature": 0.1,
        "top_p": 0.8,
        "do_sample": False,
    }
    return {
        "query_llm_generator": generator,
        "enable_query_expansion_llm": True,
        "query_expansion_generation_params": params,
        "quality_llm_generator": generator,
        "enable_quality_llm": True,
        "quality_generation_params": params,
    }


def test_text_generator_translates_to_canonical_gateway_request() -> None:
    gateway = _RecordingGateway(["政务云安全合规"])

    output = _generator(gateway).generate(
        "rewrite this query",
        system_prompt="return one query",
        max_new_tokens=96,
        temperature=0.1,
        top_p=0.8,
        do_sample=False,
        call_purpose="rag_query_rewrite",
        runtime_context={
            "task_id": "task-1",
            "run_id": "run-1",
            "model_extra": {
                "call_purpose": "tampered",
                "workflow_run_id": "tampered",
                "generation_params": {"top_p": 1.0},
            },
        },
    )

    assert output == "政务云安全合规"
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.schema_version == "model_request_v1"
    assert request.model_name == "local_qwen2_5_1_5b"
    assert request.task_id == "task-1"
    assert request.run_id == "run-1"
    assert request.extra["call_purpose"] == "rag_query_rewrite"
    assert request.extra["generation_params"] == {
        "top_p": 0.8,
        "do_sample": False,
    }
    assert request.extra["workflow_run_id"] == "run-1"


def test_rag_request_caller_metadata_cannot_forge_canonical_lineage() -> None:
    request = RAGToolInputSchema(
        task_id="real-task",
        run_id="real-run",
        agent_name="SchemeWriterAgent",
        query="政务云建设方案",
        extra={
            "extra_metadata": {
                "task_id": "forged-task",
                "run_id": "forged-run",
                "workflow_run_id": "forged-workflow",
                "caller_agent": "ForgedAgent",
                "section_id": "section-1",
            }
        },
    )

    invocation = RAGRequestMapper(allow_legacy_unscoped=True).map(request)
    lineage = invocation.payload["extra_metadata"]
    assert lineage["task_id"] == "real-task"
    assert lineage["run_id"] == "real-run"
    assert lineage["workflow_run_id"] == "real-run"
    assert lineage["caller_agent"] == "SchemeWriterAgent"
    assert lineage["section_id"] == "section-1"


def test_rag_run_metadata_records_concrete_model_lineage_not_adapter_name() -> None:
    expansion = SimpleNamespace(
        strategy="multi_query",
        rewritten_queries=["rewrite"],
        hyde_query=None,
        retrieval_queries=["query", "rewrite"],
        metadata={},
        to_dict=lambda: {"strategy": "multi_query"},
    )
    retrieval = SimpleNamespace(
        query_expansion=expansion,
        retrieval_plan={"query_transform_mode": "multi_query"},
        correction_triggered=False,
        evidence_quality={},
        reranked_results=[],
        results=[],
    )
    generator = object()

    metadata = RAGRunRecordBuilder().build_metadata(
        pipeline_name="pipeline",
        pipeline_version="v1",
        retrievers=[],
        source_fusion=None,
        query_fusion=None,
        candidate_enricher=None,
        reranker=None,
        evidence_assessor=None,
        corrective_retrieval_gate=None,
        corrective_query_planner=None,
        context_packer=None,
        eval_top_k=5,
        filter_expr=None,
        keyword_doc_ids=[],
        retrieval=retrieval,
        enable_query_expansion_llm=True,
        query_llm_generator=generator,
        model_calls=[
            {
                "model_call_id": "call-rag-1",
                "call_purpose": "rag_query_rewrite",
                "selected_profile": "local_1_5b",
                "selected_model": "qwen-1.5b",
                "provider": "local_huggingface",
            }
        ],
        query_expansion_generation_params={},
        extra_metadata={
            "rag_run_id": "rag-1",
            "query_expansion_model_name": "forged-model",
            "query_expansion_models": ["forged-model"],
            "model_calls": [{"model_call_id": "forged-call"}],
            "model_call_ids": ["forged-call"],
        },
    )

    assert metadata["query_expansion_model_name"] == "qwen-1.5b"
    assert metadata["query_expansion_model_profiles"] == ["local_1_5b"]
    assert metadata["query_expansion_model_call_ids"] == ["call-rag-1"]
    assert metadata["query_expansion_models"] == ["qwen-1.5b"]
    assert metadata["model_call_ids"] == ["call-rag-1"]
    assert metadata["model_calls"][0]["selected_model"] == "qwen-1.5b"
    assert metadata["query_expansion_model_name"] != generator.__class__.__name__


def test_query_transform_plugins_use_the_injected_gateway() -> None:
    gateway = _RecordingGateway(
        [
            "政务云安全合规\n政务云总体架构",
            "政务云采用统一资源池、分区分域和纵深防御架构。",
        ]
    )
    context = _llm_context(_generator(gateway))
    state = QueryExpansionResult(
        original_query="生成一个政务云的建设方案",
        retrieval_queries=["生成一个政务云的建设方案"],
    )

    MultiQueryTransformer(
        build_context=context,
        num_rewrites=2,
        use_llm=True,
        fallback_to_deterministic=False,
    ).transform(state)
    HyDEQueryTransformer(
        build_context=context,
        use_llm=True,
        fallback_to_deterministic=False,
    ).transform(state)

    assert state.rewritten_queries == ["政务云安全合规", "政务云总体架构"]
    assert state.hyde_query == "政务云采用统一资源池、分区分域和纵深防御架构。"
    assert [item.extra["call_purpose"] for item in gateway.requests] == [
        "rag_query_rewrite",
        "rag_hyde",
    ]


def test_crag_and_corrective_query_use_gateway_without_mutating_evidence() -> None:
    gateway = _RecordingGateway(
        [
            '{"relevance_label":"irrelevant","score":0.1,"reason":"缺少建设细节"}',
            '{"queries":["政务云分区分域建设要求","政务云安全合规验收标准"]}',
        ]
    )
    context = _llm_context(_generator(gateway))
    evidence = [
        {
            "parent_chunk_id": "parent-1",
            "text": "通用云平台简介",
            "score": 0.2,
            "rank": 1,
            "metadata": {"source": "test"},
        }
    ]
    before = deepcopy(evidence)
    assessor = CRAGEvidenceAssessorPlugin(
        build_context=context,
        use_llm=True,
        fallback_to_deterministic=False,
        confidence_threshold=0.55,
        min_relevant_chunks=1,
    )

    assessment = assessor.assess(
        query="政务云建设方案",
        results=evidence,
    )
    plan = SectionGapCorrectiveQueryPlanner(
        build_context=context,
        max_queries=2,
        use_llm=True,
        fallback_to_deterministic=False,
    ).plan(
        query="政务云建设方案",
        assessment=assessment,
    )

    assert evidence == before
    assert assessment.sufficient is False
    assert plan.queries == (
        "政务云分区分域建设要求",
        "政务云安全合规验收标准",
    )
    assert [item.extra["call_purpose"] for item in gateway.requests] == [
        "rag_evidence_assessment",
        "rag_corrective_query",
    ]


def test_gateway_failure_is_not_converted_to_fake_success() -> None:
    generator = _generator(_RecordingGateway(fail=True))

    with pytest.raises(RuntimeError, match="gateway unavailable"):
        generator.generate("query", call_purpose="rag_query_rewrite")


def test_rag_service_factory_injects_the_same_gateway_instance() -> None:
    gateway = _RecordingGateway()
    options = RuntimeOptions(
        use_real_rag=True,
        rag_project_root=PROJECT_ROOT,
        enable_agent_self_rag=True,
        enable_semantic_gate=False,
        semantic_gate_model_name="local_qwen2_5_1_5b",
        rag_static_retrieval_spec_file=(
            PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml"
        ),
        rag_intent_policy_file=(
            PROJECT_ROOT / "backend/rag/config/intent_policy_v1.yaml"
        ),
        rag_retrieval_gate_policy_file=(
            PROJECT_ROOT / "backend/rag/config/retrieval_gate_policy_v1.yaml"
        ),
    )

    observed = RAGServiceFactory().build(
        options,
        model_gateway=gateway,
        model_name="local_qwen2_5_1_5b",
    )

    assert isinstance(observed, ObservedRAGService)
    assert isinstance(observed.inner, RAGService)
    runtime_factory = observed.inner.retrieval_runtime.runtime_factory
    assert runtime_factory.model_gateway is gateway
    assert runtime_factory.model_name == "local_qwen2_5_1_5b"


def test_public_rag_schema_contract_is_unchanged() -> None:
    request = RAGToolInputSchema(
        task_id="task-1",
        run_id="run-1",
        agent_name="SchemeWriterAgent",
        query="政务云建设方案",
    )
    bundle = RAGService(PROJECT_ROOT, retrieval_runtime=_EmptyRuntime()).retrieve(
        request
    )

    assert isinstance(bundle, EvidenceBundleSchema)
    assert request.schema_version == "rag_tool_input_v1"
    assert bundle.schema_version == "rag_evidence_contract_v1"
    assert bundle.trace is not None
    assert bundle.trace.schema_version == "rag_trace_v1"


def test_rag_service_never_normalizes_cancellation_to_failed_evidence() -> None:
    class _CancelledRuntime:
        def retrieve(self, _payload: dict) -> dict:
            raise WorkflowExecutionCancelled("cancel retrieval")

    request = RAGToolInputSchema(
        task_id="task-cancelled",
        run_id="run-cancelled",
        agent_name="SchemeWriterAgent",
        query="政务云建设方案",
    )

    with pytest.raises(WorkflowExecutionCancelled):
        RAGService(
            PROJECT_ROOT,
            retrieval_runtime=_CancelledRuntime(),
            allow_legacy_unscoped=True,
        ).retrieve(request)


def test_rag_source_tree_has_no_private_huggingface_llm_loader() -> None:
    rag_root = PROJECT_ROOT / "backend/rag"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in rag_root.rglob("*.py")
    )

    assert "AutoModelForCausalLM" not in source
    assert "LocalLLMGenerator" not in source
    assert "rag.llm.local_llm" not in source
    assert not (rag_root / "llm/local_llm.py").exists()
    assert not (rag_root / "configs/LLMConfig.py").exists()


class _EmptyRuntime:
    def retrieve(self, payload: dict) -> dict:
        del payload
        return {
            "success": True,
            "data": {
                "run_id": "rag-run-1",
                "retrieval_results": [],
                "context_pack": {
                    "context": "",
                    "selected_results": [],
                    "selected_count": 0,
                    "dropped_count": 0,
                    "packing_strategy": "default",
                },
            },
        }
