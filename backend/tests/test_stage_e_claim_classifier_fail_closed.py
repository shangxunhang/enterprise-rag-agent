from __future__ import annotations

import pytest

from apps.enterprise_document.services.scheme_writer.writer_governance import (
    CLAIM_CLASSIFIER_ID,
    CLAIM_CLASSIFIER_POLICY_SHA256,
    CLAIM_CLASSIFIER_VERSION,
    DeterministicClaimClassifier,
    WriterClaimAuditor,
)
from stage_e_integrity_test_fixtures import make_binding


@pytest.mark.parametrize(
    ("content", "expected_type"),
    [
        ("遵循《数据安全法》开展建设。", "policy_requirement"),
        ("平台总投入二百五十万。", "current_project_cost"),
        ("人民币二百五十万元用于平台建设。", "current_project_cost"),
        ("项目投资为贰佰伍拾万元。", "current_project_cost"),
        ("系统应符合 GB/T 22239。", "technical_specification"),
        ("平台将在第三季度上线。", "current_project_schedule"),
        ("系统应在三个月内完成交付。", "current_project_schedule"),
        ("本项目部署两百台服务器。", "current_project_quantity"),
        ("平台配置三十套安全设备。", "current_project_quantity"),
        ("采购采用公开招标方式。", "procurement_requirement"),
    ],
)
def test_common_chinese_claims_are_classified_and_blocked_when_not_allowed(
    content: str,
    expected_type: str,
) -> None:
    binding = make_binding(
        allowed=["general_method"],
        forbidden=[expected_type],
    )
    audit = WriterClaimAuditor().audit(content, binding=binding)

    assert expected_type in [item.claim_type.value for item in audit.generated_claims]
    assert audit.passed is False
    assert audit.human_review_required is True
    assert any(
        item.violation_code == "forbidden_claim_type_generated"
        and item.claim_type is not None
        and item.claim_type.value == expected_type
        for item in audit.violations
    )


def test_unclassified_substantive_sentence_fails_closed() -> None:
    binding = make_binding(allowed=["general_method"])
    audit = WriterClaimAuditor().audit(
        "平台具备持续演进能力。",
        binding=binding,
    )

    assert audit.generated_claims == []
    assert audit.passed is False
    assert audit.human_review_required is True
    assert audit.checks["all_substantive_sentences_classified"] is False
    assert [item.violation_code for item in audit.violations] == [
        "unclassified_substantive_claim"
    ]


def test_pure_heading_and_diagnostic_refusal_are_not_substantive_claims() -> None:
    classifier = DeterministicClaimClassifier()
    for sentence in (
        "### 实施方案",
        "实施方案",
        "当前不生成正式章节正文。",
        "已由最终证据门禁转入人工复核。",
        "[cit_001]",
    ):
        result = classifier.classify_sentence(sentence)
        assert result.substantive is False
        assert result.claim_types == ()


def test_classification_is_independent_of_allowed_set() -> None:
    sentence = "遵循《数据安全法》开展建设。"
    classifier = DeterministicClaimClassifier()
    direct = classifier.classify_sentence(sentence)
    outputs = []
    for allowed in (
        ["general_method"],
        ["policy_requirement"],
        ["template_structure"],
    ):
        binding = make_binding(allowed=allowed)
        audit = WriterClaimAuditor(classifier=classifier).audit(
            sentence,
            binding=binding,
        )
        outputs.append(
            [item.claim_type.value for item in audit.generated_claims]
        )

    expected = [item.value for item in direct.claim_types]
    assert expected == ["policy_requirement"]
    assert outputs == [expected, expected, expected]


def test_classifier_identity_and_policy_are_frozen() -> None:
    classifier = DeterministicClaimClassifier()
    assert classifier.classifier_id == CLAIM_CLASSIFIER_ID
    assert classifier.classifier_version == CLAIM_CLASSIFIER_VERSION
    assert classifier.policy_sha256 == CLAIM_CLASSIFIER_POLICY_SHA256
    assert len(classifier.policy_sha256) == 64
