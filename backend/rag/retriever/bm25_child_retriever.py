# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：_iter_jsonl_paths、load_jsonl_dicts、_is_cjk_sequence、default_keyword_tokenize、_safe_float、_parse_json_list、normalize_child_chunk_record、BM25ChildRetriever。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/retriever/bm25_child_retriever.py
=============================================

P2 keyword child retriever:
- Build an in-memory BM25 index from child_chunks.jsonl.
- Search child_chunk_v1.text by keyword/token matching.
- Return normalized child hits compatible with ParentChildRetriever / HybridParentChildRetriever.

Notes:
- Pure Python implementation; no rank_bm25 / jieba dependency required.
- Tokenizer is deliberately conservative:
  * Latin/code tokens: regex words such as parent_chunk_id, Milvus, PARENT_CHUNK_SIZE.
  * CJK text: single characters + overlapping 2-grams to support Chinese matching.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-\.]+|[\u4e00-\u9fff]+", re.UNICODE)


# 阅读注释（函数）：处理 iter jsonl paths 相关逻辑。
def _iter_jsonl_paths(path: str | Path) -> Iterator[Path]:
    """处理 iter jsonl paths 相关逻辑。

    参数:
        path: 目标文件或目录路径。

    返回:
        Iterator[Path]

    阅读提示:
        主要直接调用：Path, p.is_file, p.is_dir, sorted, p.iterdir, x.is_file, x.name.startswith, x.suffix.lower。
    """
    p = Path(path)
    if p.is_file():
        yield p
        return
    if p.is_dir():
        candidates = sorted(
            x for x in p.iterdir()
            if x.is_file() and (x.name.startswith("part-") or x.suffix.lower() in {".jsonl", ".json"})
        )
        for item in candidates:
            yield item
        return
    raise FileNotFoundError(f"Child chunk path not found: {path}")


# 阅读注释（函数）：加载 jsonl dicts。
def load_jsonl_dicts(path: str | Path) -> List[Dict[str, Any]]:
    """加载 jsonl dicts。

    参数:
        path: 目标文件或目录路径。

    返回:
        List[Dict[str, Any]]

    阅读提示:
        主要直接调用：_iter_jsonl_paths, file_path.open, enumerate, line.strip, json.loads, ValueError, isinstance, records.append。
    """
    records: List[Dict[str, Any]] = []
    for file_path in _iter_jsonl_paths(path):
        with file_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL: file={file_path}, line={line_no}, err={exc}") from exc
                if isinstance(obj, dict):
                    records.append(obj)
    return records


# 阅读注释（函数）：判断 cjk sequence。
def _is_cjk_sequence(token: str) -> bool:
    """判断 cjk sequence。

    参数:
        token: Token，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：bool, all。
    """
    return bool(token) and all("\u4e00" <= ch <= "\u9fff" for ch in token)


# 阅读注释（函数）：处理 default keyword tokenize 相关逻辑。
def default_keyword_tokenize(text: str) -> List[str]:
    """Tokenize mixed Chinese/English/code text for BM25.

    This is not meant to replace a production Chinese analyzer. It is good enough
    for local MVP and technical docs containing parameters, IDs, and Chinese prose.
    """
    if not text:
        return []
    tokens: List[str] = []
    for match in _TOKEN_PATTERN.finditer(str(text)):
        piece = match.group(0).strip().lower()
        if not piece:
            continue
        if _is_cjk_sequence(piece):
            chars = list(piece)
            tokens.extend(chars)
            if len(chars) >= 2:
                tokens.extend("".join(chars[i:i + 2]) for i in range(len(chars) - 1))
        else:
            tokens.append(piece)
            # Split common code-ish separators while retaining original token.
            for sub in re.split(r"[_\-\.]+", piece):
                if sub and sub != piece:
                    tokens.append(sub)
    return tokens


# 阅读注释（函数）：处理 safe float 相关逻辑。
def _safe_float(value: Any, default: float = 0.0) -> float:
    """处理 safe float 相关逻辑。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        float

    阅读提示:
        主要直接调用：float。
    """
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


# 阅读注释（函数）：解析 JSON 列表。
def _parse_json_list(value: Any) -> List[Any]:
    """解析 JSON 列表。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        List[Any]

    阅读提示:
        主要直接调用：isinstance, json.loads。
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return [value]
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


# 阅读注释（函数）：规范化 子块 文本块 记录。
def normalize_child_chunk_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """规范化 子块 文本块 记录。

    参数:
        record: 记录，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：dict, child.get, str, _parse_json_list。
    """
    child = dict(record)
    child_id = child.get("child_chunk_id") or child.get("chunk_id")
    child["child_chunk_id"] = str(child_id or "")
    child["chunk_id"] = str(child.get("chunk_id") or child["child_chunk_id"])
    child["parent_chunk_id"] = str(child.get("parent_chunk_id") or "")
    child["tenant_id"] = str(child.get("tenant_id") or "")
    child["kb_id"] = str(child.get("kb_id") or "")
    child["file_id"] = str(child.get("file_id") or "")
    child["doc_id"] = str(child.get("doc_id") or "")
    if "source_unit_ids" not in child:
        child["source_unit_ids"] = _parse_json_list(child.get("source_unit_ids_json"))
    return child


# 阅读注释（类）：封装 bm25 子块 retriever，集中封装相关状态、依赖和行为。
class BM25ChildRetriever:
    """In-memory BM25 retriever over child_chunk_v1 records."""

    # 阅读注释（函数）：初始化 BM25ChildRetriever，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        child_chunks: Iterable[Dict[str, Any]],
        *,
        tokenizer: Callable[[str], List[str]] = default_keyword_tokenize,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        """初始化 BM25ChildRetriever，保存运行所需的依赖、配置或状态。

        参数:
            child_chunks: 子块 chunks，具体约束请结合类型标注和调用方确认。
            tokenizer: tokenizer，具体约束请结合类型标注和调用方确认。
            k1: k1，具体约束请结合类型标注和调用方确认。
            b: b，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：float, ValueError, normalize_child_chunk_record, len, defaultdict, self._build_index。
        """
        self.tokenizer = tokenizer
        self.k1 = float(k1)
        self.b = float(b)
        if self.k1 <= 0:
            raise ValueError("k1 must be > 0")
        if not (0 <= self.b <= 1):
            raise ValueError("b must be between 0 and 1")

        self.child_chunks = [normalize_child_chunk_record(x) for x in child_chunks]
        self.n_docs = len(self.child_chunks)
        if self.n_docs == 0:
            raise ValueError("BM25ChildRetriever requires at least one child chunk")

        self.doc_tokens: List[List[str]] = []
        self.doc_tf: List[Counter[str]] = []
        self.doc_len: List[int] = []
        self.df: Dict[str, int] = defaultdict(int)
        self.idf: Dict[str, float] = {}
        self.avgdl = 0.0

        self._build_index()

    # 阅读注释（函数）：根据 jsonl 创建 BM25ChildRetriever。
    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        tokenizer: Callable[[str], List[str]] = default_keyword_tokenize,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "BM25ChildRetriever":
        """根据 jsonl 创建 BM25ChildRetriever。

        参数:
            path: 目标文件或目录路径。
            tokenizer: tokenizer，具体约束请结合类型标注和调用方确认。
            k1: k1，具体约束请结合类型标注和调用方确认。
            b: b，具体约束请结合类型标注和调用方确认。

        返回:
            'BM25ChildRetriever'

        阅读提示:
            主要直接调用：cls, load_jsonl_dicts。
        """
        return cls(load_jsonl_dicts(path), tokenizer=tokenizer, k1=k1, b=b)

    # 阅读注释（函数）：构建 索引。
    def _build_index(self) -> None:
        """构建 索引。

        返回:
            None

        阅读提示:
            主要直接调用：str, child.get, self.tokenizer, Counter, self.doc_tokens.append, self.doc_tf.append, max, len。
        """
        total_len = 0
        for child in self.child_chunks:
            text = str(child.get("text") or "")
            tokens = self.tokenizer(text)
            tf = Counter(tokens)
            self.doc_tokens.append(tokens)
            self.doc_tf.append(tf)
            length = max(len(tokens), 1)
            self.doc_len.append(length)
            total_len += length
            for term in tf.keys():
                self.df[term] += 1

        self.avgdl = total_len / max(self.n_docs, 1)
        self.idf = {
            term: math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))
            for term, df in self.df.items()
        }

    # 阅读注释（函数）：计算 doc 的评分。
    def _score_doc(self, query_terms: List[str], doc_idx: int) -> float:
        """计算 doc 的评分。

        参数:
            query_terms: 查询 terms，具体约束请结合类型标注和调用方确认。
            doc_idx: doc idx，具体约束请结合类型标注和调用方确认。

        返回:
            float

        阅读提示:
            主要直接调用：tf.get, self.idf.get, max, float。
        """
        if not query_terms:
            return 0.0
        tf = self.doc_tf[doc_idx]
        dl = self.doc_len[doc_idx]
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if freq <= 0:
                continue
            idf = self.idf.get(term, 0.0)
            denom = freq + self.k1 * (1.0 - self.b + self.b * dl / max(self.avgdl, 1e-9))
            score += idf * (freq * (self.k1 + 1.0)) / denom
        return float(score)

    # 阅读注释（函数）：搜索 BM25ChildRetriever。
    def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        min_score: float = 0.0,
        tenant_id: Optional[str] = None,
        kb_ids: Optional[Iterable[str]] = None,
        file_ids: Optional[Iterable[str]] = None,
        doc_id: Optional[str] = None,
        doc_ids: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """搜索 BM25ChildRetriever。

        参数:
            query: 当前检索或生成查询。
            top_k: top k，具体约束请结合类型标注和调用方确认。
            min_score: min score，具体约束请结合类型标注和调用方确认。
            doc_id: doc 标识，具体约束请结合类型标注和调用方确认。
            doc_ids: doc 标识集合，具体约束请结合类型标注和调用方确认。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：strip, str, ValueError, self.tokenizer, allowed_doc_ids.add, enumerate, child.get, self._score_doc。
        """
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")
        query_terms = self.tokenizer(str(query))
        if not query_terms:
            return []

        required_tenant_id = str(tenant_id or "").strip()
        allowed_kb_ids = {str(item) for item in (kb_ids or []) if str(item)}
        allowed_file_ids = {str(item) for item in (file_ids or []) if str(item)}
        allowed_doc_ids = {str(item) for item in (doc_ids or []) if str(item)}
        if doc_id:
            allowed_doc_ids.add(str(doc_id))

        scored: List[tuple[float, int]] = []
        for idx, child in enumerate(self.child_chunks):
            if required_tenant_id and str(child.get("tenant_id") or "") != required_tenant_id:
                continue
            if allowed_kb_ids and str(child.get("kb_id") or "") not in allowed_kb_ids:
                continue
            if allowed_file_ids and str(child.get("file_id") or "") not in allowed_file_ids:
                continue
            if allowed_doc_ids and str(child.get("doc_id")) not in allowed_doc_ids:
                continue
            score = self._score_doc(query_terms, idx)
            if score > float(min_score):
                scored.append((score, idx))

        scored.sort(key=lambda x: x[0], reverse=True)
        hits: List[Dict[str, Any]] = []
        for rank, (score, idx) in enumerate(scored[: int(top_k)], start=1):
            child = self.child_chunks[idx]
            hits.append({
                "rank": rank,
                "score": _safe_float(score),
                "retrieval_source": "keyword",
                "chunk_id": child.get("chunk_id"),
                "child_chunk_id": child.get("child_chunk_id"),
                "parent_chunk_id": child.get("parent_chunk_id"),
                "tenant_id": child.get("tenant_id"),
                "kb_id": child.get("kb_id"),
                "file_id": child.get("file_id"),
                "doc_id": child.get("doc_id"),
                "child_chunk": child,
                "raw_hit": {
                    "bm25_score": _safe_float(score),
                    "doc_index": idx,
                    "query_terms": query_terms,
                },
            })
        return hits

    # 阅读注释（函数）：处理 len 相关逻辑。
    def __len__(self) -> int:
        """处理 len 相关逻辑。

        返回:
            int
        """
        return self.n_docs
