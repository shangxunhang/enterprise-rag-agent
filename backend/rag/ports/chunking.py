"""离线父子分块构建的稳定端口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable


@dataclass
class ParentChildChunkResult:
    """父子分块端口的稳定返回值。"""

    parents: list[dict[str, Any]]
    children: list[dict[str, Any]]


@runtime_checkable
class ChunkerPort(Protocol):
    """把 ``cleaned_text_unit_v1`` 记录转换为父子 Chunk 结果。

    ``execution_metadata`` 必须描述本次实际分块实现、配置和版本，供离线 Manifest、验证
    和索引血缘记录；Pipeline 不应根据具体类名推断这些信息。
    """
    def chunk_records(
        self,
        records: Iterable[dict[str, Any]],
    ) -> ParentChildChunkResult: ...

    def execution_metadata(self) -> dict[str, Any]: ...
