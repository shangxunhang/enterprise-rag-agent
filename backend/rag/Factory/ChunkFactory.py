"""根据旧版字符串配置创建内置 Chunker。

当前主链已有 ``rag.chunker.chunk_factory`` 和插件注册表；本模块保留历史工厂行为，
只支持 fixed_size、paragraph、heading 和 recursive 四种实现。
"""

from rag.chunker.FixedSizeChunker import FixedSizeChunker
from rag.chunker.HeadingChunker import HeadingChunker
from rag.chunker.ParagraphChunker import ParagraphChunker
from rag.chunker.RecursiveChunker import RecursiveChunker


def build_chunker(
    chunker_type: str,
    chunk_size: int,
    chunk_overlap: int,
):
    """按类型名创建一个旧版 Chunker 实例。

    Args:
        chunker_type: ``fixed_size``、``paragraph``、``heading`` 或 ``recursive``。
        chunk_size: 目标块大小，具体单位由对应实现决定。
        chunk_overlap: 相邻块重叠大小。

    Returns:
        与类型名对应的 Chunker 实例。

    Raises:
        ValueError: 类型名不在支持集合中时抛出。
    """

    # 逐类型显式构造，保持每个实现接收相同位置参数的旧接口。
    if chunker_type == "fixed_size":
        return FixedSizeChunker(chunk_size, chunk_overlap)
    if chunker_type == "paragraph":
        return ParagraphChunker(chunk_size, chunk_overlap)
    if chunker_type == "heading":
        return HeadingChunker(chunk_size, chunk_overlap)
    if chunker_type == "recursive":
        return RecursiveChunker(chunk_size, chunk_overlap)

    # 未知类型不静默回退固定分块，防止索引版本在配置错误时悄然改变。
    raise ValueError(f"Unsupported chunker_type: {chunker_type}")
