"""Deterministic template-section chunking."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from rag.configs.SchemaConfig import (
    DEFAULT_CHILD_CHUNK_VERSION,
    DEFAULT_PARENT_CHUNK_VERSION,
)
from rag.ports.chunking import ParentChildChunkResult
from rag.plugins.chunkers.structured_common import (
    LogicalChildSpec,
    LogicalParentSpec,
    chunk_text_units,
    detect_heading,
    join_unit_text,
    materialize_document,
    normalize_units,
    profile_from_units,
    update_heading_stack,
)


_PLACEHOLDER_PATTERNS = (
    re.compile(r"\{\{\s*([^{}]+?)\s*\}\}"),
    re.compile(r"\$\{\s*([^{}]+?)\s*\}"),
    re.compile(r"【\s*([^【】]+?)\s*】"),
    re.compile(r"<\s*([^<>]+?)\s*>"),
    re.compile(r"_{3,}"),
    re.compile(r"\bX{2,}\b", re.I),
    re.compile(r"待填写|请填写|此处填写|根据实际情况填写"),
)
_REQUIRED_RE = re.compile(r"(?:应包括|应包含|需填写|必须填写|必填|填写内容|填写要求)[：:]?\s*(.*)")
_STYLE_RE = re.compile(r"字体|字号|格式|样式|排版|说明|编制要求|写作要求|参考|示例|例如")
_EXAMPLE_RE = re.compile(r"示例|例如|参考项目|样例|假设")
_EXAMPLE_VALUE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:万元|亿元|元|台|套|个|项|月|年|%|TB|GB|MB)?")


def _extract_placeholders(text: str) -> list[str]:
    values: list[str] = []
    for pattern in _PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip() if match.lastindex else match.group(0).strip()
            if value and value not in values:
                values.append(value)
    return values


def _extract_required_fields(text: str) -> list[str]:
    values: list[str] = []
    for line in str(text or "").splitlines():
        match = _REQUIRED_RE.search(line)
        if match:
            value = match.group(1).strip() or line.strip()
            if value and value not in values:
                values.append(value)
        key_value = re.match(r"^\s*([^：:]{1,50})[：:]\s*(?:_{3,}|待填写|请填写)?\s*$", line)
        if key_value:
            value = key_value.group(1).strip()
            if value and value not in values:
                values.append(value)
    return values


def _style_hints(text: str) -> list[str]:
    values: list[str] = []
    for line in str(text or "").splitlines():
        value = line.strip()
        if value and _STYLE_RE.search(value) and value not in values:
            values.append(value)
    return values[:20]


class TemplateSectionChunker:
    """Chunk templates by section while keeping example values non-factual."""

    STRATEGY = "template_section"

    def __init__(
        self,
        *,
        parent_chunk_size: int = 2600,
        parent_chunk_overlap: int = 0,
        child_chunk_size: int = 900,
        child_chunk_overlap: int = 0,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str = "template_section",
        build_context: Any = None,
    ) -> None:
        self.parent_chunk_size = int(parent_chunk_size)
        self.child_chunk_size = int(child_chunk_size)
        self.parent_chunk_version = str(parent_chunk_version)
        self.child_chunk_version = str(child_chunk_version)
        self.created_at = deterministic_created_at
        self.chunker_name = chunker_name
        self._stats: dict[str, int] = {}

    def _chunk_doc(
        self, doc_id: str, records: list[dict[str, Any]]
    ) -> ParentChildChunkResult:
        units = normalize_units(records)
        if not units:
            return ParentChildChunkResult(parents=[], children=[])
        profile = profile_from_units(units)
        allowed = set(
            (profile.get("content_capabilities") or {}).get(
                "allowed_claim_types", []
            )
        )
        if profile.get("kb_id") != "template_style_kb":
            raise ValueError("template chunks must use template_style_kb")
        if profile.get("evidence_role") != "template_style":
            raise ValueError("template chunks require evidence_role=template_style")
        if allowed - {"template_structure", "style_guidance"}:
            raise ValueError("template profile cannot carry factual claim capability")

        heading_stack: list[tuple[int, str]] = []
        sections: list[tuple[list[str], str | None, int | None, list[dict[str, Any]]]] = []
        current_path: list[str] = []
        current_title: str | None = None
        current_level: int | None = None
        current_units: list[dict[str, Any]] = []
        heading_count = 0

        def flush() -> None:
            nonlocal current_units
            if current_units:
                sections.append(
                    (list(current_path), current_title, current_level, current_units)
                )
                current_units = []

        for unit in units:
            text = str(unit.get("text") or "").strip()
            heading = detect_heading(text, str(unit.get("unit_type") or ""))
            if heading is not None:
                flush()
                heading_count += 1
                heading_stack = update_heading_stack(heading_stack, heading)
                current_path = [title for _, title in heading_stack]
                current_title = heading.title
                current_level = heading.level
                current_units = [unit]
            else:
                current_units.append(unit)
        flush()
        if not sections:
            sections = [([], None, None, units)]

        parents: list[LogicalParentSpec] = []
        fallback_document = heading_count == 0
        placeholder_total = 0
        required_total = 0
        example_sections = 0
        for section_index, (path, title, level, section_units) in enumerate(
            sections, start=1
        ):
            children: list[LogicalChildSpec] = []
            groups = chunk_text_units(section_units, max_chars=self.child_chunk_size)
            for part_index, group in enumerate(groups, start=1):
                text = join_unit_text(group)
                placeholders = _extract_placeholders(text)
                required_fields = _extract_required_fields(text)
                hints = _style_hints(text)
                is_example = bool(_EXAMPLE_RE.search(text))
                example_values = _EXAMPLE_VALUE_RE.findall(text) if is_example else []
                placeholder_total += len(placeholders)
                required_total += len(required_fields)
                example_sections += int(is_example)
                child_path = list(path)
                if len(groups) > 1:
                    child_path.append(f"part_{part_index}")
                children.append(
                    LogicalChildSpec(
                        text=text,
                        units=list(group),
                        representation_type="template_section",
                        structure_path=child_path,
                        metadata={
                            "section_path": child_path,
                            "section_title": title,
                            "required_fields": required_fields,
                            "placeholder_fields": placeholders,
                            "style_hints": hints,
                            "is_example_content": is_example,
                            "example_values": example_values,
                            "template_claim_types": sorted(allowed),
                        },
                        fallback_used=fallback_document,
                        fallback_reason=(
                            "no_template_heading_structure"
                            if fallback_document
                            else None
                        ),
                        fallback_strategy=(
                            "document_template_section"
                            if fallback_document
                            else None
                        ),
                        actual_strategy=(
                            "document_template_section"
                            if fallback_document
                            else self.STRATEGY
                        ),
                    )
                )
            parents.append(
                LogicalParentSpec(
                    children=children,
                    units=section_units,
                    representation_type="template_section_group",
                    structure_path=path,
                    title=title,
                    metadata={
                        "section_path": path,
                        "section_title": title,
                        "heading_level": level,
                        "section_index": section_index,
                    },
                    fallback_used=fallback_document,
                    fallback_reason=(
                        "no_template_heading_structure"
                        if fallback_document
                        else None
                    ),
                    fallback_strategy=(
                        "document_template_section"
                        if fallback_document
                        else None
                    ),
                    actual_strategy=(
                        "document_template_section"
                        if fallback_document
                        else self.STRATEGY
                    ),
                )
            )

        result = materialize_document(
            doc_id=doc_id,
            parents=parents,
            original_strategy=self.STRATEGY,
            parent_chunk_version=self.parent_chunk_version,
            child_chunk_version=self.child_chunk_version,
            created_at=self.created_at,
            chunker_name=self.chunker_name,
        )
        self._stats = {
            "heading_count": heading_count,
            "placeholder_field_count": placeholder_total,
            "required_field_count": required_total,
            "example_section_count": example_sections,
            "fallback_document_count": int(fallback_document),
        }
        return result

    def chunk_records(
        self, records: Iterable[dict[str, Any]]
    ) -> ParentChildChunkResult:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            doc_id = str(record.get("doc_id") or "").strip()
            if not doc_id:
                raise ValueError("template chunking requires doc_id")
            groups[doc_id].append(dict(record))
        parents: list[dict[str, Any]] = []
        children: list[dict[str, Any]] = []
        for doc_id in sorted(groups):
            result = self._chunk_doc(doc_id, groups[doc_id])
            parents.extend(result.parents)
            children.extend(result.children)
        return ParentChildChunkResult(parents=parents, children=children)

    def execution_metadata(self) -> dict[str, Any]:
        return {"strategy": self.STRATEGY, **self._stats}


__all__ = ["TemplateSectionChunker"]
