"""把每行一个对象的 UTF-8 JSONL 文件转换为标准 Document 列表。"""

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from rag.reader.base_reader import BaseReader
from rag.schema.document_schema import build_document


class JsonlReader(BaseReader):
    """逐行读取 JSONL，并跳过损坏、非对象或无正文的记录。

    单行错误不会终止整个文件读取；错误行和原因通过标准输出记录。因此批量导入后
    需要结合日志核对跳过数量，不能仅凭返回文档数判断源文件完全有效。
    """

    def read(self, path: Path) -> List[Dict]:
        """读取 JSONL 文件并映射为标准文档列表。

        Args:
            path: JSONL 文件路径。

        Returns:
            按有效行顺序生成的 Document 字典列表。
        """

        documents: List[Dict] = []

        # 第一步：逐行读取，enumerate 使用 0-based 索引供回退 doc_id 生成。
        with open(path, "r", encoding="utf-8") as handle:
            for line_index, line in enumerate(handle):
                normalized = line.strip()

                # 空行没有记录语义，直接跳过。
                if not normalized:
                    continue

                # 第二步：单行 JSON 解析失败时记录 1-based 行号并继续。
                try:
                    record = json.loads(normalized)
                except json.JSONDecodeError as exc:
                    print(
                        f"[JsonlReader] JSON 解析失败: {path}, "
                        f"line={line_index + 1}, error={exc}"
                    )
                    continue

                # JSONL 每行必须是对象，数组和标量无法映射为 Document。
                if not isinstance(record, dict):
                    print(
                        f"[JsonlReader] 跳过非 dict 行: {path}, "
                        f"line={line_index + 1}"
                    )
                    continue

                # 第三步：把业务字段映射为统一文档；空正文返回 None。
                document = self._record_to_document(
                    record=record,
                    path=path,
                    line_idx=line_index,
                )
                if document is not None:
                    documents.append(document)

        return documents

    def _record_to_document(
        self,
        record: Dict[str, Any],
        path: Path,
        line_idx: int,
    ) -> Dict | None:
        """将一条 JSONL 对象映射为标准 Document。

        正文按 ``content > text > body`` 选择；身份和企业 metadata 优先使用源记录，
        缺失时根据文件名、行号和默认值补齐。
        """

        # 第一步：兼容三种常见正文字段，并拒绝空白正文。
        text = (
            record.get("content")
            or record.get("text")
            or record.get("body")
            or ""
        )
        if not text or not str(text).strip():
            print(
                f"[JsonlReader] 跳过空 content 行: {path}, "
                f"line={line_idx + 1}"
            )
            return None

        # 第二步：优先使用源 doc_id，否则按文件名和 0-based 行号生成稳定身份。
        doc_id = (
            record.get("doc_id")
            or f"{path.stem}_line_{line_idx:04d}"
        )

        # 第三步：提取基础来源字段和时间字段。
        title = record.get("title")
        source = record.get("source") or path.name
        doc_type = record.get("doc_type") or "jsonl"
        created_at = record.get("created_at") or str(date.today())
        updated_at = record.get("updated_at") or None

        # 第四步：统一交给 Document 构建器生成标准结构，并透传企业扩展 metadata。
        return build_document(
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
            security_level=record.get(
                "security_level",
                "internal",
            ),
        )
