# -*- coding: utf-8 -*-
"""把 canonical 子块和向量写入 Milvus Lite 父子块集合。

本模块定义物理集合 Schema、索引参数、子块记录映射、批量插入和旧 JSONL 辅助函数。
向量检索只存子块文本和父块身份；父级完整上下文仍由 ``ParentChunkStore`` 回填。
租户、知识库、文件和文档作用域作为标量字段持久化，供在线 Retriever 强制过滤。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np

from rag.configs.SchemaConfig import (
    DEFAULT_CHILD_CHUNK_SCHEMA_VERSION,
    DEFAULT_EMBEDDING_VERSION,
    DEFAULT_STATUS,
)
from rag.schema.VectorIndexRecord_Schema import (
    build_vector_index_record_v2,
)
from rag.store.parent_chunk_store import load_jsonl_dicts


# Milvus VARCHAR 字段的协议上限。过长 metadata 在映射阶段截断。
DEFAULT_ID_MAX_LENGTH = 512
DEFAULT_DOC_ID_MAX_LENGTH = 512
DEFAULT_FILE_ID_MAX_LENGTH = 512
DEFAULT_TENANT_ID_MAX_LENGTH = 256
DEFAULT_KB_ID_MAX_LENGTH = 256
DEFAULT_TITLE_MAX_LENGTH = 2048
DEFAULT_SECTION_MAX_LENGTH = 2048
DEFAULT_SOURCE_MAX_LENGTH = 2048
DEFAULT_SECURITY_LEVEL_MAX_LENGTH = 64
DEFAULT_STATUS_MAX_LENGTH = 64
DEFAULT_MODEL_MAX_LENGTH = 1024
DEFAULT_VERSION_MAX_LENGTH = 256
DEFAULT_INDEX_NAME_MAX_LENGTH = 512
DEFAULT_EXTRA_JSON_MAX_LENGTH = 65535


def ensure_milvus_lite_windows_manifest_replace_compatibility() -> bool:
    """Patch one upstream Windows-only atomic-replace incompatibility.

    The installed Milvus Lite Manifest implementation uses ``os.rename`` for
    the final commit point.  POSIX replaces an existing target, while Windows
    raises WinError 183.  The method already writes/fsyncs a temporary file and
    copies the previous manifest; replacing only the commit primitive with
    ``os.replace`` preserves that contract and makes it cross-platform.
    """

    if os.name != "nt":
        return False
    import milvus_lite.storage.manifest as manifest_module

    manifest_class = manifest_module.Manifest
    if getattr(manifest_class, "_rag_windows_replace_patch", False):
        return True

    def save(self: Any) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        new_version = self._version + 1
        payload = self._to_payload()
        payload["version"] = new_version
        target_path = os.path.join(
            self._data_dir, manifest_module.MANIFEST_FILENAME
        )
        prev_path = os.path.join(
            self._data_dir, manifest_module.MANIFEST_PREV_FILENAME
        )
        tmp_path = os.path.join(
            self._data_dir, manifest_module.MANIFEST_TMP_FILENAME
        )
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            if os.path.exists(target_path):
                try:
                    shutil.copy2(target_path, prev_path)
                except OSError as exc:
                    manifest_module.logger.warning(
                        "manifest: failed to create .prev backup: %s", exc
                    )
            os.replace(tmp_path, target_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
        self._version = new_version
        try:
            directory_fd = os.open(self._data_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    manifest_class.save = save
    manifest_class._rag_windows_replace_patch = True
    return True


def _safe_str(
    value: Any,
    *,
    max_length: int | None = None,
) -> str:
    """把可选值转换为字符串并按 Milvus VARCHAR 上限截断。"""

    text = "" if value is None else str(value)
    if max_length is not None and len(text) > max_length:
        text = text[:max_length]
    return text


def _safe_int(value: Any, default: int = -1) -> int:
    """尽力转换整数，失败时返回默认值。"""

    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _safe_float_list(
    vector: Any,
    expected_dim: int,
) -> List[float]:
    """把向量转换为一维 float32 列表并严格校验维度。

    Args:
        vector: NumPy 数组或可转换序列。
        expected_dim: 集合 Schema 声明的向量维度。

    Raises:
        ValueError: 实际维度与 expected_dim 不一致时抛出。
    """

    # 第一步：统一为一维 float32 数组。
    array = np.asarray(vector, dtype=np.float32).reshape(-1)

    # 第二步：维度不匹配会破坏集合协议，禁止静默截断或补零。
    if array.shape[0] != expected_dim:
        raise ValueError(
            f"Vector dim mismatch: expected={expected_dim}, "
            f"actual={array.shape[0]}"
        )
    return array.tolist()


def _extra_json(extra: Any) -> str:
    """把扩展 metadata 序列化为确定性紧凑 JSON 并限制字段长度。"""

    payload = extra if isinstance(extra, dict) else {}
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if len(text) > DEFAULT_EXTRA_JSON_MAX_LENGTH:
        raise ValueError(
            "Milvus extra_json exceeds canonical metadata limit; "
            f"actual={len(text)} limit={DEFAULT_EXTRA_JSON_MAX_LENGTH}"
        )
    return text


def build_child_chunk_collection_schema(
    client: Any,
    *,
    dim: int,
    max_text_chars: int = 8192,
):
    """构造父子块子级向量集合的 Milvus Schema。

    Args:
        client: 提供 ``create_schema`` 的 MilvusClient。
        dim: 向量维度。
        max_text_chars: 子块正文 VARCHAR 上限。

    Returns:
        已添加主键、向量、作用域、来源、Chunk 血缘和 Embedding 血缘字段的 Schema。
    """

    from pymilvus import DataType

    # 第一步：创建禁止动态字段的显式 Schema，避免历史记录随意新增顶层键。
    schema = client.create_schema(
        auto_id=False,
        enable_dynamic_field=False,
    )

    # 第二步：child_chunk_id 同时作为物理主键和逻辑 Chunk 身份。
    schema.add_field(
        field_name="chunk_id",
        datatype=DataType.VARCHAR,
        is_primary=True,
        max_length=DEFAULT_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="child_chunk_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="parent_chunk_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_ID_MAX_LENGTH,
    )

    # 第三步：持久化租户/知识库/文件/文档作用域，在线检索必须据此过滤。
    schema.add_field(
        field_name="doc_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_DOC_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="file_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_FILE_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="tenant_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_TENANT_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="kb_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_KB_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="document_type",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="evidence_role",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="project_relation",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="project_id",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_ID_MAX_LENGTH,
    )
    schema.add_field(
        field_name="authority_level",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )

    # 第四步：子块正文和文档展示字段用于返回匹配片段与来源信息。
    schema.add_field(
        field_name="text",
        datatype=DataType.VARCHAR,
        max_length=max_text_chars,
    )
    schema.add_field(
        field_name="title",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_TITLE_MAX_LENGTH,
    )
    schema.add_field(
        field_name="section",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_SECTION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="section_level",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="source",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_SOURCE_MAX_LENGTH,
    )

    # 第五步：页码和字符边界支持引用定位和离线核查。
    schema.add_field(
        field_name="page_start",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="page_end",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="start_char",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="end_char",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="token_count",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="child_index",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="parent_index",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="global_chunk_index",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="chunk_level",
        datatype=DataType.INT64,
    )

    # 第六步：保留协议、状态、安全等级和 Embedding/索引血缘。
    schema.add_field(
        field_name="schema_version",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="status",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_STATUS_MAX_LENGTH,
    )
    schema.add_field(
        field_name="security_level",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_SECURITY_LEVEL_MAX_LENGTH,
    )
    schema.add_field(
        field_name="embedding_model",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_MODEL_MAX_LENGTH,
    )
    schema.add_field(
        field_name="embedding_version",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )
    schema.add_field(
        field_name="embedding_dim",
        datatype=DataType.INT64,
    )
    schema.add_field(
        field_name="index_name",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_INDEX_NAME_MAX_LENGTH,
    )
    schema.add_field(
        field_name="index_version",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_VERSION_MAX_LENGTH,
    )

    # 第七步：原始 extra 以 JSON 字符串保存，避免启用动态字段。
    schema.add_field(
        field_name="extra_json",
        datatype=DataType.VARCHAR,
        max_length=DEFAULT_EXTRA_JSON_MAX_LENGTH,
    )

    # 第八步：向量字段维度必须与 Embedding 和 Manifest 一致。
    schema.add_field(
        field_name="vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=dim,
    )
    return schema


def create_or_reset_child_chunk_collection(
    client: Any,
    *,
    collection_name: str,
    dim: int,
    metric_type: str = "COSINE",
    recreate: bool = True,
    max_text_chars: int = 8192,
) -> None:
    """创建或按配置重建子块集合及向量索引。

    Args:
        client: MilvusClient。
        collection_name: 集合名称。
        dim: 向量维度。
        metric_type: COSINE/IP/L2 等 Milvus 度量。
        recreate: 集合存在时是否删除重建。
        max_text_chars: text 字段长度上限。
    """

    # 第一步：集合存在且允许重建时先删除旧集合；禁止重建时直接复用旧集合。
    if client.has_collection(collection_name):
        if recreate:
            client.drop_collection(collection_name)
        else:
            return

    # 第二步：构造显式 Schema。
    schema = build_child_chunk_collection_schema(
        client,
        dim=dim,
        max_text_chars=max_text_chars,
    )

    # 第三步：为 vector 字段创建自动选择索引类型的向量索引。
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        metric_type=metric_type,
        index_type="AUTOINDEX",
    )

    # 第四步：创建集合并同时绑定 Schema 和索引参数。
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )


def build_milvus_child_chunk_record(
    child_chunk: Dict[str, Any],
    vector: Any,
    *,
    embedding_model: str,
    embedding_dim: int,
    embedding_version: str = DEFAULT_EMBEDDING_VERSION,
    max_text_chars: int = 8192,
    index_name: str | None = None,
    index_version: str | None = None,
) -> Dict[str, Any]:
    """把 canonical 子块与同一行向量映射为 Milvus 物理记录。

    ``child_chunk`` 和 ``vector`` 保留位置参数兼容；其余配置参数必须显式命名，
    避免 Embedding 与索引血缘字段因位置错位而被静默污染。

    Raises:
        ValueError: child_chunk_id/doc_id 为空或向量维度不匹配时抛出。
    """

    # 第一步：兼容通用 chunk_id 和专用 child_chunk_id，但最终身份不能为空。
    child_chunk_id = _safe_str(
        child_chunk.get("child_chunk_id")
        or child_chunk.get("chunk_id"),
        max_length=DEFAULT_ID_MAX_LENGTH,
    )
    if not child_chunk_id:
        raise ValueError("child_chunk_id cannot be empty")

    doc_id = _safe_str(
        child_chunk.get("doc_id"),
        max_length=DEFAULT_DOC_ID_MAX_LENGTH,
    )
    if not doc_id:
        raise ValueError("doc_id cannot be empty")

    # 第二步：统一页码兼容字段。page_start/page_end 缺失时回退 page_num。
    page_num = _safe_int(child_chunk.get("page_num"), -1)
    page_start = _safe_int(
        child_chunk.get("page_start"),
        page_num,
    )
    page_end = _safe_int(
        child_chunk.get("page_end"),
        page_num,
    )

    child_extra = child_chunk.get("extra")
    if not isinstance(child_extra, dict):
        raise ValueError(f"child extra must be an object: {child_chunk_id}")
    raw_profile = child_extra.get("document_profile")
    raw_chunking = child_extra.get("type_aware_chunking")
    lineage_present = isinstance(raw_profile, dict) or isinstance(
        raw_chunking, dict
    )
    if lineage_present and not (
        isinstance(raw_profile, dict) and isinstance(raw_chunking, dict)
    ):
        raise ValueError(
            "Milvus lineage must contain both document_profile and "
            "type_aware_chunking; "
            f"child_chunk_id={child_chunk_id!r}"
        )
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    if profile and str(profile.get("document_id") or "") != doc_id:
        raise ValueError("Milvus doc_id conflicts with canonical document_profile")

    # 第三步：构造与集合 Schema 完全一致的扁平物理记录。
    return {
        "chunk_id": child_chunk_id,
        "child_chunk_id": child_chunk_id,
        "parent_chunk_id": _safe_str(
            child_chunk.get("parent_chunk_id"),
            max_length=DEFAULT_ID_MAX_LENGTH,
        ),
        "doc_id": doc_id,
        "file_id": _safe_str(
            child_chunk.get("file_id"),
            max_length=DEFAULT_FILE_ID_MAX_LENGTH,
        ),
        "tenant_id": _safe_str(
            child_chunk.get("tenant_id"),
            max_length=DEFAULT_TENANT_ID_MAX_LENGTH,
        ),
        "kb_id": _safe_str(
            child_chunk.get("kb_id"),
            max_length=DEFAULT_KB_ID_MAX_LENGTH,
        ),
        "document_type": _safe_str(
            profile.get("document_type"),
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "evidence_role": _safe_str(
            profile.get("evidence_role"),
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "project_relation": _safe_str(
            profile.get("project_relation"),
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "project_id": _safe_str(
            profile.get("project_id"),
            max_length=DEFAULT_ID_MAX_LENGTH,
        ),
        "authority_level": _safe_str(
            (profile.get("authority") or {}).get("level"),
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "text": _safe_str(
            child_chunk.get("text"),
            max_length=max_text_chars,
        ),
        "title": _safe_str(
            child_chunk.get("title"),
            max_length=DEFAULT_TITLE_MAX_LENGTH,
        ),
        "section": _safe_str(
            child_chunk.get("section"),
            max_length=DEFAULT_SECTION_MAX_LENGTH,
        ),
        "section_level": _safe_int(
            child_chunk.get("section_level"),
            -1,
        ),
        "source": _safe_str(
            child_chunk.get("source"),
            max_length=DEFAULT_SOURCE_MAX_LENGTH,
        ),
        "page_start": page_start,
        "page_end": page_end,
        "start_char": _safe_int(
            child_chunk.get("start_char"),
            -1,
        ),
        "end_char": _safe_int(
            child_chunk.get("end_char"),
            -1,
        ),
        "token_count": _safe_int(
            child_chunk.get("token_count"),
            -1,
        ),
        "child_index": _safe_int(
            child_chunk.get("child_index"),
            -1,
        ),
        "parent_index": _safe_int(
            child_chunk.get("parent_index"),
            -1,
        ),
        "global_chunk_index": _safe_int(
            child_chunk.get("global_chunk_index"),
            _safe_int(child_chunk.get("chunk_index"), -1),
        ),
        "chunk_level": _safe_int(
            child_chunk.get("chunk_level"),
            1,
        ),
        "schema_version": _safe_str(
            child_chunk.get("schema_version")
            or DEFAULT_CHILD_CHUNK_SCHEMA_VERSION,
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "status": _safe_str(
            child_chunk.get("status") or DEFAULT_STATUS,
            max_length=DEFAULT_STATUS_MAX_LENGTH,
        ),
        "security_level": _safe_str(
            child_chunk.get("security_level") or "internal",
            max_length=DEFAULT_SECURITY_LEVEL_MAX_LENGTH,
        ),
        "embedding_model": _safe_str(
            embedding_model,
            max_length=DEFAULT_MODEL_MAX_LENGTH,
        ),
        "embedding_version": _safe_str(
            embedding_version,
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "embedding_dim": int(embedding_dim),
        "index_name": _safe_str(
            index_name,
            max_length=DEFAULT_INDEX_NAME_MAX_LENGTH,
        ),
        "index_version": _safe_str(
            index_version,
            max_length=DEFAULT_VERSION_MAX_LENGTH,
        ),
        "extra_json": _extra_json(child_extra),
        "vector": _safe_float_list(vector, embedding_dim),
    }


def insert_child_chunk_records(
    *,
    client: Any,
    collection_name: str,
    records: Sequence[Dict[str, Any]],
    batch_size: int = 128,
) -> int:
    """按批次插入 Milvus 子块记录并返回实际插入数量。"""

    # 空输入无需调用 Milvus。
    if not records:
        return 0

    inserted = 0
    normalized_batch_size = max(1, int(batch_size))

    # 逐批插入，响应中的 insert_count 缺失时回退当前批次长度。
    for start in range(0, len(records), normalized_batch_size):
        batch = list(records[start : start + normalized_batch_size])
        response = client.insert(
            collection_name=collection_name,
            data=batch,
        )
        inserted += int(
            (response or {}).get("insert_count", len(batch))
        )

    return inserted


def build_child_chunk_vector_index_records(
    *,
    child_chunks: Sequence[Dict[str, Any]],
    embedding_model: str,
    embedding_dim: int,
    embedding_version: str,
    index_name: str,
    index_version: str,
    vector_db: str = "milvus",
    created_at: str | None = None,
) -> List[Dict[str, Any]]:
    """为子块批量构造逻辑 ``vector_index_record_v2`` 血缘记录。"""

    return [
        build_vector_index_record_v2(
            child_chunk=child_chunk,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            embedding_version=embedding_version,
            index_name=index_name,
            index_version=index_version,
            vector_db=vector_db,
            created_at=created_at,
        )
        for child_chunk in child_chunks
    ]


def write_jsonl_records(
    records: Iterable[Dict[str, Any]],
    output_path: str | Path,
) -> None:
    """覆盖写入紧凑 UTF-8 JSONL 文件。

    该辅助函数适用于离线未发布目录，不提供崩溃安全或幂等追加语义。
    """

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )


def load_child_chunks(path: str | Path) -> List[Dict[str, Any]]:
    """从单文件或兼容 JSONL 目录加载子块字典列表。"""

    return load_jsonl_dicts(path)
