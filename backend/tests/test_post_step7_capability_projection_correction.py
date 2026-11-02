"""Capability identity, provider preflight and lossless Writer presentation."""
from copy import deepcopy
import json
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from bootstrap.model_factory import build_default_model_routing
from core.config import get_settings
from core.hashing import canonical_sha256
from core.model_input_accounting import ModelContextBudgetExceeded, estimate_model_input, request_input_payload
from model_gateway.model_capabilities import ModelCapabilityCatalog
from model_gateway.model_contract import ModelProfile, ModelRole, RoutingPolicy
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter
from model_gateway.integrations.langchain.message_mapping import request_to_langchain_messages
from schemas.model import ModelRequestSchema
from schemas.rag import RAGEvidenceAssessmentSchema, RAGEvidenceItemSchema
from schemas.citation import CitationSchema, citation_source_identity
from apps.enterprise_document.services.agent_rag_v1.writer_evidence_projection import project_writer_evidence, _structured
from apps.enterprise_document.schemas.agent_rag_v1.research import EvidencePackageSelectionSchema
from test_model_gateway_multi_model_runtime import _StubClient, _success


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("windows_literal", [True, False])
def test_local_capability_identity_is_independent_of_host_path_parser(windows_literal):
    names = {
        "local_qwen_1_5b_model_path": "Qwen2.5-1.5B-Instruct",
        "local_qwen_3b_model_path": "Qwen2.5-3B-Instruct",
        "local_qwen_7b_model_path": "Qwen2.5-7B-Instruct-GPTQ-Int4",
    }
    paths = {
        field: PurePosixPath("/checkout") / ("D:\\models\\" + name)
        if windows_literal else PurePosixPath("/models") / name
        for field, name in names.items()
    }
    settings = replace(
        get_settings(reload=True), default_model_name="deepseek_api",
        model_capabilities_path=ROOT / "config/model_capabilities.json", **paths,
    )
    profiles = {p.profile_id: p for p in build_default_model_routing(settings).profiles}
    for profile_id, field in zip(("local_1_5b", "local_3b", "local_7b"), names):
        assert profiles[profile_id].context_window == 32768
        assert profiles[profile_id].local_path == str(paths[field])
        assert profiles[profile_id].metadata["capability_source"]["entry"]["model"] == names[field]


def test_default_catalog_preserves_output_policy_and_safe_fallback_minimum():
    settings = replace(get_settings(reload=True), default_model_name="deepseek_api", model_capabilities_path=ROOT / "config/model_capabilities.json")
    routing = build_default_model_routing(settings)
    profiles = {p.profile_id: p for p in routing.profiles}
    assert profiles["deepseek"].context_window == 1_000_000
    assert profiles["deepseek"].max_output_tokens == max(2048, settings.local_qwen_max_new_tokens)
    assert profiles["local_1_5b"].context_window == 32768
    gateway = ModelGateway("deepseek_api", router=ModelRouter("deepseek_api", profiles=routing.profiles, policies=routing.policies))
    try:
        assert gateway.routing_capabilities(model_role="section_generation")["safe_context_window"] == 32768
        assert "local_7b" not in gateway.routing_capabilities(model_role="section_generation")["candidate_profiles"]
    finally:
        gateway.close()


def _catalog_entry(**updates):
    return {"provider": "custom", "model": "actual-v1", "endpoint": "https://provider.example/v1", "context_window": 4096,
            "max_output_tokens": 1024, "source": "operator declared capability", "revision": "r1", **updates}


def test_explicit_custom_catalog_binds_provider_model_endpoint_and_revision(tmp_path):
    import json
    path = tmp_path / "capabilities.json"
    payload = {"schema_version": "model_capabilities_v1", "revision": "r1", "entries": [_catalog_entry()]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    catalog = ModelCapabilityCatalog.load(path)
    resolved = catalog.resolve(provider="custom", model="actual-v1", endpoint="https://PROVIDER.example/v1/")
    assert resolved["context_window"] == 4096
    for updates in ({"model": "actual-v2"}, {"provider": "other"}, {"endpoint": "https://other.example/v1"}):
        with pytest.raises(ValueError, match="No declared capability"):
            catalog.resolve(**{"provider": "custom", "model": "actual-v1", "endpoint": "https://provider.example/v1", **updates})
    payload["revision"] = "r2"
    changed = ModelCapabilityCatalog.model_validate(payload).resolve(provider="custom", model="actual-v1", endpoint="https://provider.example/v1")
    assert changed["capability_source"] != resolved["capability_source"]


@pytest.mark.parametrize("entries", [
    [_catalog_entry(context_window=255)], [_catalog_entry(context_window="4096")],
    [_catalog_entry(max_output_tokens=4096)], [_catalog_entry(unknown=1)],
    [_catalog_entry(), _catalog_entry(endpoint="https://provider.example/v1/")],
    [_catalog_entry(endpoint="https://user:password@provider.example/v1")],
])
def test_invalid_capability_catalog_fails_closed(entries):
    with pytest.raises(ValueError):
        ModelCapabilityCatalog.model_validate({"schema_version": "model_capabilities_v1", "revision": "r1", "entries": entries})


def test_unknown_composed_remote_model_does_not_borrow_default_window():
    settings = replace(get_settings(reload=True), default_model_name="deepseek_api", deepseek_provider_model_name="not-declared",
                       model_capabilities_path=ROOT / "config/model_capabilities.json")
    with pytest.raises(ValueError, match="No declared capability"):
        build_default_model_routing(settings)


@pytest.mark.parametrize("messages,system,prompt", [
    ([], "system", "quoted \\\"text"),
    ([{"role": " HUMAN ", "content": "actual"}, {"role": "ai", "content": "reply"}], "ignored" * 500, "ignored" * 500),
])
def test_accounting_payload_matches_actual_adapter_precedence(messages, system, prompt):
    request = ModelRequestSchema(model_call_id="m", task_id="t", run_id="r", created_at="now", messages=messages, system_prompt=system, prompt=prompt)
    payload = request_input_payload(messages=request.messages, system_prompt=request.system_prompt, prompt=request.prompt)
    roles = {"human": "user", "ai": "assistant", "system": "system"}
    assert payload["messages"] == [{"role": roles[m.type], "content": m.content} for m in request_to_langchain_messages(request)]


@pytest.mark.parametrize("headroom", [-1, 0, 1])
def test_gateway_complete_input_boundary_and_actual_output_cap(headroom):
    prompt = "预算" * 200
    tokens = estimate_model_input(request_input_payload(messages=[], prompt=prompt))
    profile = ModelProfile(profile_id="p", model_name="m", provider="fake", context_window=tokens + 64 + headroom, max_output_tokens=64)
    gateway = ModelGateway("m", router=ModelRouter("m", profiles=[profile]))
    client = _StubClient("m", _success("m")); gateway.register_client(client)
    request = ModelRequestSchema(model_call_id="id", task_id="t", run_id="r", created_at="now", model_name="m", prompt=prompt, max_tokens=999)
    try:
        if headroom < 0:
            with pytest.raises(ModelContextBudgetExceeded): gateway.generate(request)
            assert not client.calls
        else:
            gateway.generate(request)
            assert client.calls[0].max_tokens == 64
    finally:
        gateway.close()


def test_gateway_smaller_fallback_rejected_before_reservation_or_primary_call():
    profiles = [ModelProfile(profile_id=p, model_name=p, provider="fake", context_window=w, max_output_tokens=64) for p,w in [("large",4096),("small",256)]]
    gateway = ModelGateway("large", router=ModelRouter("large", profiles=profiles,
        policies=[RoutingPolicy(role=ModelRole.GENERAL, candidates=["large","small"])]))
    clients = [_StubClient(p.model_name, _success(p.model_name)) for p in profiles]
    for client in clients: gateway.register_client(client)
    request = ModelRequestSchema(model_call_id="id", task_id="t", run_id="r", created_at="now", model_role="general", prompt="长"*300, max_tokens=64)
    before = gateway.usage_snapshot()
    try:
        with pytest.raises(ModelContextBudgetExceeded, match="profile=small"): gateway.generate(request)
        assert gateway.usage_snapshot() == before
        assert all(not c.calls for c in clients)
        assert not gateway._logical_request_fingerprints
    finally:
        gateway.close()


class _Dump:
    def __init__(self, value): self.value = value
    def model_dump(self, **kwargs): return deepcopy(self.value)


@pytest.mark.parametrize("structured", [
    {"representation_type": "fact", "fact": {"record_id": "fact-1", "fact_status": "estimated", "value": "HIDDEN_BODY", "source_span": {"page": 3, "start": 12, "end": 20}}},
    {"representation_type": "table", "table": {"table_id": "table-1", "row_index": 2, "cells": ["HIDDEN_BODY"]}},
    {"representation_type": "policy", "policy": {"clause_id": "3.1", "clause_path": ["3", "3.1"]}},
    {"representation_type": "template", "template": {"required_fields": ["budget"], "is_example_content": True}},
    {"representation_type": "text", "fallback": {"used": True, "reason": "no-structure", "strategy": "text"}},
])
def test_projection_retains_governed_text_and_support_semantics_without_raw_bodies(structured):
    from contracts.evidence import build_evidence_bundle
    from schemas.rag import RetrievedChunkSchema

    chunk = RetrievedChunkSchema(rank=1, matched_chunk_id="child", context_chunk_id="parent",
                                 doc_id="doc", match_text="HIDDEN_BODY", context_text="VISIBLE正文")
    citation = CitationSchema(citation_id="C1", source_type="chunk", doc_id="doc", chunk_id="child", quote_text="HIDDEN_BODY", extra={"raw": "AUDIT_ONLY"})
    citation = citation.model_copy(update={"citation_ref": citation_source_identity(citation)})
    candidate = {"candidate_id": "candidate-1", "citation_ids": ["C1"], "document_id": "doc", "child_chunk_id": "child",
                 "prompt_visible_text": "VISIBLE正文", "structured_evidence": structured,
                 "retrieval_candidate": {"context_text": "HIDDEN_BODY", "document_profile": {"raw": "AUDIT_ONLY"},
                     "allowed_claim_types": ["technical_fact"], "forbidden_claim_types": ["budget_fact"], "project_relation": "historical_reference"}}
    assessment = RAGEvidenceAssessmentSchema(required_qualifiers=["estimated only"], covered_topics=["topic"],
        conflicts=[{"evidence_ids": ["E1"], "reason": "conflict"}],
        details={"evidence_governance_v2": {"prompt_evidence_candidates": [candidate]}, "audit": "AUDIT_ONLY"})
    bundle = build_evidence_bundle(
        query="visible projection fixture", rewritten_queries=[], selected_chunks=[chunk],
        dropped_chunks=[], citations=[citation], trace=None, max_context_chars=6000,
        context_pack={"rendered_text": "VISIBLE正文", "max_context_chars": 6000},
    ).model_copy(update={"assessment": assessment}, deep=True)
    item, citation = bundle.items[0], bundle.citations[0]
    binding = _Dump({"current_version": "v1"})
    package = SimpleNamespace(selections=[EvidencePackageSelectionSchema(evidence_id="E1", requirement_ids=("REQ1",))], evidence_bundle_binding=_Dump({"handle": "immutable", "sha256": "a"*64}))
    before = canonical_sha256({"item": item.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json"), "citation": citation.model_dump(mode="json")})
    bundle_before = canonical_sha256(bundle.model_dump(mode="json", round_trip=True))
    result = project_writer_evidence(binding=binding, package=package, bundle=bundle, context_text="VISIBLE正文", citations=[citation])
    import json
    serialized = json.dumps(result, ensure_ascii=False)
    assert serialized.count("VISIBLE正文") == 1
    assert "HIDDEN_BODY" not in serialized and "AUDIT_ONLY" not in serialized
    assert result["selections"][0]["requirement_ids"] == []
    # V5 preserves canonical unknown strings by ref, rather than treating any
    # public assessment field as a proven system/requirement-owned literal.
    assert result["assessment"]["required_qualifiers"] == []
    assert "estimated only" not in serialized
    assert result["assessment"]["assessment_ref"] == {
        "bundle_json_pointer": "/assessment",
        "sha256": canonical_sha256(assessment.model_dump(mode="json")),
    }
    assert result["assessment"]["conflicts"] == [{
        "conflict_ref": {
            "bundle_json_pointer": "/assessment/conflicts/0",
            "sha256": canonical_sha256(assessment.conflicts[0]),
        },
    }]
    assert result["citations"][0]["citation_ref"] == citation.citation_ref
    assert result["citation_usage"] == {"allowed_aliases": ["C1"], "scope": "current_section"}
    empty_package = SimpleNamespace(selections=[], evidence_bundle_binding=package.evidence_bundle_binding)
    assert project_writer_evidence(
        binding=binding, package=empty_package, bundle=bundle,
        context_text="VISIBLE正文", citations=[citation],
    )["citation_usage"] == {"allowed_aliases": [], "scope": "current_section"}
    constraint = result["source_constraints"]["candidates"][0]
    assert constraint["forbidden_claim_types"] == []
    assert constraint["source_ref"]["sha256"] == canonical_sha256(candidate)
    projected = result["source_constraints"]["candidates"][0]["structured_evidence"]
    assert projected["source_ref"]["sha256"] == canonical_sha256(structured)
    if "fact" in structured:
        assert projected["fact"]["fact_status"] == "estimated"
        raw_span = structured["fact"].get("source_span")
        if isinstance(raw_span, dict):
            assert "text" not in projected["fact"].get("source_span", {})
            assert projected["fact"]["source_span_ref"]["sha256"] == canonical_sha256(raw_span)
    assert before == canonical_sha256({"item": item.model_dump(mode="json"), "assessment": assessment.model_dump(mode="json"), "citation": citation.model_dump(mode="json")})
    assert bundle_before == canonical_sha256(bundle.model_dump(mode="json", round_trip=True))
    assert result["evidence_bundle_binding"]["evidence_bundle_sha256"] == bundle_before


def test_writer_structured_fact_source_span_keeps_location_but_never_raw_text():
    structured = {
        "schema_version": "structured_evidence_v1",
        "representation_type": "fact_record",
        "original_strategy": "fact_record",
        "actual_strategy": "fact_record",
        "fact": {
            "record_id": "fact-hidden-span",
            "fact_category": "cost",
            "fact_status": "estimated",
            "source_span": {
                "unit_order_start": 7,
                "unit_order_end": 7,
                "text": "HIDDEN_SOURCE_SPAN_TEXT",
            },
        },
        "fallback": {"used": False},
    }
    projected = _structured(
        structured,
        "/candidate/structured_evidence",
    )
    fact = projected["fact"]
    assert fact["source_span"] == {
        "unit_order_start": 7,
        "unit_order_end": 7,
    }
    assert fact["source_span_ref"]["sha256"] == canonical_sha256(
        structured["fact"]["source_span"]
    )
    assert "HIDDEN_SOURCE_SPAN_TEXT" not in json.dumps(
        projected,
        ensure_ascii=False,
    )
