# -*- coding: utf-8 -*-
"""
rag_template/tools/rag_tool.py
==============================

RAGTool wraps the completed Parent-Child RAG QA pipeline into a Tool interface.

Current capability:
query -> hybrid retrieval -> rerank -> context packing -> prompt -> local LLM answer -> capture

Agent-facing usage:
    tool = RAGTool.from_default_config()
    result = tool.run({"query": "整体性学习是什么"})

The Agent layer should only call RAGTool.run(...). It should not directly depend on:
Milvus / BM25 / RRF / Reranker / ContextPacker / PromptBuilder / LocalLLM / DataCapture.
"""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


from rag.application.rag_tool_runner import RAGToolRunner
from rag.common.coercion import split_csv
from rag.common.pathing import require_path, resolve_path
from rag.common.presentation import compact_contexts
from rag.runtime.parent_child_runtime_factory import ParentChildRuntimeFactory
from rag.tools.base_tool import BaseTool

try:
    from rag.configs.RAGConfig import (
        CHILD_CHUNKS_FILE,
        EMBEDDING_BATCH_SIZE,
        EMBEDDING_DEVICE,
        EMBEDDING_MODEL_NAME,
        PARENT_CHILD_DEDUP_PARENT,
        PARENT_CHILD_DENSE_TOP_K,
        PARENT_CHILD_EVAL_TOP_K,
        PARENT_CHILD_HYBRID_FINAL_TOP_K,
        PARENT_CHILD_HYBRID_KEYWORD_TOP_K,
        PARENT_CHILD_MAX_CONTEXT_CHARS,
        PARENT_CHILD_MAX_CONTEXT_ITEMS,
        PARENT_CHILD_MILVUS_COLLECTION_NAME,
        PARENT_CHILD_MILVUS_DB_FILE,
        PARENT_CHILD_RERANK_LOCAL_FILES_ONLY,
        PARENT_CHILD_RERANK_MAX_LENGTH,
        PARENT_CHILD_RERANK_TOP_K,
        PARENT_CHILD_RRF_K,
        PARENT_CHILD_SEARCH_METRIC_TYPE,
        PARENT_CHUNKS_FILE,
        RERANKER_BATCH_SIZE,
        RERANKER_DEVICE,
        RERANKER_MODEL_NAME,
    )
except Exception:  # pragma: no cover - defensive defaults for standalone use
    PARENT_CHUNKS_FILE = "data/processed/parent_child_chunks/parent_chunks.jsonl"
    CHILD_CHUNKS_FILE = "data/processed/parent_child_chunks/child_chunks.jsonl"
    PARENT_CHILD_MILVUS_DB_FILE = "data/processed/vector_store/milvus_parent_child.db"
    PARENT_CHILD_MILVUS_COLLECTION_NAME = "rag_child_chunks"
    PARENT_CHILD_SEARCH_METRIC_TYPE = "COSINE"
    EMBEDDING_MODEL_NAME = r"D:\models\huggingface\embedding\m3e-base"
    EMBEDDING_DEVICE = "cuda"
    EMBEDDING_BATCH_SIZE = 32
    RERANKER_MODEL_NAME = r"D:\models\huggingface\reranker\bge-reranker-v2-m3"
    RERANKER_DEVICE = "cuda"
    RERANKER_BATCH_SIZE = 16
    PARENT_CHILD_DENSE_TOP_K = 10
    PARENT_CHILD_HYBRID_KEYWORD_TOP_K = 10
    PARENT_CHILD_HYBRID_FINAL_TOP_K = 5
    PARENT_CHILD_RRF_K = 60
    PARENT_CHILD_RERANK_TOP_K = 5
    PARENT_CHILD_RERANK_MAX_LENGTH = 512
    PARENT_CHILD_RERANK_LOCAL_FILES_ONLY = True
    PARENT_CHILD_MAX_CONTEXT_CHARS = 6000
    PARENT_CHILD_MAX_CONTEXT_ITEMS = 3
    PARENT_CHILD_EVAL_TOP_K = 5
    PARENT_CHILD_DEDUP_PARENT = True

try:
    from rag.configs.LLMConfig import (
        LLM_DEVICE,
        LLM_DO_SAMPLE,
        LLM_MAX_NEW_TOKENS,
        LLM_MODEL_NAME,
        LLM_TEMPERATURE,
        LLM_TOP_P,
        QUERY_EXPANSION_DO_SAMPLE,
        QUERY_EXPANSION_LLM_DEVICE,
        QUERY_EXPANSION_LLM_ENABLED,
        QUERY_EXPANSION_LLM_MODEL_NAME,
        QUERY_EXPANSION_TEMPERATURE,
        QUERY_EXPANSION_TOP_P,
        QUERY_HYDE_MAX_NEW_TOKENS,
        QUERY_REWRITE_MAX_NEW_TOKENS,
    )
except Exception:  # pragma: no cover
    LLM_MODEL_NAME = r"D:\models\huggingface\llm\Qwen2.5-1.5B-Instruct"
    LLM_DEVICE = "cuda"
    LLM_MAX_NEW_TOKENS = 256
    LLM_TEMPERATURE = 0.7
    LLM_TOP_P = 0.9
    LLM_DO_SAMPLE = False
    QUERY_EXPANSION_LLM_ENABLED = True
    QUERY_EXPANSION_LLM_MODEL_NAME = LLM_MODEL_NAME
    QUERY_EXPANSION_LLM_DEVICE = LLM_DEVICE
    QUERY_REWRITE_MAX_NEW_TOKENS = 192
    QUERY_HYDE_MAX_NEW_TOKENS = 256
    QUERY_EXPANSION_TEMPERATURE = 0.1
    QUERY_EXPANSION_TOP_P = 0.9
    QUERY_EXPANSION_DO_SAMPLE = False


@dataclass
class RAGToolConfig:
    """Configuration for RAGTool.

    Keep this config local to the tool stage. When migrating to the enterprise
    framework, this dataclass can be replaced by backend/core/config.py.
    """

    parent_file: str = str(PARENT_CHUNKS_FILE)
    child_file: str = str(CHILD_CHUNKS_FILE)
    db_file: str = str(PARENT_CHILD_MILVUS_DB_FILE)
    capture_output: str = "data/processed/runs/rag_runs.jsonl"

    # Optional immutable offline-index lineage. If the pointer exists, runtime
    # paths and embedding metadata are resolved from its IndexManifest.
    use_active_index_manifest: bool = True
    active_index_pointer: str = "data/processed/indexes/active_index.json"
    verify_active_index_manifest_hash: bool = True
    verify_active_index_artifacts: bool = False
    index_version: str = "legacy_unversioned_index"
    dataset_version: str = "unknown"
    index_manifest_file: str = ""
    index_manifest_hash: str = ""
    index_config_hash: str = ""
    index_reproducibility_hash: str = ""
    index_lineage: Optional[Dict[str, Any]] = None

    # External, versioned online pipeline profile. Components migrated to the
    # plugin runtime are selected here instead of by Python strategy branches.
    pipeline_config_file: str = "backend/rag/profiles/hybrid_v1.yaml"

    collection_name: str = str(PARENT_CHILD_MILVUS_COLLECTION_NAME)
    metric_type: str = str(PARENT_CHILD_SEARCH_METRIC_TYPE)

    embedding_model: str = str(EMBEDDING_MODEL_NAME)
    embedding_device: str = str(EMBEDDING_DEVICE)
    embedding_batch_size: int = int(EMBEDDING_BATCH_SIZE)
    hash_embedding: bool = False
    hash_dim: int = 768

    dense_top_k: int = int(PARENT_CHILD_DENSE_TOP_K)
    keyword_top_k: int = int(PARENT_CHILD_HYBRID_KEYWORD_TOP_K)
    candidate_top_k: int = int(PARENT_CHILD_HYBRID_FINAL_TOP_K)
    rrf_k: int = int(PARENT_CHILD_RRF_K)
    dedup_parent: bool = bool(PARENT_CHILD_DEDUP_PARENT)

    skip_rerank: bool = False
    reranker_model: str = str(RERANKER_MODEL_NAME)
    reranker_device: str = str(RERANKER_DEVICE)
    reranker_batch_size: int = int(RERANKER_BATCH_SIZE)
    reranker_max_length: int = int(PARENT_CHILD_RERANK_MAX_LENGTH)
    reranker_local_files_only: bool = bool(PARENT_CHILD_RERANK_LOCAL_FILES_ONLY)
    rerank_top_k: int = int(PARENT_CHILD_RERANK_TOP_K)

    max_context_chars: int = int(PARENT_CHILD_MAX_CONTEXT_CHARS)
    max_context_items: int = int(PARENT_CHILD_MAX_CONTEXT_ITEMS)
    eval_top_k: int = int(PARENT_CHILD_EVAL_TOP_K)

    enable_llm: bool = True
    llm_model: str = str(LLM_MODEL_NAME)
    llm_device: str = str(LLM_DEVICE)
    max_new_tokens: int = int(LLM_MAX_NEW_TOKENS)
    temperature: float = float(LLM_TEMPERATURE)
    top_p: float = float(LLM_TOP_P)
    do_sample: bool = bool(LLM_DO_SAMPLE)

    # 检索前查询生成层：RAG-Fusion 改写 + HyDE 假想文档。
    # 默认复用本地 Qwen2.5-1.5B-Instruct，避免 RAG-Fusion / HyDE 仍停留在模板改写。
    enable_query_expansion_llm: bool = bool(QUERY_EXPANSION_LLM_ENABLED)
    query_expansion_llm_model: str = str(QUERY_EXPANSION_LLM_MODEL_NAME)
    query_expansion_llm_device: str = str(QUERY_EXPANSION_LLM_DEVICE)
    query_rewrite_max_new_tokens: int = int(QUERY_REWRITE_MAX_NEW_TOKENS)
    query_hyde_max_new_tokens: int = int(QUERY_HYDE_MAX_NEW_TOKENS)
    query_expansion_temperature: float = float(QUERY_EXPANSION_TEMPERATURE)
    query_expansion_top_p: float = float(QUERY_EXPANSION_TOP_P)
    query_expansion_do_sample: bool = bool(QUERY_EXPANSION_DO_SAMPLE)

    model_provider: str = "local"
    pipeline_name: str = "parent_child_hybrid_rag_tool"
    pipeline_version: str = "v1.0"

    # RAG v3 retrieval strategy. Keep default as hybrid for stable mainline runs.
    # Supported: hybrid, rag_fusion, hyde, rag_fusion_hyde, c_rag, self_rag, c_rag_self_rag, adaptive_rag.
    retrieval_strategy: str = "hybrid"
    num_rewrites: int = 3
    enable_hyde: bool = False

    # C-RAG-lite / Self-RAG-lite switches. These are strategy plugins for the
    # old engineering project; enterprise migration can move them to RAGService.
    enable_crag: bool = False
    enable_self_rag: bool = False
    crag_max_judge_chunks: int = 8
    crag_drop_irrelevant: bool = True



def _split_csv(value: Any) -> List[str]:
    """Compatibility alias for the shared coercion helper."""
    return split_csv(value)


def _resolve_path(path: str | Path, project_root: Optional[str | Path] = None) -> str:
    """Compatibility alias for the shared path resolver."""
    return resolve_path(path, project_root)


def _assert_exists(path: str | Path, name: str) -> None:
    """Compatibility alias for the shared path guard."""
    require_path(path, name)


def _compact_contexts(
    retrieval_results: List[Dict[str, Any]],
    max_text_chars: int = 500,
) -> List[Dict[str, Any]]:
    """Compatibility alias for the presentation-only compact view."""
    return compact_contexts(retrieval_results, max_text_chars)


class RAGTool(BaseTool):
    """Thin Agent-facing facade for the parent-child RAG runtime."""

    name = "rag_tool"
    description = "Parent-Child hybrid RAG QA tool."

    def __init__(
        self,
        config: Optional[RAGToolConfig] = None,
        *,
        project_root: Optional[str | Path] = None,
        runtime_factory: ParentChildRuntimeFactory | None = None,
        runner: RAGToolRunner | None = None,
    ) -> None:
        self._base_config = copy.deepcopy(config or RAGToolConfig())
        self.config = copy.deepcopy(self._base_config)
        self.project_root = (
            Path(project_root).resolve() if project_root else Path.cwd().resolve()
        )
        self.runtime_factory = runtime_factory or ParentChildRuntimeFactory()
        self.runner = runner or RAGToolRunner()
        self.engine = None
        self._initialized = False
        self._active_pointer_hash: str | None = None
        self._condition = threading.Condition(threading.RLock())
        self._inflight_requests = 0
        self._reloading = False

    @classmethod
    def from_default_config(
        cls,
        *,
        project_root: Optional[str | Path] = None,
    ) -> "RAGTool":
        return cls(RAGToolConfig(), project_root=project_root)

    def _resolved_config(self) -> RAGToolConfig:
        return self.runtime_factory.resolve_config(
            copy.deepcopy(self._base_config),
            self.project_root,
        )

    def _pointer_hash(self) -> str | None:
        if not bool(getattr(self._base_config, "use_active_index_manifest", True)):
            return None
        from rag.offline.manifest import sha256_file

        pointer = Path(getattr(self._base_config, "active_index_pointer", ""))
        if not pointer.is_absolute():
            pointer = (self.project_root / pointer).resolve()
        else:
            pointer = pointer.expanduser().resolve()
        return sha256_file(pointer) if pointer.is_file() else None

    @staticmethod
    def _close_engine(engine: Any) -> None:
        close = getattr(engine, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def initialize(self) -> None:
        with self._condition:
            while self._reloading:
                self._condition.wait()
            if self._initialized and self.engine is not None:
                return
            self._reloading = True

        try:
            new_engine, new_config = self.runtime_factory.build(
                copy.deepcopy(self._base_config),
                self.project_root,
            )
            pointer_hash = self._pointer_hash()
        except Exception:
            with self._condition:
                self._reloading = False
                self._condition.notify_all()
            raise

        with self._condition:
            self.engine = new_engine
            self.config = new_config
            self._active_pointer_hash = pointer_hash
            self._initialized = True
            self._reloading = False
            self._condition.notify_all()

    def active_index_changed(self) -> bool:
        return self._pointer_hash() != self._active_pointer_hash

    def reload_active_index(
        self,
        *,
        force: bool = False,
        timeout_seconds: float = 60.0,
    ) -> Dict[str, Any]:
        """Rebuild and atomically swap the runtime after active pointer change.

        New requests wait while reload is in progress. Existing requests finish
        before the old engine is closed. A failed rebuild leaves the old engine
        active.
        """
        self.initialize()
        target_pointer_hash = self._pointer_hash()
        with self._condition:
            if not force and target_pointer_hash == self._active_pointer_hash:
                return {
                    "status": "unchanged",
                    "index_version": getattr(self.config, "index_version", None),
                    "pointer_hash": self._active_pointer_hash,
                }
            while self._reloading:
                self._condition.wait()
            self._reloading = True
            deadline = time.monotonic() + max(0.1, float(timeout_seconds))
            while self._inflight_requests > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._reloading = False
                    self._condition.notify_all()
                    raise TimeoutError(
                        "timed out waiting for in-flight RAG requests before reload"
                    )
                self._condition.wait(timeout=remaining)
            old_engine = self.engine
            old_version = getattr(self.config, "index_version", None)

        try:
            new_engine, new_config = self.runtime_factory.build(
                copy.deepcopy(self._base_config),
                self.project_root,
            )
            resolved_pointer_hash = self._pointer_hash()
            if resolved_pointer_hash != target_pointer_hash:
                self._close_engine(new_engine)
                raise RuntimeError(
                    "active index pointer changed during runtime rebuild; retry reload"
                )
        except Exception:
            with self._condition:
                self._reloading = False
                self._condition.notify_all()
            raise

        with self._condition:
            self.engine = new_engine
            self.config = new_config
            self._active_pointer_hash = resolved_pointer_hash
            self._initialized = True

        self._close_engine(old_engine)
        with self._condition:
            self._reloading = False
            self._condition.notify_all()

        return {
            "status": "reloaded",
            "previous_index_version": old_version,
            "index_version": getattr(new_config, "index_version", None),
            "pointer_hash": resolved_pointer_hash,
        }

    def close(self, *, timeout_seconds: float = 60.0) -> None:
        with self._condition:
            while self._reloading:
                self._condition.wait()
            self._reloading = True
            deadline = time.monotonic() + max(0.1, float(timeout_seconds))
            while self._inflight_requests > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._reloading = False
                    self._condition.notify_all()
                    raise TimeoutError(
                        "timed out waiting for in-flight RAG requests before close"
                    )
                self._condition.wait(timeout=remaining)
            old_engine = self.engine
            self.engine = None
            self._initialized = False
            self._active_pointer_hash = None

        self._close_engine(old_engine)
        with self._condition:
            self._reloading = False
            self._condition.notify_all()

    def run(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.initialize()
            with self._condition:
                while self._reloading:
                    self._condition.wait()
                if self.engine is None:
                    raise RuntimeError("RAG engine is not initialized")
                engine = self.engine
                config = self.config
                self._inflight_requests += 1
            try:
                return self.runner.run(engine, config, tool_input, self.name)
            finally:
                with self._condition:
                    self._inflight_requests -= 1
                    self._condition.notify_all()
        except Exception as exc:
            return self._fail(
                error=f"{exc.__class__.__name__}: {exc}",
                metadata={"tool_stage": "rag_tool_v1"},
            )
