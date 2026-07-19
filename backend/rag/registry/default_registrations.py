# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：build_default_component_registry。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Built-in plugin registration for the current RAG runtime."""

from __future__ import annotations

from rag.plugins.candidate_enrichers import ParentChildCandidateEnricher
from rag.plugins.chunkers import (
    FixedParentChildChunkerPlugin,
    HeadingParentChildChunkerPlugin,
    ParagraphParentChildChunkerPlugin,
    RecursiveParentChildChunkerPlugin,
)
from rag.plugins.context_packers import (
    DefaultContextPacker,
    LostInMiddleContextPacker,
)
from rag.plugins.fusions import ChildRRFFusionPlugin, ParentRRFFusionPlugin
from rag.plugins.correction_gates import EvidenceSufficiencyCorrectionGate
from rag.plugins.corrective_query_planners import SectionGapCorrectiveQueryPlanner
from rag.plugins.evidence_assessors import (
    CRAGEvidenceAssessorPlugin,
    NoOpEvidenceAssessorPlugin,
)
from rag.plugins.query_transformers import (
    HyDEQueryTransformer,
    IdentityQueryTransformer,
    MultiQueryTransformer,
)
from rag.plugins.retrievers import (
    BM25ChildRetrieverPlugin,
    MilvusDenseChildRetrieverPlugin,
)
from rag.plugins.rerankers import (
    BGEParentCrossEncoderRerankerPlugin,
    NoOpParentRerankerPlugin,
)
from rag.registry.component_registry import ComponentRegistry


# 阅读注释（函数）：构建 default component 注册表。
def build_default_component_registry() -> ComponentRegistry[object]:
    """构建 default component 注册表。

    返回:
        ComponentRegistry[object]

    阅读提示:
        主要直接调用：ComponentRegistry, registry.register。
    """
    registry: ComponentRegistry[object] = ComponentRegistry()

    registry.register(
        category="chunker",
        name="fixed_parent_child",
        version="v1",
        builder=FixedParentChildChunkerPlugin,
    )
    registry.register(
        category="chunker",
        name="recursive_parent_child",
        version="v1",
        builder=RecursiveParentChildChunkerPlugin,
    )
    registry.register(
        category="chunker",
        name="heading_parent_child",
        version="v1",
        builder=HeadingParentChildChunkerPlugin,
    )
    registry.register(
        category="chunker",
        name="paragraph_parent_child",
        version="v1",
        builder=ParagraphParentChildChunkerPlugin,
    )

    registry.register(
        category="query_transformer",
        name="identity",
        version="v1",
        builder=IdentityQueryTransformer,
    )
    registry.register(
        category="query_transformer",
        name="multi_query",
        version="v1",
        builder=MultiQueryTransformer,
    )
    registry.register(
        category="query_transformer",
        name="hyde",
        version="v1",
        builder=HyDEQueryTransformer,
    )

    registry.register(
        category="retriever",
        name="milvus_dense_child",
        version="v1",
        builder=MilvusDenseChildRetrieverPlugin,
    )
    registry.register(
        category="retriever",
        name="bm25_child",
        version="v1",
        builder=BM25ChildRetrieverPlugin,
    )

    registry.register(
        category="source_fusion",
        name="rrf_child",
        version="v1",
        builder=ChildRRFFusionPlugin,
    )
    registry.register(
        category="query_fusion",
        name="rrf_parent",
        version="v1",
        builder=ParentRRFFusionPlugin,
    )

    registry.register(
        category="candidate_enricher",
        name="parent_child",
        version="v1",
        builder=ParentChildCandidateEnricher,
    )

    registry.register(
        category="reranker",
        name="bge_parent_cross_encoder",
        version="v1",
        builder=BGEParentCrossEncoderRerankerPlugin,
    )
    registry.register(
        category="reranker",
        name="noop_parent",
        version="v1",
        builder=NoOpParentRerankerPlugin,
    )


    registry.register(
        category="evidence_assessor",
        name="crag",
        version="v1",
        builder=CRAGEvidenceAssessorPlugin,
    )
    registry.register(
        category="evidence_assessor",
        name="noop_evidence",
        version="v1",
        builder=NoOpEvidenceAssessorPlugin,
    )
    registry.register(
        category="corrective_retrieval_gate",
        name="evidence_sufficiency",
        version="v1",
        builder=EvidenceSufficiencyCorrectionGate,
    )
    registry.register(
        category="corrective_query_planner",
        name="section_gap",
        version="v1",
        builder=SectionGapCorrectiveQueryPlanner,
    )

    registry.register(
        category="context_packer",
        name="default",
        version="v1",
        builder=DefaultContextPacker,
    )
    registry.register(
        category="context_packer",
        name="lost_in_middle",
        version="v1",
        builder=LostInMiddleContextPacker,
    )
    return registry
