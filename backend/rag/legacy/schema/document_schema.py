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
    """
    构造统一 Document Schema。

    Args:
        file_path: 原始文件路径
        text: 文档正文
        doc_id: 文档 ID，不传则使用 file_path.stem
        doc_type: 文档类型，不传则使用后缀名
        title: 文档标题
        source: 来源名称，不传则使用 file_path.name
        created_at: 创建时间
        updated_at: 更新时间
        metadata_kwargs: 额外 metadata 字段

    Returns:
        标准 Document Schema
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