# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：RetrievalRequest、CandidateSet。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Unified runtime envelopes for retrieval plugin composition.

The candidate payload remains a dictionary during the migration so existing
citation/rerank code keeps its exact semantics. The envelope itself is stable
and framework-independent, which lets retrievers, fusion plugins and candidate
enrichers compose without knowing each other's concrete classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 阅读注释（类）：封装 检索 请求，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class RetrievalRequest:
    """封装 检索 请求，集中封装相关状态、依赖和行为。"""
    query: str
    filter_expr: str | None = None
    tenant_id: str | None = None
    kb_ids: list[str] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)
    doc_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # 阅读注释（函数）：处理 post init 相关逻辑。
    def __post_init__(self) -> None:
        """处理 post init 相关逻辑。

        返回:
            None

        阅读提示:
            主要直接调用：strip, str, ValueError。
        """
        if not str(self.query or "").strip():
            raise ValueError("retrieval query cannot be empty")


# 阅读注释（类）：封装 candidate set，集中封装相关状态、依赖和行为。
@dataclass
class CandidateSet:
    """封装 candidate set，集中封装相关状态、依赖和行为。"""
    query: str
    source_name: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # 阅读注释（函数）：处理 copy with 相关逻辑。
    def copy_with(
        self,
        *,
        source_name: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "CandidateSet":
        """处理 copy with 相关逻辑。

        参数:
            source_name: source 名称，具体约束请结合类型标注和调用方确认。
            candidates: candidates，具体约束请结合类型标注和调用方确认。
            metadata: 随对象传递的元数据。

        返回:
            'CandidateSet'

        阅读提示:
            主要直接调用：CandidateSet, list, dict。
        """
        return CandidateSet(
            query=self.query,
            source_name=source_name or self.source_name,
            candidates=list(self.candidates if candidates is None else candidates),
            metadata=dict(self.metadata if metadata is None else metadata),
        )
