"""定义 RAG 工具面向 Agent 的输入、证据和输出边界。

本模块刻意不暴露向量索引记录等 RAG 内部模板。系统安全边界先生成不可扩大的授权
范围，Agent 只能提交进一步缩小范围的过滤条件；检索链把原始召回结果规范化为证据
条目，最终以 ``EvidenceBundleSchema`` 跨越 RAG/Agent 边界。是否有召回结果和证据
语义是否充分是两个独立事实。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field, model_validator

from schemas.citation import CitationSchema
from schemas.claim import ClaimType
from schemas.common import ErrorSchema, SchemaBase, WarningSchema
from schemas.status import ExecutionStatus


def _normalized_contract_text(value: str) -> str:
    """Return a stable normalized representation used only for contract identity."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def build_evidence_requirement_id(
    *,
    section_id: str,
    topic: str,
    ordinal: int,
) -> str:
    """Build a replay-stable requirement id without random UUID state."""

    if ordinal < 1:
        raise ValueError("requirement ordinal must be at least 1")
    identity = "\x1f".join(
        (
            _normalized_contract_text(section_id),
            _normalized_contract_text(topic),
            str(ordinal),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"requirement_{digest}"


class EvidenceRequirementSchema(SchemaBase):
    """One public evidence need within a section requirements envelope."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    requirement_id: str
    topic: str
    purpose: Optional[str] = None
    importance: Literal["required", "optional"] = "required"
    expected_source_types: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_requirement(self) -> "EvidenceRequirementSchema":
        object.__setattr__(self, "requirement_id", self.requirement_id.strip())
        object.__setattr__(self, "topic", self.topic.strip())
        purpose = self.purpose.strip() if self.purpose is not None else None
        object.__setattr__(self, "purpose", purpose or None)
        object.__setattr__(
            self,
            "expected_source_types",
            list(
                dict.fromkeys(
                    item.strip()
                    for item in self.expected_source_types
                    if item.strip()
                )
            )
        )
        if not self.requirement_id or not self.topic:
            raise ValueError("requirement_id and topic cannot be empty")
        return self


class EvidenceRequirementsSchema(SchemaBase):
    """Canonical section envelope containing item-level evidence requirements."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: str = "evidence_requirements_v2"
    section_id: str
    section_title: Optional[str] = None
    section_type: str
    requirements: List[EvidenceRequirementSchema] = Field(default_factory=list)

    @classmethod
    def from_topics(
        cls,
        *,
        section_id: str,
        section_type: str,
        topics: List[str],
        section_title: Optional[str] = None,
    ) -> "EvidenceRequirementsSchema":
        """Create fallback items from legacy topics at the RAG compatibility edge."""

        stable_topics = list(
            dict.fromkeys(str(topic).strip() for topic in topics if str(topic).strip())
        )
        requirements = [
            EvidenceRequirementSchema(
                requirement_id=build_evidence_requirement_id(
                    section_id=section_id,
                    topic=topic,
                    ordinal=ordinal,
                ),
                topic=topic,
            )
            for ordinal, topic in enumerate(stable_topics, start=1)
        ]
        return cls(
            section_id=section_id,
            section_title=section_title,
            section_type=section_type,
            requirements=requirements,
        )

    @property
    def required_topics(self) -> List[str]:
        """Legacy read-only projection; item requirements remain authority."""

        return [item.topic for item in self.requirements]

    @model_validator(mode="after")
    def validate_envelope(self) -> "EvidenceRequirementsSchema":
        object.__setattr__(self, "section_id", self.section_id.strip())
        section_title = (
            self.section_title.strip() if self.section_title is not None else None
        )
        object.__setattr__(self, "section_title", section_title or None)
        object.__setattr__(self, "section_type", self.section_type.strip())
        if not self.section_id or not self.section_type:
            raise ValueError("section_id and section_type cannot be empty")
        if not self.requirements:
            raise ValueError("requirements cannot be empty")
        requirement_ids = [item.requirement_id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement_id values must be unique")
        normalized_topics = [
            _normalized_contract_text(item.topic) for item in self.requirements
        ]
        if len(normalized_topics) != len(set(normalized_topics)):
            raise ValueError("requirement topics must be unique within a section")
        return self


class RetrievalAccessScopeSchema(SchemaBase):
    """保存应用或安全边界确认后的有效检索授权范围。

    该对象不是完整认证/RBAC 模型，而是检索器必须执行的最终边界。Agent 和业务调用方
    可以在此基础上缩小文件或文档范围，但绝不能增加未授权租户或知识库。
    """

    # 严格类型并禁止未知字段，避免安全范围因拼写错误被静默忽略。
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    # 当前检索授权范围契约版本。
    schema_version: str = "retrieval_access_scope_v1"
    # 已授权租户标识。
    tenant_id: str
    # 当前调用获准访问的知识库标识集合。
    authorized_kb_ids: List[str] = Field(default_factory=list)
    # 当前调用获准访问的原始文件标识；空集合的具体语义由安全边界定义。
    allowed_file_ids: List[str] = Field(default_factory=list)
    # 当前调用获准访问的规范文档标识。
    allowed_doc_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self) -> "RetrievalAccessScopeSchema":
        """规范化授权标识并保证租户和知识库边界非空。

        Returns:
            去除空白、空值和重复项后的当前授权范围。

        Raises:
            ValueError: 租户为空或没有任何已授权知识库时抛出。
        """

        # 第一步：规范化租户标识，禁止仅包含空白的值。
        self.tenant_id = str(self.tenant_id or "").strip()

        # 第二步：按原始顺序去重知识库标识，并过滤空项。
        self.authorized_kb_ids = list(
            dict.fromkeys(
                str(item).strip()
                for item in self.authorized_kb_ids
                if str(item).strip()
            )
        )

        # 第三步：以相同规则规范化文件白名单。
        self.allowed_file_ids = list(
            dict.fromkeys(
                str(item).strip()
                for item in self.allowed_file_ids
                if str(item).strip()
            )
        )

        # 第四步：以相同规则规范化文档白名单。
        self.allowed_doc_ids = list(
            dict.fromkeys(
                str(item).strip()
                for item in self.allowed_doc_ids
                if str(item).strip()
            )
        )

        # 第五步：没有租户就无法形成有效隔离边界。
        if not self.tenant_id:
            raise ValueError("retrieval access scope requires tenant_id")

        # 第六步：至少一个知识库必须由系统明确授权，禁止无边界全库检索。
        if not self.authorized_kb_ids:
            raise ValueError(
                "retrieval access scope requires at least one authorized_kb_id"
            )

        return self


class RetrievalNarrowingSchema(SchemaBase):
    """保存调用方可控、且只能进一步缩小授权范围的结构化过滤条件。"""

    # 严格类型并禁止额外键，防止任意过滤表达式绕过安全边界。
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        arbitrary_types_allowed=True,
    )

    # 可选租户确认值；只能与授权范围一致，不能切换租户。
    tenant_id: Optional[str] = None
    # 调用方希望进一步限制的文件标识。
    file_ids: List[str] = Field(default_factory=list)
    # 调用方希望进一步限制的文档标识。
    doc_ids: List[str] = Field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        """提供旧字典式调用方需要的受限键访问。

        Args:
            key: 只能是 tenant_id、file_ids 或 doc_ids。

        Returns:
            对应 Schema 字段值。

        Raises:
            KeyError: 请求任何未声明过滤字段时抛出。
        """

        # 白名单限制字典式访问，防止调用方误以为该对象接受任意过滤表达式。
        if key not in {"tenant_id", "file_ids", "doc_ids"}:
            raise KeyError(key)
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        """提供与字典 ``get`` 相似的兼容读取接口。

        Args:
            key: 要读取的允许过滤字段。
            default: 字段名不在白名单时返回的默认值。

        Returns:
            合法字段值，或未知键对应的默认值。
        """

        try:
            # 复用受限 __getitem__，保证允许键集合只有一份定义。
            return self[key]
        except KeyError:
            return default


class RAGDocumentContextSchema(SchemaBase):
    """Stable document semantics needed by RAG retrieval and correction."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )

    project_name: Optional[str] = None
    document_title: Optional[str] = None
    required_sections: List[str] = Field(default_factory=list)
    citation_required_sections: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_document_context(self) -> "RAGDocumentContextSchema":
        project_name = self.project_name.strip() if self.project_name else None
        document_title = self.document_title.strip() if self.document_title else None
        object.__setattr__(self, "project_name", project_name or None)
        object.__setattr__(self, "document_title", document_title or None)
        for field_name in ("required_sections", "citation_required_sections"):
            values = list(
                dict.fromkeys(
                    item.strip()
                    for item in getattr(self, field_name)
                    if item.strip()
                )
            )
            object.__setattr__(self, field_name, values)
        return self


class RAGToolInputSchema(SchemaBase):
    """表示 Agent 提交给 RAG 工具的一次标准检索请求。"""

    # 严格类型并禁止未知字段，避免过滤和预算参数被静默忽略。
    model_config = ConfigDict(extra="forbid", strict=True, arbitrary_types_allowed=True)

    # 当前 RAG 工具输入契约版本。
    schema_version: str = "rag_tool_input_v1"

    # 请求所属业务任务标识。
    task_id: str
    # 请求所属单次运行标识。
    run_id: str
    # 发起检索的 Agent 名称。
    agent_name: str

    # 主检索查询。
    query: str
    # 查询扩展器或改写器产生的附加查询。
    rewritten_queries: List[str] = Field(default_factory=list)

    # 兼容调用方声明的知识库范围；系统强制边界以 access_scope 为准。
    kb_ids: List[str] = Field(default_factory=list)
    # 应用或安全层生成的不可扩大授权范围。
    access_scope: Optional[RetrievalAccessScopeSchema] = None

    # 调用方可控的进一步缩小过滤条件，不能覆盖 access_scope 中的强制边界。
    filters: RetrievalNarrowingSchema = Field(
        default_factory=RetrievalNarrowingSchema
    )

    # 是否要求最终证据包生成可追溯引用。
    need_citation: bool = True

    # 最终拼接证据上下文的最大字符数。
    max_context_chars: Optional[int] = Field(default=6000, ge=1)
    # 最终允许进入上下文的最大证据项数量。
    max_context_items: Optional[int] = Field(default=3, ge=1)
    # 调用方声明的证据上下文 token 上限；RAG 内部安全上限仍然优先。
    max_context_tokens: Optional[int] = Field(default=None, ge=1)

    # 一次检索在 caller、gateway 和 RAG trace 中共享的稳定标识。
    retrieval_trace_id: Optional[str] = None
    # 文档、章节或恢复等稳定业务检索范围。
    retrieval_scope: str = "document"
    # RetrievalPlanner 使用的稳定调用任务类型。
    task_type: Optional[str] = None
    # Canonical Section Envelope；requirements[] 是唯一 authority。
    evidence_requirements: Optional[EvidenceRequirementsSchema] = None
    # RAG 确认需要理解的最小稳定文档语义。
    document_context: Optional[RAGDocumentContextSchema] = None

    # 仅承载 optional/diagnostic/experimental metadata，不承载 canonical 语义。
    extra: Dict[str, Any] = Field(default_factory=dict)


class RetrievedChunkSchema(SchemaBase):
    """描述一个召回并可能经过重排的规范文本片段。

    ``match_text`` 是实际命中查询的细粒度文本，``context_text`` 是提供给生成阶段的较
    完整上下文。父子分块场景通常匹配 child、返回 parent，两者不能混为同一字段。
    """

    # 当前召回片段契约版本。
    schema_version: str = "retrieved_chunk_v1"

    # 当前片段在最终召回或重排结果中的排名。
    rank: int

    # 初始检索或融合阶段分数。
    score: Optional[float] = None
    # 分数语义，例如 dense_distance、dense_similarity、keyword_score 或 rrf_score。
    score_type: str = "unknown"

    # 交叉编码器或其他重排器分数。
    rerank_score: Optional[float] = None
    # 重排分数语义，例如 cross_encoder_logit 或 normalized_score。
    rerank_score_type: Optional[str] = None

    # 实际命中查询的片段标识。
    matched_chunk_id: str
    # 最终提供上下文文本的片段标识。
    context_chunk_id: str

    # 父子分块体系中的子片段标识。
    child_chunk_id: Optional[str] = None
    # 父子分块体系中的父片段标识。
    parent_chunk_id: Optional[str] = None
    # 片段所属规范文档标识。
    doc_id: str

    # 查询实际匹配的粒度，例如 child、parent、chunk 或 document。
    matched_granularity: str = "child"
    # 提供给生成阶段的上下文粒度。
    context_granularity: str = "parent"

    # 实际支持检索匹配和 grounding 的文本。
    match_text: str
    # 提供给生成模型的扩展上下文文本。
    context_text: str

    # 来源文档或片段标题。
    title: Optional[str] = None
    # 来源章节名称。
    section: Optional[str] = None
    # 来源章节层级。
    section_level: Optional[int] = None
    # 来源起始页码。
    page_start: Optional[int] = None
    # 来源结束页码。
    page_end: Optional[int] = None

    # 产生该候选的检索分支，例如 dense、keyword 或 graph。
    retrieval_sources: List[str] = Field(default_factory=list)

    # 索引字段、融合细节和来源元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 尚未进入稳定召回片段契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class RAGContextSchema(SchemaBase):
    """保存最终渲染给生成模型的有界 RAG 上下文。"""

    # 当前 RAG 上下文契约版本。
    schema_version: str = "rag_context_v1"

    # 最终渲染的完整证据上下文文本。
    context_text: str

    # 实际进入上下文的 context_chunk_id 集合。
    used_context_chunk_ids: List[str] = Field(default_factory=list)
    # 对应查询命中的 matched_chunk_id 集合。
    matched_chunk_ids: List[str] = Field(default_factory=list)
    # 实际进入上下文的文档标识集合。
    used_doc_ids: List[str] = Field(default_factory=list)

    # 上下文最大字符预算。
    max_context_chars: int = 6000
    # 最终上下文实际字符数，必须与 context_text 长度一致。
    used_context_chars: int = 0
    # 最终进入上下文的证据项数量。
    context_item_count: int = 0
    # 可选 token 预算。
    token_budget: Optional[int] = None
    # 上下文构建器估计或计算的 token 使用量。
    tokens_used: int = 0
    # 因预算被截断的证据项标识。
    truncated_item_ids: List[str] = Field(default_factory=list)

    # 上下文渲染格式，例如 plain_text、markdown 或 structured。
    context_format: str = "markdown"

    # 渲染器、估算器和其他上下文扩展信息。
    extra: Dict[str, Any] = Field(default_factory=dict)


class RAGTraceSchema(SchemaBase):
    """保存一次 RAG 调用的策略、模型、索引、数量和耗时摘要。"""

    # 当前 RAG 轨迹契约版本。
    schema_version: str = "rag_trace_v1"

    # 实际检索模式，例如 dense、keyword 或 hybrid。
    retrieval_mode: str

    # 主查询文本。
    query: str
    # 实际参与检索的改写查询集合。
    rewritten_queries: List[str] = Field(default_factory=list)

    # 实际使用的 embedding 模型名称。
    embedding_model: Optional[str] = None
    # embedding 模型或部署版本。
    embedding_version: Optional[str] = None

    # 实际使用的 reranker 模型名称。
    reranker_model: Optional[str] = None
    # reranker 模型或部署版本。
    reranker_version: Optional[str] = None

    # 实际查询的索引名称。
    index_name: Optional[str] = None
    # 索引数据或结构版本。
    index_version: Optional[str] = None
    # 实际向量数据库类型，例如 milvus、faiss 或 elasticsearch。
    vector_db: Optional[str] = None

    # dense 分支请求召回数量。
    dense_top_k: Optional[int] = None
    # keyword 分支请求召回数量。
    keyword_top_k: Optional[int] = None
    # 融合后进入重排前的候选数量。
    candidate_top_k: Optional[int] = None
    # 重排后保留的候选数量。
    rerank_top_k: Optional[int] = None

    # 本次上下文字符预算。
    max_context_chars: Optional[int] = None

    # 初始召回候选数量。
    retrieved_count: int = 0
    # 完成重排的候选数量。
    reranked_count: int = 0
    # 最终进入上下文的证据项数量。
    context_item_count: int = 0

    # 整条 RAG 调用毫秒耗时。
    latency_ms: Optional[int] = None

    # 各阶段细分耗时、分支结果和其他扩展信息。
    extra: Dict[str, Any] = Field(default_factory=dict)


class EvidenceDisposition(str, Enum):
    """表示一个规范证据项是否进入最终模型上下文。"""

    # 证据项被上下文构建器保留。
    SELECTED = "selected"
    # 证据项被预算、去重、质量或策略规则丢弃。
    DROPPED = "dropped"


class EvidenceStatus(str, Enum):
    """Canonical public status of completed evidence assessment."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICTED = "conflicted"


# Import compatibility only: both names resolve to the same three-state Enum object.
EvidenceAssessmentStatus = EvidenceStatus


class RAGEvidenceLineageSchema(SchemaBase):
    """保存生成当前证据包的索引、模型和检索计划版本血缘。"""

    # 当前证据血缘契约版本。
    schema_version: str = "rag_evidence_lineage_v1"

    # 查询索引名称。
    index_name: Optional[str] = None
    # 索引版本。
    index_version: Optional[str] = None
    # 构建索引使用的数据集版本。
    dataset_version: Optional[str] = None
    # 向量数据库类型。
    vector_db: Optional[str] = None

    # embedding 模型名称。
    embedding_model: Optional[str] = None
    # embedding 模型版本。
    embedding_version: Optional[str] = None
    # 向量维度。
    embedding_dim: Optional[int] = None

    # reranker 模型名称。
    reranker_model: Optional[str] = None
    # reranker 模型版本。
    reranker_version: Optional[str] = None

    # 本次动态检索计划标识。
    retrieval_plan_id: Optional[str] = None
    # 静态检索规格标识。
    static_retrieval_spec_id: Optional[str] = None
    # 静态检索规格版本。
    static_retrieval_spec_version: Optional[str] = None
    # 静态检索规格内容哈希。
    static_retrieval_spec_hash: Optional[str] = None

    # 尚未进入稳定血缘契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class RAGEvidenceItemSchema(SchemaBase):
    """表示跨越 RAG 边界的一个规范证据项。

    ``match_text`` 是用于 grounding 的细粒度命中文本；``context_text`` 是生成模型可见
    的扩展上下文。``disposition`` 明确该项是否真正进入提示词，不能仅凭 items 中存在
    就推断它已被使用。
    """

    # 当前规范证据项契约版本。
    schema_version: str = "rag_evidence_item_v1"

    # 证据项唯一标识。
    evidence_id: str
    # 证据项是否进入最终上下文。
    disposition: EvidenceDisposition
    # 当前证据项在最终对应集合中的排名。
    rank: int
    # 进入上下文选择前的候选排名。
    pre_context_rank: Optional[int] = None

    # 实际命中查询的片段标识。
    matched_chunk_id: str
    # 为生成提供上下文的片段标识。
    context_chunk_id: str
    # 父子分块体系中的子片段标识。
    child_chunk_id: Optional[str] = None
    # 父子分块体系中的父片段标识。
    parent_chunk_id: Optional[str] = None
    # 证据所属规范文档标识。
    doc_id: str

    # 直接支撑匹配和声明 grounding 的文本。
    match_text: str
    # 最终可供生成模型使用的扩展文本。
    context_text: str

    # 来源标题。
    title: Optional[str] = None
    # 来源章节。
    section: Optional[str] = None
    # 来源起始页码。
    page_start: Optional[int] = None
    # 来源结束页码。
    page_end: Optional[int] = None

    # 初始检索或融合分数。
    score: Optional[float] = None
    # 初始分数语义。
    score_type: str = "unknown"
    # 重排分数。
    rerank_score: Optional[float] = None
    # 重排分数语义。
    rerank_score_type: Optional[str] = None
    # 产生该项的检索分支集合。
    retrieval_sources: List[str] = Field(default_factory=list)

    # 该证据项向提示词暴露的引用标识；丢弃项必须为空。
    citation_ids: List[str] = Field(default_factory=list)
    # 证据被丢弃时的稳定原因。
    drop_reason: Optional[str] = None

    # 来源、融合和上下文选择元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 尚未进入稳定证据项契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class RAGEvidenceAssessmentSchema(SchemaBase):
    """Public evidence facts consumed across the RAG/Application boundary."""

    # 当前证据评估契约版本。
    schema_version: str = "rag_evidence_assessment_v1"

    # Assessment lifecycle is separate from the canonical three-state result.
    assessment_state: Literal["pending", "completed"] = "pending"
    # Kept as ``status`` for wire compatibility; it is the canonical EvidenceStatus.
    status: Optional[EvidenceStatus] = None
    # 是否至少有一个证据项进入最终上下文。
    evidence_available: bool = False
    # 进入上下文的证据项数量。
    selected_evidence_count: int = 0
    # 未进入上下文的证据项数量。
    dropped_evidence_count: int = 0
    # 证据包公开的引用数量。
    citation_count: int = 0

    # 执行语义判断的规则、模型或评估器名称。
    judge_name: Optional[str] = None
    # 评估器版本。
    judge_version: Optional[str] = None
    # 评估器输出的可选分数。
    score: Optional[float] = None
    # Coverage and conflict facts distinguish partial-like and absent-like insufficiency.
    coverage_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    required_topics: List[str] = Field(default_factory=list)
    covered_topics: List[str] = Field(default_factory=list)
    missing_topics: List[str] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    # Claim capabilities are evidence facts, not generation permissions.
    supported_claim_types: List[ClaimType] = Field(default_factory=list)
    forbidden_claim_types: List[ClaimType] = Field(default_factory=list)
    # Set-level source facts needed by the Application-owned Gate policy.
    authoritative_evidence_present: bool = False
    historical_or_reference_evidence_present: bool = False
    # Usage constraints derived from evidence certainty (for example estimates).
    required_qualifiers: List[str] = Field(default_factory=list)
    # Facts about the evidence that was actually exposed to the prompt.
    prompt_evidence_source: Optional[str] = None
    prompt_evidence_contract_missing: bool = False
    # 解释充分、不充分或冲突的稳定原因码。
    reason_codes: List[str] = Field(default_factory=list)
    # 评估明细。
    details: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def project_legacy_status(cls, value: Any) -> Any:
        """Normalize legacy PARTIAL/ABSENT without restoring them as enum members."""

        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw_status = data.get("status", data.get("evidence_status"))
        normalized = (
            str(getattr(raw_status, "value", raw_status) or "").strip().lower()
        )
        if normalized in {"partial", "absent"}:
            data["status"] = EvidenceStatus.INSUFFICIENT
        elif normalized == "not_assessed" or not normalized:
            data["status"] = None
        elif raw_status is not None:
            data["status"] = raw_status
        data.pop("evidence_status", None)
        data.setdefault(
            "assessment_state",
            "pending" if data.get("status") is None else "completed",
        )
        return data

    @model_validator(mode="after")
    def validate_assessment_lifecycle(self) -> "RAGEvidenceAssessmentSchema":
        object.__setattr__(
            self,
            "supported_claim_types",
            list(dict.fromkeys(self.supported_claim_types)),
        )
        object.__setattr__(
            self,
            "forbidden_claim_types",
            list(dict.fromkeys(self.forbidden_claim_types)),
        )
        object.__setattr__(
            self,
            "required_qualifiers",
            list(
                dict.fromkeys(
                    item.strip()
                    for item in self.required_qualifiers
                    if item.strip()
                )
            ),
        )
        prompt_source = (
            self.prompt_evidence_source.strip()
            if self.prompt_evidence_source is not None
            else None
        )
        object.__setattr__(self, "prompt_evidence_source", prompt_source or None)
        if self.assessment_state == "pending" and self.status is not None:
            raise ValueError("pending assessment requires status=null")
        if self.assessment_state == "completed" and self.status is None:
            raise ValueError("completed assessment requires a canonical EvidenceStatus")
        for field_name in (
            "required_topics",
            "covered_topics",
            "missing_topics",
            "reason_codes",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique")
        if set(self.covered_topics) & set(self.missing_topics):
            raise ValueError("topics cannot be both covered and missing")
        claim_overlap = set(self.supported_claim_types) & set(
            self.forbidden_claim_types
        )
        if claim_overlap:
            raise ValueError(
                "claim types cannot be both supported and forbidden: "
                + ", ".join(sorted(item.value for item in claim_overlap))
            )
        if self.status is EvidenceStatus.SUFFICIENT and self.conflicts:
            raise ValueError("sufficient evidence cannot contain conflicts")
        if self.status is EvidenceStatus.CONFLICTED and not self.conflicts:
            raise ValueError("conflicted evidence requires conflicts")
        return self

    @property
    def evidence_status(self) -> Optional[EvidenceStatus]:
        """Explicit public name for callers migrating from the ``status`` field."""

        return self.status


class EvidenceBundleSchema(SchemaBase):
    """表示跨越 RAG/Agent 边界的唯一规范证据包。

    ``items``、选择/丢弃标识、引用、血缘和评估是结构化事实源；``context`` 以及
    ``RAGToolOutputSchema`` 上的旧字段是确定性投影。证据包可以成功返回但评估为
    insufficient，这时生成层仍必须遵守禁止臆测规则。
    """

    # 当前规范证据包契约版本。
    schema_version: str = "rag_evidence_contract_v1"

    # 产生证据包的主查询。
    query: str
    # 实际参与检索的改写查询集合。
    rewritten_queries: List[str] = Field(default_factory=list)

    # 可选业务任务标识。
    task_id: Optional[str] = None
    # 可选单次运行标识。
    run_id: Optional[str] = None
    # RAG 边界执行状态。
    status: ExecutionStatus = ExecutionStatus.SUCCESS
    # 与详细检索轨迹关联的标识。
    retrieval_trace_id: Optional[str] = None
    # 产生本 Bundle 的 RAG invocation 范围，例如 document / section / recovery。
    retrieval_scope: str = "document"
    # 本次 invocation 实际使用的 Evidence Requirements immutable provenance snapshot。
    evidence_requirements: Optional[EvidenceRequirementsSchema] = None

    # 全部规范证据项，包括选中和丢弃项。
    items: List[RAGEvidenceItemSchema] = Field(default_factory=list)
    # 最终进入上下文的 evidence_id 集合。
    selected_evidence_ids: List[str] = Field(default_factory=list)
    # 最终未进入上下文的 evidence_id 集合。
    dropped_evidence_ids: List[str] = Field(default_factory=list)

    # 仅对选中证据公开的引用来源集合。
    citations: List[CitationSchema] = Field(default_factory=list)
    # 最终渲染给模型的有界证据上下文。
    context: RAGContextSchema
    # 生成当前证据包的索引、模型和计划版本。
    lineage: RAGEvidenceLineageSchema = Field(default_factory=RAGEvidenceLineageSchema)
    # 证据可用性和语义充分性判断。
    assessment: RAGEvidenceAssessmentSchema = Field(
        default_factory=RAGEvidenceAssessmentSchema
    )

    # CRAG、自纠错或重新检索等步骤的结构化轨迹。
    correction_trace: List[Dict[str, Any]] = Field(default_factory=list)
    # 上下文项、字符和 token 预算使用信息。
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    # 可选 RAG 策略和运行摘要。
    trace: Optional[RAGTraceSchema] = None
    # 不阻止返回但影响证据解释的告警集合。
    warnings: List[WarningSchema] = Field(default_factory=list)
    # RAG 边界失败时的结构化错误。
    error: Optional[ErrorSchema] = None

    # 尚未进入稳定证据包契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contract(self) -> "EvidenceBundleSchema":
        """验证证据身份、选择状态、引用和上下文预算完全一致。

        Returns:
            校验通过且评估计数字段已根据事实重算的当前证据包。

        Raises:
            ValueError: 证据或引用标识重复/缺失、选择与 disposition 不一致、丢弃项
                暴露引用，或上下文字符、token 和项目数量违反契约时抛出。
        """

        # 第一步：建立证据标识映射并拒绝重复 evidence_id。
        item_by_id = {item.evidence_id: item for item in self.items}
        if len(item_by_id) != len(self.items):
            raise ValueError("evidence_id values must be unique")

        # 第二步：选中和丢弃集合必须互斥。
        selected = set(self.selected_evidence_ids)
        dropped = set(self.dropped_evidence_ids)
        if selected & dropped:
            raise ValueError("selected and dropped evidence ids must be disjoint")

        # 第三步：所有选择引用必须指向 items 中真实存在的证据项。
        unknown = (selected | dropped) - set(item_by_id)
        if unknown:
            raise ValueError(
                f"evidence id references are missing from items: {sorted(unknown)}"
            )

        # 第四步：selected 列表与证据项 disposition 必须一致。
        for evidence_id in selected:
            if item_by_id[evidence_id].disposition != EvidenceDisposition.SELECTED:
                raise ValueError(
                    f"selected evidence has wrong disposition: {evidence_id}"
                )

        # 第五步：dropped 列表与证据项 disposition 必须一致。
        for evidence_id in dropped:
            if item_by_id[evidence_id].disposition != EvidenceDisposition.DROPPED:
                raise ValueError(
                    f"dropped evidence has wrong disposition: {evidence_id}"
                )

        # 第六步：公开引用标识必须唯一。
        citation_ids = [item.citation_id for item in self.citations]
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("citation_id values must be unique")
        citation_id_set = set(citation_ids)

        # 第七步：逐项验证证据引用存在，并禁止丢弃证据向提示词暴露引用。
        for item in self.items:
            missing = set(item.citation_ids) - citation_id_set
            if missing:
                raise ValueError(
                    "evidence item references unknown citations: "
                    f"{item.evidence_id} -> {sorted(missing)}"
                )
            if item.disposition == EvidenceDisposition.DROPPED and item.citation_ids:
                raise ValueError(
                    "dropped evidence must not expose prompt citation ids"
                )

        # 第八步：上下文记录的字符使用量必须等于真实渲染文本长度。
        if self.context.used_context_chars != len(self.context.context_text):
            raise ValueError(
                "context used_context_chars must equal rendered text length"
            )

        # 第九步：渲染文本不能超过字符预算。
        if self.context.used_context_chars > self.context.max_context_chars:
            raise ValueError("rendered context exceeds max_context_chars")

        # 第十步：声明 token 预算时，实际或估计使用量不能超过预算。
        if (
            self.context.token_budget is not None
            and self.context.tokens_used > self.context.token_budget
        ):
            raise ValueError("rendered context exceeds token_budget")

        # 第十一步：上下文项数量必须与选中证据数量一致。
        if self.context.context_item_count != len(self.selected_evidence_ids):
            raise ValueError(
                "context item count must match selected evidence count"
            )

        # 第十二步：可用性和计数字段由规范集合重算，不能由调用方独立声明。
        self.assessment.evidence_available = bool(self.selected_evidence_ids)
        self.assessment.selected_evidence_count = len(self.selected_evidence_ids)
        self.assessment.dropped_evidence_count = len(self.dropped_evidence_ids)
        self.assessment.citation_count = len(self.citations)

        return self

    @property
    def success(self) -> bool:
        """返回 RAG 边界状态是否产生了可消费结果。

        部分成功仍返回 ``True``；该属性不表示证据语义充分，充分性必须读取
        ``assessment.status``。
        """

        return self.status in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.PARTIAL_SUCCESS,
        }

    @property
    def error_message(self) -> Optional[str]:
        """返回结构化错误的兼容消息视图。"""

        return self.error.message if self.error is not None else None


# Step-12 旧名称的临时导入别名。运行时只有一个证据契约，不能把别名视为第二套 Schema。
RAGEvidenceContractSchema = EvidenceBundleSchema


class RAGToolOutputSchema(SchemaBase):
    """表示 RAG 工具返回给当前 Agent/业务调用方的兼容输出。

    ``evidence`` 是唯一规范证据契约；``retrieved_chunks``、``context``、``citations`` 和
    ``trace`` 保留给尚未完成迁移的消费者。方案生成通常使用 retrieve-only，只有
    RAG-QA 工作流才可能填写 ``answer``。
    """

    # 当前 RAG 工具输出契约版本。
    schema_version: str = "rag_tool_output_v1"

    # 输出所属业务任务标识。
    task_id: str
    # 输出所属单次运行标识。
    run_id: str

    # RAG 工具本次执行状态。
    status: ExecutionStatus

    # 主查询。
    query: str
    # 实际参与检索的改写查询集合。
    rewritten_queries: List[str] = Field(default_factory=list)

    # 规范证据包；存在时应作为下游事实源。
    evidence: Optional[EvidenceBundleSchema] = None

    # 迁移期召回片段投影。
    retrieved_chunks: List[RetrievedChunkSchema] = Field(default_factory=list)
    # 迁移期最终上下文投影。
    context: Optional[RAGContextSchema] = None

    # 迁移期引用投影。
    citations: List[CitationSchema] = Field(default_factory=list)
    # 迁移期 RAG 轨迹投影。
    trace: Optional[RAGTraceSchema] = None

    # RAG-QA 工作流可选答案；retrieve-only 方案生成应保持为空。
    answer: Optional[str] = None

    # 不阻止返回但需要暴露的告警集合。
    warnings: List[WarningSchema] = Field(default_factory=list)
    # RAG 工具失败时的结构化错误。
    error: Optional[ErrorSchema] = None

    # 尚未进入稳定工具输出契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def project_evidence_contract(self) -> "RAGToolOutputSchema":
        """从规范证据包单向补齐尚未显式提供的兼容投影。

        Returns:
            context 和 citations 已按需补齐的当前输出对象。

        显式兼容字段不会被覆盖，避免迁移期调用方提供的受控投影丢失；新代码应直接
        读取 ``evidence``，不要依赖这些字段形成新的事实源。
        """

        # 第一步：没有规范证据包时保留旧调用方构造方式，不进行投影。
        if self.evidence is None:
            return self

        # 第二步：调用方未显式提供 context 时，从规范证据包单向投影。
        if not self.context:
            self.context = self.evidence.context

        # 第三步：调用方未显式提供 citations 时，复制规范引用集合。
        if not self.citations:
            self.citations = list(self.evidence.citations)

        return self
