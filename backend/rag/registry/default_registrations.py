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
from rag.plugins.evidence_graders import (
    CRAGCorrectiveEvidenceGraderPlugin,
    CRAGLiteEvidenceGraderPlugin,
    NoOpEvidenceGraderPlugin,
)
from rag.plugins.generation_checkers import (
    NoOpGenerationCheckerPlugin,
    SelfRAGLiteGenerationCheckerPlugin,
)
from rag.plugins.repair_strategies import (
    LocalRewriteRepairStrategyPlugin,
    NoOpRepairStrategyPlugin,
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
from rag.routing.policy import ExplainableRuleProfileRouterPlugin


def build_default_component_registry() -> ComponentRegistry[object]:
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
        category="profile_router",
        name="explainable_rules",
        version="v1",
        builder=ExplainableRuleProfileRouterPlugin,
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
        category="fusion",
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
        category="evidence_grader",
        name="crag_lite",
        version="v1",
        builder=CRAGLiteEvidenceGraderPlugin,
    )
    registry.register(
        category="evidence_grader",
        name="crag_corrective",
        version="v1",
        builder=CRAGCorrectiveEvidenceGraderPlugin,
    )
    registry.register(
        category="evidence_grader",
        name="noop_evidence",
        version="v1",
        builder=NoOpEvidenceGraderPlugin,
    )

    registry.register(
        category="generation_checker",
        name="self_rag_lite",
        version="v1",
        builder=SelfRAGLiteGenerationCheckerPlugin,
    )
    registry.register(
        category="generation_checker",
        name="noop_generation",
        version="v1",
        builder=NoOpGenerationCheckerPlugin,
    )

    registry.register(
        category="repair_strategy",
        name="local_rewrite",
        version="v1",
        builder=LocalRewriteRepairStrategyPlugin,
    )
    registry.register(
        category="repair_strategy",
        name="noop_repair",
        version="v1",
        builder=NoOpRepairStrategyPlugin,
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
