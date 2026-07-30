from __future__ import annotations

import numpy as np
import pytest

from rag.vector_store.milvus_child_chunk_store import build_milvus_child_chunk_record


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
