"""V4 candidate Verifier semantic prompt: prospective opt-in and frozen v2/v3 safety.

Fake provider only: checks real ContextPackage / ModelRequest / M6 behavior,
not LLM semantic quality or the frozen first-chapter Gold.
"""
import json
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.claim_support import SupportRelation
from apps.enterprise_document.schemas.agent_rag_v1.verification import VerificationVerdict
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.claim_verifier_composition import (
    TargetClaimVerifierComposition as Composer,
    TargetClaimVerifierError,
)
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimBindingError
from backend.tests.test_bca014_i5_verifier_project_input_basis import (
    Gateway,
    fixture,
    model_material,
    replace_writer,
)


def _v4_project(project):
    data = project.model_dump(mode="json")
    data["schema_version"] = "project_input_v3"
    data["generation_requirements"].setdefault("extra", {})[
        Composer.STRICT_DECISION_PROFILE_KEY
    ] = Composer.STRICT_SEMANTIC_PROFILE
    return ProjectInputSchema.model_validate(data)


def _v4_fixture(tmp_path, **kwargs):
    fx = fixture(tmp_path, **kwargs)
    fx.composer.model_gateway = Gateway()
    fx.composer.writer_execution_store = replace_writer(
        fx, tmp_path / "v4-writer.sqlite3", project=_v4_project(fx.project)
    )
    return fx


def test_v2_v3_frozen_profiles_and_new_v4_hash():
    assert Composer.prompt_profile_sha256() == (
        "9a0f17eb4fd5298595e6bef738e6f31b94a197f23c1e10b6671886968a44135a"
    )
    assert Composer.prompt_profile_sha256(strict_decision=True) == (
        "eb504b87173ed57d690f9a056308089b0e97b7f6a3ed67dfb7e5945ad7a8e4dd"
    )
    assert Composer.prompt_profile_sha256(
        strict_decision=True, semantic_v4=True
    ) == "4af538360d30b84a0c7283dff475fa714bf4014092df435a3b5a85cbd644afe4"
    assert Composer._instructions_v4()[0] == Composer._instructions(strict_decision=True)[0]
    assert Composer._instructions_v4()[2] == Composer._instructions(strict_decision=True)[2]
    assert Composer._instructions_v4()[1] != Composer._instructions(strict_decision=True)[1]
    with pytest.raises(ValueError, match="requires strict decision"):
        Composer.prompt_profile_sha256(semantic_v4=True)


def test_v4_exact_role_rule_coverage_without_new_authority():
    role = Composer._instructions_v4()[1]
    for fragment in (
        "every material factual assertion",
        "positive authority",
        "A conservative inference",
        "structured project_name",
        "An explicitly scoped absence",
        "Historical sources may support",
        "allowed_authority_refs",
        "admissible_positive_basis_refs",
        "exact_source_ref",
        "FACT_SNAPSHOT and EVIDENCE_PACKAGE",
        "SUPPORTED/PARTIALLY_SUPPORTED",
        "NOT_SUPPORTED",
        "CONTRADICTED, INDETERMINATE, and Binding CONTRADICTS forbid VERIFIED",
        "does not independently disqualify",
        "positive basis may be retained",
        "Never include a positive_basis_ref solely because it is mechanically admissible",
        "CONFLICTED means",
        "UNSUPPORTED means",
        "REJECTED means",
        "admissible_verdicts",
        "Return VERIFIED only when every material assertion",
    ):
        assert fragment in role


def test_v4_opt_in_preserves_v3_selection_for_existing_tasks(tmp_path):
    fx = fixture(tmp_path)
    previous_project = fx.project.model_dump(mode="json")
    previous_project["schema_version"] = "project_input_v3"
    previous_project["generation_requirements"].setdefault("extra", {})[
        Composer.STRICT_DECISION_PROFILE_KEY
    ] = Composer.STRICT_DECISION_PROFILE
    original = ProjectInputSchema.model_validate(previous_project)
    assert Composer._strict_decision_from_project(original)
    assert not Composer._semantic_v4_from_project(original)
    selected = _v4_project(fx.project)
    assert Composer._strict_decision_from_project(selected)
    assert Composer._semantic_v4_from_project(selected)
    assert selected.generation_requirements.extra[Composer.STRICT_DECISION_PROFILE_KEY] == (
        Composer.STRICT_SEMANTIC_PROFILE
    )
    assert not Composer._strict_decision_from_project(fx.project)
    assert not Composer._semantic_v4_from_project(fx.project)
    with pytest.raises(TargetClaimVerifierError, match="Unsupported prospective"):
        Composer._strict_decision_from_project({
            "schema_version": "project_input_v3",
            "generation_requirements": {"extra": {
                Composer.STRICT_DECISION_PROFILE_KEY: "claim-unrecognized-profile"
            }},
        })


def test_v4_context_package_and_actual_fake_model_request(tmp_path):
    fx = _v4_fixture(tmp_path, support_relation=SupportRelation.INDETERMINATE)
    invocation, material = model_material(fx)
    assert invocation.deterministic_validation_facts["verifier_prompt_profile_sha256"] == (
        Composer.prompt_profile_sha256(strict_decision=True, semantic_v4=True)
    )
    assert Composer._bound_instruction_version(invocation) == Composer.STRICT_SEMANTIC_INSTRUCTION_VERSION
    package, _ = fx.composer._package(
        material, occurrence="v4-prospective-package",
        invocation=invocation, validate=lambda: None,
    )
    assert package.instruction_binding.role_instruction_version == Composer.STRICT_SEMANTIC_INSTRUCTION_VERSION
    assert package.instruction_binding.prompt_template_version == Composer.STRICT_SEMANTIC_INSTRUCTION_VERSION
    messages = package.model_input.final_input_material.inline_final_input_payload["messages"]
    assert len(messages) == 5
    assert any("An explicitly scoped absence" in m["content"] for m in messages)
    contracts = [json.loads(m["content"]) for m in messages
                 if m["content"].startswith("{") and '"admissible_verdicts"' in m["content"]]
    assert len(contracts) == 1
    assert contracts[0]["admissible_verdicts"] == ["UNSUPPORTED", "CONFLICTED", "REJECTED"]
    assert "VERIFIED" not in contracts[0]["admissible_verdicts"]
    with patch.object(fx.composer, "_material", side_effect=lambda *args: model_material(fx)):
        candidate = fx.composer._evaluate(fx.claim, fx.binding, fx.support, fx.context)
    assert candidate.recommended_verdict is VerificationVerdict.UNSUPPORTED
    assert len(fx.composer.model_gateway.calls) == 1
    request = fx.composer.model_gateway.calls[0]
    assert request.prompt == ""
    assert request.messages
    assert request.extra["prompt_version"] == Composer.STRICT_SEMANTIC_INSTRUCTION_VERSION
    assert any("An explicitly scoped absence" in m["content"] for m in request.messages)
    assert request.extra["llm_context_summary"]["context_sha256"]


def test_default_v2_gateway_bound_identity_replays_without_v4_opt_in(tmp_path):
    """Control for the legacy fixture SHA mismatch: bind Gateway before input."""
    fx = fixture(tmp_path)
    fx.composer.model_gateway = Gateway()
    expected, _ = fx.composer.build_input(
        fx.claim, fx.binding, fx.support, fx.context
    )
    assert Composer._bound_instruction_version(expected) == Composer.INSTRUCTION_VERSION
    assert expected.deterministic_validation_facts["verifier_prompt_profile_sha256"] == (
        Composer.prompt_profile_sha256()
    )
    with patch.object(fx.composer, "_material", side_effect=lambda *args: model_material(fx)):
        first = fx.composer._evaluate(
            fx.claim, fx.binding, fx.support, fx.context
        )
        replay = fx.composer._evaluate(
            fx.claim, fx.binding, fx.support, fx.context
        )
    assert first.verifier_semantic_input_sha256 == expected.verifier_semantic_input_sha256
    assert replay == first
    assert len(fx.composer.model_gateway.calls) == 1


def test_v4_fake_native_commit_and_durable_replay_no_redispatch(tmp_path):
    fx = _v4_fixture(tmp_path, support_relation=SupportRelation.INDETERMINATE)
    composer = fx.composer
    with patch.object(composer, "_material", side_effect=lambda *args: model_material(fx)):
        first = composer.verify_claims(
            records=(fx.claim,), binding_records=(fx.binding,),
            support_records=(fx.support,), operation_context=fx.context,
        )
    assert len(first) == 1
    assert first[0].verification_result.verdict is VerificationVerdict.UNSUPPORTED
    assert len(composer.model_gateway.calls) == 1
    composer.validate_v2_identity(
        fx.claim, fx.binding, fx.support, first[0].verification_result, fx.context.active_lineage
    )
    with patch.object(composer, "_material", side_effect=lambda *args: model_material(fx)):
        repeated = composer.verify_claims(
            records=(fx.claim,), binding_records=(fx.binding,),
            support_records=(fx.support,), operation_context=fx.context,
        )
    assert repeated == first
    assert len(composer.model_gateway.calls) == 1


def test_v4_strict_m6_still_blocks_illegal_verified(tmp_path):
    fx = _v4_fixture(tmp_path, support_relation=SupportRelation.INDETERMINATE)
    gateway = fx.composer.model_gateway
    original_generate = gateway.generate

    def illegal_result(request):
        response = original_generate(request)
        data = json.loads(response.content)
        invocation, _ = fx.composer.build_input(fx.claim, fx.binding, fx.support, fx.context)
        data["verdict"] = "VERIFIED"
        data["positive_basis_refs"] = [
            invocation.verifier_project_input_basis.project_input_basis_ref
        ]
        return response.model_copy(update={"content": json.dumps(data)})

    with patch.object(gateway, "generate", side_effect=illegal_result), patch.object(
        fx.composer, "_material", side_effect=lambda *args: model_material(fx)
    ):
        with pytest.raises(ClaimBindingError, match="VERIFIED"):
            fx.composer.verify_claims(
                records=(fx.claim,), binding_records=(fx.binding,),
                support_records=(fx.support,), operation_context=fx.context,
            )
    assert len(gateway.calls) == 1
