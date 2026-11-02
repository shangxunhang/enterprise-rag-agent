"""把单个 UTF-8 TXT 文件转换为标准 Document 字典。"""

from pathlib import Path
from typing import Dict, List

from rag.reader.base_reader import BaseReader
from rag.schema.document_schema import build_document


class TxtReader(BaseReader):
    """读取纯文本文件，不执行清洗或分块。"""

    def read_txt_file(file_path: Path) -> str:
        """读取单个 UTF-8 文本文件的原始内容。

        Args:
            file_path: TXT 文件路径。

        Returns:
            文件完整文本，保留原换行和空白。

        该方法保留为旧调用方兼容入口；主批量链使用实例方法 ``read``。
        """

        # 使用显式 UTF-8 编码，编码错误直接暴露给上层批量读取策略。
        with open(file_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def read(self, path: Path) -> List[Dict]:
        """读取 TXT 文件并构造一个标准文档。

        Args:
            path: TXT 文件路径。

        Returns:
            有有效非空文本时返回单元素列表；空文件返回空列表。
        """

        # 第一步：读取原始文本，不在 Reader 层改变内容。
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()

        # 第二步：纯空白文件不创建无意义文档和后续空 Chunk。
        if not text or not text.strip():
            print(f"[TxtReader] 跳过空文件: {path}")
            return []

        # 第三步：由统一 Document 构建器生成身份、来源和 metadata。
        document = build_document(
            file_path=path,
            text=text,
        )

        return [document]
