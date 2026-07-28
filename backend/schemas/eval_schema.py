"""定义 RAG 黄金样本、端到端任务样本、评测指标和评测结果。

RAG 策略评测关注文档/片段召回和关键词命中；端到端任务评测还可检查章节、引用和
完整业务输出。黄金样本采用严格 Schema，至少必须声明一种可验证目标，防止生成一个
看似合法但无法计算任何指标的空样本。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import ErrorSchema, SchemaBase, WarningSchema


class RAGEvalSampleSchema(SchemaBase):
    """表示所有 RAG 策略评测共用的规范黄金样本。"""

    # 严格类型并禁止未知字段，避免不同评测器各自解释拼写错误或隐式转换。
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    # 当前 RAG 黄金样本只接受唯一固定契约版本。
    schema_version: Literal["rag_strategy_eval_sample_v1"] = (
        "rag_strategy_eval_sample_v1"
    )

    # 黄金样本唯一标识。
    sample_id: str
    # 实际提交给检索链的查询文本。
    query: str
    # 可选参考答案，用于答案级质量或关键词评测。
    reference_answer: str = ""

    # 期望被召回的规范文档标识。
    expected_doc_ids: List[str] = Field(default_factory=list)
    # 期望被召回的父片段标识。
    expected_parent_chunk_ids: List[str] = Field(default_factory=list)
    # 期望被召回的子片段标识。
    expected_child_chunk_ids: List[str] = Field(default_factory=list)
    # 期望出现在检索上下文中的关键词。
    expected_keywords: List[str] = Field(default_factory=list)
    # 期望出现在生成答案中的关键词。
    answer_keywords: List[str] = Field(default_factory=list)

    # 本样本要求应用的检索过滤表达式。
    filter_expr: Optional[str] = None
    # 关键词检索分支单独期望命中的文档标识。
    keyword_doc_ids: List[str] = Field(default_factory=list)

    # 黄金集稳定标识。
    golden_set_id: Optional[str] = None
    # 黄金集版本，用于结果复现和数据变更追踪。
    golden_set_version: Optional[str] = None
    # 构造该黄金样本所依据的来源引用。
    source_refs: List[str] = Field(default_factory=list)
    # 难度、领域、切分标签等样本元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("sample_id", "query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """去除首尾空白并拒绝空样本标识或空查询。

        Args:
            value: 待校验字段值。

        Returns:
            规范化后的非空字符串。
        """

        # 第一步：统一把输入转为字符串并去除首尾空白。
        normalized = str(value or "").strip()

        # 第二步：空标识无法追踪，空查询也无法执行有效检索，因此直接拒绝。
        if not normalized:
            raise ValueError("sample_id/query cannot be blank")

        return normalized

    @model_validator(mode="after")
    def _require_evaluation_target(self) -> "RAGEvalSampleSchema":
        """保证黄金样本至少声明一种可计算的评测目标。

        Returns:
            校验通过后的当前样本。

        Raises:
            ValueError: 文档、片段、上下文关键词和答案关键词全部为空时抛出。
        """

        # 至少一个目标集合非空，评测器才能计算召回或关键词类指标。
        if not any(
            (
                self.expected_doc_ids,
                self.expected_parent_chunk_ids,
                self.expected_child_chunk_ids,
                self.expected_keywords,
                self.answer_keywords,
            )
        ):
            raise ValueError(
                "eval sample must provide at least one expected id, keyword, "
                "or answer keyword"
            )

        return self


class EvalExpectedSchema(SchemaBase):
    """描述端到端任务样本期望满足的结构和证据要求。"""

    # 严格类型并禁止未知顶层字段。
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    # 最终输出必须包含的章节标题集合。
    required_sections: List[str] = Field(default_factory=list)
    # 检索链期望使用或命中的文档标识。
    expected_doc_ids: List[str] = Field(default_factory=list)
    # 最终输出或上下文期望包含的关键词。
    expected_keywords: List[str] = Field(default_factory=list)
    # 当前任务是否必须生成引用。
    expected_citation_required: bool = True
    # 尚未进入稳定期望契约的扩展目标。
    extra: Dict[str, Any] = Field(default_factory=dict)


class EvalSampleSchema(SchemaBase):
    """表示一次可重放的端到端业务任务评测样本。"""

    # 严格类型并禁止未知顶层字段。
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    # 当前端到端样本契约版本。
    schema_version: str = "eval_sample_v1"

    # 样本唯一标识。
    sample_id: str
    # 样本对应的业务任务类型。
    task_type: str

    # 重放时使用的用户输入。
    user_input: str
    # 重放时使用的完整规范项目输入。
    project_input: Dict[str, Any] = Field(default_factory=dict)

    # 样本允许或指定使用的文件标识。
    file_ids: List[str] = Field(default_factory=list)
    # 样本允许或指定使用的知识库标识。
    kb_ids: List[str] = Field(default_factory=list)
    # 样本指定的输出模板标识。
    template_id: Optional[str] = None

    # 对结构、证据和关键词的预期结果。
    expected: EvalExpectedSchema = Field(default_factory=EvalExpectedSchema)

    # 所属评测集标识。
    eval_set_id: str
    # 所属评测集版本。
    eval_set_version: str

    # 样本创建时间。
    created_at: str

    # 难度、领域和其他尚未进入稳定样本契约的信息。
    extra: Dict[str, Any] = Field(default_factory=dict)


class EvalMetricsSchema(SchemaBase):
    """保存一次评测计算出的核心指标集合。"""

    # 评测主判定是否通过；不等同于运行时调用是否成功。
    success: bool
    # 最终输出是否包含全部要求章节。
    has_required_sections: Optional[bool] = None
    # 最终输出是否满足引用要求。
    has_citations: Optional[bool] = None
    # 期望目标在前 K 个结果中的命中率或命中值。
    hit_at_k: Optional[float] = None
    # 检索结果的平均倒数排名。
    mrr: Optional[float] = None
    # 期望关键词在检索上下文中的命中比例。
    context_keyword_hit: Optional[float] = None
    # 被评测链路的毫秒耗时。
    latency_ms: Optional[int] = None
    # 其他评测器特有指标。
    extra: Dict[str, Any] = Field(default_factory=dict)


class EvalResultSchema(SchemaBase):
    """把评测指标、错误和告警关联到具体样本及任务运行。"""

    # 当前评测结果契约版本。
    schema_version: str = "eval_result_v1"

    # 评测结果记录唯一标识。
    eval_result_id: str
    # 被评测样本标识。
    sample_id: str

    # 重放产生的业务任务标识。
    task_id: str
    # 重放产生的单次运行标识。
    run_id: str

    # 被评测任务类型。
    task_type: str

    # 结构化核心指标。
    metrics: EvalMetricsSchema
    # 可选综合分数；其公式和量表应通过评测器版本或 extra 记录。
    score: Optional[float] = None

    # 评测执行失败时的结构化错误。
    error: Optional[ErrorSchema] = None
    # 不阻断评测结果但影响解释的告警集合。
    warnings: List[WarningSchema] = Field(default_factory=list)

    # 评测结果创建时间。
    created_at: str

    # 评测器版本、明细和其他扩展信息。
    extra: Dict[str, Any] = Field(default_factory=dict)
