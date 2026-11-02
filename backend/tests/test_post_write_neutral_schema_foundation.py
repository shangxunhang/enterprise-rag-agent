"""Step 3.4 neutral Post-write schema foundation acceptance tests."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from apps.enterprise_document.schemas.post_write import (
    CorrectionPolicyRefSchema,
    DeliveryGateDecisionRefSchema,
    EvidenceSnapshotRefSchema,
    PostWriteControlDecisionRefSchema,
    PostWriteEntryRefSchema,
    QualityDeliveryContractRefSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import WriterSubjectRefSchema
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterGenerationInvocationRefSchema,
    WriterLogicalExecutionKey,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _writer_invocation_ref() -> WriterGenerationInvocationRefSchema:
    subject = WriterSubjectRefSchema(
        run_id="run-1",
        document_id="document-1",
        section_id="section-1",
    )
    route = PreWriteRouteDecisionRefSchema(
        route_decision_id="route-1",
    )
    key = WriterLogicalExecutionKey(
        writer_subject_ref=subject,
        write_instance_id="write-1",
        route_decision_ref=route,
    )
    return WriterGenerationInvocationRefSchema(
        logical_execution_key=key,
        invocation_sha256=SHA_B,
    )


@pytest.mark.parametrize(
    ("model_cls", "payload"),
    [
        (
            PostWriteEntryRefSchema,
            {
                "entry_id": "entry-1",
                "entry_schema_version": "post_write_entry_v1",
                "entry_contract_version": "post_write_entry_contract_v1",
                "entry_sha256": SHA_A,
            },
        ),
        (
            QualityDeliveryContractRefSchema,
            {
                "contract_id": "contract-1",
                "contract_schema_version": "quality_delivery_contract_v1",
                "contract_version": "2026-09-03",
                "contract_hash": SHA_B,
            },
        ),
        (
            DeliveryGateDecisionRefSchema,
            {
                "delivery_gate_decision_id": "gate-decision-1",
                "schema_version": "delivery_gate_decision_v1",
                "gate_contract_version": "delivery_gate_contract_v1",
                "decision_hash": SHA_C,
            },
        ),
        (
            CorrectionPolicyRefSchema,
            {
                "policy_id": "correction-policy",
                "policy_schema_version": "correction_policy_v1",
                "policy_version": "v1",
                "policy_hash": SHA_D,
            },
        ),
        (
            PostWriteControlDecisionRefSchema,
            {
                "control_decision_id": "control-1",
                "schema_version": "post_write_control_decision_v1",
                "control_contract_version": "post_write_control_contract_v1",
                "control_decision_hash": SHA_A,
            },
        ),
    ],
)
def test_exact_ref_round_trip_is_stable(model_cls, payload) -> None:
    value = model_cls.model_validate(payload)
    assert model_cls.model_validate(value.model_dump(mode="json")) == value


def test_evidence_snapshot_ref_reuses_exact_writer_invocation_ref() -> None:
    writer_ref = _writer_invocation_ref()
    value = EvidenceSnapshotRefSchema(
        evidence_bundle_schema_version="evidence_bundle_v2",
        evidence_bundle_sha256=SHA_C,
        writer_invocation_ref=writer_ref,
    )
    assert value.writer_invocation_ref == writer_ref
    assert EvidenceSnapshotRefSchema.model_validate(
        value.model_dump(mode="json")
    ) == value


@pytest.mark.parametrize(
    "payload",
    [
        {
            "entry_id": "entry-1",
            "entry_schema_version": "v1",
            "entry_contract_version": "v1",
            "entry_sha256": "NOT-A-SHA",
        },
        {
            "entry_id": "",
            "entry_schema_version": "v1",
            "entry_contract_version": "v1",
            "entry_sha256": SHA_A,
        },
    ],
)
def test_invalid_exact_ref_fails_closed(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        PostWriteEntryRefSchema.model_validate(payload)


def test_exact_refs_forbid_extra_fields_and_are_frozen() -> None:
    with pytest.raises(ValidationError):
        QualityDeliveryContractRefSchema.model_validate(
            {
                "contract_id": "contract-1",
                "contract_schema_version": "v1",
                "contract_version": "v1",
                "contract_hash": SHA_A,
                "current_policy_result": "allow",
            }
        )

    value = PostWriteEntryRefSchema(
        entry_id="entry-1",
        entry_schema_version="v1",
        entry_contract_version="v1",
        entry_sha256=SHA_B,
    )
    with pytest.raises(ValidationError):
        value.entry_id = "entry-2"


def test_foundation_step5_integration_exposes_structural_and_validation_foundation_only() -> None:
    module = __import__(
        "apps.enterprise_document.schemas.post_write.foundation",
        fromlist=["*"],
    )
    exported = set(vars(module))
    assert {
        "ArtifactStateRecordSchema",
        "ValidationRunSchema",
        "ValidationFactSchema",
        "ValidationCompletionSchema",
    }.issubset(exported)

    forbidden = {
        "CurrentnessProofSchema",
        "QualityDeliveryContractSchema",
        "DeliveryGateDecisionSchema",
        "CorrectionPolicySchema",
        "PostWriteControlDecisionSchema",
        "ParagraphRepairAuthorization",
        "RunCompletionAssessmentSchema",
        "PostWriteReleaseRequestSchema",
    }
    assert forbidden.isdisjoint(exported)


def test_foundation_imports_in_fresh_python_process() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from apps.enterprise_document.schemas.post_write import "
                "EvidenceSnapshotRefSchema, PostWriteEntryRefSchema, "
                "QualityDeliveryContractRefSchema, DeliveryGateDecisionRefSchema, "
                "CorrectionPolicyRefSchema, PostWriteControlDecisionRefSchema; "
                "assert EvidenceSnapshotRefSchema.__name__ == 'EvidenceSnapshotRefSchema'"
            ),
        ],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_foundation_dependency_direction_is_neutral() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "enterprise_document"
        / "schemas"
        / "post_write"
        / "foundation.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)

    forbidden_prefixes = (
        "apps.enterprise_document.services.post_write",
        "apps.enterprise_document.schemas.post_write.entry",
        "apps.enterprise_document.schemas.post_write.currentness",
        "apps.enterprise_document.schemas.post_write.document",
        "apps.enterprise_document.schemas.post_write.gate",
        "apps.enterprise_document.schemas.post_write.control",
        "apps.enterprise_document.schemas.post_write.release",
        "rag.",
    )
    assert not any(
        module == prefix.rstrip(".") or module.startswith(prefix)
        for module in imports
        for prefix in forbidden_prefixes
    )
