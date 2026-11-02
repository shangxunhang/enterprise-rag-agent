"""保留旧 ``rag.Factory.reader_factory`` 导入路径的兼容转发层。

Reader 工厂的唯一实现位于 ``rag.reader.reader_factory``。本模块不复制扩展名路由
或批量读取逻辑，避免历史包名和当前包名形成两套行为。
"""

from rag.reader.reader_factory import get_reader, load_documents

# 只重新导出旧调用方实际依赖的两个函数。
__all__ = ["get_reader", "load_documents"]
