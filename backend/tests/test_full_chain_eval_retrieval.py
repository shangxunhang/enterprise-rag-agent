from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from types import SimpleNamespace
import pytest

from core.diagnostics import DiagnosticConfig, DiagnosticSession, capture_pool
from eval.full_chain.domains.retrieval import (check_retrieval_edge, compare_rankings, evaluate_pool_transition, extract_retrieval)
from eval.full_chain.snapshot import SnapshotView
from rag.application.parent_child_retrieval import ParentChildRetrievalPipeline
from rag.plugins.candidate_enrichers import ParentChildCandidateEnricher
from rag.plugins.fusions import ChildRRFFusionPlugin, ParentRRFFusionPlugin
from rag.plugins.retrievers import BM25ChildRetrieverPlugin, MilvusDenseChildRetrieverPlugin
from rag.plugins.rerankers import NoOpParentRerankerPlugin
from rag.ports.quality import CorrectiveQueryPlan, EvidenceAssessment, EvidenceJudgement
from rag.query.query_expander import QueryExpansionResult
from rag.reranker.parent_child_reranker import ParentChildReranker
from test_rag_config_driven_retrieval_stack import _build_context
from test_rag_corrective_retrieval_loop import _pipeline, _Planner, _CountingAssessor, _CountingQueryPlanner
from rag.plugins.correction_gates import EvidenceSufficiencyCorrectionGate


DESIGN_CASES = tuple(f"R{number:02d}" for number in range(1, 13))


def _candidate(identifier, *, parent=None, text="useful sentence", tenant="tenant", index="index-v1"):
    return {"child_chunk_id": identifier, "chunk_id": identifier, "parent_chunk_id": parent or "parent-" + identifier,
            "doc_id": "doc", "tenant_id": tenant, "index_version": index, "rank": 1, "score": 1,
            "text": text, "parent_text": text, "prompt_visible_text": text}


def _session(tmp_path, label="capture", **limits):
    return DiagnosticSession(DiagnosticConfig(tmp_path / label, tmp_path, label, **limits))


def _snapshot(session):
    rows = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    return SnapshotView.from_records({"diagnostic_events": rows})


class _TwoQueries:
    def transform(self, query, *, mode):
        return QueryExpansionResult(original_query=query, strategy=mode, retrieval_queries=[query, query + " alternate"])


def _real_pipeline(top_k=2):
    context = _build_context()
    return ParentChildRetrievalPipeline(retrievers=[MilvusDenseChildRetrieverPlugin(build_context=context, top_k=3),
        BM25ChildRetrieverPlugin(build_context=context, top_k=2)], source_fusion=ChildRRFFusionPlugin(),
        query_fusion=ParentRRFFusionPlugin(top_k=2), candidate_enricher=ParentChildCandidateEnricher(build_context=context, top_k=top_k),
        reranker=NoOpParentRerankerPlugin(top_k=2), query_transform_selector=_TwoQueries(), retrieval_planner=_Planner(0),
        evidence_assessor=_CountingAssessor(), corrective_retrieval_gate=EvidenceSufficiencyCorrectionGate(),
        corrective_query_planner=_CountingQueryPlanner())


@pytest.mark.parametrize("case", DESIGN_CASES, ids=DESIGN_CASES)
def test_retrieval_design_cases(case, tmp_path):
    useful, irrelevant = _candidate("useful"), _candidate("irrelevant")
    if case == "R01":
        pipeline = _real_pipeline(top_k=1)
        session = _session(tmp_path)
        with session:
            pipeline.run("fixed query", filter_expr=None, keyword_doc_ids=None)
        batch = extract_retrieval(_snapshot(session))
        before = next(stage.payload["candidates"] for stage in batch.stages if stage.payload.get("evidence_key") == "R-PARENT" and stage.payload.get("phase") == "BEFORE_GROUPING")
        after = next(stage.payload["candidates"] for stage in batch.stages if stage.payload.get("evidence_key") == "R-PARENT" and stage.payload.get("phase") == "RESULT")
        loss = evaluate_pool_transition(before, after, allowed_transformation="CHILD_TO_PARENT", expected_useful_ids=["child-3"])
        assert loss["useful_dropped"] == ["child-3"]
        assert any(stage.payload.get("evidence_key") == "R-DENSE" and stage.payload.get("complete") for stage in batch.stages)
    elif case == "R02":
        other = _candidate("second", parent=useful["parent_chunk_id"])
        merged = {**useful, "matched_child_chunk_ids": ["useful", "second"]}
        assert evaluate_pool_transition([useful, other], [merged], allowed_transformation="CHILD_TO_PARENT", expected_useful_ids=["useful", "second"])["assertion"] is True
        assert evaluate_pool_transition([useful, other], [useful], allowed_transformation="CHILD_TO_PARENT", expected_useful_ids=["useful", "second"])["useful_dropped"] == ["second"]
    elif case == "R03":
        comparison = compare_rankings([useful, irrelevant], [irrelevant, useful], ["useful"], top_k=1, synthetic=True)
        assert comparison["state"] == "MEASURED"
        assert comparison["delta"]["ndcg_at_k"] == -1 and comparison["delta"]["mrr"] == -1
        assert compare_rankings([useful], [useful], ["useful"], top_k=1)["state"] == "UNMEASURED"
    elif case == "R04":
        session = _session(tmp_path)
        reranker, model = _fixed_reranker()
        with session:
            output = reranker.rerank("query", [_candidate("long", text="prefix " * 20 + "tail fact")], text_field="parent_text")
        rows = _snapshot(session).records()
        tokenized = next(row["payload"] for row in rows if row.get("event_type") == "R-RERANK" and row.get("payload", {}).get("phase") == "TOKENIZED")
        assert tokenized["input_token_lengths"] == [8] and tokenized["max_length"] == 8
        assert model.calls == 1 and output[0]["child_chunk_id"] == "long"
        request = next(row["payload"] for row in rows if row.get("event_type") == "R-RERANK" and row.get("payload", {}).get("phase") == "REQUEST")
        assert request["actual_pairs"][0][1].endswith("tail fact")
        assert model.seen_ids == [[0, 1, 2, 3, 4, 5, 6, 7]]
    elif case == "R05":
        assessment = EvidenceAssessment(False, 1, "fixed", item_judgements=(EvidenceJudgement("useful", "irrelevant", 1, "wrong reject"), EvidenceJudgement("irrelevant", "irrelevant", 1, "correct reject")))
        _, selected, report = ParentChildRetrievalPipeline._apply_final_assessment(results=[useful, irrelevant], assessment=assessment)
        assert report["rejected_irrelevant_ids"] == ["useful", "irrelevant"]
        loss = evaluate_pool_transition([useful, irrelevant], selected, expected_useful_ids=["useful"])
        assert loss["useful_dropped"] == ["useful"]
    elif case == "R06":
        forbidden = _candidate("other_project_quantity")
        result = evaluate_pool_transition([useful, forbidden], [useful], expected_useful_ids=["useful", "other_project_quantity"], allowed_ids=["useful"])
        assert result["assertion"] is True and result["useful_dropped"] == []
        assert evaluate_pool_transition([useful, forbidden], [forbidden], allowed_ids=["useful"])["wrong_use_allow"] == ["other_project_quantity"]
    elif case == "R07":
        packed = {**useful, "prompt_visible_text": "prefix"}
        loss = evaluate_pool_transition([useful], [packed], expected_useful_ids=["useful"], required_text={"useful": "useful sentence"})
        assert loss["dropped"] == [] and loss["visibility_lost"] == ["useful"] and loss["assertion"] is False
    elif case == "R08":
        pipeline, retriever, _, _ = _pipeline(correction_budget=1)
        original = retriever.retrieve
        def failing(request):
            if request.query == "second correction": raise RuntimeError("fixed second query failure")
            return original(request)
        retriever.retrieve = failing
        class Queries:
            def plan(self, **kwargs): return CorrectiveQueryPlan(("first correction", "second correction"))
        pipeline.corrective_query_planner = Queries()
        session = _session(tmp_path)
        with session, pytest.raises(RuntimeError, match="fixed second query failure"):
            pipeline.run("fixed query", filter_expr=None, keyword_doc_ids=None)
        batch = extract_retrieval(_snapshot(session))
        assert any(stage.payload.get("evidence_key") == "R-DENSE" and stage.measurement_state == "TECHNICAL_FAILURE" for stage in batch.stages)
        assert any(stage.payload.get("evidence_key") == "R-CORRECT" and stage.payload.get("phase") == "BEGIN" for stage in batch.stages)
        assert not any(stage.payload.get("evidence_key") == "R-CORRECT" and stage.payload.get("phase") == "RESULT" for stage in batch.stages)
    elif case == "R09":
        snapshot = SnapshotView.from_records({"retrieval": {"schema_version": "rag_retrieval_run_v1", "run_id": "historical", "query_expansion": {}, "p2_results": [useful], "retrieval_results": [useful], "context_pack": {"selected_results": [useful]}}})
        batch = extract_retrieval(snapshot)
        assert "NOT_RECORDED:R-DENSE" in batch.limitations and "NOT_RECORDED:R-PARENT" in batch.limitations
        assert next(edge for edge in batch.edges if edge.edge_id == "X04").measurement_state == "UNMEASURED"
    elif case == "R10":
        session = _session(tmp_path, max_pool_items=1)
        with session:
            capture_pool("R-DENSE", [useful, irrelevant], unit_id="C04", operation_id="too_many")
        batch = extract_retrieval(_snapshot(session))
        assert "CAPTURE_INCOMPLETE" in batch.limitations
        assert all(stage.measurement_state == "UNMEASURED" for stage in batch.stages if stage.unit_id == "C04")
    elif case == "R11":
        wrong = {**useful, "tenant_id": "other_tenant", "index_version": "index-v2"}
        assert evaluate_pool_transition([useful], [wrong])["wrong_binding"] == ["useful"]
        assert compare_rankings([useful], [wrong], ["useful"], top_k=1, synthetic=True)["state"] == "NOT_COMPARABLE"
    elif case == "R12":
        result = compare_rankings([useful], [useful], ["useful"], top_k=1, synthetic=True, before_prompt="v1", after_prompt="v2", response_replayed=True)
        assert result == {"state": "NOT_COMPARABLE", "reason": "OLD_RESPONSE_NEW_PROMPT_MECHANISM_ONLY"}


class _Tensor:
    def __init__(self, values): self.values = values
    def to(self, _): return self
    def tolist(self): return self.values
    def view(self, _): return self
    def detach(self): return self
    def cpu(self): return self
    def float(self): return self


class _NoGrad:
    def __enter__(self): return self
    def __exit__(self, *args): return False


def _fixed_reranker():
    class Tokenizer:
        name_or_path = "fixed_synthetic_tokenizer"
        def __call__(self, pairs, **kwargs):
            return {"input_ids": _Tensor([list(range(kwargs["max_length"])) for _ in pairs]),
                    "attention_mask": _Tensor([[1] * kwargs["max_length"] for _ in pairs])}
    class Model:
        calls = 0
        seen_ids = None
        def __call__(self, **inputs):
            self.calls += 1; self.seen_ids = inputs["input_ids"].tolist()
            return SimpleNamespace(logits=_Tensor([.8] * len(self.seen_ids)))
    reranker = ParentChildReranker.__new__(ParentChildReranker)
    reranker.model_name = "fixed_fake_model"; reranker.device = "cpu"; reranker.batch_size = 4; reranker.max_length = 8
    reranker.tokenizer = Tokenizer(); reranker.model = Model(); reranker._torch = SimpleNamespace(no_grad=_NoGrad)
    return reranker, reranker.model


def test_real_configured_component_capture_equivalence_and_complete_finite_pools(tmp_path):
    baseline = _real_pipeline().run("fixed query", filter_expr=None, keyword_doc_ids=None)
    session = _session(tmp_path)
    with session:
        candidate = _real_pipeline().run("fixed query", filter_expr=None, keyword_doc_ids=None)
    assert asdict(candidate) == asdict(baseline)
    assert session.health()["completeness"] == "COMPLETE" and session.lost_count == 0
    batch = extract_retrieval(_snapshot(session))
    for key in ("R-DENSE", "R-BM25", "R-SOURCE-FUSION", "R-PARENT", "R-QUERY-FUSION", "R-RERANK", "R-CRAG"):
        pools = [stage for stage in batch.stages if stage.payload.get("evidence_key") == key and "candidates" in stage.payload]
        assert pools and all(stage.payload["complete"] for stage in pools)
    dense = [stage for stage in batch.stages if stage.payload.get("evidence_key") == "R-DENSE" and "candidates" in stage.payload]
    assert len(dense) == 2 and len({stage.key for stage in dense}) == 2
    assert all([item["child_chunk_id"] for item in stage.payload["candidates"]] == ["child-1", "child-2", "child-3"] for stage in dense)


def test_pool_digest_missing_end_and_no_seal_are_never_complete(tmp_path):
    session = _session(tmp_path)
    with session:
        capture_pool("R-DENSE", [_candidate("one")], unit_id="C04", operation_id="one")
    rows = _snapshot(session).records()
    for mutate in (lambda values: [row for row in values if row.get("event_type") != "POOL_END"],
                   lambda values: [row for row in values if row.get("event_type") != "SESSION_SEAL"],
                   lambda values: [{**row, "end_digest": "tampered"} if row.get("event_type") == "POOL_END" else row for row in values]):
        batch = extract_retrieval(SnapshotView.from_records({"diagnostic_events": mutate(rows)}))
        assert all(stage.measurement_state == "UNMEASURED" for stage in batch.stages if stage.unit_id == "C04")


def test_real_reranker_fixed_boundary_enabled_disabled_returns_identical_scores(tmp_path):
    rows = [_candidate("one", text="first"), _candidate("two", text="second")]
    baseline, baseline_model = _fixed_reranker(); candidate, candidate_model = _fixed_reranker()
    expected = baseline.rerank("fixed query", rows, top_k=1)
    session = _session(tmp_path)
    with session:
        actual = candidate.rerank("fixed query", rows, top_k=1)
    assert actual == expected and baseline_model.calls == candidate_model.calls == 1
    assert session.health()["completeness"] == "COMPLETE"


def capture_fixed_retrieval_snapshot(tmp_path, label="full_engine"):
    """Root integration fixture: real configured chain through governance/packing."""
    from rag.context.context_gate import ContextGate, ContextRequirements
    from rag.plugins.context_packers import DefaultContextPacker, LostInMiddleContextPacker
    from rag.rag_engine.parent_child_rag_engine import ParentChildRAGEngine
    from test_stage_d_evidence_governance_v2 import _profile
    context = _build_context()
    profile = _profile(document_id="doc-1", document_type="historical_feasibility_study", evidence_role="historical_case",
        project_relation="other_project", authority_level="reference", allowed=["general_method", "reference_case"],
        forbidden=["current_project_cost"], kb_id="historical_case_kb")
    for backend in (context["resource_pool"].dense, context["resource_pool"].keyword):
        original_search = backend.search
        def with_profile(*args, _original=original_search, **kwargs):
            rows = _original(*args, **kwargs)
            for row in rows:
                row["child_chunk"].update(tenant_id="default", kb_id="historical_case_kb",
                    file_id="doc-1.pdf", document_profile=profile)
            return rows
        backend.search = with_profile
    requirements = ContextRequirements(model_context_window=8192, prompt_reserved_tokens=1024,
        section_token_budget=4096, max_evidence_items=5, max_context_chars=6000)
    gate = ContextGate(default_packer=DefaultContextPacker(), lost_in_middle_packer=LostInMiddleContextPacker(),
        default_requirements=requirements)
    engine = ParentChildRAGEngine(retrievers=[MilvusDenseChildRetrieverPlugin(build_context=context, top_k=3),
        BM25ChildRetrieverPlugin(build_context=context, top_k=2)], source_fusion=ChildRRFFusionPlugin(),
        query_fusion=ParentRRFFusionPlugin(top_k=2), candidate_enricher=ParentChildCandidateEnricher(build_context=context, top_k=2),
        reranker=NoOpParentRerankerPlugin(top_k=2), context_gate=gate, evidence_assessor=_CountingAssessor(),
        corrective_retrieval_gate=EvidenceSufficiencyCorrectionGate(), corrective_query_planner=_CountingQueryPlanner(),
        query_transform_selector=_TwoQueries(), retrieval_planner=_Planner(0), enable_query_expansion_llm=False)
    session = _session(tmp_path, label)
    with session:
        output = engine.run("parent context", extra_metadata={"section_id": "fixed-section", "section_title": "parent context"})
    assert session.health()["completeness"] == "COMPLETE"
    return _snapshot(session), output


@pytest.mark.parametrize("edge_id", ["X03", "X04", "X05", "X06", "X07"])
def test_real_retrieval_edges_normal_and_single_missing_or_visibility_mutation(edge_id, tmp_path):
    snapshot, output = capture_fixed_retrieval_snapshot(tmp_path)
    batch = extract_retrieval(snapshot)
    def pool(key, phase="RESULT"):
        stages = [stage for stage in batch.stages if stage.payload.get("evidence_key") == key and stage.payload.get("phase") == phase and "candidates" in stage.payload]
        assert stages
        return stages[0].payload["candidates"]
    if edge_id == "X03":
        source, target = pool("R-SOURCE-FUSION"), pool("R-PARENT")
        expected = [row["child_chunk_id"] for row in source]
    elif edge_id == "X04":
        source, target = pool("R-DENSE") + pool("R-BM25"), pool("R-SOURCE-FUSION")
        expected = list({row["child_chunk_id"] for row in source})
    elif edge_id == "X05":
        source, target = pool("R-PARENT"), pool("R-RERANK")
        expected = [row["parent_chunk_id"] for row in source]
    elif edge_id == "X06":
        source, target = pool("R-RERANK"), pool("R-GOV")
        expected = [row["child_chunk_id"] for row in target]
    else:
        source, target = pool("R-GOV"), output["context_pack"]["selected_results"]
        expected = [row["child_chunk_id"] for row in source]
    assert source and target
    required = {row["child_chunk_id"]: row["prompt_visible_text"] for row in target} if edge_id == "X07" else None
    good = check_retrieval_edge(edge_id, source, target, expected_useful_ids=expected, required_text=required)
    assert good["assertion"] is True
    negative = [{**row, "prompt_visible_text": ""} for row in target] if edge_id == "X07" else []
    bad = check_retrieval_edge(edge_id, source, negative, expected_useful_ids=expected, required_text=required)
    assert bad["assertion"] is False
    missing = check_retrieval_edge(edge_id, source, None, complete=False, expected_useful_ids=expected, required_text=required)
    assert missing["state"] == "UNMEASURED" and missing["assertion"] is None
    edge = next(item for item in batch.edges if item.edge_id == edge_id)
    assert edge.source_refs and edge.target_refs
