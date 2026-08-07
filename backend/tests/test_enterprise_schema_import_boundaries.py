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
