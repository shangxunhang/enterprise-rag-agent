"""当前 RAG 运行时的内置插件注册清单。

该模块只声明默认可用的 ``category/name@version`` 与 Builder 映射，不选择实际策略；
静态检索配置决定一次运行启用哪些组件及其参数。
"""

from __future__ import annotations

from rag.plugins.candidate_enrichers import ParentChildCandidateEnricher
from rag.plugins.chunkers import (
    FixedParentChildChunkerPlugin,
    HeadingParentChildChunkerPlugin,
    ParagraphParentChildChunkerPlugin,
    RecursiveParentChildChunkerPlugin,
    TypeAwareParentChildChunkerPlugin,
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


def build_default_component_registry() -> ComponentRegistry[object]:
    """注册内置分块、Query 变换、召回、融合、补全、重排、纠错和上下文打包插件。"""
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
        category="chunker",
        name="type_aware_parent_child",
        version="v1",
        builder=TypeAwareParentChildChunkerPlugin,
    )
    registry.register(
        category="chunker",
        name="type_aware_parent_child",
        version="v2",
        builder=TypeAwareParentChildChunkerPlugin,
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
