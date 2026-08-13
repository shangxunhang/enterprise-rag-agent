"""Deterministic project fact-record chunking without LLM extraction."""

from __future__ import annotations

import json
import re
from collections import OrderedDict, defaultdict
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
from rag.plugins.chunkers.table_row_record import (
    parse_markdown_table,
    parse_structured_table,
)


_KEY_VALUE_RE = re.compile(r"^\s*([^：:\n]{1,60})[：:]\s*(.+?)\s*$")
_NUMBERED_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)*[\.、)]?|[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)\s*(.+)$"
)
_UNIT_RE = re.compile(
    r"(?P<number>-?\d+(?:\.\d+)?)\s*(?P<unit>万元|亿元|元|台|套|个|项|月|年|天|%|TB|GB|MB|核|卡|路|套/年|吨|tCO2e|kgCO2e)\b",
    re.IGNORECASE,
)
_TOC_RE = re.compile(r"(?:\.{2,}|…{2,}|\s)(?:\d+|[IVXLCDM]+)\s*$")


def infer_fact_status(
    text: str,
    profile: dict[str, Any],
    *,
    predicate: str | None = None,
    fact_category: str | None = None,
) -> str:
    """Infer status only from fact-bearing wording or authority semantics.

    Process descriptions such as ``用户已经确认告知书`` and procurement
    obligations such as ``由成交供应商支付`` are not project-fact status
    declarations.  Authority fallback is restricted to predicates that denote
    investment, schedule, scale, quantity or configuration rather than any
    occurrence of the generic word ``建设``.
    """

    value = str(text or "")
    predicate_value = str(predicate or "")
    category = str(fact_category or infer_fact_category(predicate_value or value))
    if re.search(
        r"(?:最终|正式)?(?:确认|核定)(?:投资|金额|数量|配置|工期|方案|结果)"
        r"|(?:实际|最终)(?:投资|金额|数量|配置|工期)"
        r"|已建成|现状(?:为|是)",
        value,
    ):
        return "confirmed"
    if re.search(
        r"(?:决定|决议|批复)(?:采用|建设|采购|投资|配置|实施)"
        r"|(?:中标|成交)(?:结果|金额)(?:为|是|：|:)"
        r"|(?:中标|成交)供应商(?:为|是|：|:)"
        r"|确定采用|审议通过",
        value,
    ):
        return "decided"
    if re.search(r"估算|概算|预算|暂估|测算|约为|预计投资", value):
        return "estimated"
    if re.search(r"拟建设|拟建|拟采购|拟采用|拟新增|建议建设|建议采用", value):
        return "proposed"
    if re.search(r"计划建设|计划采购|计划于|规划建设|将建设|预计完成", value):
        return "planned"

    authority = profile.get("authority") or {}
    source_form = profile.get("source_document_form")
    if (
        authority.get("level") in {"reference", "draft"}
        and source_form == "feasibility_study"
    ):
        if category == "cost" and re.search(
            r"投资|金额|费用|预算|概算|估算", predicate_value
        ):
            return "estimated"
        if category == "schedule" and re.search(
            r"工期|进度|实施周期|建设周期|计划(?:时间|日期|安排|进度|工期)",
            predicate_value,
        ):
            return "planned"
        if re.search(
            r"建设规模|建设内容|设备配置|系统配置|采购清单|功能清单|数量",
            predicate_value,
        ) or re.search(r"建设\s*\d+", value):
            return "proposed"
    return "unknown"


def infer_fact_category(text: str) -> str:
    value = str(text or "")
    if re.search(r"投资|金额|费用|预算|概算|估算|单价|造价", value):
        return "cost"
    if re.search(r"工期|进度|计划|日期|时间|月|年", value):
        return "schedule"
    if re.search(r"配置|型号|规格|CPU|GPU|内存|存储|带宽|节点", value, re.I):
        return "configuration"
    if re.search(r"数量|台|套|个|项|路|卡", value):
        return "quantity"
    if re.search(r"决定|决议|批复|成交|中标", value):
        return "decision"
    if re.search(r"要求|应当|必须|不得|需", value):
        return "requirement"
    return "general"


def extract_unit(value: str) -> str | None:
    match = _UNIT_RE.search(str(value or ""))
    return match.group("unit") if match else None


class ProjectFactRecordChunker:
    """Extract deterministic facts and disclose unsupported text fallback."""

    STRATEGY = "fact_record"

    def __init__(
        self,
        *,
        parent_chunk_size: int = 2400,
        parent_chunk_overlap: int = 0,
        child_chunk_size: int = 1000,
        child_chunk_overlap: int = 0,
        unit: str = "char",
        parent_chunk_version: str = DEFAULT_PARENT_CHUNK_VERSION,
        child_chunk_version: str = DEFAULT_CHILD_CHUNK_VERSION,
        deterministic_created_at: str | None = None,
        chunker_name: str = "project_fact_record",
        build_context: Any = None,
    ) -> None:
        self.parent_chunk_size = int(parent_chunk_size)
        self.child_chunk_size = int(child_chunk_size)
        self.parent_chunk_version = str(parent_chunk_version)
        self.child_chunk_version = str(child_chunk_version)
        self.created_at = deterministic_created_at
        self.chunker_name = chunker_name
        self._stats: dict[str, int] = {}

    def _fact_child(
        self,
        *,
        unit: dict[str, Any],
        path: list[str],
        record_id: str,
        subject: str,
        predicate: str,
        value: str,
        profile: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        evidence_text: str | None = None,
    ) -> LogicalChildSpec:
        source_text = str(unit.get("text") or "")
        rendered_text = str(evidence_text or source_text)
        category = infer_fact_category(f"{predicate} {value}")
        status = infer_fact_status(
            rendered_text,
            profile,
            predicate=predicate,
            fact_category=category,
        )
        payload = {
            "record_id": record_id,
            "fact_category": category,
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "unit": extract_unit(value),
            "fact_status": status,
            "source_span": {
                "unit_order_start": unit.get("unit_order"),
                "unit_order_end": unit.get("unit_order"),
                "text": rendered_text,
            },
            "source_unit_ids": [unit.get("unit_id")],
            "source_page": unit.get("page_start"),
        }
        payload.update(metadata or {})
        return LogicalChildSpec(
            text=rendered_text,
            units=[unit],
            representation_type="fact_record",
            structure_path=path,
            metadata=payload,
            actual_strategy=self.STRATEGY,
        )

    def _chunk_doc(
        self, doc_id: str, records: list[dict[str, Any]]
    ) -> ParentChildChunkResult:
        units = normalize_units(records)
        if not units:
            return ParentChildChunkResult(parents=[], children=[])
        profile = profile_from_units(units)
        heading_stack: list[tuple[int, str]] = []
        buckets: OrderedDict[str, dict[str, Any]] = OrderedDict()
        unsupported: dict[str, list[dict[str, Any]]] = defaultdict(list)
        toc_mode = False
        toc_filtered = 0
        fact_count = 0
        fallback_count = 0

        def bucket(path: list[str]) -> dict[str, Any]:
            key = " > ".join(path) or "document_preamble"
            if key not in buckets:
                buckets[key] = {"path": list(path), "children": [], "units": []}
            return buckets[key]

        for unit in units:
            text = str(unit.get("text") or "").strip()
            compact = "".join(text.split())
            if compact in {"目录", "目次"}:
                toc_mode = True
                toc_filtered += 1
                continue
            heading = detect_heading(text, str(unit.get("unit_type") or ""))
            if toc_mode:
                if _TOC_RE.search(text):
                    toc_filtered += 1
                    continue
                if heading is None:
                    toc_filtered += 1
                    continue
                toc_mode = False
            if heading is not None:
                heading_stack = update_heading_stack(heading_stack, heading)
                bucket([title for _, title in heading_stack])["units"].append(unit)
                continue

            path = [title for _, title in heading_stack]
            current = bucket(path)
            current["units"].append(unit)
            subject = path[-1] if path else str(profile.get("document_id") or doc_id)

            if unit.get("unit_type") == "table":
                unit_extra = unit.get("extra")
                structured_table = (
                    unit_extra.get("structured_table")
                    if isinstance(unit_extra, dict)
                    else None
                )
                if isinstance(structured_table, dict):
                    _, rows = parse_structured_table(structured_table)
                else:
                    _, rows = parse_markdown_table(text)
                if rows:
                    for row in rows:
                        fact_count += 1
                        value = json.dumps(
                            row.normalized_fields,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        rendered_row = "\n".join(
                            f"{key}: {item_value}"
                            for key, item_value in row.normalized_fields.items()
                        )
                        current["children"].append(
                            self._fact_child(
                                unit=unit,
                                path=[*path, f"table_row_{row.row_index}"],
                                record_id=f"{doc_id}_fact_{fact_count:06d}",
                                subject=subject,
                                predicate="table_row",
                                value=value,
                                profile=profile,
                                metadata={
                                    "table_header": row.header,
                                    "table_cells": row.cells,
                                    "table_normalized_fields": row.normalized_fields,
                                    "table_row_index": row.row_index,
                                },
                                evidence_text=rendered_row,
                            )
                        )
                    continue

            key_value = _KEY_VALUE_RE.match(text)
            if key_value:
                fact_count += 1
                predicate = key_value.group(1).strip()
                value = key_value.group(2).strip()
                current["children"].append(
                    self._fact_child(
                        unit=unit,
                        path=path,
                        record_id=f"{doc_id}_fact_{fact_count:06d}",
                        subject=subject,
                        predicate=predicate,
                        value=value,
                        profile=profile,
                    )
                )
                continue

            numbered = _NUMBERED_RE.match(text)
            if numbered and len(text) <= 1200:
                fact_count += 1
                item_id = numbered.group(1).strip()
                value = numbered.group(2).strip()
                current["children"].append(
                    self._fact_child(
                        unit=unit,
                        path=[*path, item_id],
                        record_id=f"{doc_id}_fact_{fact_count:06d}",
                        subject=subject,
                        predicate="numbered_requirement",
                        value=value,
                        profile=profile,
                    )
                )
                continue

            unsupported[" > ".join(path) or "document_preamble"].append(unit)

        for key, unsupported_units in unsupported.items():
            target = buckets[key]
            for index, group in enumerate(
                chunk_text_units(unsupported_units, max_chars=self.child_chunk_size),
                start=1,
            ):
                fallback_count += 1
                target["children"].append(
                    LogicalChildSpec(
                        text=join_unit_text(group),
                        units=list(group),
                        representation_type="text_evidence",
                        structure_path=[*target["path"], f"fallback_{index}"],
                        metadata={
                            "record_id": f"{doc_id}_fallback_{fallback_count:06d}",
                            "fact_category": "unstructured_text",
                            "subject": target["path"][-1] if target["path"] else doc_id,
                            "predicate": None,
                            "value": None,
                            "unit": None,
                            "fact_status": "unknown",
                            "source_span": {
                                "unit_order_start": group[0].get("unit_order"),
                                "unit_order_end": group[-1].get("unit_order"),
                                "text": join_unit_text(group),
                            },
                            "source_unit_ids": [u.get("unit_id") for u in group],
                            "source_page": group[0].get("page_start"),
                        },
                        fallback_used=True,
                        fallback_reason="unsupported_fact_structure",
                        fallback_strategy="text_evidence",
                        actual_strategy="text_evidence",
                    )
                )

        logical_parents: list[LogicalParentSpec] = []
        for section_index, item in enumerate(buckets.values(), start=1):
            children = item["children"]
            if not children:
                continue
            path = item["path"]
            logical_parents.append(
                LogicalParentSpec(
                    children=children,
                    units=item["units"],
                    representation_type="fact_section",
                    structure_path=path,
                    title=path[-1] if path else None,
                    metadata={
                        "section_index": section_index,
                        "heading_path": path,
                        "structured_fact_count": sum(
                            child.representation_type == "fact_record"
                            for child in children
                        ),
                        "fallback_chunk_count": sum(
                            child.fallback_used for child in children
                        ),
                    },
                    fallback_used=all(child.fallback_used for child in children),
                    fallback_reason=(
                        "unsupported_fact_structure"
                        if all(child.fallback_used for child in children)
                        else None
                    ),
                    fallback_strategy=(
                        "text_evidence"
                        if all(child.fallback_used for child in children)
                        else None
                    ),
                    actual_strategy=(
                        "text_evidence"
                        if all(child.fallback_used for child in children)
                        else self.STRATEGY
                    ),
                )
            )

        result = materialize_document(
            doc_id=doc_id,
            parents=logical_parents,
            original_strategy=self.STRATEGY,
            parent_chunk_version=self.parent_chunk_version,
            child_chunk_version=self.child_chunk_version,
            created_at=self.created_at,
            chunker_name=self.chunker_name,
            max_parent_chars=self.parent_chunk_size,
        )
        self._stats = {
            "fact_record_count": fact_count,
            "fallback_chunk_count": fallback_count,
            "toc_units_filtered": toc_filtered,
        }
        return result

    def chunk_records(
        self, records: Iterable[dict[str, Any]]
    ) -> ParentChildChunkResult:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            doc_id = str(record.get("doc_id") or "").strip()
            if not doc_id:
                raise ValueError("fact-record chunking requires doc_id")
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


__all__ = [
    "ProjectFactRecordChunker",
    "extract_unit",
    "infer_fact_category",
    "infer_fact_status",
]
