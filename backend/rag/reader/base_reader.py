"""定义所有本地文档 Reader 必须实现的统一读取端口。"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List


class BaseReader(ABC):
    """把一种文件格式转换为标准 Document 字典列表。

    Reader 只负责文件解析和字段映射，不负责文本清洗、分块、Embedding 或索引写入。
    具体实现包括 TXT、JSON 和 JSONL；未来 PDF/Word Reader 也应遵守同一返回契约。
    """

    @abstractmethod
    def read(self, path: Path) -> List[Dict]:
        """读取一个文件并返回零个或多个标准文档。

        Args:
            path: 待读取文件路径。

        Returns:
            标准 Document 字典列表。空文件或没有有效记录时可返回空列表。

        Raises:
            具体异常由格式实现决定；调用方可选择失败即停或记录后跳过。
        """

        # 抽象方法没有默认读取行为，子类必须显式实现格式解析。
        pass
