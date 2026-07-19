# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：ParentChildResourcePool。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Lazy, per-runtime resource pool for heavy retrieval dependencies."""

from __future__ import annotations

from pathlib import Path
from typing import Any


# 阅读注释（类）：封装 父块 子块 resource pool，集中封装相关状态、依赖和行为。
class ParentChildResourcePool:
    """Load vector/BM25/parent resources only when selected plugins need them."""

    # 阅读注释（函数）：初始化 ParentChildResourcePool，保存运行所需的依赖、配置或状态。
    def __init__(self, *, runtime_config: Any, project_root: Path) -> None:
        """初始化 ParentChildResourcePool，保存运行所需的依赖、配置或状态。

        参数:
            runtime_config: 运行时 配置，具体约束请结合类型标注和调用方确认。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：Path。
        """
        self.runtime_config = runtime_config
        self.project_root = Path(project_root)
        self._dense_retriever: Any | None = None
        self._keyword_retriever: Any | None = None
        self._parent_store: Any | None = None
        self._parent_rerankers: dict[tuple[Any, ...], Any] = {}

    # 阅读注释（函数）：获取 dense retriever。
    def get_dense_retriever(self) -> Any:
        """获取 dense retriever。

        返回:
            Any

        阅读提示:
            主要直接调用：MilvusChildRetriever。
        """
        if self._dense_retriever is None:
            from rag.retriever.milvus_child_retriever import MilvusChildRetriever

            cfg = self.runtime_config
            self._dense_retriever = MilvusChildRetriever(
                db_file=cfg.db_file,
                collection_name=cfg.collection_name,
                metric_type=cfg.metric_type,
                embedding_model=cfg.embedding_model,
                embedding_device=cfg.embedding_device,
                embedding_batch_size=1,
                hash_embedding=cfg.hash_embedding,
                hash_dim=cfg.hash_dim,
            )
        return self._dense_retriever

    # 阅读注释（函数）：获取 keyword retriever。
    def get_keyword_retriever(self) -> Any:
        """获取 keyword retriever。

        返回:
            Any

        阅读提示:
            主要直接调用：BM25ChildRetriever.from_jsonl。
        """
        if self._keyword_retriever is None:
            from rag.retriever.bm25_child_retriever import BM25ChildRetriever

            self._keyword_retriever = BM25ChildRetriever.from_jsonl(
                self.runtime_config.child_file
            )
        return self._keyword_retriever


    # 阅读注释（函数）：获取 父块 reranker。
    def get_parent_reranker(
        self,
        *,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
        max_length: int | None = None,
        local_files_only: bool | None = None,
    ) -> Any:
        """Return a cached parent cross-encoder reranker resource.

        The static retrieval spec selects the reranker plugin and behaviour. Model location
        and device default to the deployment/runtime configuration, while an
        explicit plugin parameter may override an individual resource field.
        """
        cfg = self.runtime_config
        resolved_model_name = str(model_name or cfg.reranker_model)
        resolved_device = str(device or cfg.reranker_device)
        resolved_batch_size = int(
            cfg.reranker_batch_size if batch_size is None else batch_size
        )
        resolved_max_length = int(
            cfg.reranker_max_length if max_length is None else max_length
        )
        resolved_local_files_only = bool(
            cfg.reranker_local_files_only
            if local_files_only is None
            else local_files_only
        )
        key = (
            resolved_model_name,
            resolved_device,
            resolved_batch_size,
            resolved_max_length,
            resolved_local_files_only,
        )
        if key not in self._parent_rerankers:
            from rag.reranker.parent_child_reranker import ParentChildReranker

            self._parent_rerankers[key] = ParentChildReranker(
                model_name=resolved_model_name,
                device=resolved_device,
                batch_size=resolved_batch_size,
                max_length=resolved_max_length,
                local_files_only=resolved_local_files_only,
            )
        return self._parent_rerankers[key]

    # 阅读注释（函数）：获取 父块 store。
    def get_parent_store(self) -> Any:
        """获取 父块 store。

        返回:
            Any

        阅读提示:
            主要直接调用：ParentChunkStore.from_jsonl。
        """
        if self._parent_store is None:
            from rag.store.parent_chunk_store import ParentChunkStore

            self._parent_store = ParentChunkStore.from_jsonl(
                self.runtime_config.parent_file
            )
        return self._parent_store
