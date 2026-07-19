# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：JsonReader。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
src/rag_template/reader/json_reader.py
=====================================

JSON Reader。

支持：
1. 单条 JSON object
2. JSON array
"""

import json
from datetime import date
from pathlib import Path
from typing import List, Dict, Any

from rag.reader.base_reader import BaseReader
from rag.schema.document_schema import build_document


# 阅读注释（类）：封装 JSON reader，集中封装相关状态、依赖和行为。
class JsonReader(BaseReader):
    """
    JSON 文件读取器。
    """

    # 阅读注释（函数）：读取 JsonReader。
    def read(self, path: Path) -> List[Dict]:
        """
        读取 json 文件，并转换为 Document Schema 列表。

        Args:
            path: json 文件路径

        Returns:
            documents: 标准 document 列表
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            records = [data]
        elif isinstance(data, list):
            records = data
        else:
            raise ValueError(f"不支持的 JSON 顶层结构: {type(data)}，文件: {path}")

        documents = []

        for idx, record in enumerate(records):
            if not isinstance(record, dict):
                print(f"[JsonReader] 跳过非 dict 记录: {path}, index={idx}")
                continue

            doc = self._record_to_document(
                record=record,
                path=path,
                index=idx,
            )

            if doc is not None:
                documents.append(doc)

        return documents

    # 阅读注释（函数）：记录 to 文档。
    def _record_to_document(
        self,
        record: Dict[str, Any],
        path: Path,
        index: int,
    ) -> Dict | None:
        """
        将单条 JSON record 转成 Document Schema。
        """
        text = (
            record.get("content")
            or record.get("text")
            or record.get("body")
            or ""
        )

        if not text or not str(text).strip():
            print(f"[JsonReader] 跳过空 content 记录: {path}, index={index}")
            return None

        doc_id = record.get("doc_id") or f"{path.stem}_{index:04d}"

        title = record.get("title")
        source = record.get("source") or path.name
        doc_type = record.get("doc_type") or "json"
        created_at = record.get("created_at")or str(date.today())
        updated_at = record.get("updated_at")or None

        """
        
        
    text: str,
    doc_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    title: Optional[str] = None,
    source: Optional[str] = None,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    **metadata_kwargs,
        """
        doc = build_document(
            file_path=path,
            doc_id=str(doc_id),
            text=str(text),
            source=source,
            source_path=str(path),
            doc_type=doc_type,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            # 下面这些是企业 metadata 扩展，有就传，没有就为空
            department=record.get("department"),
            project_id=record.get("project_id"),
            project_name=record.get("project_name"),
            version=record.get("version"),
            status=record.get("status", "active"),
            security_level=record.get("security_level", "internal"),
        )

        return doc
