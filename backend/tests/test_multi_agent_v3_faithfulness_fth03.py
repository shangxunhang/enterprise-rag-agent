from __future__ import annotations

import pytest

from apps.enterprise_document.schemas.post_write.foundation import ValidationSemanticState
from apps.enterprise_document.schemas.post_write.quality import FaithfulnessCertaintyRelation
from apps.enterprise_document.schemas.post_write.requirement import RequirementRefSchema
from apps.enterprise_document.services.post_write.faithfulness import (
    FaithfulnessCertaintyProjector,
    FaithfulnessIntegrityError,
    FaithfulnessValidator,
)
from apps.enterprise_document.services.post_write.shared_claim_support import (
    SharedClaimEvidenceSupportEvaluator,
)
from backend.tests.test_multi_agent_v3_post_write_semantic_quality import (
    _sif07_target,
    _subject_hash, _prepare_support,
)


def _requirement_ref(seed: str = "a") -> RequirementRefSchema:
    return RequirementRefSchema(
        requirement_snapshot_id="requirement-snapshot://fth03-test",
        requirement_semantic_contract_version="post-write-requirements-v2",
        requirement_hash=seed * 64,
    )


def _target_support(tmp_path):
    target, _claim, runtime, context, _calls, _state = _sif07_target(tmp_path)
    _target_set, support = _prepare_support(
        support_adapter=SharedClaimEvidenceSupportEvaluator(runtime),claim_target=target,
        selected_evidence_refs=("evidence:C1",),target_set_contract_version="faithfulness-target-v1",
        support_semantic_contract_version="support-contract-v1",operation_context=context)
    return target, support


@pytest.mark.parametrize(
    ("relation", "expected_state"),
    [
        (FaithfulnessCertaintyRelation.ALIGNED, ValidationSemanticState.PASS),
        (FaithfulnessCertaintyRelation.ESCALATED, ValidationSemanticState.FAIL),
        (FaithfulnessCertaintyRelation.INDETERMINATE, ValidationSemanticState.INDETERMINATE),
    ],
)
def test_fth03_certainty_relation_projects_independent_faithfulness_fact(
    tmp_path, relation, expected_state
):
    target, support = _target_support(tmp_path)
    fact = FaithfulnessCertaintyProjector.project(
        validation_run_id="validation-run:fth03-current",
        subject_hash=_subject_hash(),
        claim_target=target,
        support_record=support,
        requirement_ref=_requirement_ref(),
        applicable_requirement_ids=(),
        certainty_relation=relation,
        certainty_reason_codes=("EXACT_CERTAINTY_TEST",),
        physical_support_receipt_ref="artifact://claim-support/test/candidate",
        related_refs=("source-snapshot://fth03",),
    )

    assert fact.semantic_state is expected_state
    assert fact.payload["support_relation"] == support.relation.value
    assert fact.payload["rule_ids"] == ["PW-R-FTH-03"]
    observation = fact.payload["certainty_observation"]
    assert observation["certainty_relation"] == relation.value
    assert observation["claim_evidence_support_ref"] == support.support_ref.model_dump(
        mode="json"
    )
    assert observation["evidence_target_set_ref"] == support.evidence_target_set_ref.model_dump(
        mode="json"
    )
    assert observation["requirement_ref"] == _requirement_ref().model_dump(mode="json")
    assert observation["applicable_requirement_ids"] == []
    assert support.relation.value == "SUPPORTED"


def test_fth03_fact_identity_binds_exact_receipt_and_requirement(tmp_path):
    target, support = _target_support(tmp_path)
    common = dict(
        validation_run_id="validation-run:fth03-current",
        subject_hash=_subject_hash(),
        claim_target=target,
        support_record=support,
        applicable_requirement_ids=(),
        certainty_relation="ALIGNED",
        certainty_reason_codes=("EXACT_CERTAINTY_TEST",),
        related_refs=(),
    )
    first = FaithfulnessCertaintyProjector.project(
        **common,
        requirement_ref=_requirement_ref("a"),
        physical_support_receipt_ref="artifact://claim-support/one/candidate",
    )
    changed_receipt = FaithfulnessCertaintyProjector.project(
        **common,
        requirement_ref=_requirement_ref("a"),
        physical_support_receipt_ref="artifact://claim-support/two/candidate",
    )
    changed_requirement = FaithfulnessCertaintyProjector.project(
        **common,
        requirement_ref=_requirement_ref("b"),
        physical_support_receipt_ref="artifact://claim-support/one/candidate",
    )
    assert len({first.fact_id, changed_receipt.fact_id, changed_requirement.fact_id}) == 3


def test_fth03_rejects_foreign_validation_subject(tmp_path):
    target, support = _target_support(tmp_path)
    with pytest.raises(FaithfulnessIntegrityError, match="different ValidationSubject"):
        FaithfulnessCertaintyProjector.project(
            validation_run_id="validation-run:fth03-current",
            subject_hash="f" * 64,
            claim_target=target,
            support_record=support,
            requirement_ref=_requirement_ref(),
            certainty_relation="ALIGNED",
            certainty_reason_codes=(),
            physical_support_receipt_ref="artifact://claim-support/test/candidate",
        )
