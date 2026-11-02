from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from rag.vector_store.milvus_child_chunk_store import (
    build_milvus_child_chunk_record,
)


def _child_chunk(**overrides):
    chunk = {
        "chunk_id": "child-001",
        "child_chunk_id": "child-001",
        "parent_chunk_id": "parent-001",
        "doc_id": "doc-001",
        "file_id": "file-001",
        "tenant_id": "tenant-001",
        "kb_id": "kb-001",
        "text": "abcdefghij",
        "title": "title",
        "section": "section",
        "extra": {"source": "test"},
    }
    chunk.update(overrides)
    return chunk


def _record_kwargs():
    return {
        "embedding_model": "embedding-test",
        "embedding_dim": 4,
        "embedding_version": "embedding-v1",
        "max_text_chars": 5,
        "index_name": "index-children",
        "index_version": "index-v1",
    }


def test_positional_and_keyword_core_arguments_build_identical_records() -> None:
    child_chunk = _child_chunk()
    vector = np.asarray([1.0, 0.5, 0.25, 0.125], dtype=np.float32)

    positional = build_milvus_child_chunk_record(
        child_chunk,
        vector,
        **_record_kwargs(),
    )
    keyword = build_milvus_child_chunk_record(
        child_chunk=child_chunk,
        vector=vector,
        **_record_kwargs(),
    )

    assert positional == keyword


def test_record_preserves_identity_scope_lineage_and_text_truncation() -> None:
    record = build_milvus_child_chunk_record(
        _child_chunk(),
        [1.0, 0.5, 0.25, 0.125],
        **_record_kwargs(),
    )

    assert record["chunk_id"] == "child-001"
    assert record["child_chunk_id"] == "child-001"
    assert record["parent_chunk_id"] == "parent-001"
    assert record["doc_id"] == "doc-001"
    assert record["file_id"] == "file-001"
    assert record["tenant_id"] == "tenant-001"
    assert record["kb_id"] == "kb-001"
    assert record["index_name"] == "index-children"
    assert record["index_version"] == "index-v1"
    assert record["text"] == "abcde"
    assert record["embedding_dim"] == 4
    assert record["vector"] == [1.0, 0.5, 0.25, 0.125]


def test_missing_child_chunk_identity_is_rejected() -> None:
    child_chunk = _child_chunk(child_chunk_id=None, chunk_id=None)

    with pytest.raises(ValueError, match="child_chunk_id cannot be empty"):
        build_milvus_child_chunk_record(
            child_chunk,
            [1.0, 0.5, 0.25, 0.125],
            **_record_kwargs(),
        )


def test_missing_doc_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="doc_id cannot be empty"):
        build_milvus_child_chunk_record(
            _child_chunk(doc_id=None),
            [1.0, 0.5, 0.25, 0.125],
            **_record_kwargs(),
        )


def test_vector_dimension_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="Vector dim mismatch: expected=4, actual=3"):
        build_milvus_child_chunk_record(
            _child_chunk(),
            [1.0, 0.5, 0.25],
            **_record_kwargs(),
        )


def test_configuration_arguments_remain_keyword_only() -> None:
    with pytest.raises(TypeError):
        build_milvus_child_chunk_record(
            _child_chunk(),
            [1.0, 0.5, 0.25, 0.125],
            "embedding-test",
            embedding_dim=4,
        )


def test_unknown_keyword_is_rejected() -> None:
    kwargs = _record_kwargs()
    kwargs["unknown_option"] = True

    with pytest.raises(TypeError):
        build_milvus_child_chunk_record(
            _child_chunk(),
            [1.0, 0.5, 0.25, 0.125],
            **kwargs,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows Milvus Lite compatibility")
def test_windows_manifest_replace_supports_multiple_insert_batches(
    tmp_path: Path,
) -> None:
    # Importing FAISS/Milvus into the already large pytest process is unstable
    # in this Windows environment.  The compatibility boundary is therefore
    # exercised in an isolated interpreter, just like the production builder.
    code = textwrap.dedent(
        r'''
        import sys
        from pathlib import Path

        from pymilvus import MilvusClient

        # Preserve the production bootstrap import order so the isolated
        # interpreter initializes shared RAG schemas before vector-store imports.
        import rag.mapping.request_mapper  # noqa: F401
        from rag.vector_store.milvus_child_chunk_store import (
            build_milvus_child_chunk_record,
            create_or_reset_child_chunk_collection,
            ensure_milvus_lite_windows_manifest_replace_compatibility,
            insert_child_chunk_records,
        )

        db_path = Path(sys.argv[1])
        assert ensure_milvus_lite_windows_manifest_replace_compatibility()
        client = MilvusClient(str(db_path))
        create_or_reset_child_chunk_collection(
            client=client,
            collection_name="probe",
            dim=4,
            max_text_chars=1024,
        )
        profile = {
            "document_id": "doc-001",
            "document_type": "current_project_requirement",
            "evidence_role": "project_fact",
            "project_relation": "current_project",
            "project_id": "project-001",
            "authority": {"level": "reference"},
        }
        chunking = {
            "strategy": "fact_record",
            "representation_type": "fact_record",
        }
        records = []
        for index in range(7):
            child = {
                "chunk_id": f"child-{index:03d}",
                "child_chunk_id": f"child-{index:03d}",
                "parent_chunk_id": "parent-001",
                "doc_id": "doc-001",
                "file_id": "file-001",
                "tenant_id": "tenant-001",
                "kb_id": "kb-001",
                "text": f"text-{index}",
                "title": "title",
                "section": "section",
                "extra": {
                    "document_profile": profile,
                    "type_aware_chunking": chunking,
                },
            }
            records.append(
                build_milvus_child_chunk_record(
                    child,
                    [1.0, 0.5, 0.25, 0.125],
                    embedding_model="embedding-test",
                    embedding_dim=4,
                    embedding_version="embedding-v1",
                    max_text_chars=1024,
                    index_name="index-children",
                    index_version="index-v1",
                )
            )
        assert insert_child_chunk_records(
            client=client,
            collection_name="probe",
            records=records,
            batch_size=2,
        ) == 7
        client.close()
        reopened = MilvusClient(str(db_path))
        try:
            assert int(reopened.get_collection_stats("probe")["row_count"]) == 7
        finally:
            reopened.close()
        '''
    )
    env = os.environ.copy()
    backend_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        [str(backend_root), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "milvus_manifest_replace.db")],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
