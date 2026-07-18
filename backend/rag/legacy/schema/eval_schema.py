"""
eval_schema.py
==============

RAG 评估模块的数据协议层。

本文件只负责定义评估过程中使用的标准数据结构，包括：
1. 单条评估样本 EvalSample 的字段规范
2. 检索结果 RetrievalResult 的字段规范
3. 单条样本评估结果 EvalResult 的字段规范
4. 最终评估报告 EvalReport 的字段规范

它不负责真正执行检索、生成或指标计算。
retrieval_eval.py、generation_eval.py 和 eval_runner.py 都应基于这里定义的 schema 进行数据读写，
从而保证整个评估模块的数据格式统一、可扩展、可维护。
"""

"""
eval_schema.py
==============

RAG 评估模块的数据协议层。

本文件只负责定义评估过程中使用的标准数据结构，包括：
1. 单条评估样本 EvalSample 的字段规范
2. 检索结果 RetrievalResult 的字段规范
3. 单条样本评估结果 EvalResult 的字段规范
4. 最终评估报告 EvalReport 的字段规范

它不负责真正执行检索、生成或指标计算。
retrieval_eval.py、generation_eval.py 和 eval_runner.py 都应基于这里定义的 schema 进行数据读写，
从而保证整个评估模块的数据格式统一、可扩展、可维护。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvalSample:
    """
    单条 RAG 评估样本。

    用于描述一个 query 的标准答案、期望命中的文档、期望关键词等信息。
    第一版主要服务于轻量级检索评估和关键词级生成评估。
    """

    query: str
    expected_doc_ids: List[str] = field(default_factory=list)
    expected_keywords: List[str] = field(default_factory=list)
    answer_keywords: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    """
    标准化检索结果。

    不管底层使用 FAISS、Milvus、Qdrant、BM25 还是 Hybrid Retriever，
    最终都应该转换成这个统一格式，供 eval 模块计算指标。
    """

    rank: int
    score: float
    doc_id: str
    chunk_id: str
    text: str
    source: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalEvalResult:
    """
    单条样本的检索评估结果。
    """

    hit_at_k: float
    recall_at_k: float
    mrr: float
    context_keyword_hit: float


@dataclass
class GenerationEvalResult:
    """
    单条样本的生成评估结果。
    """
    # Answer Relevance 的轻量代理指标
    answer_keyword_hit: float
    #  Answer Faithfulness 的轻量代理指标
    answer_context_overlap: float
    # 来源追踪正确性
    citation_hit: float

@dataclass
class EvalResult:
    """
    单条样本的完整评估结果。

    包含 query、检索评估结果、生成评估结果，以及可选的原始检索结果和答案。
    """

    query: str
    retrieval_eval: RetrievalEvalResult
    generation_eval: Optional[GenerationEvalResult] = None
    answer: Optional[str] = None
    retrieved_results: List[RetrievalResult] = field(default_factory=list)


@dataclass
class EvalReport:
    """
    整体评估报告。

    用于汇总整个 eval_dataset 上的平均指标。
    """
    num_samples: int
    top_k: int
    avg_hit_at_k: float
    avg_recall_at_k: float
    avg_mrr: float
    avg_context_keyword_hit: float
    avg_answer_keyword_hit: Optional[float] = None
    avg_answer_context_overlap: Optional[float] = None
    avg_citation_hit: Optional[float] = None
    details: List[EvalResult] = field(default_factory=list)