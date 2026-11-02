"""Built-in fixed and evidence-aware structured chunkers."""

from rag.plugins.chunkers.heading_parent_paragraph_child import HeadingParagraphChunker
from rag.plugins.chunkers.policy_article_clause import PolicyArticleClauseChunker
from rag.plugins.chunkers.project_fact_record import ProjectFactRecordChunker
from rag.plugins.chunkers.table_row_record import TableRowRecordChunker
from rag.plugins.chunkers.template_section import TemplateSectionChunker
from rag.plugins.chunkers.plugin import (
    FixedParentChildChunkerPlugin,
    HeadingParentChildChunkerPlugin,
    ParagraphParentChildChunkerPlugin,
    RecursiveParentChildChunkerPlugin,
    TypeAwareParentChildChunkerPlugin,
)

__all__ = [
    "FixedParentChildChunkerPlugin",
    "RecursiveParentChildChunkerPlugin",
    "HeadingParentChildChunkerPlugin",
    "ParagraphParentChildChunkerPlugin",
    "TypeAwareParentChildChunkerPlugin",
    "HeadingParagraphChunker",
    "PolicyArticleClauseChunker",
    "ProjectFactRecordChunker",
    "TableRowRecordChunker",
    "TemplateSectionChunker",
]
