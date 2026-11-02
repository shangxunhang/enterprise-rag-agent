"""Configured Reader 共用的统一文档记录构造器。"""
from pathlib import Path
from typing import Dict, Any

import copy
from datetime import date


def build_document(
    file_path: Path,
    text: str,
    doc_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    title: Optional[str] = None,
    source: Optional[str] = None,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    **metadata_kwargs,
) -> Dict[str, Any]:
    """构造兼容 ``Document`` 字典，并隔离模板中的可变 metadata。

    已知 metadata 字段覆盖模板默认值；未知字段统一进入 ``metadata.extra``，避免 Reader
    随意扩展顶层结构。未提供的文档 ID、类型和来源分别由文件名与后缀派生。
    """
    metadata = copy.deepcopy(DOCUMENT_METADATA_TEMPLATE)

    metadata["source"] = source or file_path.name
    metadata["source_path"] = str(file_path)
    metadata["doc_type"] = doc_type or file_path.suffix.replace(".", "")
    metadata["title"] = title
    metadata["created_at"] = created_at or str(date.today())
    metadata["updated_at"] = updated_at

    for key, value in metadata_kwargs.items():
        if key in metadata:
            metadata[key] = value
        else:
            metadata.setdefault("extra", {})
            metadata["extra"][key] = value

    doc = {
        "doc_id": doc_id or file_path.stem,
        "text": text,
        "metadata": metadata,
    }

    return doc
