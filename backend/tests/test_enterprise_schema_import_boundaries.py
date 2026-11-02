"""Regression coverage for enterprise-document schema import boundaries."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "source",
    [
        (
            "from rag.application.parent_child_retrieval import "
            "ParentChildRetrievalPipeline; "
            "assert ParentChildRetrievalPipeline.__name__ == "
            "'ParentChildRetrievalPipeline'"
        ),
        (
            "from apps.enterprise_document.schemas.release_publication import "
            "PublishedReleaseSchema; "
            "assert PublishedReleaseSchema.__name__ == 'PublishedReleaseSchema'"
        ),
        (
            "from apps.enterprise_document.schemas.scheme_writer.output import "
            "SchemeWriterOutputSchema; "
            "from apps.enterprise_document.schemas.release_publication import "
            "PublishedReleaseSchema; "
            "assert SchemeWriterOutputSchema.__name__ == 'SchemeWriterOutputSchema'; "
            "assert PublishedReleaseSchema.__name__ == 'PublishedReleaseSchema'"
        ),
        (
            "from apps.enterprise_document.schemas.agent_rag_v1 import "
            "ArtifactReferenceSchema as LegacyArtifactReferenceSchema; "
            "from apps.enterprise_document.schemas.artifact_reference import "
            "ArtifactReferenceSchema; "
            "assert LegacyArtifactReferenceSchema is ArtifactReferenceSchema"
        ),
        (
            "from apps.enterprise_document.schemas.post_write.foundation import "
            "OverallQualityState, QualityDeliveryContractRefSchema, "
            "DeliveryGateDecisionRefSchema; "
            "from apps.enterprise_document.schemas.post_write.document import "
            "AssemblyInputManifestSchema, SectionAssemblyCandidateSchema; "
            "from apps.enterprise_document.schemas.post_write.currentness import "
            "CurrentnessProofSchema, ActiveAssemblySectionSelectionSchema; "
            "from apps.enterprise_document.schemas.post_write.gate import "
            "QualityDeliveryContractSchema, QualityPolicyProjectionSchema, "
            "DeliveryGateDecisionSchema; "
            "from apps.enterprise_document.schemas.post_write.control import "
            "DocumentCorrectionDispatchProjectionSchema; "
            "assert OverallQualityState.PASS.value == 'PASS'; "
            "assert AssemblyInputManifestSchema.__name__ == 'AssemblyInputManifestSchema'; "
            "assert CurrentnessProofSchema.__name__ == 'CurrentnessProofSchema'; "
            "assert DeliveryGateDecisionSchema.__name__ == 'DeliveryGateDecisionSchema'; "
            "assert DocumentCorrectionDispatchProjectionSchema.__name__ == "
            "'DocumentCorrectionDispatchProjectionSchema'"
        ),
    ],
)
def test_schema_imports_succeed_in_fresh_python_process(source: str) -> None:
    """Each supported import order must work without relying on module cache."""

    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        "fresh-process schema import failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def test_post_write_gate_dependency_neutral_authorities_have_one_definition_site() -> None:
    post_write_root = (
        BACKEND_ROOT
        / "apps"
        / "enterprise_document"
        / "schemas"
        / "post_write"
    )
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in post_write_root.glob("*.py")
    }

    assert sum(
        text.count("class OverallQualityState(")
        for text in sources.values()
    ) == 1
    assert sum(
        text.count("class QualityDeliveryContractRefSchema(")
        for text in sources.values()
    ) == 1
    assert sum(
        text.count("class DeliveryGateDecisionRefSchema(")
        for text in sources.values()
    ) == 1
    assert "class OverallQualityState(" in sources["foundation.py"]
    assert "class QualityDeliveryContractRefSchema(" in sources["foundation.py"]
    assert "class DeliveryGateDecisionRefSchema(" in sources["foundation.py"]


def test_post_write_document_schema_dag_has_no_reverse_control_or_currentness_import() -> None:
    document = (
        BACKEND_ROOT
        / "apps"
        / "enterprise_document"
        / "schemas"
        / "post_write"
        / "document.py"
    ).read_text(encoding="utf-8")

    assert "from .currentness import" not in document
    assert "from .control import" not in document
    assert "schemas.post_write.currentness" not in document
    assert "schemas.post_write.control" not in document
    assert "class SectionAssemblyCandidateStore" not in document
    assert "CurrentnessProofSchema" not in document
    assert "ArtifactUsabilityIntegrityProofSchema" not in document


def test_post_write_foundation_does_not_reverse_import_currentness_or_gate() -> None:
    foundation = (
        BACKEND_ROOT
        / "apps"
        / "enterprise_document"
        / "schemas"
        / "post_write"
        / "foundation.py"
    ).read_text(encoding="utf-8")

    assert "from .currentness import" not in foundation
    assert "from .gate import" not in foundation
    assert "schemas.post_write.currentness" not in foundation
    assert "schemas.post_write.gate" not in foundation
