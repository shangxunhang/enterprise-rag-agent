"""单个检索运行时的重量级资源延迟加载池。

插件选择由静态检索规范决定；资源池只按需创建并复用 Embedding、Milvus 子块召回器、
BM25、Parent Store 和 Cross-Encoder。关闭时统一释放有 ``close`` 钩子的资源，避免每个
插件自行管理显存和连接生命周期。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ParentChildResourcePool:
    """仅在启用插件需要时加载父子检索资源，并在同一运行时内缓存。"""

    def __init__(self, *, runtime_config: Any, project_root: Path) -> None:
        """保存部署配置和项目根目录，并初始化各资源的空缓存。"""
        self.runtime_config = runtime_config
        self.project_root = Path(project_root)
        self._dense_retriever: Any | None = None
        self._embedding_backend: Any | None = None
        self._keyword_retriever: Any | None = None
        self._parent_store: Any | None = None
        self._parent_rerankers: dict[tuple[Any, ...], Any] = {}

    def get_embedding_backend(self) -> Any | None:
        """Load and own the real embedding model once per retrieval runtime."""
        cfg = self.runtime_config
        if bool(cfg.hash_embedding):
            return None
        if self._embedding_backend is None:
            from rag.embed.embedder import TextEmbedder

            self._embedding_backend = TextEmbedder(
                model_name=str(cfg.embedding_model),
                device=str(cfg.embedding_device),
                batch_size=1,
            )
        return self._embedding_backend

    def get_dense_retriever(self) -> Any:
        """首次请求时构造 Milvus 子块召回器，并复用同一 Embedding Backend。"""
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
                embedding_backend=self.get_embedding_backend(),
                hash_embedding=cfg.hash_embedding,
                hash_dim=cfg.hash_dim,
            )
        return self._dense_retriever

    def get_keyword_retriever(self) -> Any:
        """首次请求时从离线子块 JSONL 构建并缓存内存 BM25。"""
        if self._keyword_retriever is None:
            from rag.retriever.bm25_child_retriever import BM25ChildRetriever

            self._keyword_retriever = BM25ChildRetriever.from_jsonl(
                self.runtime_config.child_file
            )
        return self._keyword_retriever


    def get_parent_reranker(
        self,
        *,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int | None = None,
        max_length: int | None = None,
        local_files_only: bool | None = None,
    ) -> Any:
        """按模型、设备、批量、长度和本地模式组成的资源键缓存 Cross-Encoder。

        静态检索规范选择插件与行为；插件参数可覆盖部署默认资源字段，但不同资源配置不会
        错误复用同一个模型实例。
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

    def close(self) -> None:
        """Release every heavyweight resource owned by this runtime pool."""
        resources = [
            self._dense_retriever,
            self._embedding_backend,
            *self._parent_rerankers.values(),
        ]
        seen: set[int] = set()
        for resource in resources:
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._dense_retriever = None
        self._embedding_backend = None
        self._keyword_retriever = None
        self._parent_store = None
        self._parent_rerankers.clear()

    def get_parent_store(self) -> Any:
        """首次请求时加载父块 JSONL 索引，并在运行时内复用。"""
        if self._parent_store is None:
            from rag.store.parent_chunk_store import ParentChunkStore

            self._parent_store = ParentChunkStore(
                self.runtime_config.parent_file
            )
        return self._parent_store
