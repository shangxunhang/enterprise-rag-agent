# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：JsonlReader。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
src/rag_template/reader/jsonl_reader.py
======================================

JSONL Reader。

每行一个 JSON object。
"""

import json
from datetime import date
from pathlib import Path
from typing import List, Dict, Any

from rag.reader.base_reader import BaseReader
from rag.schema.document_schema import build_document


# 阅读注释（类）：封装 jsonl reader，集中封装相关状态、依赖和行为。
class JsonlReader(BaseReader):
    """
    JSONL 文件读取器。
    """

    # 阅读注释（函数）：读取 JsonlReader。
    def read(self, path: Path) -> List[Dict]:
        """
        读取 jsonl 文件，并转换为 Document Schema 列表。

        Args:
            path: jsonl 文件路径

        Returns:
            documents: 标准 document 列表
        """
        documents = []

        with open(path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f):
                line = line.strip()

                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"[JsonlReader] JSON 解析失败: {path}, line={line_idx + 1}, error={e}")
                    continue

                if not isinstance(record, dict):
                    print(f"[JsonlReader] 跳过非 dict 行: {path}, line={line_idx + 1}")
                    continue

                doc = self._record_to_document(
                    record=record,
                    path=path,
                    line_idx=line_idx,
                )

                if doc is not None:
                    documents.append(doc)

        return documents

    # 阅读注释（函数）：记录 to 文档。
    def _record_to_document(
        self,
        record: Dict[str, Any],
        path: Path,
        line_idx: int,
    ) -> Dict | None:
        """
        将单条 JSONL record 转成 Document Schema。
        """
        text = (
            record.get("content")
            or record.get("text")
            or record.get("body")
            or ""
        )

        if not text or not str(text).strip():
            print(f"[JsonlReader] 跳过空 content 行: {path}, line={line_idx + 1}")
            return None

        doc_id = record.get("doc_id") or f"{path.stem}_line_{line_idx:04d}"

        title = record.get("title")
        source = record.get("source") or path.name
        doc_type = record.get("doc_type") or "jsonl"
        created_at = record.get("created_at")or str(date.today())
        updated_at = record.get("updated_at")or None

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
            department=record.get("department"),
            project_id=record.get("project_id"),
            project_name=record.get("project_name"),
            version=record.get("version"),
            status=record.get("status", "active"),
            security_level=record.get("security_level", "internal"),
        )

        return doc
