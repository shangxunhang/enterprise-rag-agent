"""
src/rag_template/reader/reader_factory.py
========================================

Reader 工厂模块。

职责：
1. 根据文件后缀选择对应 Reader
2. 扫描原始数据目录
3. 统一返回 Document Schema 列表
"""

from pathlib import Path
from typing import List, Dict

from rag.configs.ReaderFactoryConfig import READER_MAP
from rag.reader.base_reader import BaseReader






def get_reader(file_path: Path) -> BaseReader:
    """
    根据文件后缀获取 reader。

    Args:
        file_path: 文件路径

    Returns:
        reader 实例
    """
    suffix = file_path.suffix.lower()

    reader_cls = READER_MAP.get(suffix)

    if reader_cls is None:
        raise ValueError(f"暂不支持的文件类型: {suffix}, file={file_path}")

    return reader_cls()


def load_documents(raw_data_dir: Path) -> List[Dict]:
    """
    扫描 raw_data_dir 下所有支持的文件，并读取为统一 Document Schema。

    Args:
        raw_data_dir: 原始数据目录

    Returns:
        documents: 标准 Document Schema 列表
    """
    if not raw_data_dir.exists():
        raise FileNotFoundError(f"原始数据目录不存在: {raw_data_dir}")

    documents = []

    supported_suffixes = set(READER_MAP.keys())

    files = sorted(
        [
            p for p in raw_data_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in supported_suffixes
        ]
    )

    print(f"[ReaderFactory] 支持文件数量: {len(files)}")

    for file_path in files:
        reader = get_reader(file_path)

        try:
            docs = reader.read(file_path)
        except Exception as e:
            print(f"[ReaderFactory] 读取失败: {file_path}, error={e}")
            continue

        documents.extend(docs)

    return documents