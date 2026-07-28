# -*- coding: utf-8 -*-
"""基于 ``child_chunk_v1`` 的纯 Python 内存 BM25 召回器。

实现不依赖 rank_bm25 或 jieba：英文、数字和代码标识按正则词段切分，并保留常见分隔符
子词；连续中文同时生成单字和重叠二元组。召回结果与 Milvus 子块候选形状一致，可直接
进入 RRF 与父块回填。租户、知识库、文件和文档范围在评分前过滤。
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
    """按稳定文件名顺序遍历单文件或 Spark/JSONL 输出目录。"""
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
    """加载所有 JSON 对象行；解析错误包含具体文件和行号。"""
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
    """判断一个正则词段是否全部由基础 CJK 字符组成。"""
    return bool(token) and all("\u4e00" <= ch <= "\u9fff" for ch in token)


def default_keyword_tokenize(text: str) -> List[str]:
    """为中文、英文和代码混合技术文档生成保守 BM25 词项。

    该分词器用于本地 MVP 与可复现测试，不等同于生产中文分析器；其优势是无外部词典、
    对参数名和 ID 友好且结果稳定。
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
    """安全规范化 BM25 分数。"""
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _parse_json_list(value: Any) -> List[Any]:
    """恢复 JSON 字符串形式的来源 ID 列表。"""
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
    """统一子块、父块、租户和材料标识，并恢复 ``source_unit_ids``。"""
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


class BM25ChildRetriever:
    """对 ``child_chunk_v1.text`` 建立内存 BM25 索引并返回标准子块命中。"""

    def __init__(
        self,
        child_chunks: Iterable[Dict[str, Any]],
        *,
        tokenizer: Callable[[str], List[str]] = default_keyword_tokenize,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        """规范化子块并预计算词频、文档频率、长度和 IDF。

        ``k1`` 控制词频饱和，``b`` 控制文档长度归一化；非法参数和空语料立即失败。
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

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        tokenizer: Callable[[str], List[str]] = default_keyword_tokenize,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> "BM25ChildRetriever":
        """从单个 JSONL 或分区目录加载子块并构建内存索引。"""
        return cls(load_jsonl_dicts(path), tokenizer=tokenizer, k1=k1, b=b)

    def _build_index(self) -> None:
        """一次性计算每个子块的词频、长度、全局 DF、平均长度和 BM25 IDF。"""
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
        """按标准 Okapi BM25 公式计算一个子块对 Query 词项的相关度。"""
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
        tenant_id: Optional[str] = None,
        kb_ids: Optional[Iterable[str]] = None,
        file_ids: Optional[Iterable[str]] = None,
        doc_id: Optional[str] = None,
        doc_ids: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """在授权缩小范围内评分全部子块，并返回前 ``top_k`` 个正分候选。

        租户、KB、文件和文档过滤在评分前执行，避免越权材料参与 DF 之外的本次候选排序；
        索引本身仍覆盖构造时全部语料，因此严格多租户生产部署应按授权域构建独立索引或
        使用后端级隔离。``raw_hit`` 仅记录分数、语料索引和 Query 词项用于诊断。
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

    def __len__(self) -> int:
        """返回索引中的子块数量。"""
        return self.n_docs
