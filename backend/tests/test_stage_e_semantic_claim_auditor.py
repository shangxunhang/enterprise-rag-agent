from __future__ import annotations

import copy

from apps.enterprise_document.services.scheme_writer.semantic_claim_classifier import (
    SEMANTIC_CLAIM_CLASSIFIER_ID,
    SemanticClaimClassifier,
)
from apps.enterprise_document.services.scheme_writer.writer_governance import (
    WriterClaimAuditor,
    extract_substantive_sentence_spans,
)
from schemas.model import ModelResponseSchema
from stage_e_integrity_test_fixtures import evaluate, make_binding, make_section


class _QueuedGateway:
    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        content = self.contents.pop(0) if self.contents else "{}"
        return ModelResponseSchema(
            model_call_id=request.model_call_id,
            task_id=request.task_id,
            run_id=request.run_id,
            model_name="local_qwen2_5_3b-test",
            success=True,
            content=content,
            finish_reason="stop",
            created_at=request.created_at,
        )


def _semantic_auditor(gateway: _QueuedGateway, *, max_retry_depth: int = 3):
    return WriterClaimAuditor(
        classifier=SemanticClaimClassifier(
            model_gateway=gateway,
            batch_size=10,
            max_retry_depth=max_retry_depth,
        )
    )


def test_structural_labels_are_not_sent_as_substantive_claims() -> None:
    rows = extract_substantive_sentence_spans(
        "2. **系统设计**：\n我们将部署防火墙。"
    )
    assert [row["text"] for row in rows] == ["我们将部署防火墙。"]


def test_semantic_classifier_does_not_turn_plain_yitao_into_quantity() -> None:
    gateway = _QueuedGateway(
        '{"items":[{"id":"s0","claim_types":["current_project_fact"],"confidence":0.96}]}'
    )
    binding = make_binding(
        allowed=["current_project_fact"],
        forbidden=["current_project_quantity"],
    )
    audit = _semantic_auditor(gateway).audit(
        "本项目旨在制定一套全面且高效的政务云建设方案。",
        binding=binding,
        runtime_context={"task_id": "task_semantic", "workflow_run_id": binding.run_id},
    )

    assert audit.passed is True
    assert [item.claim_type.value for item in audit.generated_claims] == [
        "current_project_fact"
    ]
    assert audit.integrity.classifier_id == SEMANTIC_CLAIM_CLASSIFIER_ID
    assert gateway.requests[0].model_role == "semantic_gate"
    assert "不因出现‘一套/一个’等普通量词" in gateway.requests[0].prompt


def test_semantic_claim_type_is_still_rejected_by_deterministic_policy() -> None:
    gateway = _QueuedGateway(
        '{"items":[{"id":"s0","claim_types":["current_project_configuration"],"confidence":0.99}]}'
    )
    binding = make_binding(
        allowed=["general_method"],
        forbidden=["current_project_configuration"],
    )
    audit = _semantic_auditor(gateway).audit(
        "我们将选用华为公司的高端路由器和交换机。",
        binding=binding,
        runtime_context={"task_id": "task_semantic", "workflow_run_id": binding.run_id},
    )

    assert audit.passed is False
    assert audit.generated_claims[0].claim_type.value == "current_project_configuration"
    assert [item.violation_code for item in audit.violations] == [
        "forbidden_claim_type_generated"
    ]


def test_malformed_semantic_output_retries_then_fails_closed() -> None:
    gateway = _QueuedGateway("{}", "{}")
    binding = make_binding(allowed=["general_method"])
    audit = _semantic_auditor(gateway, max_retry_depth=1).audit(
        "平台具备持续演进能力。",
        binding=binding,
        runtime_context={"task_id": "task_semantic", "workflow_run_id": binding.run_id},
    )

    assert len(gateway.requests) == 2
    assert audit.generated_claims == []
    assert audit.passed is False
    assert audit.checks["all_substantive_sentences_classified"] is False
    assert [item.violation_code for item in audit.violations] == [
        "semantic_unclassified_substantive_claim"
    ]


def test_hard_gate_rejects_forged_semantic_claim_type_by_policy_recomputation() -> None:
    gateway = _QueuedGateway(
        '{"items":[{"id":"s0","claim_types":["general_method"],"confidence":0.98}]}'
    )
    binding = make_binding(
        allowed=["general_method"],
        forbidden=["current_project_fact"],
    )
    content = "采用分阶段实施方法，形成闭环治理流程。"
    audit = _semantic_auditor(gateway).audit(
        content,
        binding=binding,
        runtime_context={"task_id": "task_semantic", "workflow_run_id": binding.run_id},
    )
    forged = copy.deepcopy(audit.model_dump(mode="json", exclude_none=True))
    forged["generated_claims"][0]["claim_type"] = "current_project_fact"
    forged["violations"] = []
    forged["passed"] = True
    forged["human_review_required"] = False
    forged["checks"]["claim_types_within_allowed_set"] = True
    section = make_section(content=content, binding=binding)
    section.extra["claim_audit"] = forged
    section.extra["formal_deliverable_eligible"] = True
    section.extra["need_human_review"] = False

    result = evaluate(section)

    assert result.passed is False
    assert result.checks["writer_claim_audit_recomputed_valid"] is False
    assert result.metadata["writer_failure_code_section_ids"][
        "writer_claim_audit_recomputation_mismatch"
    ] == [section.section_id]
    assert len(gateway.requests) == 1


def test_hard_gate_reuses_persisted_semantics_without_second_model_call() -> None:
    gateway = _QueuedGateway(
        '{"items":[{"id":"s0","claim_types":["general_method"],"confidence":0.98}]}'
    )
    binding = make_binding(allowed=["general_method"])
    content = "采用分阶段实施方法，形成闭环治理流程。"
    audit = _semantic_auditor(gateway).audit(
        content,
        binding=binding,
        runtime_context={"task_id": "task_semantic", "workflow_run_id": binding.run_id},
    )
    section = make_section(content=content, binding=binding)
    section.extra["claim_audit"] = audit.model_dump(mode="json", exclude_none=True)
    section.extra["formal_deliverable_eligible"] = True
    section.extra["need_human_review"] = False

    result = evaluate(section)

    assert result.passed is True
    assert result.checks["writer_claim_audit_recomputed_valid"] is True
    assert len(gateway.requests) == 1
