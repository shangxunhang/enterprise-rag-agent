"""RAG 单次运行记录的持久化端口。"""
from __future__ import annotations

from typing import Any, Dict, Protocol


class RAGRunCapturePort(Protocol):
    """接收完整 RAG 运行记录并返回实际持久化后的标准记录。

    Pipeline 只依赖该协议，不关心实现使用 JSONL、数据库还是测试内存；实现方应负责
    幂等、序列化和外部副作用时机。
    """

    def capture(self, record: Dict[str, Any]) -> Dict[str, Any]: ...
