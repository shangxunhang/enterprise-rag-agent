"""E2E-SM-001: prospective Verifier semantic-guidance tests, no paid provider.

The legacy profile is byte-identical. Real saved Claim006 remains a hard-fail
counterexample. A prospective new V3 Writer source owns the opt-in and exact
request identity; Fake verifier never supplies factual authority.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from model_gateway.fake_llm_client import FakeLLMClient
import backend.tests.test_fpbca_bcd_local_closure_minimal as current_mainline

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.agent_rag_v1.verification import (
    ClaimBindingRelation,
    VerificationVerdict,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.planning import (
    SectionPlanEntrySchema,
    SectionStrategyCode,
)
from apps.enterprise_document.services.agent_rag_v1.writer_context import (
    TargetWriterContextComposer,
)
from core.hashing import canonical_sha256
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
    TargetClaimVerifierComposition,
    TargetClaimVerifierError,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import (
    ClaimBindingError,
    ClaimVerificationRuntime,
)
from backend.tests.test_bca014_i5_verifier_project_input_basis import (
    Gateway,
    fixture,
    model_material,
    replace_writer,
)
from backend.tests.test_bca014_verifier_prompt_provenance_clarification import (
    _guard_candidate,
    _matrix_binding,
    PI,
)
from backend.tests.test_fpbca_v6_indeterminate_verifier_real_badcase_20261009 import (
    _frozen as actual_v6_failed_claim,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
    admissible_positive_basis_refs,
)


def _strict_project(project):
    data = project.model_dump(mode="json")
    data["schema_version"] = "project_input_v3"
    extra = dict(data["generation_requirements"].get("extra") or {})
    extra[TargetClaimVerifierComposition.STRICT_DECISION_PROFILE_KEY] = (
        TargetClaimVerifierComposition.STRICT_DECISION_PROFILE
    )
    data["generation_requirements"]["extra"] = extra
    return ProjectInputSchema.model_validate(data)


def _strict_fixture(tmp_path, **kwargs):
    fx = fixture(tmp_path, **kwargs)
    fx.composer.model_gateway = Gateway()
    strict_project = _strict_project(fx.project)
    fx.composer.writer_execution_store = replace_writer(
        fx, tmp_path / "prospective-writer.sqlite3", project=strict_project
    )
    return fx


@pytest.mark.parametrize("relation", list(SupportRelation))
@pytest.mark.parametrize("contradiction", [False, True])
@pytest.mark.parametrize("positive", [(), (PI,)])
def test_v3_admissible_verdict_projection_is_no_more_permissive_than_m6(
    relation, contradiction, positive
):
    binding = _matrix_binding("contradicts" if contradiction else "context_only")
    model_verdicts = TargetClaimVerifierComposition._admissible_verdicts_from_m6_inputs(
        support_relation=relation,
        binding_facts=binding.facts,
        positive_refs=positive,
    )
    assert set(model_verdicts) <= {
        "VERIFIED", "UNSUPPORTED", "CONFLICTED", "REJECTED"
    }
    assert "UNSUPPORTED" in model_verdicts
    assert "CONFLICTED" in model_verdicts
    assert "REJECTED" in model_verdicts
    candidate = _guard_candidate(binding, positive, VerificationVerdict.VERIFIED)
    support = type("S", (), {"relation": relation, "supporting_refs": ()})()
    try:
        ClaimVerificationRuntime.validate_v2_provenance(binding, support, candidate)
        m6_rejects = False
    except ClaimBindingError:
        m6_rejects = True
    # The mask is only a necessary condition. It must never advertise
    # VERIFIED when the full existing M6 provenance guard already rejects it.
    if m6_rejects:
        assert "VERIFIED" not in model_verdicts


def test_new_model_verdict_mask_rejects_actual_frozen_v6_illegal_candidate():
    """Native failed Claim is checked without dispatching or replaying its Run."""
    claim, binding, support, candidate, _native = actual_v6_failed_claim()
    projected = admissible_positive_basis_refs(
        binding,
        support,
        candidate.verifier_project_input_basis.project_input_basis_ref,
    )
    assert projected
    assert support.relation is SupportRelation.INDETERMINATE
    allowed = TargetClaimVerifierComposition._admissible_verdicts_from_m6_inputs(
        support_relation=support.relation,
        binding_facts=binding.facts,
        positive_refs=projected,
    )
    assert allowed == ("UNSUPPORTED", "CONFLICTED", "REJECTED")
    assert candidate.recommended_verdict.value not in allowed
    with pytest.raises(ClaimBindingError, match="VERIFIED forbidden"):
        ClaimVerificationRuntime.validate_v2_provenance(
            binding, support, candidate
        )


def test_existing_default_verifier_profile_is_byte_identical():
    composer = TargetClaimVerifierComposition
    assert composer.INSTRUCTION_VERSION == "v2-positive-provenance-v1"
    assert composer.prompt_profile_sha256() == composer.prompt_profile_sha256(
        strict_decision=False
    )
    legacy = composer._instructions()
    again = composer._instructions(strict_decision=False)
    assert legacy == again
    assert "admissible_verdicts" not in "\n".join(legacy)
    new = composer._instructions(strict_decision=True)
    assert new[0] == legacy[0]
    assert new[1].startswith(legacy[1])
    assert new[2].startswith(legacy[2])
    assert "causal prediction about downstream outcomes" in new[1]
    assert composer.prompt_profile_sha256(strict_decision=True) != composer.prompt_profile_sha256()


def test_strict_profile_cannot_reuse_an_existing_default_profile_input(tmp_path):
    """A new prospective Writer task may not silently alias its old verifier input."""
    fx = fixture(tmp_path)
    fx.composer.model_gateway = Gateway()
    legacy, _ = model_material(fx)
    strict = _strict_project(fx.project)
    fx.composer.writer_execution_store = replace_writer(
        fx, tmp_path / "other-writer.sqlite3", project=strict
    )
    prospective, material = model_material(fx)
    assert prospective.verifier_semantic_input_sha256 != legacy.verifier_semantic_input_sha256
    assert prospective.deterministic_validation_facts["verifier_prompt_profile_sha256"] != (
        legacy.deterministic_validation_facts["verifier_prompt_profile_sha256"]
    )
    with pytest.raises(TargetClaimVerifierError, match="current prompt profile"):
        fx.composer._package(
            material, occurrence="e2esm-forbidden-old-profile",
            invocation=legacy, validate=lambda: None
        )
    assert fx.composer.model_gateway.calls == []


def test_real_v6_project_delta_preserves_writer_instruction_and_changes_verifier_profile():
    """Read-only original V6 business input: prospective flag never becomes Writer authority."""
    original = Path(
        "D:/MyCode/_FPBCA_REAL_E2E_20261009_20261009T112450Z"
    ) / "audit/PROJECT_INPUT_V3.json"
    assert original.is_file()
    base = ProjectInputSchema.model_validate(
        json.loads(original.read_text(encoding="utf-8"))
    )
    assert base.schema_version == "project_input_v3"
    assert base.generation_requirements.citation_style == "citation_id_ascii_brackets_v2"
    candidate = _strict_project(base)
    assert canonical_sha256(base.model_dump(mode="json")) != canonical_sha256(
        candidate.model_dump(mode="json")
    )
    assert TargetClaimVerifierComposition._strict_decision_from_project(base) is False
    assert TargetClaimVerifierComposition._strict_decision_from_project(candidate) is True
    # A pure writer-instruction fixture proves the Verifier profile flag
    # neither adds source facts nor changes legacy writer citation grammar.
    entry = SectionPlanEntrySchema(
        section_id="offline-scope-check",
        section_title=base.generation_requirements.required_sections[0],
        section_order=1,
        applicability="applicable",
        strategy=SectionStrategyCode.TECHNICAL_GENERATE,
        citation_required=False,
    )
    old_bind, old_prompt = TargetWriterContextComposer._instructions(
        project_input=base, section=entry,
        gate_binding=None, route_decision=None, decision=None
    )
    new_bind, new_prompt = TargetWriterContextComposer._instructions(
        project_input=candidate, section=entry,
        gate_binding=None, route_decision=None, decision=None
    )
    assert old_bind == new_bind
    assert old_prompt == new_prompt
    assert "exact ASCII square-bracket marker [C1]" in new_prompt.role_instruction


def test_profile_opt_in_rejects_unknown_mode_and_non_v3():
    cls = TargetClaimVerifierComposition
    with pytest.raises(TargetClaimVerifierError, match="Unsupported prospective"):
        cls._strict_decision_from_project({
            "schema_version": "project_input_v3",
            "generation_requirements": {"extra": {
                cls.STRICT_DECISION_PROFILE_KEY: "unknown",
            }},
        })
    with pytest.raises(TargetClaimVerifierError, match="Unsupported prospective"):
        cls._strict_decision_from_project({
            "schema_version": "project_input_v2",
            "generation_requirements": {"extra": {
                cls.STRICT_DECISION_PROFILE_KEY: cls.STRICT_DECISION_PROFILE,
            }},
        })


def test_exact_prospective_writer_project_profile_reaches_fake_verifier_model_request(
    tmp_path
):
    fx = _strict_fixture(tmp_path, support_relation=SupportRelation.INDETERMINATE)
    invocation, material = model_material(fx)
    composer = fx.composer
    assert material["project_input"]["schema_version"] == "project_input_v3"
    assert material["project_input"]["generation_requirements"]["extra"][
        composer.STRICT_DECISION_PROFILE_KEY
    ] == composer.STRICT_DECISION_PROFILE
    assert invocation.deterministic_validation_facts["verifier_prompt_profile_sha256"] == (
        composer.prompt_profile_sha256(strict_decision=True)
    )
    package, _ = composer._package(
        material,
        occurrence="e2e-sm-prospective-package",
        invocation=invocation,
        validate=lambda: None,
    )
    instr = package.instruction_binding
    assert instr.role_instruction_version == composer.STRICT_DECISION_INSTRUCTION_VERSION
    messages = package.model_input.final_input_material.inline_final_input_payload["messages"]
    contract = [
        json.loads(row["content"]) for row in messages
        if row["content"].startswith("{") and '"admissible_verdicts"' in row["content"]
    ]
    assert len(contract) == 1
    assert contract[0]["admissible_verdicts"] == ["UNSUPPORTED", "CONFLICTED", "REJECTED"]
    assert "VERIFIED" not in contract[0]["admissible_verdicts"]
    assert "Writer ProjectInput rows support part" in str(messages)

    with patch.object(composer, "_material", side_effect=lambda *args: model_material(fx)):
        candidate = composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
    assert candidate.recommended_verdict is VerificationVerdict.UNSUPPORTED
    assert len(composer.model_gateway.calls) == 1
    request = composer.model_gateway.calls[0]
    assert request.extra["prompt_version"] == composer.STRICT_DECISION_INSTRUCTION_VERSION
    assert request.messages
    assert any('"admissible_verdicts"' in row["content"] for row in request.messages)
    assert any("Writer ProjectInput rows support part" in row["content"] for row in request.messages)
    assert request.extra["llm_context_summary"]["context_sha256"]


def test_new_strict_legal_negative_can_commit_through_unchanged_m6(tmp_path):
    fx = _strict_fixture(tmp_path, support_relation=SupportRelation.INDETERMINATE)
    composer = fx.composer
    with patch.object(composer, "_material", side_effect=lambda *args: model_material(fx)):
        results = composer.verify_claims(
            records=(fx.claim,),
            binding_records=(fx.binding,),
            support_records=(fx.support,),
            operation_context=fx.context,
        )
    assert len(results) == 1
    result = results[0]
    assert result.verification_result.verdict is VerificationVerdict.UNSUPPORTED
    assert len(composer.model_gateway.calls) == 1
    # A read/replay of this successful prospective v3 request cannot redispatch.
    with patch.object(composer, "_material", side_effect=lambda *args: model_material(fx)):
        replayed = composer.verify_claims(
            records=(fx.claim,),
            binding_records=(fx.binding,),
            support_records=(fx.support,),
            operation_context=fx.context,
        )
    assert replayed == results
    assert len(composer.model_gateway.calls) == 1


def test_v08_fake_mainline_is_a_writer_only_negative_scope_control(
    tmp_path, monkeypatch
):
    """The existing V08 Fake demo is not a Verifier/Post-write Target E2E."""
    project_factory = current_mainline._project_input
    original_generate = FakeLLMClient.generate
    observed = []

    def project_with_strict_verifier(task_id):
        return _strict_project(project_factory(task_id))

    def observe(client, request):
        observed.append(request.model_copy(deep=True))
        return original_generate(client, request)

    with monkeypatch.context() as scoped:
        scoped.setattr(current_mainline, "_project_input", project_with_strict_verifier)
        scoped.setattr(FakeLLMClient, "generate", observe)
        current_mainline.test_v08_current_template_reaches_actual_writer_model_request(
            tmp_path, scoped
        )
    assert any(req.extra.get("call_purpose") == "scheme_section_generation" for req in observed)
    # This historical V08 configuration ran a one-Section old-path
    # document assembly (partial_success), but does not cross the native
    # Target Verifier. Deliberately reject it as an E2E acceptance harness.
    verifier = [
        req for req in observed
        if req.extra.get("call_purpose") == "claim_verification"
    ]
    assert verifier == []


def test_strict_profile_does_not_let_fake_illegal_verified_pass_m6(tmp_path):
    fx = _strict_fixture(tmp_path, support_relation=SupportRelation.INDETERMINATE)
    gateway = fx.composer.model_gateway
    original = gateway.generate

    def illegal_provider(request):
        response = original(request)
        payload = json.loads(response.content)
        payload["verdict"] = "VERIFIED"
        # A mechanically admissible positive ProjectInput ref is necessary so
        # this counterexample reaches the existing M6 Support veto.
        exact_input, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
        payload["positive_basis_refs"] = [exact_input.verifier_project_input_basis.project_input_basis_ref]
        return response.model_copy(update={"content": json.dumps(payload)})

    with patch.object(gateway, "generate", side_effect=illegal_provider), patch.object(
        fx.composer, "_material", side_effect=lambda *args: model_material(fx)
    ):
        with pytest.raises(ClaimBindingError, match="VERIFIED"):
            fx.composer.verify_claims(
                records=(fx.claim,), binding_records=(fx.binding,),
                support_records=(fx.support,), operation_context=fx.context,
            )
    assert len(gateway.calls) == 1
