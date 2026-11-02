"""I5 exact physical Writer owner, identities, local Context and no redispatch."""
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    VerifierClaimInputSchema, VerifierSemanticResultCandidateSchema,
    VerifierProjectInputBasisV1, project_input_basis_ref,
    build_verifier_semantic_input_sha256,
    build_verifier_registration_scope_sha256, build_verifier_original_root,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterGenerationInvocationSchema, WriterGenerationInvocationRefSchema,
    WriterExecutionResultSchema, WriterExecutionStatus,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
    TargetClaimVerifierComposition, TargetClaimVerifierError,
)
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from schemas.context import ContextSourceFamily
from schemas.model import ModelResponseSchema
from schemas.failure import FailureCategory, FailureOrigin
from backend.tests import test_multi_agent_v3_claim_verification_runtime as owners


def fixture(tmp_path, **kwargs):
    env, runtime, claim, binding, support_owner, support, state = owners._setup_verification_chain(tmp_path, **kwargs)
    composer = env["verifier"]
    context = owners._verification_context(env, "verification://i5")
    invocation, project = composer.build_input(claim, binding, support, context)
    return SimpleNamespace(env=env, runtime=runtime, claim=claim, binding=binding,
        support_owner=support_owner, support=support, state=state, composer=composer,
        context=context, invocation=invocation, project=project)


def replace_writer(fx, path, *, project=None, key=None):
    """New valid physical owner, never mutate immutable invocation bytes."""
    ref = fx.invocation.verifier_project_input_basis.writer_generation_invocation_ref
    old = fx.composer.writer_execution_store.resolve_writer_generation_invocation(ref)
    data = old.model_dump(mode="json")
    if project is not None:
        data["unified_writer_input_snapshot"]["project_input"] = project.model_dump(mode="json")
    if key is not None:
        data.update(writer_subject_ref=key.writer_subject_ref.model_dump(mode="json"),
            write_instance_id=key.write_instance_id, route_decision_ref=key.route_decision_ref.model_dump(mode="json"))
    invocation = WriterGenerationInvocationSchema.model_validate(data)
    key = key or ref.logical_execution_key
    digest = canonical_sha256(invocation.model_dump(mode="json"))
    old_result = fx.composer.writer_execution_store.get_writer_execution(ref.logical_execution_key).result.model_dump(mode="json")
    old_result["logical_execution_key"] = key.model_dump(mode="json")
    old_result["artifact"]["section_id"] = key.writer_subject_ref.section_id
    old_result["artifact"]["input"]["writer_generation_invocation"] = invocation.model_dump(mode="json")
    result = WriterExecutionResultSchema.model_validate(old_result)
    store = SQLitePreWriteExecutionStore(path)
    store.reserve_writer_execution(key, semantic_fingerprint=digest)
    store.mark_writer_dispatched(key, semantic_fingerprint=digest)
    store.persist_writer_result(key, semantic_fingerprint=digest, result=result)
    return store


def model_material(fx):
    invocation, project = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    return invocation, {"invocation": invocation.model_dump(mode="json"),
        "claim": fx.claim.model_dump(mode="json"), "project_input": project.model_dump(mode="json"),
        "paragraph_text": "The approved capacity is 100 MW.", "facts": [], "evidence": [],
        "binding_facts": [i.model_dump(mode="json") for i in fx.binding.facts],
        "shared_support": {"relation": fx.support.relation.value,
            "support_ref": fx.support.support_ref.model_dump(mode="json"),
            "supporting_refs": list(fx.support.supporting_refs), "contradicting_refs": list(fx.support.contradicting_refs)},
        "policy_id": fx.composer.POLICY_ID, "policy_version": fx.composer.POLICY_VERSION}


class Gateway:
    def __init__(self):
        self.calls = []
        self.stop = False

    def routing_capabilities(self, **kwargs):
        return {"safe_context_window": 65536, "safe_max_output_tokens": 2048}

    def generate(self, request):
        self.calls.append(request)
        if self.stop:
            raise KeyboardInterrupt("physical execution lost after durable intent")
        return ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id,
            run_id=request.run_id, model_name="controlled-i5", success=True, created_at="test",
            content=json.dumps({"verdict": "UNSUPPORTED", "positive_basis_refs": [],
                "rationale": "Controlled model result", "issues": []}))


def test_basis_is_typed_exact_and_deterministic(tmp_path):
    fx = fixture(tmp_path)
    basis, project = fx.composer.resolve_project_input_basis(fx.claim, fx.context.active_lineage)
    assert basis == fx.invocation.verifier_project_input_basis
    assert basis.project_input_sha256 == canonical_sha256(project.model_dump(mode="json"))
    assert set(type(basis).model_fields) == {"schema_version", "writer_generation_invocation_ref", "project_input_sha256", "project_input_basis_ref"}
    other = basis.writer_generation_invocation_ref.model_copy(update={"invocation_sha256": "a" * 64})
    assert project_input_basis_ref(other, basis.project_input_sha256) != basis.project_input_basis_ref
    assert all(i.source_kind != "PROJECT_INPUT" for i in fx.binding.exact_authorized_source_refs)


@pytest.mark.parametrize("change", ["project", "support", "policy", "response"])
def test_identity_changes_bind_semantic_input_only_as_required(tmp_path, change):
    fx = fixture(tmp_path)
    old = fx.invocation
    if change == "project":
        fx.composer.writer_execution_store = replace_writer(fx, tmp_path / "other.db",
            project=fx.project.model_copy(update={"total_staff": 13}))
    elif change == "support":
        fx.support = fx.support.model_copy(update={"support_ref": fx.support.support_ref.model_copy(
            update={"support_semantic_fingerprint": "c" * 64})})
    elif change == "policy":
        fx.composer.POLICY_VERSION = "v2-test"
    else:
        data = old.model_dump(mode="json")
        data["response_contract"] = "canonical_claim_verifier_v2-test"
        data["verifier_semantic_input_sha256"] = build_verifier_semantic_input_sha256(data)
        new = VerifierClaimInputSchema.model_validate(data)
    if change != "response":
        new, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
    assert new.verifier_semantic_input_sha256 != old.verifier_semantic_input_sha256
    assert (new.authority_universe_sha256 == old.authority_universe_sha256) == (change != "project")
    if change == "project":
        assert new.verifier_project_input_basis.project_input_sha256 != old.verifier_project_input_basis.project_input_sha256


@pytest.mark.parametrize("field", ["project_input_sha256", "project_input_basis_ref", "writer_generation_invocation_ref"])
def test_basis_spoof_rejected(tmp_path, field):
    fx = fixture(tmp_path)
    data = fx.invocation.verifier_project_input_basis.model_dump(mode="json")
    if field == "writer_generation_invocation_ref":
        data[field]["invocation_sha256"] = "f" * 64
    else:
        data[field] = "f" * 64
    with pytest.raises(ValueError):
        VerifierProjectInputBasisV1.model_validate(data)


@pytest.mark.parametrize("field", ["run", "section", "write", "route"])
def test_exact_resolver_never_uses_another_writer(tmp_path, field):
    fx = fixture(tmp_path)
    key = fx.invocation.verifier_project_input_basis.writer_generation_invocation_ref.logical_execution_key
    data = key.model_dump(mode="json")
    if field in {"run", "section"}:
        data["writer_subject_ref"][field + "_id"] = "another"
    elif field == "write":
        data["write_instance_id"] = "another"
    else:
        data["route_decision_ref"]["route_decision_id"] = "another"
    new_key = type(key).model_validate(data)
    fx.composer.writer_execution_store = replace_writer(fx, tmp_path / "other.db", key=new_key)
    with pytest.raises(TargetClaimVerifierError):
        fx.composer.resolve_project_input_basis(fx.claim, fx.context.active_lineage)


def test_context_source_and_model_material_cover_project_input(tmp_path):
    fx = fixture(tmp_path)
    fx.composer.model_gateway = Gateway()
    invocation, material = model_material(fx)
    before_catalog = tuple(ContextSourceFamily)
    package, _ = fx.composer._package(material, occurrence="test-i5", invocation=invocation, validate=lambda: None)
    selected = [i for i in package.selected_materializations if "project_input" in i.materialized_payload]
    assert len(selected) == 1
    source = selected[0]
    assert source.materialized_payload["project_input"] == fx.project.model_dump(mode="json")
    source_binding = next(i.source_binding for i in package.candidate_decisions if i.candidate_id == source.candidate_id)
    assert source_binding.authority_owner == "exact-WriterGenerationInvocation"
    assert source_binding.source_schema_id == "verifier_writer_project_input_v1"
    assert source_binding.source_semantic_fingerprint == canonical_sha256(source.materialized_payload)
    assert tuple(ContextSourceFamily) == before_catalog
    assert "PROJECT_INPUT" not in ContextSourceFamily.__members__
    fx.composer.writer_execution_store = replace_writer(fx, tmp_path / "other.db", project=fx.project.model_copy(update={"total_staff": 13}))
    invocation2, material2 = model_material(fx)
    package2, _ = fx.composer._package(material2, occurrence="test-i5-2", invocation=invocation2, validate=lambda: None)
    source2 = next(i for i in package2.selected_materializations if "project_input" in i.materialized_payload)
    assert next(i.source_binding for i in package2.candidate_decisions if i.candidate_id == source2.candidate_id).source_semantic_fingerprint != source_binding.source_semantic_fingerprint
    assert package2.model_input.final_model_input_sha256 != package.model_input.final_model_input_sha256


@pytest.mark.parametrize("scenario", ["replay", "intent", "v1-input", "v1-result"])
def test_provider_receipts_replay_and_fail_closed(tmp_path, scenario):
    fx = fixture(tmp_path)
    fx.composer.model_gateway = gateway = Gateway()
    # The fixture's initial input predates Gateway output-cap binding; compare
    # replay against the exact input created with the same active Gateway.
    fx.invocation, _ = fx.composer.build_input(
        fx.claim, fx.binding, fx.support, fx.context
    )
    gateway.stop = scenario == "intent"
    # Foreign source composition is covered by canonical_section tests; this
    # fixture uses real durable Claim/Writer/Artifact and the exact model path.
    with patch.object(fx.composer, "_material", side_effect=lambda *args: model_material(fx)):
        if gateway.stop:
            with pytest.raises(KeyboardInterrupt):
                fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
        else:
            first = fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
        refs = fx.composer.store.list_references()
        if scenario.startswith("v1"):
            target = next(i for i in refs if i.artifact_type == ("canonical_verifier_input" if scenario == "v1-input" else "canonical_verifier_candidate"))
            original = fx.composer.store.get_json
            def read(ref, **identity):
                data = original(ref, **identity)
                if ref == target.artifact_ref:
                    data["schema_version"] = data["schema_version"].replace("v2", "v1")
                return data
            with patch.object(fx.composer.store, "get_json", side_effect=read), pytest.raises(TargetClaimVerifierError, match="requires V2"):
                fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
        elif gateway.stop:
            with pytest.raises(TargetClaimVerifierError) as error:
                fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
            assert error.value.error_code == "VERIFIER_INDETERMINATE_EXECUTION"
        else:
            assert fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context) == first
            assert first.verifier_semantic_input_sha256 == fx.invocation.verifier_semantic_input_sha256
        assert len(gateway.calls) == 1


def test_missing_writer_owner_fails_at_startup(tmp_path):
    fx = fixture(tmp_path)
    with pytest.raises(TargetClaimVerifierError, match="canonical Writer"):
        TargetClaimVerifierComposition(claim_runtime=fx.runtime, support_composition=fx.composer.support,
            model_gateway=None, writer_execution_store=None)


@pytest.mark.parametrize("change", ["project", "policy", "response"])
def test_changed_semantic_input_never_aliases_provider_occurrence(tmp_path, change):
    fx = fixture(tmp_path)
    fx.composer.model_gateway = gateway = Gateway()
    with patch.object(fx.composer, "_material", side_effect=lambda *args: model_material(fx)):
        original_input, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
        first = fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
        original_key, original_occurrence, original_prefix = fx.composer._physical(original_input)
        assert first.verifier_semantic_input_sha256 == original_input.verifier_semantic_input_sha256
        assert first.verification_occurrence_ref == original_occurrence
        assert len(gateway.calls) == 1
        assert gateway.calls[0].model_call_id == "model_call_verifier_" + original_key
        original_root = build_verifier_original_root(original_input)
        original_recovery = fx.runtime.resolve_verifier_local_recovery(original_root)
        assert original_recovery["authorization"] is None
        assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
        journal_before = fx.runtime.ledger_path.read_bytes()
        events_before = [json.loads(line) for line in journal_before.decode("utf-8").splitlines()]
        assert not any(event["event_type"] in {"verifier_correction_authorized", "verification_committed"}
            for event in events_before)
        references_before = fx.composer.store.list_references()
        artifacts_before = {path.relative_to(fx.composer.store.root): path.read_bytes()
            for path in fx.composer.store.root.rglob("*") if path.is_file()}
        identity = fx.composer._artifact_identity(original_input)
        candidate_before = fx.composer.store.get_bytes(original_prefix + "/candidate", **identity)

        def assert_changed_input_refused():
            # Cognition identity changes, while the registered execution scope
            # still belongs to the immutable first root. Pure construction is
            # not permission to publish a second intent or dispatch a Provider.
            changed_input, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
            assert original_input.verifier_semantic_input_sha256 == build_verifier_semantic_input_sha256(
                original_input.model_dump(mode="json"))
            assert changed_input.verifier_semantic_input_sha256 == build_verifier_semantic_input_sha256(
                changed_input.model_dump(mode="json"))
            assert changed_input.verifier_semantic_input_sha256 != original_input.verifier_semantic_input_sha256
            changed_key, changed_occurrence, changed_prefix = fx.composer._physical(changed_input)
            assert changed_key != original_key
            assert changed_occurrence != original_occurrence
            assert changed_prefix != original_prefix
            assert build_verifier_registration_scope_sha256(changed_input) == build_verifier_registration_scope_sha256(original_input)
            assert build_verifier_original_root(changed_input).original_root_ref != original_root.original_root_ref
            with pytest.raises(TargetClaimVerifierError) as error:
                fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
            assert error.value.error_code == "VERIFIER_REGISTERED_SCOPE_COLLISION"
            assert error.value.failure_category is FailureCategory.UNKNOWN
            assert error.value.failure_origin is FailureOrigin.UNKNOWN
            assert str(error.value) == "Verifier registration scope already binds a different initial input"
            assert len(gateway.calls) == 1
            assert fx.runtime.ledger_path.read_bytes() == journal_before
            assert fx.runtime.resolve_verifier_local_recovery(original_root) == original_recovery
            assert fx.runtime.resolve_claim_head(fx.claim.claim_ref) is None
            assert fx.composer.store.list_references() == references_before
            assert fx.composer.store.get_bytes(original_prefix + "/candidate", **identity) == candidate_before
            assert json.loads(candidate_before)["candidate"] == first.model_dump(mode="json")
            assert {path.relative_to(fx.composer.store.root): path.read_bytes()
                for path in fx.composer.store.root.rglob("*") if path.is_file()} == artifacts_before

        if change == "project":
            fx.composer.writer_execution_store = replace_writer(fx, tmp_path / "other.db",
                project=fx.project.model_copy(update={"total_staff": 13}))
            assert_changed_input_refused()
        elif change == "policy":
            fx.composer.POLICY_VERSION = "v2-test-change"
            assert_changed_input_refused()
        else:
            with patch("apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition.VERIFIER_RESPONSE_CONTRACT",
                    "canonical_claim_verifier_v2-test-change"):
                assert_changed_input_refused()


def test_untyped_writer_project_snapshot_is_rejected(tmp_path):
    fx = fixture(tmp_path)
    fx.composer.writer_execution_store = replace_writer(fx, tmp_path / "other.db",
        project=SimpleNamespace(model_dump=lambda **kwargs: {"untyped": "not ProjectInput"}))
    with pytest.raises(ValueError):
        fx.composer.resolve_project_input_basis(fx.claim, fx.context.active_lineage)


@pytest.mark.parametrize("version", ["verifier_claim_input_v1", "verifier_claim_input_v999"])
def test_input_schema_has_no_v1_or_unknown_dispatch(tmp_path, version):
    fx = fixture(tmp_path)
    data = fx.invocation.model_dump(mode="json")
    data["schema_version"] = version
    with pytest.raises(ValueError):
        VerifierClaimInputSchema.model_validate(data)


@pytest.mark.parametrize("version", ["verifier_semantic_result_candidate_v1", "verifier_semantic_result_candidate_v999"])
def test_candidate_schema_has_no_v1_or_unknown_dispatch(tmp_path, version):
    fx = fixture(tmp_path)
    candidate, _ = TargetClaimVerifierComposition.parse_candidate({"verdict": "UNSUPPORTED",
        "positive_basis_refs": [], "rationale": "Controlled", "issues": []}, invocation=fx.invocation,
        occurrence="test", receipt_ref="artifact://test")
    data = candidate.model_dump(mode="json")
    data["schema_version"] = version
    with pytest.raises(ValueError):
        VerifierSemanticResultCandidateSchema.model_validate(data)
