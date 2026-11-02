"""把单对象或对象数组 JSON 文件转换为标准 Document 列表。"""

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from rag.reader.base_reader import BaseReader
from rag.schema.document_schema import build_document


class JsonReader(BaseReader):
    """读取 JSON 对象或对象数组，并跳过非对象、无正文的记录。"""

    def read(self, path: Path) -> List[Dict]:
        """读取 JSON 文件并映射为标准文档列表。

        Args:
            path: JSON 文件路径。

        Returns:
            按数组顺序生成的有效 Document 字典列表；单对象文件视为单元素数组。

        Raises:
            ValueError: JSON 顶层既不是对象也不是列表时抛出。
            json.JSONDecodeError: 文件不是合法 JSON 时抛出。
        """

        # 第一步：读取完整 JSON 文档。
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)

        # 第二步：把单对象统一包装为列表；其他标量根结构不符合文档集合契约。
        if isinstance(data, dict):
            records = [data]
        elif isinstance(data, list):
            records = data
        else:
            raise ValueError(
                f"不支持的 JSON 顶层结构: {type(data)}，文件: {path}"
            )

        documents: List[Dict] = []

        # 第三步：逐记录映射，非对象项记录后跳过，不终止同文件其他记录。
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                print(
                    f"[JsonReader] 跳过非 dict 记录: "
                    f"{path}, index={index}"
                )
                continue

            document = self._record_to_document(
                record=record,
                path=path,
                index=index,
            )
            if document is not None:
                documents.append(document)

        return documents

    def _record_to_document(
        self,
        record: Dict[str, Any],
        path: Path,
        index: int,
    ) -> Dict | None:
        """把一个 JSON 对象映射为标准 Document。

        正文按 ``content > text > body`` 选择；缺少 doc_id 时使用文件名和数组索引
        生成稳定回退身份。企业 metadata 只做透传，不在 Reader 层解释其业务含义。
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
                f"[JsonReader] 跳过空 content 记录: "
                f"{path}, index={index}"
            )
            return None

        # 第二步：提取或生成文档身份和基础来源信息。
        doc_id = record.get("doc_id") or f"{path.stem}_{index:04d}"
        title = record.get("title")
        source = record.get("source") or path.name
        doc_type = record.get("doc_type") or "json"
        created_at = record.get("created_at") or str(date.today())
        updated_at = record.get("updated_at") or None

        # 第三步：由统一构建器生成标准结构，并透传企业扩展字段。
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
