"""定义基于 DataCapture 记录的轻量 RAG 评测结果协议。

这些 Schema 承载确定性的词法代理指标，不宣称等价于完整 RAGAS 或人工评审。
逐项指标保留原因和细节，便于定位低分来源；报告对象只负责聚合，不重新计算分数。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import SchemaBase


class RAGEvalMetricSchema(SchemaBase):
    """描述一个可解释的 RAG 评测指标。

    Attributes:
        schema_version: 指标协议版本。
        name: 稳定指标名称，用于报告列和权重映射。
        score: 通常位于 ``[0, 1]`` 的归一化分数。
        reason: 面向人工阅读的简短判定说明。
        details: 计算过程、命中数量或代理特征等扩展诊断数据。
    """

    schema_version: str = "rag_eval_metric_v1"
    name: str
    score: float = 0.0
    reason: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


class RAGEvalResultSchema(SchemaBase):
    """保存一条采集样本的完整 RAG 评测结果。

    结果同时保留六项细分指标、加权总分、人工复核标记和输入规模统计。
    ``*_proxy`` 字段明确表示这些分数是确定性代理，不是模型裁判结论。
    """

    schema_version: str = "rag_eval_result_v1"

    # 样本与原始运行身份，用于回溯 DataCapture 记录和任务产物。
    sample_id: str
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    capture_type: Optional[str] = None

    # 归一化后的核心问答文本，便于报告和错误分析直接展示。
    query: str = ""
    answer: str = ""

    # 六项可解释指标分别衡量检索、回答依据、相关性、引用和结构完整度。
    context_precision: RAGEvalMetricSchema
    context_recall_proxy: RAGEvalMetricSchema
    faithfulness_proxy: RAGEvalMetricSchema
    answer_relevance_proxy: RAGEvalMetricSchema
    citation_coverage: RAGEvalMetricSchema
    completeness_proxy: RAGEvalMetricSchema

    # 总分由独立权重函数计算；人工复核由阈值和质量标记共同决定。
    overall_score: float = 0.0
    need_human_review: bool = True
    quality_flags: List[str] = Field(default_factory=list)

    # 输入规模统计用于解释极端分数和筛查空样本。
    retrieved_chunk_num: int = 0
    citation_num: int = 0
    context_chars: int = 0
    answer_chars: int = 0

    # metadata 描述评估器和指标配置；extra 保留来源样本特有信息。
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


class RAGEvalReportSchema(SchemaBase):
    """聚合一个 JSONL 样本文件的 RAG 评测结果。

    Attributes:
        report_id: 本次报告唯一标识。
        run_id: 当全部样本来自同一运行时记录该运行标识。
        source_path: 被评测 JSONL 文件路径。
        created_at: 报告生成时间。
        total: 成功解析并评测的样本数。
        average_*: 对各逐样本指标进行宏平均后的批次级结果。
        results: 按输入顺序保存的逐样本结果。
    """

    schema_version: str = "rag_eval_report_v1"

    report_id: str
    run_id: Optional[str] = None
    source_path: str
    created_at: str

    total: int = 0
    average_overall_score: float = 0.0
    average_context_precision: float = 0.0
    average_context_recall_proxy: float = 0.0
    average_faithfulness_proxy: float = 0.0
    average_answer_relevance_proxy: float = 0.0
    average_citation_coverage: float = 0.0
    average_completeness_proxy: float = 0.0

    results: List[RAGEvalResultSchema] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
