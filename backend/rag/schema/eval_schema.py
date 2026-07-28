"""旧 RAG Engine 评测层使用的兼容数据协议。

黄金样本已统一复用 ``schemas.eval_schema.RAGEvalSampleSchema``；本文件保留检索结果、
单样本指标和汇总报告的 dataclass 形状，供早期 eval runner 使用。这里只定义数据，不
执行检索、生成或指标计算。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from schemas.eval_schema import RAGEvalSampleSchema


# Legacy engine code keeps this public name, but the sample contract itself is
# canonical and shared with ``eval.rag.strategy``.
EvalSample = RAGEvalSampleSchema


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
