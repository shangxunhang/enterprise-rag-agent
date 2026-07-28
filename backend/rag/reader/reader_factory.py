"""根据文件扩展名选择 Reader，并批量加载原始数据目录。"""

from pathlib import Path
from typing import Dict, List

from rag.configs.ReaderFactoryConfig import READER_MAP
from rag.reader.base_reader import BaseReader


def get_reader(file_path: Path) -> BaseReader:
    """为一个文件创建对应格式的 Reader。

    Args:
        file_path: 用扩展名选择 Reader 的文件路径。

    Returns:
        ``READER_MAP`` 中注册的 Reader 新实例。

    Raises:
        ValueError: 文件扩展名未注册时抛出。
    """

    # 第一步：统一扩展名大小写，避免 .JSON 与 .json 被视为不同格式。
    suffix = file_path.suffix.lower()

    # 第二步：从静态映射中查找 Reader 类。
    reader_cls = READER_MAP.get(suffix)
    if reader_cls is None:
        raise ValueError(
            f"暂不支持的文件类型: {suffix}, file={file_path}"
        )

    # 第三步：每次读取创建独立实例，当前 Reader 均为无状态对象。
    return reader_cls()


def load_documents(raw_data_dir: Path) -> List[Dict]:
    """递归扫描目录并读取所有已注册格式的文件。

    Args:
        raw_data_dir: 原始数据根目录。

    Returns:
        按排序后文件路径依次拼接的标准 Document 字典列表。

    Raises:
        FileNotFoundError: 原始数据目录不存在时抛出。

    单个文件读取失败会打印错误并跳过，保证批量导入可继续；因此调用方需要结合日志
    和最终文档数判断是否存在数据缺失。
    """

    # 第一步：拒绝不存在的根目录，避免把路径错误误认为空数据集。
    if not raw_data_dir.exists():
        raise FileNotFoundError(
            f"原始数据目录不存在: {raw_data_dir}"
        )

    documents: List[Dict] = []
    supported_suffixes = set(READER_MAP.keys())

    # 第二步：递归筛选已注册扩展名的普通文件，并排序保证执行顺序稳定。
    files = sorted(
        [
            path
            for path in raw_data_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in supported_suffixes
        ]
    )
    print(f"[ReaderFactory] 支持文件数量: {len(files)}")

    # 第三步：逐文件选择 Reader 并读取；单文件异常不终止整批任务。
    for file_path in files:
        reader = get_reader(file_path)
        try:
            file_documents = reader.read(file_path)
        except Exception as exc:
            print(
                f"[ReaderFactory] 读取失败: {file_path}, error={exc}"
            )
            continue

        # 第四步：保持 Reader 返回顺序追加到总文档集合。
        documents.extend(file_documents)

    return documents
