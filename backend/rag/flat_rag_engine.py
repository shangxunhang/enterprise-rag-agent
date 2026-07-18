"""
src/rag_template/flat_rag_engine.py
==========================

RAGEngine 是 rag-template 对外暴露的统一入口。

核心职责：
1. 封装 embedder / retriever / reranker / prompt / llm
2. 对外提供 retrieve()：只检索
3. 对外提供 retrieve_context()：检索 + 拼接上下文，推荐给 Agent/RAGTool 使用
4. 对外提供 answer()：完整 RAG 问答，适合纯 RAG demo/CLI 使用

设计原则：
- Agent 项目优先调用 retrieve_context()
- 单独 RAG 项目可以调用 answer()
- 不让 Agent 直接依赖 TextEmbedder / Retriever / Reranker / PromptBuilder / LocalLLMGenerator
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from rag.configs.RAGConfig import (
    EMBEDDING_MODEL_NAME,
    EMBEDDING_DEVICE,
    EMBEDDING_BATCH_SIZE,
    FAISS_INDEX_FILE,
    CHUNK_META_FILE,
    USE_RERANKER,
    RETRIEVAL_TOP_K,
    FINAL_TOP_K,
    RERANKER_MODEL_NAME,
    RERANKER_DEVICE,
    RERANKER_BATCH_SIZE,
    MILVUS_LITE_DB_FILE,
    MILVUS_COLLECTION_NAME,
    MILVUS_DIM,
)
from rag.configs.LLMConfig import (
    LLM_MODEL_NAME,
    LLM_DEVICE,
    LLM_MAX_NEW_TOKENS,
    LLM_TEMPERATURE,
    LLM_TOP_P,
    LLM_DO_SAMPLE,
)
from rag.embed.embedder import TextEmbedder

from rag.reranker.reranker import TextReranker
from rag.prompt.prompt_builder import build_rag_prompt
from rag.llm.local_llm import LocalLLMGenerator
from rag.ports.pipeline import FlatRetrieverPort


VectorBackend = Literal["faiss", "milvus"]


@dataclass
class RAGEngineConfig:
    """
    RAGEngine 运行配置。

    它不是替代 RAGConfig.py / LLMConfig.py，
    而是把当前 Engine 实例需要的参数集中起来，方便外部注入。
    """

    # vector backend
    vector_backend: VectorBackend = "faiss"

    # embedding
    embedding_model_name: str = EMBEDDING_MODEL_NAME
    embedding_device: str = EMBEDDING_DEVICE
    embedding_batch_size: int = EMBEDDING_BATCH_SIZE

    # FAISS
    faiss_index_file: Path = FAISS_INDEX_FILE
    chunk_meta_file: Path = CHUNK_META_FILE

    # Milvus Lite
    milvus_lite_db_file: Path = MILVUS_LITE_DB_FILE
    milvus_collection_name: str = MILVUS_COLLECTION_NAME
    milvus_dim: int = MILVUS_DIM

    # retrieval / rerank
    use_reranker: bool = USE_RERANKER
    retrieval_top_k: int = RETRIEVAL_TOP_K
    final_top_k: int = FINAL_TOP_K
    reranker_model_name: str = RERANKER_MODEL_NAME
    reranker_device: str = RERANKER_DEVICE
    reranker_batch_size: int = RERANKER_BATCH_SIZE

    # llm
    llm_model_name: str = LLM_MODEL_NAME
    llm_device: str = LLM_DEVICE
    llm_max_new_tokens: int = LLM_MAX_NEW_TOKENS
    llm_temperature: float = LLM_TEMPERATURE
    llm_top_p: float = LLM_TOP_P
    llm_do_sample: bool = LLM_DO_SAMPLE

    # lazy load
    lazy_load_reranker: bool = True
    lazy_load_llm: bool = True


class RAGEngine:
    """
    rag-template 对外统一入口。

    推荐用法：

    1. Agent-RAG 场景：
        engine = RAGEngine(vector_backend="faiss", load_llm=False)
        result = engine.retrieve_context("FAISS 是什么？")

    2. 纯 RAG Demo 场景：
        engine = RAGEngine(vector_backend="faiss", load_llm=True)
        result = engine.answer("FAISS 是什么？")
    """

    def __init__(
        self,
        config: Optional[RAGEngineConfig] = None,
        *,
        vector_backend: Optional[VectorBackend] = None,
        use_reranker: Optional[bool] = None,
        load_llm: bool = False,
    ) -> None:
        self.config = config or RAGEngineConfig()

        if vector_backend is not None:
            self.config.vector_backend = vector_backend

        if use_reranker is not None:
            self.config.use_reranker = use_reranker

        self.embedder: Optional[TextEmbedder] = None
        self.retriever: Optional[FlatRetrieverPort] = None
        self.reranker: Optional[TextReranker] = None
        self.llm: Optional[LocalLLMGenerator] = None

        self._init_embedder()
        self._init_retriever()

        if self.config.use_reranker and not self.config.lazy_load_reranker:
            self._init_reranker()

        if load_llm or not self.config.lazy_load_llm:
            self._init_llm()

    # ------------------------------------------------------------------
    # 初始化组件
    # ------------------------------------------------------------------

    def _init_embedder(self) -> None:
        self.embedder = TextEmbedder(
            model_name=self.config.embedding_model_name,
            device=self.config.embedding_device,
            batch_size=self.config.embedding_batch_size,
        )

    def _init_retriever(self) -> None:
        if self.embedder is None:
            raise RuntimeError("embedder 尚未初始化")

        if self.config.vector_backend == "faiss":
            from rag.retriever.retriever import FaissRetriever

            self.retriever = FaissRetriever(
                index_path=self.config.faiss_index_file,
                chunk_meta_path=self.config.chunk_meta_file,
                embedder=self.embedder,
            )
            return

        if self.config.vector_backend == "milvus":
            from rag.retriever.milvus_retriever import MilvusRetriever

            self.retriever = MilvusRetriever(
                db_file=self.config.milvus_lite_db_file,
                collection_name=self.config.milvus_collection_name,
                dim=self.config.milvus_dim,
                embedder=self.embedder,
            )
            return

        raise ValueError(f"不支持的 vector_backend: {self.config.vector_backend}")

    def _init_reranker(self) -> None:
        if self.reranker is None:
            self.reranker = TextReranker(
                model_name=self.config.reranker_model_name,
                device=self.config.reranker_device,
                batch_size=self.config.reranker_batch_size,
            )

    def _init_llm(self) -> None:
        if self.llm is None:
            self.llm = LocalLLMGenerator(
                model_name=self.config.llm_model_name,
                device=self.config.llm_device,
            )

    # ------------------------------------------------------------------
    # 对外 API 1：只检索
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        top_k: Optional[int] = None,
        retrieval_top_k: Optional[int] = None,
        use_reranker: Optional[bool] = None,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        检索 query 相关 chunks。

        这是最底层对外检索接口。

        Args:
            query:
                用户问题。
            top_k:
                最终返回 chunk 数量。
            retrieval_top_k:
                粗召回数量。
            use_reranker:
                是否启用 reranker。
            filter_expr:
                Milvus 过滤表达式，FAISS 后端暂不使用。

        Returns:
            List[Dict[str, Any]]
        """
        self._validate_query(query)

        if self.retriever is None:
            raise RuntimeError("retriever 尚未初始化")

        final_top_k = top_k or self.config.final_top_k
        enable_reranker = self.config.use_reranker if use_reranker is None else use_reranker

        rough_top_k = retrieval_top_k or (
            self.config.retrieval_top_k if enable_reranker else final_top_k
        )

        if self.config.vector_backend == "milvus":
            retrieved_chunks = self.retriever.search(
                query=query,
                top_k=rough_top_k,
                filter_expr=filter_expr,
            )
        else:
            retrieved_chunks = self.retriever.search(
                query=query,
                top_k=rough_top_k,
            )

        if not enable_reranker:
            return retrieved_chunks[:final_top_k]

        self._init_reranker()

        assert self.reranker is not None

        return self.reranker.rerank(
            query=query,
            retrieved_chunks=retrieved_chunks,
            top_k=final_top_k,
        )

    # ------------------------------------------------------------------
    # 对外 API 2：检索 + 拼接 context，推荐给 Agent 用
    # ------------------------------------------------------------------

    def retrieve_context(
        self,
        query: str,
        *,
        top_k: Optional[int] = None,
        retrieval_top_k: Optional[int] = None,
        use_reranker: Optional[bool] = None,
        filter_expr: Optional[str] = None,
        include_metadata: bool = True,
    ) -> Dict[str, Any]:
        """
        检索并拼接上下文。

        这是 Agent / RAGTool 推荐使用的接口。

        它不会调用 RAG 内部 LLM。
        它只返回 context 和 retrieved_chunks。
        最终回答应由 Agent Finalizer 生成。

        Returns:
            {
                "query": str,
                "context": str,
                "retrieved_chunks": list[dict],
                "metadata": dict,
            }
        """
        self._validate_query(query)

        retrieved_chunks = self.retrieve(
            query=query,
            top_k=top_k,
            retrieval_top_k=retrieval_top_k,
            use_reranker=use_reranker,
            filter_expr=filter_expr,
        )

        context = self._format_context(
            retrieved_chunks=retrieved_chunks,
            include_metadata=include_metadata,
        )

        return {
            "query": query,
            "context": context,
            "retrieved_chunks": retrieved_chunks,
            "metadata": {
                "vector_backend": self.config.vector_backend,
                "use_reranker": self.config.use_reranker if use_reranker is None else use_reranker,
                "retrieval_top_k": retrieval_top_k or self.config.retrieval_top_k,
                "final_top_k": top_k or self.config.final_top_k,
                "retrieved_count": len(retrieved_chunks),
            },
        }

    # ------------------------------------------------------------------
    # 对外 API 3：构造 RAG Prompt
    # ------------------------------------------------------------------

    def build_prompt(
        self,
        query: str,
        retrieved_chunks: List[Dict[str, Any]],
    ) -> str:
        """
        根据 query 和 retrieved_chunks 构造 RAG prompt。

        这个接口主要给纯 RAG answer() 用。
        Agent 项目一般不直接用它。
        """
        self._validate_query(query)

        if retrieved_chunks is None:
            raise ValueError("retrieved_chunks 不能为 None")

        return build_rag_prompt(
            query=query,
            retrieved_chunks=retrieved_chunks,
        )

    # ------------------------------------------------------------------
    # 对外 API 4：完整 RAG 问答，适合纯 RAG demo
    # ------------------------------------------------------------------

    def answer(
        self,
        query: str,
        *,
        top_k: Optional[int] = None,
        retrieval_top_k: Optional[int] = None,
        use_reranker: Optional[bool] = None,
        filter_expr: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        do_sample: Optional[bool] = None,
        return_prompt: bool = True,
        return_chunks: bool = True,
    ) -> Dict[str, Any]:
        """
        完整 RAG 问答流程。

        注意：
        这个方法会调用 RAG 内部 LLM。
        Agent-RAG 场景不推荐优先使用这个方法。
        Agent-RAG 更推荐 retrieve_context()。
        """
        self._validate_query(query)

        retrieved_chunks = self.retrieve(
            query=query,
            top_k=top_k,
            retrieval_top_k=retrieval_top_k,
            use_reranker=use_reranker,
            filter_expr=filter_expr,
        )

        prompt = self.build_prompt(
            query=query,
            retrieved_chunks=retrieved_chunks,
        )

        self._init_llm()
        assert self.llm is not None

        answer = self.llm.generate(
            prompt=prompt,
            max_new_tokens=max_new_tokens or self.config.llm_max_new_tokens,
            temperature=temperature if temperature is not None else self.config.llm_temperature,
            top_p=top_p if top_p is not None else self.config.llm_top_p,
            do_sample=do_sample if do_sample is not None else self.config.llm_do_sample,
        )

        result: Dict[str, Any] = {
            "query": query,
            "answer": answer,
            "metadata": {
                "vector_backend": self.config.vector_backend,
                "use_reranker": self.config.use_reranker if use_reranker is None else use_reranker,
                "retrieval_top_k": retrieval_top_k or self.config.retrieval_top_k,
                "final_top_k": top_k or self.config.final_top_k,
                "llm_model_name": self.config.llm_model_name,
            },
        }

        if return_chunks:
            result["retrieved_chunks"] = retrieved_chunks

        if return_prompt:
            result["prompt"] = prompt

        return result

    def ask(self, query: str, **kwargs: Any) -> str:
        """
        简化接口：只返回 answer 字符串。

        适合 CLI / demo 使用。
        Agent 项目不建议用 ask()，因为 Agent 需要 chunks / metadata。
        """
        return self.answer(query=query, **kwargs)["answer"]

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_query(query: str) -> None:
        if not isinstance(query, str):
            raise TypeError("query 必须是 str")

        if not query.strip():
            raise ValueError("query 不能为空")

    @staticmethod
    def _format_context(
        retrieved_chunks: List[Dict[str, Any]],
        include_metadata: bool = True,
    ) -> str:
        """
        将 retrieved_chunks 拼成适合 Agent Finalizer 使用的 context。

        尽量兼容不同 retriever 返回结构：
        - chunk_id
        - doc_id
        - source
        - metadata.source
        - text
        - score
        - rerank_score
        """
        if not retrieved_chunks:
            return "根据现有资料无法回答。"

        context_parts = []

        for idx, chunk in enumerate(retrieved_chunks, start=1):
            chunk_id = chunk.get("chunk_id", f"chunk_{idx}")
            doc_id = chunk.get("doc_id", "")
            text = chunk.get("text", "")
            source = chunk.get("source") or chunk.get("metadata", {}).get("source", "")
            score = chunk.get("score")
            rerank_score = chunk.get("rerank_score")

            if include_metadata:
                header_items = [f"chunk_id={chunk_id}"]

                if doc_id:
                    header_items.append(f"doc_id={doc_id}")

                if source:
                    header_items.append(f"source={source}")

                if score is not None:
                    header_items.append(f"score={score}")

                if rerank_score is not None:
                    header_items.append(f"rerank_score={rerank_score}")

                header = " | ".join(header_items)
                context_parts.append(f"[{idx}] {header}\n{text}")
            else:
                context_parts.append(f"[{idx}] {text}")

        return "\n\n".join(context_parts)


if __name__ == "__main__":
    engine = RAGEngine(vector_backend="milvus", load_llm=False)

    result = engine.retrieve_context("FAISS 是什么？")

    print("\n[Question]")
    print(result["query"])

    print("\n[Context]")
    print(result["context"])

    print("\n[Metadata]")
    print(result["metadata"])