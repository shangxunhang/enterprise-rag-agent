"""Run final Stage E identity, lineage, integrity, and fail-closed acceptance."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for path in (BACKEND_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from apps.enterprise_document.schemas.gate_decision import (  # noqa: E402
    ClaimRiskLevel,
    DeliveryMode,
    GateDecisionSchema,
)
from apps.enterprise_document.schemas.scheme_writer import (  # noqa: E402
    SchemeDraftSchema,
    SchemeSectionSchema,
    SectionEvalSchema,
)
from apps.enterprise_document.schemas.writer_governance import (  # noqa: E402
    WriterGateBindingSchema,
)
from apps.enterprise_document.services.document_gate import (  # noqa: E402
    evaluate_scheme_draft,
)
from apps.enterprise_document.services.scheme_writer.writer_governance import (  # noqa: E402
    CLAIM_CLASSIFIER_POLICY_SHA256,
    DeterministicClaimClassifier,
    WriterClaimAuditor,
    blocked_claim_audit,
    extract_writer_gate_binding,
    inject_required_qualifiers,
)
from rag.offline.resolver import ActiveIndexResolver  # noqa: E402
from rag.runtime.retrieval_runtime import (  # noqa: E402
    RetrievalRuntime,
    RetrievalRuntimeConfig,
)
from rag.schema.evidence_assessment_v2 import EvidenceStatus  # noqa: E402
from rag.services.rag_service import RAGService  # noqa: E402
from run_stage_d_online_acceptance import UNKNOWN_QUERY, _service_request  # noqa: E402
from schemas.rag import EvidenceBundleSchema  # noqa: E402
from schemas.status import ExecutionStatus  # noqa: E402


DOCUMENT_ID = "document_stage_e_runtime_v3"
RUN_ID = "stage-e-runtime-v3"
SECTION_ID = "section_stage_e_runtime_v3_001"
SECTION_TITLE = "实施方案"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="E:/rag-agent-runtime/stage_e_final_runtime_acceptance_v3.json",
    )
    parser.add_argument(
        "--capture-output",
        default="E:/rag-agent-runtime/stage_e_final_runtime_capture_v3.jsonl",
    )
    return parser


def _binding(
    *,
    section_id: str = SECTION_ID,
    section_title: str = SECTION_TITLE,
    mode: DeliveryMode = DeliveryMode.GROUNDED,
    allowed: list[str] | None = None,
    forbidden: list[str] | None = None,
    qualifiers: list[str] | None = None,
    human_review: bool = False,
) -> WriterGateBindingSchema:
    status = EvidenceStatus.SUFFICIENT
    if mode in {DeliveryMode.BLOCKED, DeliveryMode.FRAMEWORK_ONLY}:
        status = EvidenceStatus.ABSENT
    elif mode is DeliveryMode.HUMAN_REVIEW:
        status = EvidenceStatus.CONFLICTED
    return WriterGateBindingSchema(
        section_id=section_id,
        section_title=section_title,
        section_order=1,
        document_id=DOCUMENT_ID,
        run_id=RUN_ID,
        gate_decision=GateDecisionSchema(
            policy_id="stage_e_runtime_integrity_v3",
            evidence_status=status,
            delivery_mode=mode,
            claim_risk_level=ClaimRiskLevel.HIGH,
            allowed_claim_types=allowed or [],
            forbidden_claim_types=forbidden or [],
            required_qualifiers=qualifiers or [],
            covered_topics=[section_title]
            if status is not EvidenceStatus.ABSENT
            else [],
            missing_topics=[]
            if status is not EvidenceStatus.ABSENT
            else [section_title],
            human_review_required=human_review,
            reason_codes=["stage_e_runtime_integrity_v3"],
        ),
        generation_allowed=mode
        in {
            DeliveryMode.GROUNDED,
            DeliveryMode.QUALIFIED,
            DeliveryMode.FRAMEWORK_ONLY,
        },
        evidence_contract_sha256="a" * 64,
        prompt_evidence_source="context_pack_selected_results",
        prompt_evidence_contract_sha256="b" * 64,
    )


def _section(
    content: str,
    binding: WriterGateBindingSchema,
    *,
    upstream_eligible: bool | None = None,
) -> SchemeSectionSchema:
    audit = (
        WriterClaimAuditor().audit(content, binding=binding)
        if binding.generation_allowed
        else blocked_claim_audit(content, binding)
    )
    recomputed = bool(
        binding.generation_allowed
        and binding.gate_decision.delivery_mode
        not in {DeliveryMode.BLOCKED, DeliveryMode.HUMAN_REVIEW}
        and not binding.gate_decision.human_review_required
        and audit.passed
    )
    return SchemeSectionSchema(
        section_id=binding.section_id,
        section_title=binding.section_title,
        section_order=binding.section_order or 1,
        content=content,
        status=(
            ExecutionStatus.SUCCESS
            if binding.generation_allowed
            else ExecutionStatus.PARTIAL_SUCCESS
        ),
        eval_result=SectionEvalSchema(
            passed=audit.passed,
            checks={"claim_audit_passed": audit.passed},
            failures=[] if audit.passed else ["claim_audit_failed"],
        ),
        extra={
            "writer_gate_binding": binding.model_dump(
                mode="json", exclude_none=True
            ),
            "writer_execution_status": binding.execution_status.value,
            "claim_audit": audit.model_dump(mode="json", exclude_none=True),
            "required_qualifiers_applied": list(audit.applied_qualifiers),
            "formal_deliverable_eligible": (
                recomputed if upstream_eligible is None else upstream_eligible
            ),
            "need_human_review": bool(
                binding.gate_decision.human_review_required
                or audit.human_review_required
            ),
            "generation_blocked": not binding.generation_allowed,
            "generation_block_reason": (
                "writer_generation_blocked"
                if not binding.generation_allowed
                else None
            ),
        },
    )


def _hard_gate(section: SchemeSectionSchema) -> dict[str, Any]:
    draft = SchemeDraftSchema(
        draft_id=f"draft_{section.section_id}",
        document_id=DOCUMENT_ID,
        task_id="task_stage_e_runtime_v3",
        run_id=RUN_ID,
        title="Stage E Runtime V3",
        full_text=section.content,
        sections=[section],
        required_sections=[section.section_title],
        created_at="2026-08-04T07:00:00+00:00",
    )
    return evaluate_scheme_draft(
        draft,
        citation_required=False,
        retrieved_chunk_ids=[],
        evidence_sufficient=True,
        workflow_complete=True,
    ).model_dump(mode="json")


def _canonical_normalized(bundle: EvidenceBundleSchema) -> dict[str, Any]:
    extra = dict(bundle.extra or {})
    canonical_extra = {
        key: extra.get(key)
        for key in (
            "gate_decision",
            "generation_allowed",
            "prompt_evidence_source",
            "prompt_evidence_contract",
        )
        if key in extra
    }
    return {
        "evidence": {
            "schema_version": bundle.schema_version,
            "task_id": bundle.task_id,
            "run_id": bundle.run_id,
            "query": bundle.query,
            "extra": canonical_extra,
        },
        "extra": canonical_extra,
    }


def _type_values(content: str) -> list[str]:
    return [
        item.value
        for item in DeterministicClaimClassifier()
        .classify_sentence(content)
        .claim_types
    ]


def main() -> int:
    args = _parser().parse_args()
    pointer = PROJECT_ROOT / "data/processed/indexes/active_index.json"
    resolver = ActiveIndexResolver(
        verify_manifest_hash=True,
        verify_artifacts=False,
    )
    active_before = resolver.resolve(pointer)

    grounded_binding = _binding(allowed=["general_method"])
    grounded = _section(
        "采用分阶段实施方法，形成闭环治理流程。",
        grounded_binding,
    )
    grounded_gate = _hard_gate(grounded)

    def gate_for_binding_update(**updates: Any) -> dict[str, Any]:
        binding = grounded_binding.model_copy(update=updates)
        return _hard_gate(
            _section(
                "采用分阶段实施方法，形成闭环治理流程。",
                binding,
            )
        )

    missing_section_order_gate = gate_for_binding_update(section_order=None)
    missing_document_id_gate = gate_for_binding_update(document_id=None)
    missing_run_id_gate = gate_for_binding_update(run_id=None)
    missing_evidence_hash_gate = gate_for_binding_update(
        evidence_contract_sha256=None
    )
    missing_prompt_hash_gate = gate_for_binding_update(
        prompt_evidence_contract_sha256=None
    )
    cross_document_gate = gate_for_binding_update(document_id="document_other")
    cross_run_gate = gate_for_binding_update(run_id="run_other")
    wrong_order_section = _section(
        "采用分阶段实施方法，形成闭环治理流程。",
        grounded_binding,
    ).model_copy(update={"section_order": 2}, deep=True)
    wrong_order_gate = _hard_gate(wrong_order_section)

    stale = copy.deepcopy(grounded)
    stale.content = "遵循《数据安全法》开展建设。"
    stale.extra["formal_deliverable_eligible"] = True
    stale_gate = _hard_gate(stale)

    policy_audit = WriterClaimAuditor().audit(
        "遵循《数据安全法》开展建设。",
        binding=grounded_binding,
    )
    money_audit = WriterClaimAuditor().audit(
        "平台总投入二百五十万。",
        binding=grounded_binding,
    )
    technical_audit = WriterClaimAuditor().audit(
        "系统应符合 GB/T 22239。",
        binding=grounded_binding,
    )
    schedule_audit = WriterClaimAuditor().audit(
        "平台将在第三季度上线。",
        binding=grounded_binding,
    )
    quantity_audit = WriterClaimAuditor().audit(
        "本项目部署两百台服务器。",
        binding=grounded_binding,
    )
    procurement_audit = WriterClaimAuditor().audit(
        "采购采用公开招标方式。",
        binding=grounded_binding,
    )
    unclassified_audit = WriterClaimAuditor().audit(
        "平台具备持续演进能力。",
        binding=grounded_binding,
    )

    qualifier_binding = _binding(
        mode=DeliveryMode.QUALIFIED,
        allowed=["current_project_cost"],
        qualifiers=["根据现有估算材料", "最终金额以审批或采购结果为准"],
    )
    qualified_content, _ = inject_required_qualifiers(
        "本项目预算金额为250万元。",
        qualifier_binding,
    )
    qualified = _section(qualified_content, qualifier_binding)
    qualifier_removed = copy.deepcopy(qualified)
    qualifier_removed.content = qualifier_removed.content.replace(
        "最终金额以审批或采购结果为准", ""
    )
    qualifier_removed_gate = _hard_gate(qualifier_removed)

    binding_a = _binding(section_id="section_A", section_title="章节A", allowed=["general_method"])
    binding_b = _binding(section_id="section_B", section_title="章节B", allowed=["general_method"])
    section_a = _section("采用分阶段实施方法，形成闭环治理流程。", binding_a)
    section_b = _section("采用分阶段实施方法，形成闭环治理流程。", binding_b)
    cross_binding = copy.deepcopy(section_b)
    cross_binding.extra["writer_gate_binding"] = copy.deepcopy(
        section_a.extra["writer_gate_binding"]
    )
    cross_binding_gate = _hard_gate(cross_binding)
    cross_audit = copy.deepcopy(section_b)
    cross_audit.extra["claim_audit"] = copy.deepcopy(section_a.extra["claim_audit"])
    cross_audit_gate = _hard_gate(cross_audit)

    def tampered_audit(field_path: tuple[str, ...], value: Any) -> dict[str, Any]:
        section = copy.deepcopy(grounded)
        target = section.extra["claim_audit"]
        for key in field_path[:-1]:
            target = target[key]
        target[field_path[-1]] = value
        return _hard_gate(section)

    binding_hash_gate = tampered_audit(
        ("integrity", "writer_gate_binding_sha256"), "f" * 64
    )
    evidence_hash_gate = tampered_audit(
        ("integrity", "evidence_contract_sha256"), "c" * 64
    )
    prompt_hash_gate = tampered_audit(
        ("integrity", "prompt_evidence_contract_sha256"), "d" * 64
    )
    allowed_boundary_gate = tampered_audit(
        ("allowed_claim_types",), ["template_structure"]
    )
    forbidden_boundary_gate = tampered_audit(
        ("forbidden_claim_types",), ["current_project_cost"]
    )
    qualifier_boundary_gate = tampered_audit(
        ("required_qualifiers",), ["伪造限定语"]
    )
    span_section = copy.deepcopy(grounded)
    span_section.extra["claim_audit"]["generated_claims"][0]["span_start"] += 1
    span_gate = _hard_gate(span_section)
    claim_section = copy.deepcopy(grounded)
    claim_section.extra["claim_audit"]["generated_claims"][0]["section_id"] = "section_other"
    claim_section_gate = _hard_gate(claim_section)

    forged_formal = copy.deepcopy(grounded)
    forged_formal.content += "正文篡改。"
    forged_formal.extra["formal_deliverable_eligible"] = True
    forged_formal_gate = _hard_gate(forged_formal)

    classifier = DeterministicClaimClassifier()
    allowed_bias_outputs = []
    for allowed in (["general_method"], ["policy_requirement"], ["template_structure"]):
        binding = _binding(allowed=allowed)
        audit = WriterClaimAuditor(classifier=classifier).audit(
            "遵循《数据安全法》开展建设。",
            binding=binding,
        )
        allowed_bias_outputs.append(
            [item.claim_type.value for item in audit.generated_claims]
        )

    runtime = RetrievalRuntime(
        RetrievalRuntimeConfig(
            capture_output=str(Path(args.capture_output).expanduser()),
            index_source_mode="manifest",
            active_index_pointer="data/processed/indexes/active_index.json",
            verify_active_index_manifest_hash=True,
            verify_active_index_artifacts=False,
        ),
        project_root=PROJECT_ROOT,
    )
    service = RAGService(PROJECT_ROOT, retrieval_runtime=runtime)
    try:
        unknown_bundle = service.retrieve(
            _service_request(
                query=UNKNOWN_QUERY,
                run_id="stage-e-v2-unknown-cost",
                topics=["预算金额", "投资估算"],
            )
        )
    finally:
        service.close()
    unknown_binding = extract_writer_gate_binding(
        _canonical_normalized(unknown_bundle),
        section_id=SECTION_ID,
        section_title=SECTION_TITLE,
        section_order=1,
        document_id=DOCUMENT_ID,
        run_id=RUN_ID,
    )
    if unknown_binding is None:
        raise RuntimeError("active unknown-cost retrieval did not publish a writer gate")
    unknown = _section(
        "现有证据不足，当前不生成正式章节正文。",
        unknown_binding,
    )
    unknown_gate = _hard_gate(unknown)
    active_after = resolver.resolve(pointer)

    scheme_writer_init = (
        PROJECT_ROOT
        / "backend/apps/enterprise_document/services/scheme_writer/__init__.py"
    ).read_text(encoding="utf-8")

    hard_failures = {
        "stale_claim_audit_content_mutation_not_rejected": not (
            not stale_gate["passed"]
            and not stale_gate["checks"]["writer_audit_content_hash_valid"]
        ),
        "qualified_content_qualifier_removal_not_rejected": not (
            not qualifier_removed_gate["passed"]
            and not qualifier_removed_gate["checks"][
                "writer_required_qualifiers_applied"
            ]
        ),
        "cross_section_writer_binding_reuse_not_rejected": not (
            not cross_binding_gate["passed"]
            and not cross_binding_gate["checks"][
                "writer_binding_section_identity_valid"
            ]
        ),
        "cross_section_claim_audit_reuse_not_rejected": not (
            not cross_audit_gate["passed"]
            and not cross_audit_gate["checks"][
                "writer_audit_section_identity_valid"
            ]
        ),
        "writer_gate_binding_hash_mismatch_not_rejected": binding_hash_gate["checks"][
            "writer_audit_binding_hash_valid"
        ],
        "evidence_contract_hash_mismatch_not_rejected": evidence_hash_gate["checks"][
            "writer_audit_evidence_lineage_valid"
        ],
        "prompt_evidence_contract_hash_mismatch_not_rejected": prompt_hash_gate["checks"][
            "writer_audit_evidence_lineage_valid"
        ],
        "claim_audit_allowed_boundary_mismatch_not_rejected": allowed_boundary_gate["checks"][
            "writer_audit_claim_boundary_valid"
        ],
        "claim_audit_forbidden_boundary_mismatch_not_rejected": forbidden_boundary_gate["checks"][
            "writer_audit_claim_boundary_valid"
        ],
        "claim_audit_qualifier_boundary_mismatch_not_rejected": qualifier_boundary_gate["checks"][
            "writer_audit_claim_boundary_valid"
        ],
        "generated_claim_span_mismatch_not_rejected": span_gate["checks"][
            "writer_audit_claim_spans_valid"
        ],
        "generated_claim_section_mismatch_not_rejected": claim_section_gate["checks"][
            "writer_audit_section_identity_valid"
        ],
        "unclassified_substantive_claim_not_blocked": not (
            not unclassified_audit.passed
            and unclassified_audit.human_review_required
            and any(
                item.violation_code == "unclassified_substantive_claim"
                for item in unclassified_audit.violations
            )
        ),
        "allowed_set_biased_classification_detected": not (
            allowed_bias_outputs
            == [["policy_requirement"], ["policy_requirement"], ["policy_requirement"]]
        ),
        "policy_synonym_claim_bypassed": not (
            "policy_requirement" in [item.claim_type.value for item in policy_audit.generated_claims]
            and not policy_audit.passed
        ),
        "technical_standard_claim_bypassed": not (
            "technical_specification" in [item.claim_type.value for item in technical_audit.generated_claims]
            and not technical_audit.passed
        ),
        "chinese_money_claim_bypassed": not (
            "current_project_cost" in [item.claim_type.value for item in money_audit.generated_claims]
            and not money_audit.passed
        ),
        "natural_language_schedule_claim_bypassed": not (
            "current_project_schedule" in [item.claim_type.value for item in schedule_audit.generated_claims]
            and not schedule_audit.passed
        ),
        "chinese_quantity_claim_bypassed": not (
            "current_project_quantity" in [item.claim_type.value for item in quantity_audit.generated_claims]
            and not quantity_audit.passed
        ),
        "procurement_claim_bypassed": not (
            "procurement_requirement" in [item.claim_type.value for item in procurement_audit.generated_claims]
            and not procurement_audit.passed
        ),
        "formal_deliverable_boolean_trusted_without_recompute": not (
            not forged_formal_gate["passed"]
            and not forged_formal_gate["checks"][
                "writer_formal_deliverable_eligible"
            ]
        ),
        "hidden_runtime_delegation_layer_present": "__getattr__" in scheme_writer_init,
        "active_unknown_blocked_not_rejected": not (
            unknown_binding.gate_decision.delivery_mode is DeliveryMode.BLOCKED
            and not unknown_binding.generation_allowed
            and not unknown_gate["passed"]
            and not unknown_gate["checks"]["writer_generation_allowed"]
        ),
        "grounded_method_not_deliverable": not grounded_gate["passed"],
        "missing_binding_section_order_not_rejected": not (
            not missing_section_order_gate["passed"]
            and not missing_section_order_gate["checks"][
                "writer_binding_identity_fields_present"
            ]
            and not missing_section_order_gate["checks"][
                "writer_binding_section_identity_valid"
            ]
        ),
        "missing_binding_document_id_not_rejected": not (
            not missing_document_id_gate["passed"]
            and not missing_document_id_gate["checks"][
                "writer_binding_identity_fields_present"
            ]
            and not missing_document_id_gate["checks"][
                "writer_binding_section_identity_valid"
            ]
        ),
        "missing_binding_run_id_not_rejected": not (
            not missing_run_id_gate["passed"]
            and not missing_run_id_gate["checks"][
                "writer_binding_identity_fields_present"
            ]
            and not missing_run_id_gate["checks"][
                "writer_binding_section_identity_valid"
            ]
        ),
        "missing_evidence_contract_hash_not_rejected": not (
            not missing_evidence_hash_gate["passed"]
            and not missing_evidence_hash_gate["checks"][
                "writer_evidence_lineage_fields_present"
            ]
            and not missing_evidence_hash_gate["checks"][
                "writer_audit_evidence_lineage_valid"
            ]
        ),
        "missing_prompt_evidence_contract_hash_not_rejected": not (
            not missing_prompt_hash_gate["passed"]
            and not missing_prompt_hash_gate["checks"][
                "writer_evidence_lineage_fields_present"
            ]
            and not missing_prompt_hash_gate["checks"][
                "writer_audit_evidence_lineage_valid"
            ]
        ),
        "cross_document_writer_binding_reuse_not_rejected": not (
            not cross_document_gate["passed"]
            and not cross_document_gate["checks"][
                "writer_binding_section_identity_valid"
            ]
        ),
        "cross_run_writer_binding_reuse_not_rejected": not (
            not cross_run_gate["passed"]
            and not cross_run_gate["checks"][
                "writer_binding_section_identity_valid"
            ]
        ),
        "wrong_section_order_binding_not_rejected": not (
            not wrong_order_gate["passed"]
            and not wrong_order_gate["checks"][
                "writer_binding_section_identity_valid"
            ]
        ),
        "complete_identity_and_lineage_control_failed": not (
            grounded_gate["passed"]
            and grounded_gate["checks"][
                "writer_binding_identity_fields_present"
            ]
            and grounded_gate["checks"][
                "writer_evidence_lineage_fields_present"
            ]
            and grounded_gate["checks"][
                "writer_formal_deliverable_eligible"
            ]
        ),
        "active_pointer_or_manifest_changed": not (
            active_before["pointer_hash"] == active_after["pointer_hash"]
            and active_before["manifest_hash"] == active_after["manifest_hash"]
            and active_before["index_version"] == active_after["index_version"]
        ),
    }

    report = {
        "schema_version": "stage_e_final_runtime_acceptance_v3",
        "passed": not any(hard_failures.values()),
        "classifier": {
            "policy_sha256": CLAIM_CLASSIFIER_POLICY_SHA256,
            "allowed_set_outputs": allowed_bias_outputs,
        },
        "active_index": {
            "index_version": active_after["index_version"],
            "pointer_path": active_after["pointer_path"],
            "pointer_hash": active_after["pointer_hash"],
            "manifest_path": active_after["manifest_path"],
            "manifest_hash": active_after["manifest_hash"],
        },
        "scenarios": {
            "grounded_method": grounded_gate,
            "stale_policy_mutation": stale_gate,
            "direct_policy_claim": policy_audit.model_dump(mode="json"),
            "chinese_money": money_audit.model_dump(mode="json"),
            "technical_standard": technical_audit.model_dump(mode="json"),
            "natural_language_schedule": schedule_audit.model_dump(mode="json"),
            "chinese_quantity": quantity_audit.model_dump(mode="json"),
            "procurement": procurement_audit.model_dump(mode="json"),
            "unclassified_substantive": unclassified_audit.model_dump(mode="json"),
            "cross_section_binding": cross_binding_gate,
            "cross_section_audit": cross_audit_gate,
            "qualified_qualifier_removal": qualifier_removed_gate,
            "active_unknown_blocked": unknown_gate,
            "missing_binding_section_order": missing_section_order_gate,
            "missing_binding_document_id": missing_document_id_gate,
            "missing_binding_run_id": missing_run_id_gate,
            "missing_evidence_contract_hash": missing_evidence_hash_gate,
            "missing_prompt_evidence_contract_hash": missing_prompt_hash_gate,
            "cross_document_writer_binding": cross_document_gate,
            "cross_run_writer_binding": cross_run_gate,
            "wrong_section_order_writer_binding": wrong_order_gate,
            "complete_identity_and_lineage_control": grounded_gate,
        },
        "hard_failures": hard_failures,
    }
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "output": str(output),
                "hard_failure_count": sum(hard_failures.values()),
                "classifier_policy_sha256": CLAIM_CLASSIFIER_POLICY_SHA256,
                "active_index": active_after["index_version"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
