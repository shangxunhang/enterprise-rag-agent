"""构造普通 Chunk、父块和子块的标准字典协议。

本模块位于历史 dataclass 体系，返回普通字典而不是 Pydantic 对象。所有构建函数都
从 ``extra`` 创建新字典，避免调用方可变对象被后续修改；父子块函数显式记录层级、
父级身份、Chunker 版本和字符边界，供索引、检索和引用血缘复用。
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rag.configs.SchemaConfig import *


def _now_iso() -> str:
    """返回当前本地时间 ISO 字符串，保留旧版无时区行为。"""

    return datetime.now().isoformat()


def build_chunk(
    chunk_id: str,
    doc_id: str,
    text: str,
    chunk_index: int,
    source: Optional[str] = None,
    source_path: Optional[str] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    page_num: Optional[int] = None,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    token_count: Optional[int] = None,
    chunk_level: int = DEFAULT_CHUNK_LEVEL,
    parent_chunk_id: Optional[str] = None,
    chunker_name: str = DEFAULT_CHUNKER_NAME,
    chunker_version: str = DEFAULT_CHUNKER_VERSION,
    created_at: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    status: str = DEFAULT_STATUS,
    security_level: str = DEFAULT_SECURITY_LEVEL,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一个普通扁平 Chunk 字典。

    Args:
        chunk_id: 当前块全局身份。
        doc_id: 来源文档身份。
        text: 当前块正文。
        chunk_index: 当前块在文档中的 0-based 顺序。
        source: 可读来源名称。
        source_path: 原始文件路径。
        title: 文档标题。
        section: 当前章节名称。
        page_num: 可选页码。
        start_char: 在来源文本中的起始字符偏移。
        end_char: 结束字符偏移。
        token_count: 可选 Token 数。
        chunk_level: 逻辑层级，普通块默认 0。
        parent_chunk_id: 可选父块身份。
        chunker_name: 生成该块的 Chunker 名称。
        chunker_version: Chunker 版本。
        created_at: 显式创建时间；省略时使用当前本地时间。
        language: 文本语言。
        status: 业务状态。
        security_level: 数据安全等级。
        extra: 扩展 metadata。

    Returns:
        符合 ``chunk_v1`` 字段布局的普通字典。
    """

    # 统一构造完整字段，即使可选值为 None 也保留键，便于旧调用方直接访问。
    return {
        "schema_version": DEFAULT_CHUNK_SCHEMA_VERSION,
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "text": text,
        "chunk_index": chunk_index,
        "source": source,
        "source_path": source_path,
        "title": title,
        "section": section,
        "page_num": page_num,
        "start_char": start_char,
        "end_char": end_char,
        "token_count": token_count,
        "chunk_level": chunk_level,
        "parent_chunk_id": parent_chunk_id,
        "chunker_name": chunker_name,
        "chunker_version": chunker_version,
        "created_at": created_at or _now_iso(),
        "language": language,
        "status": status,
        "security_level": security_level,
        "extra": dict(extra or {}),
    }


def build_parent_chunk(
    *,
    parent_chunk_id: str,
    doc_id: str,
    text: str,
    parent_index: int,
    child_chunk_ids: Optional[List[str]] = None,
    source: Optional[str] = None,
    source_path: Optional[str] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    page_num: Optional[int] = None,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    token_count: Optional[int] = None,
    chunker_name: str = DEFAULT_PARENT_CHILD_CHUNKER_NAME,
    chunker_version: str = DEFAULT_PARENT_CHILD_CHUNKER_VERSION,
    created_at: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    status: str = DEFAULT_STATUS,
    security_level: str = DEFAULT_SECURITY_LEVEL,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造父级上下文 Chunk。

    父块用于生成上下文，不直接参与细粒度匹配。``child_chunk_ids`` 保存当前父块下的
    子块顺序；调用方可先构造父块，再在子块生成完成后回填该列表。
    """

    return {
        "schema_version": DEFAULT_PARENT_CHUNK_SCHEMA_VERSION,
        "chunk_id": parent_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "doc_id": doc_id,
        "text": text,
        "chunk_index": parent_index,
        "parent_index": parent_index,
        "chunk_level": 0,
        "child_chunk_ids": list(child_chunk_ids or []),
        "source": source,
        "source_path": source_path,
        "title": title,
        "section": section,
        "page_num": page_num,
        "start_char": start_char,
        "end_char": end_char,
        "token_count": token_count,
        "chunker_name": chunker_name,
        "chunker_version": chunker_version,
        "created_at": created_at or _now_iso(),
        "language": language,
        "status": status,
        "security_level": security_level,
        "extra": dict(extra or {}),
    }


def build_child_chunk(
    *,
    child_chunk_id: str,
    parent_chunk_id: str,
    doc_id: str,
    text: str,
    child_index: int,
    parent_index: int,
    global_chunk_index: int,
    source: Optional[str] = None,
    source_path: Optional[str] = None,
    title: Optional[str] = None,
    section: Optional[str] = None,
    page_num: Optional[int] = None,
    start_char: Optional[int] = None,
    end_char: Optional[int] = None,
    token_count: Optional[int] = None,
    chunker_name: str = DEFAULT_PARENT_CHILD_CHUNKER_NAME,
    chunker_version: str = DEFAULT_PARENT_CHILD_CHUNKER_VERSION,
    created_at: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    status: str = DEFAULT_STATUS,
    security_level: str = DEFAULT_SECURITY_LEVEL,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造参与向量匹配的子级 Chunk。

    Args:
        child_chunk_id: 子块身份。
        parent_chunk_id: 所属父块身份。
        doc_id: 来源文档身份。
        text: 子块匹配文本。
        child_index: 在当前父块中的 0-based 顺序。
        parent_index: 父块在文档中的 0-based 顺序。
        global_chunk_index: 子块在整篇文档中的全局顺序。

    Returns:
        同时保留 ``chunk_id`` 和 ``child_chunk_id`` 的 ``child_chunk_v1`` 字典，兼容
        通用 Chunk 调用方和父子块专用调用方。
    """

    return {
        "schema_version": DEFAULT_CHILD_CHUNK_SCHEMA_VERSION,
        "chunk_id": child_chunk_id,
        "child_chunk_id": child_chunk_id,
        "parent_chunk_id": parent_chunk_id,
        "doc_id": doc_id,
        "text": text,
        "chunk_index": global_chunk_index,
        "global_chunk_index": global_chunk_index,
        "child_index": child_index,
        "parent_index": parent_index,
        "chunk_level": 1,
        "source": source,
        "source_path": source_path,
        "title": title,
        "section": section,
        "page_num": page_num,
        "start_char": start_char,
        "end_char": end_char,
        "token_count": token_count,
        "chunker_name": chunker_name,
        "chunker_version": chunker_version,
        "created_at": created_at or _now_iso(),
        "language": language,
        "status": status,
        "security_level": security_level,
        "extra": dict(extra or {}),
    }
