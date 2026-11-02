"""定义证据来源引用和引用到生成声明的绑定关系。

``CitationSchema`` 描述证据来自哪份文档、哪个片段、表格行或页码；
``CitationBindingSchema`` 则说明该来源支持目标文档中的哪一条具体声明。两者分离后，
同一来源可支持多个声明，引用覆盖率和声明级可追溯性也能独立评测。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional

from pydantic import Field

from .common import SchemaBase


class CitationSchema(SchemaBase):
    """描述一个可追溯证据来源及其定位信息。"""

    # 当前引用来源契约版本。
    schema_version: str = "citation_v2"
    # 跨 RAG invocation 稳定的规范来源身份；RAG producer 负责生成，Application 只读。
    citation_ref: Optional[str] = None
    # 当前 EvidenceBundle 内部的局部渲染标识，例如 C1/C2；不是稳定业务身份。
    citation_id: str

    # 来源类别，例如文档片段、表格、政策条款或用户提供材料。
    source_type: str
    # 检索系统中的规范文档标识。
    doc_id: Optional[str] = None
    # 原始上传或文件系统中的文件标识。
    file_id: Optional[str] = None
    # 跨索引或跨系统使用的原始来源文档标识。
    source_document_id: Optional[str] = None

    # 父子分块体系中的父片段标识。
    parent_chunk_id: Optional[str] = None
    # 父子分块体系中的子片段标识。
    child_chunk_id: Optional[str] = None
    # 不区分父子层级时使用的通用片段标识。
    chunk_id: Optional[str] = None

    # 表格来源标识。
    table_id: Optional[str] = None
    # 引用涉及的具体表格行标识集合。
    row_ids: List[str] = Field(default_factory=list)

    # 来源文档或条目的标题。
    title: Optional[str] = None
    # 来源所在章节、条款或结构位置。
    section: Optional[str] = None
    # 来源起始页码。
    page_start: Optional[int] = None
    # 来源结束页码；单页来源可与起始页相同。
    page_end: Optional[int] = None

    # 可直接支撑声明的原文摘录。
    quote_text: Optional[str] = None
    # 对来源内容的规范化摘要，不替代原文证据。
    summary: Optional[str] = None
    # 当前系统对来源匹配或支持强度的置信值。
    confidence: Optional[float] = None

    # 索引版本、检索分数或其他尚未进入稳定引用契约的信息。
    extra: Dict[str, Any] = Field(default_factory=dict)


def citation_source_identity(citation: CitationSchema) -> str:
    """计算 Citation 的稳定来源身份。

    该 payload 与历史 ``DocumentCitationRegistry._identity`` 完全一致，Phase 4B 将其
    抽取为共享算法，使 RAG producer 与 Application document projection 复用同一身份。
    ``citation_id`` 只在来源定位事实全部缺失时作为兼容兜底，不参与正常来源身份。
    """

    payload: Dict[str, Any] = {
        "source_document_id": citation.source_document_id or citation.doc_id,
        "file_id": citation.file_id,
        "parent_chunk_id": citation.parent_chunk_id,
        "child_chunk_id": citation.child_chunk_id,
        "chunk_id": citation.chunk_id,
        "table_id": citation.table_id,
        "row_ids": list(citation.row_ids),
        "page_start": citation.page_start,
        "page_end": citation.page_end,
        "quote_text": " ".join((citation.quote_text or "").split()),
        "title": citation.title,
        "section": citation.section,
    }
    if not any(value for value in payload.values()):
        payload["original_citation_id"] = citation.citation_id
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prompt_visible_citation_text(citation: Any) -> str:
    """Return only explicitly governed Citation text for Writer prompts.

    Raw ``quote_text`` is grounding/audit evidence and must never be used as an
    implicit fallback when governance produced no prompt-visible projection.
    """

    extra = getattr(citation, "extra", None)
    extra = dict(extra) if isinstance(extra, Mapping) else {}
    direct = str(extra.get("prompt_visible_text") or "").strip()
    if direct:
        return direct
    candidate = extra.get("prompt_evidence_candidate_v1")
    candidate = dict(candidate) if isinstance(candidate, Mapping) else {}
    return str(candidate.get("prompt_visible_text") or "").strip()


class CitationBindingSchema(SchemaBase):
    """把一个来源引用绑定到目标文档中的具体声明位置。"""

    # 当前引用绑定契约版本。
    schema_version: str = "citation_binding_v1"

    # 绑定关系唯一标识。
    binding_id: str
    # 被绑定的 ``CitationSchema.citation_id``。
    citation_id: str

    # 目标生成文档标识。
    target_document_id: str
    # 目标章节标识。
    target_section_id: str
    # 目标段落标识。
    target_paragraph_id: str
    # 目标声明标识，是引用覆盖和事实核验的最小业务单位。
    target_claim_id: str
    # 引用标记在该声明或段落中的索引位置。
    target_marker_index: Optional[int] = None
    # 同一目标位置中引用标记的显示序号。
    marker_ordinal: Optional[int] = None
    # 引用标记在目标文本中的起始字符偏移。
    target_marker_start: Optional[int] = None
    # 引用标记在目标文本中的结束字符偏移。
    target_marker_end: Optional[int] = None

    # 冗余保存来源文档标识，便于不联表时快速追踪。
    source_document_id: Optional[str] = None
    # 直接支持声明的来源片段标识。
    source_chunk_id: Optional[str] = None
    # 来源片段所属父片段标识。
    source_parent_chunk_id: Optional[str] = None

    # 被该引用支持的目标声明文本快照。
    claim_text: str
    # 实际用于支持声明的来源原文快照。
    quote_text: Optional[str] = None
    # 该来源对目标声明的支持置信值。
    confidence: Optional[float] = None

    # 绑定算法、验证结果和其他扩展元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)
