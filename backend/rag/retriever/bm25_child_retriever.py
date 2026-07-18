# -*- coding: utf-8 -*-
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


def _iter_jsonl_paths(path: str | Path) -> Iterator[Path]:
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


def load_jsonl_dicts(path: str | Path) -> List[Dict[str, Any]]:
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


def _is_cjk_sequence(token: str) -> bool:
    return bool(token) and all("\u4e00" <= ch <= "\u9fff" for ch in token)


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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _parse_json_list(value: Any) -> List[Any]:
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


def normalize_child_chunk_record(record: Dict[str, Any]) -> Dict[str, Any]:
    child = dict(record)
    child_id = child.get("child_chunk_id") or child.get("chunk_id")
    child["child_chunk_id"] = str(child_id or "")
    child["chunk_id"] = str(child.get("chunk_id") or child["child_chunk_id"])
    child["parent_chunk_id"] = str(child.get("parent_chunk_id") or "")
    child["doc_id"] = str(child.get("doc_id") or "")
    if "source_unit_ids" not in child:
        child["source_unit_ids"] = _parse_json_list(child.get("source_unit_ids_json"))
    return child


class BM25ChildRetriever:
    """In-memory BM25 retriever over child_chunk_v1 records."""

    def __init__(
        self,
        child_chunks: Iterable[Dict[str, Any]],
        *,
        tokenizer: Callable[[str], List[str]] = default_keyword_tokenize,
        k1: float = 1.5,
        b: float = 0.75,
    ):
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

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        tokenizer: Callable[[str], List[str]] = default_keyword_tokenize,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "BM25ChildRetriever":
        return cls(load_jsonl_dicts(path), tokenizer=tokenizer, k1=k1, b=b)

    def _build_index(self) -> None:
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

    def _score_doc(self, query_terms: List[str], doc_idx: int) -> float:
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

    def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        min_score: float = 0.0,
        doc_id: Optional[str] = None,
        doc_ids: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")
        query_terms = self.tokenizer(str(query))
        if not query_terms:
            return []

        allowed_doc_ids = {str(item) for item in (doc_ids or []) if str(item)}
        if doc_id:
            allowed_doc_ids.add(str(doc_id))

        scored: List[tuple[float, int]] = []
        for idx, child in enumerate(self.child_chunks):
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
                "doc_id": child.get("doc_id"),
                "child_chunk": child,
                "raw_hit": {
                    "bm25_score": _safe_float(score),
                    "doc_index": idx,
                    "query_terms": query_terms,
                },
            })
        return hits

    def __len__(self) -> int:
        return self.n_docs
