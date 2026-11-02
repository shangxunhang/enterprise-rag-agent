"""检索插件组合使用的统一运行信封。

迁移期间单个候选仍保留字典形状，以维持现有引用和重排语义；``RetrievalRequest`` 与
``CandidateSet`` 本身是稳定、框架无关的边界，使召回器、融合器和补全器无需了解彼此
具体实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetrievalRequest:
    """一次召回所需的 Query、结构化授权缩小条件和运行元数据快照。"""
    query: str
    filter_expr: str | None = None
    tenant_id: str | None = None
    kb_ids: list[str] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """拒绝空 Query；授权与过滤一致性由更外层请求边界校验。"""
        if not str(self.query or "").strip():
            raise ValueError("retrieval query cannot be empty")


@dataclass
class CandidateSet:
    """同一 Query、同一处理阶段的一组候选及其来源/融合元数据。"""
    query: str
    source_name: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def copy_with(
        self,
        *,
        source_name: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "CandidateSet":
        """复制信封并选择性替换来源、候选或元数据，避免插件就地污染上游结果。"""
        return CandidateSet(
            query=self.query,
            source_name=source_name or self.source_name,
            candidates=list(self.candidates if candidates is None else candidates),
            metadata=dict(self.metadata if metadata is None else metadata),
        )
