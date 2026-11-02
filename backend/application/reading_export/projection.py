"""Pure display projection for an already verified Target Markdown document.

The caller proves Artifact, output, publication, TerminalOutcome and optional
source-display ownership before calling this module. No business owner is run
and no canonical content is changed here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from application.terminal_status_view import STATUS_PROJECTION_VERSION
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
    DocumentCitationProjection,
    scan_citation_markers,
)
from core.hashing import canonical_sha256


READING_RULE_VERSION = "src-rev-03-markdown-reading-v1"
TITLE_RULE_VERSION = "reading-title-suppression-v1"
_HEADING = re.compile(r"## ([^\r\n]+)\Z")
_TITLE_MARKUP = frozenset("\\[]`*_<>!~#$&")


@dataclass(frozen=True)
class ReadingProjection:
    reading_markdown: str
    report_markdown: str
    title_transformations: tuple[dict[str, Any], ...]
    citation_diagnostics: tuple[dict[str, Any], ...]
    source_display_missing: tuple[dict[str, Any], ...]
    statistics: dict[str, int]
    section_completeness: dict[str, Any]
    used_aliases: tuple[str, ...]
    source_display_hashes: tuple[dict[str, str | None], ...]


def _markdown_text(value: str) -> str:
    """Escape only exporter-authored fields, never source Section content."""
    return (value.replace("\\", "\\\\").replace("\r", " ").replace("\n", " ")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")
            .replace("`", "\\`").replace("*", "\\*").replace("_", "\\_")
            .replace("#", "\\#"))


def _suppress_duplicate_title(content: str, title: str, section_id: str) -> tuple[str, dict[str, Any]]:
    """Remove at most one exact, unindented `## title` first nonblank line.

    This v1 rule deliberately does not recognize indentation, closing hashes,
    alternate heading levels, inline Markdown/entity metacharacters, leading
    or trailing title whitespace, or numbered headings. Even an exact
    structured title containing markup is retained because it is not plain
    heading text.
    Offsets are Python Unicode codepoint indices in the original content.
    """
    transformation: dict[str, Any] = {
        "section_id": section_id,
        "section_title": title,
        "rule_version": TITLE_RULE_VERSION,
        "transformed": False,
        "source_character_range": None,
        "original_heading_text": None,
        "offset_unit": "unicode_codepoint",
    }
    cursor = 0
    for line in content.splitlines(keepends=True):
        line_text = line.rstrip("\r\n")
        if not line_text.strip():
            cursor += len(line)
            continue
        match = _HEADING.fullmatch(line_text)
        if (match is None or match.group(1) != title or title != title.strip()
                or any(character in _TITLE_MARKUP for character in title)):
            return content, transformation
        end = cursor + len(line)
        transformation.update({
            "transformed": True,
            "source_character_range": {"start": cursor, "end": end},
            "original_heading_text": content[cursor:end],
        })
        return content[:cursor] + content[end:], transformation
    return content, transformation


def _source_field(origin: Mapping[str, Any], locator: Mapping[str, Any], key: str) -> str | None:
    """Use only text already frozen in the catalog origin, when present."""
    for record in (origin, locator):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _source_location(origin: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    locator = origin.get("source_locator")
    if not isinstance(locator, Mapping):
        locator = {}
    missing = []
    parts = []
    section = _source_field(origin, locator, "section")
    if section is not None:
        parts.append("章节 " + section)
    else:
        missing.append("section")
    start, end = locator.get("page_start"), locator.get("page_end")
    if isinstance(start, int) and not isinstance(start, bool) and start > 0:
        if isinstance(end, int) and not isinstance(end, bool) and end > start:
            parts.append(f"第 {start}–{end} 页")
        elif end is None or end == start:
            parts.append(f"第 {start} 页")
        else:
            missing.append("page")
    else:
        missing.append("page")
    table_id = locator.get("table_id")
    if isinstance(table_id, str) and table_id:
        parts.append("表格 " + table_id)
    return "；".join(parts) if parts else "未记录", tuple(missing)


def _require_sections(payload: Mapping[str, Any], projection: DocumentCitationProjection) -> list[tuple[str, str, str]]:
    included = payload.get("included_sections")
    if not isinstance(included, list) or len(included) != len(projection.section_alias_maps):
        raise ValueError("final included Section/citation map cardinality differs")
    sections = []
    for item, alias_map in zip(included, projection.section_alias_maps):
        if not isinstance(item, Mapping) or not isinstance(alias_map, Mapping):
            raise ValueError("final Section/citation map is malformed")
        visible = item.get("visible_payload")
        key = item.get("section_key")
        if (not isinstance(key, str) or not key or not isinstance(visible, Mapping)
                or alias_map.get("section_key") != key):
            raise ValueError("final Section differs from frozen citation map")
        section_id = visible.get("section_id")
        title = visible.get("section_title")
        content = visible.get("content")
        if (not isinstance(section_id, str) or not section_id or not isinstance(title, str)
                or not isinstance(content, str)):
            raise ValueError("final Section visible payload is incomplete")
        sections.append((section_id, title, content))
    return sections


def _section_completeness(payload: Mapping[str, Any]) -> dict[str, Any]:
    included = payload["included_sections"]
    missing = payload.get("missing_section_keys")
    withheld = payload.get("withheld_section_keys")
    partial = payload.get("partial")
    if (not isinstance(missing, list) or not isinstance(withheld, list)
            or type(partial) is not bool):
        raise ValueError("published Section completeness fields are malformed")
    included_keys = [item["section_key"] for item in included]
    all_keys = included_keys + missing + withheld
    if (any(not isinstance(key, str) or not key.strip() for key in all_keys)
            or len(set(all_keys)) != len(all_keys)
            or partial != bool(missing or withheld)):
        raise ValueError("published Section completeness differs from frozen keys")
    return {
        "partial": partial,
        "included_section_keys": tuple(included_keys),
        "missing_section_keys": tuple(missing),
        "withheld_section_keys": tuple(withheld),
        "listed_section_count": len(all_keys),
        "required_section_count": len(all_keys),
    }


def _require_status(view: Mapping[str, Any]) -> None:
    if (view.get("status_projection_version") != STATUS_PROJECTION_VERSION
            or view.get("source_mode") != "verified_target"
            or view.get("release_status") != "published"
            or view.get("publication_exists") is not True
            or not view.get("publication_ref")
            or view.get("control_action") not in {"OUTPUT", "OUTPUT_BEST_EFFORT"}):
        raise ValueError("reading requires a verified published Target status view")
    for name in ("result_id", "task_id", "run_id", "execution_status", "business_quality_status"):
        if not isinstance(view.get(name), str) or not view[name]:
            raise ValueError(f"reading status view lacks {name}")


def project_reading_document(
    *,
    visible_payload: Mapping[str, Any],
    citation_projection: Mapping[str, Any],
    status_view: Mapping[str, Any],
) -> ReadingProjection:
    """Render an in-memory reading copy from exact, caller-authorized sources."""
    if not isinstance(visible_payload, Mapping) or not isinstance(citation_projection, Mapping):
        raise TypeError("reading requires final payload and citation projection mappings")
    if visible_payload.get("citation_projection") != citation_projection:
        raise ValueError("reading citation projection differs from final Artifact payload")
    projection = DocumentCitationProjection.model_validate(citation_projection)
    if (projection.catalog.scope != "DOCUMENT"
            or any(catalog.scope != "SECTION" for catalog in projection.source_catalogs)):
        raise ValueError("reading requires the final Document citation catalog")
    if not isinstance(status_view, Mapping):
        raise TypeError("reading requires an explicit terminal status view")
    _require_status(status_view)
    sections = _require_sections(visible_payload, projection)
    completeness = _section_completeness(visible_payload)

    groups = projection.catalog.groups()
    reading_sections = []
    transformations = []
    diagnostics = []
    used_aliases = []
    seen_aliases = set()
    for ordinal, (section_id, title, content) in enumerate(sections, start=1):
        reading_content, transformation = _suppress_duplicate_title(content, title, section_id)
        transformations.append(transformation)
        reading_sections.append((ordinal, title, reading_content))
        for marker in scan_citation_markers(content):
            code = None
            if not marker.well_formed:
                code = "MALFORMED_MARKER"
            elif marker.alias.lower().startswith("fact_"):
                code = "INTERNAL_FACT_MARKER"
            else:
                group = groups.get(marker.alias, ())
                if not group:
                    code = "UNKNOWN_ALIAS"
                elif len(group) != 1 or not group[0].source_identity_consistent:
                    code = "AMBIGUOUS_SOURCE"
                if marker.alias not in seen_aliases and code in {None, "AMBIGUOUS_SOURCE"}:
                    used_aliases.append(marker.alias)
                    seen_aliases.add(marker.alias)
            if code is not None:
                diagnostics.append({
                    "code": code, "section_id": section_id, "field": "content",
                    "source_character_range": {"start": marker.start, "end": marker.end},
                    "offset_unit": "unicode_codepoint", "token": marker.token, "alias": marker.alias,
                })

    table_rows = []
    display_missing = []
    display_hashes = []
    for alias in used_aliases:
        group = groups[alias]
        if len(group) != 1 or not group[0].source_identity_consistent:
            table_rows.append((alias, "来源无法唯一确定", "未记录", "AMBIGUOUS_SOURCE"))
            continue
        entry = group[0]
        for origin in entry.origin_refs:
            origin_hash = canonical_sha256(origin)
            locator = origin.get("source_locator")
            if not isinstance(locator, Mapping):
                locator = {}
            display_hashes.append({"origin_sha256": origin_hash,
                                   "display_sha256": None})
            name = _source_field(origin, locator, "title") or "未记录"
            location, missing = _source_location(origin)
            if name == "未记录":
                missing = ("title", *missing)
            if missing:
                display_missing.append({"alias": alias, "origin_sha256": origin_hash,
                                        "fields": list(missing)})
            table_rows.append((alias, name, location, ""))

    lines = ["# 阅读导出版", "", "本文件是已发布原件的只读派生展示；原业务结果未重新评估。", "",
             "原生成情况：" + _markdown_text(status_view["execution_status"]),
             "原质量结果：" + _markdown_text(status_view["business_quality_status"]),
             "原控制动作：" + _markdown_text(status_view["control_action"]),
             "原发布结果：" + _markdown_text(status_view["release_status"]), "",
             "原文档标题：未记录", ""]
    if completeness["partial"]:
        lines.extend([
            "原件章节情况：部分内容",
            "原件章节计数：已包含 " + str(len(sections)) + "；缺失 "
            + str(len(completeness["missing_section_keys"])) + "；未纳入 "
            + str(len(completeness["withheld_section_keys"])),
            "缺失章节键：" + ("、".join(_markdown_text(key) for key in
                              completeness["missing_section_keys"]) or "无"),
            "未纳入章节键：" + ("、".join(_markdown_text(key) for key in
                               completeness["withheld_section_keys"]) or "无"),
            "",
        ])
    for ordinal, title, content in reading_sections:
        lines.append(f"## {ordinal}、{_markdown_text(title)}")
        lines.append("")
        # Only the title slice above may change content; boundary newlines are
        # owned by this renderer and excluded from content statistics.
        lines.append(content)
        lines.append("")
    lines.extend(["## 正文引用来源", "", "| 引用 | 来源名称 | 记录位置 | 备注 |", "| --- | --- | --- | --- |"])
    for alias, name, location, note in table_rows:
        lines.append("| " + " | ".join(_markdown_text(value) for value in
                                     (f"[{alias}]", name, location, note)) + " |")
    if not table_rows:
        lines.append("| 未记录 | 未记录 | 未记录 | 正文未使用可解析 Citation |")
    reading = "\n".join(lines) + "\n"

    source_count = sum(len(content) for _, _, content in sections)
    reading_count = sum(len(content) for _, _, content in reading_sections)
    statistics = {"source_content_codepoints": source_count,
                  "reading_content_codepoints": reading_count,
                  "reading_file_utf8_bytes": len(reading.encode("utf-8")),
                  "actual_section_count": len(sections),
                  "missing_section_count": len(completeness["missing_section_keys"]),
                  "withheld_section_count": len(completeness["withheld_section_keys"]),
                  "listed_section_count": completeness["listed_section_count"],
                  "required_section_count": completeness["required_section_count"]}
    report = ["## 阅读投影", "", f"- 导出规则版本：`{READING_RULE_VERSION}`",
              f"- 标题规则版本：`{TITLE_RULE_VERSION}`",
              f"- 状态映射版本：`{STATUS_PROJECTION_VERSION}`",
              f"- 原章节 content Unicode 码点数：{source_count}",
              f"- 阅读章节 content Unicode 码点数：{reading_count}",
              f"- 阅读文件 UTF-8 字节数：{statistics['reading_file_utf8_bytes']}",
              f"- 实际章节数：{len(sections)}",
              f"- 原件要求章节数：{completeness['required_section_count']}",
              f"- 缺失章节数：{statistics['missing_section_count']}",
              f"- 未纳入章节数：{statistics['withheld_section_count']}",
              f"- 原件 partial 标记：{str(completeness['partial']).lower()}",
              "- 缺失章节键：" + ("、".join(_markdown_text(key) for key in
                              completeness["missing_section_keys"]) or "无"),
              "- 未纳入章节键：" + ("、".join(_markdown_text(key) for key in
                               completeness["withheld_section_keys"]) or "无"),
              "", "### 标题变换", ""]
    for item in transformations:
        if item["transformed"]:
            span = item["source_character_range"]
            report.append(f"- {_markdown_text(item['section_id'])}: 抑制一次，原 content Unicode 码点区间 "
                          f"[{span['start']}, {span['end']})，原标题 "
                          f"`{_markdown_text(item['original_heading_text'].rstrip(chr(10) + chr(13)))}`")
        else:
            report.append(f"- {_markdown_text(item['section_id'])}: 无变换")
    report.extend(["", "### 引用诊断", ""])
    for item in diagnostics:
        span = item["source_character_range"]
        report.append(f"- {item['code']}：章节 {_markdown_text(item['section_id'])}，原 content Unicode 码点区间 "
                      f"[{span['start']}, {span['end']})，标记 `{_markdown_text(item['token'])}`")
    if not diagnostics:
        report.append("- 无")
    report.extend(["", "### 来源展示信息缺失", ""])
    for item in display_missing:
        report.append(f"- [{_markdown_text(item['alias'])}] / {item['origin_sha256']}："
                      + ", ".join(item["fields"]) + " 未记录")
    if not display_missing:
        report.append("- 无")
    return ReadingProjection(
        reading_markdown=reading, report_markdown="\n".join(report) + "\n",
        title_transformations=tuple(transformations), citation_diagnostics=tuple(diagnostics),
        source_display_missing=tuple(display_missing), statistics=statistics,
        section_completeness=completeness,
        used_aliases=tuple(used_aliases), source_display_hashes=tuple(display_hashes),
    )
