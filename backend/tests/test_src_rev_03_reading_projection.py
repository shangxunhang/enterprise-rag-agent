"""SRCREV-03 pure reading projection: source strings and owner facts stay intact."""

from __future__ import annotations

from copy import deepcopy

import pytest

from application.reading_export.projection import (
    READING_RULE_VERSION,
    project_reading_document,
)
from application.terminal_status_view import STATUS_PROJECTION_VERSION
from apps.enterprise_document.services.agent_rag_v1.post_write_citation_projection import (
    CitationAliasCatalog,
    CitationAliasEntry,
    DocumentCitationProjection,
    assemble_document_citation_projection,
)
from core.hashing import canonical_sha256


def _status(**changes):
    status = {
        "status_projection_version": STATUS_PROJECTION_VERSION,
        "source_mode": "verified_target",
        "result_id": "sha256:" + "a" * 64,
        "task_id": "task-read",
        "run_id": "run-read",
        "phase": "evaluate_document_gate",
        "execution_status": "partial_success",
        "business_quality_status": "failed",
        "control_action": "OUTPUT_BEST_EFFORT",
        "release_status": "published",
        "publication_exists": True,
        "publication_ref": "post-write-release://sha256/" + "b" * 64,
    }
    status.update(changes)
    return status


def _origin(section_id: str, *, page: int | None = None, table: str | None = None,
            title: str | None = None, section: str | None = None):
    locator = {
        "source_type": "chunk", "doc_id": "source-" + section_id,
        "page_start": page, "page_end": page, "table_id": table,
    }
    if title is not None:
        locator["title"] = title
    if section is not None:
        locator["section"] = section
    return {"section_id": section_id, "source_locator": locator,
        "citation_source_identity": "source:" + section_id,
        "exact_evidence_ref": "evidence:" + section_id}


def _catalog(section_id: str, stable_ref: str, *, origin=None, alias="C1"):
    actual = origin or _origin(section_id)
    return CitationAliasCatalog.build(scope="SECTION", source_refs=({"section_key": section_id},),
        entries=(CitationAliasEntry(alias=alias, citation_ref=stable_ref,
            exact_evidence_ref="evidence:" + section_id,
            source_identity_consistent=True, origin_refs=(actual,)),))


def _final(sections, catalogs, *, missing=(), withheld=()):
    source = {"payload_kind": "document", "document_logical_id": "doc-read",
        "included_sections": [{"section_key": section_id, "visible_payload": {
            "payload_kind": "section", "section_id": section_id,
            "section_title": title, "content": content,
        }} for section_id, title, content in sections],
        "missing_section_keys": list(missing), "withheld_section_keys": list(withheld),
        "partial": bool(missing or withheld)}
    return assemble_document_citation_projection(source, catalogs)


def _project(final, **kwargs):
    return project_reading_document(visible_payload=final,
        citation_projection=final["citation_projection"], status_view=_status(), **kwargs)


def test_ordered_body_exact_slices_and_independent_status_axes():
    first = "## 项目背景\n计划部署 12 台服务器[C1]。\n\n| 型号 | 数量 |\n| A | 12 |\n"
    second = "## 1、建设目标\n另一处资料[C1]，原文仍有[C999]、[C 1]、[fact_run_hw]。"
    origins = (_origin("first", page=3), _origin("second", page=8))
    final = _final((("first", "项目背景", first), ("second", "建设目标", second)),
                   (_catalog("first", "ref-a", origin=origins[0]),
                    _catalog("second", "ref-b", origin=origins[1])))
    original = deepcopy(final)
    result = _project(final)
    assert final == original
    assert "## 1、项目背景\n\n计划部署 12 台服务器[C1]。\n\n| 型号 | 数量 |\n| A | 12 |" in result.reading_markdown
    assert "## 项目背景\n" not in result.reading_markdown
    assert "## 2、建设目标\n\n## 1、建设目标\n另一处资料[C2]" in result.reading_markdown
    assert result.used_aliases == ("C1", "C2")
    assert "原生成情况：partial\\_success" in result.reading_markdown
    assert "原质量结果：failed" in result.reading_markdown
    assert "原控制动作：OUTPUT\\_BEST\\_EFFORT" in result.reading_markdown
    assert "原发布结果：published" in result.reading_markdown
    assert "原文档标题：未记录" in result.reading_markdown
    assert "第 3 页" in result.reading_markdown and "第 8 页" in result.reading_markdown
    assert "title, section 未记录" in result.report_markdown
    assert "[C999]" in result.reading_markdown and "[C 1]" in result.reading_markdown
    assert "[fact_run_hw]" in result.reading_markdown
    assert {item["code"] for item in result.citation_diagnostics} == {
        "UNKNOWN_ALIAS", "MALFORMED_MARKER", "INTERNAL_FACT_MARKER"}
    assert result.statistics == {
        "source_content_codepoints": len(first) + len(second),
        "reading_content_codepoints": len(first) - len("## 项目背景\n") + len(second.replace("[C1]", "[C2]")),
        "reading_file_utf8_bytes": len(result.reading_markdown.encode("utf-8")),
        "actual_section_count": 2,
        "missing_section_count": 0,
        "withheld_section_count": 0,
        "listed_section_count": 2,
        "required_section_count": 2,
    }
    assert READING_RULE_VERSION in result.report_markdown


@pytest.mark.parametrize("content,suppressed", (
    ("## 项目背景\n正文", True),
    ("\n \n## 项目背景\r\n正文", True),
    ("### 项目背景\n正文", False),
    ("# 项目背景\n正文", False),
    ("## 1、项目背景\n正文", False),
    ("## 项目背景 [C1]\n正文", False),
    ("## **项目背景**\n正文", False),
    ("> ## 项目背景\n正文", False),
    ("- ## 项目背景\n正文", False),
    ("```md\n## 项目背景\n```", False),
    ("正文\n## 项目背景", False),
    ("## 项目背景 附注\n正文", False),
    ("## 项目背景  \n正文", False),
    ("## 项目背景 #\n正文", False),
))
def test_title_rule_is_exact_first_standalone_level_two_heading(content, suppressed):
    final = _final((("one", "项目背景", content),), (_catalog("one", "ref-a"),))
    result = _project(final)
    change = result.title_transformations[0]
    assert change["transformed"] is suppressed
    if suppressed:
        span = change["source_character_range"]
        assert change["offset_unit"] == "unicode_codepoint"
        assert change["original_heading_text"] == content[span["start"]:span["end"]]
        assert result.statistics["reading_content_codepoints"] == len(content) - (span["end"] - span["start"])
    else:
        assert change["source_character_range"] is None
        assert content in result.reading_markdown


@pytest.mark.parametrize("marked_title", (
    "项目[C1]背景", "**项目背景**", "[项目背景](https://example.invalid)",
    "`项目背景`", "<em>项目背景</em>", "项目_背景", "项目\\*背景",
    "项目 &amp; 背景", "项目 & 背景",
))
def test_matching_structured_title_with_inline_markup_is_not_suppressed(marked_title):
    content = f"## {marked_title}\n正文"
    final = _final((("one", marked_title, content),), (_catalog("one", "ref-a"),))
    result = _project(final)
    assert result.title_transformations[0]["transformed"] is False
    assert content in result.reading_markdown


@pytest.mark.parametrize("spaced_title", (
    " 项目背景", "项目背景 ", "\t项目背景", "项目背景\t",
))
def test_matching_structured_title_with_edge_whitespace_is_not_suppressed(spaced_title):
    content = f"## {spaced_title}\n正文"
    final = _final((("one", spaced_title, content),), (_catalog("one", "ref-a"),))
    result = _project(final)
    assert result.title_transformations[0]["transformed"] is False
    assert content in result.reading_markdown


def test_final_catalog_first_use_multi_origin_and_unreferenced_alias():
    origins = (_origin("one", page=3), _origin("two", page=8))
    first = _catalog("one", "same-ref", origin=origins[0])
    second = _catalog("two", "same-ref", origin=origins[1])
    final = _final((("one", "章节一", "前文[C1]。"), ("two", "章节二", "后文[C1]。")),
                   (first, second))
    result = _project(final)
    assert result.used_aliases == ("C1",)
    assert result.reading_markdown.count("| \\[C1\\]") == 2
    assert "第 3–8 页" not in result.reading_markdown
    assert "第 3 页" in result.reading_markdown and "第 8 页" in result.reading_markdown
    assert len(result.source_display_hashes) == 2

    unused = _final((("one", "章节一", "没有引用。"),), (first,))
    unused_result = _project(unused)
    assert unused_result.used_aliases == ()
    assert "第 3 页" not in unused_result.reading_markdown


def test_ambiguous_alias_keeps_marker_and_neutral_source_row():
    catalog = CitationAliasCatalog.build(scope="SECTION", source_refs=({"section_key": "one"},),
        entries=(CitationAliasEntry(alias="C1", citation_ref="ref-a", exact_evidence_ref="evidence:a",
            source_identity_consistent=True, origin_refs=(_origin("one", page=3),)),
                 CitationAliasEntry(alias="C1", citation_ref="ref-b", exact_evidence_ref="evidence:b",
            source_identity_consistent=True, origin_refs=(_origin("one", page=8),))))
    final = _final((("one", "章节一", "无法确认的引用[C1]。"),), (catalog,))
    result = _project(final)
    assert "无法确认的引用[C1]" in result.reading_markdown
    assert result.used_aliases == ("C1",)
    assert "来源无法唯一确定" in result.reading_markdown
    assert [item["code"] for item in result.citation_diagnostics] == ["AMBIGUOUS_SOURCE"]


def test_missing_source_display_is_not_invented_and_table_fields_are_escaped():
    origin = _origin("one", page=5, table="表|1")
    final = _final((("one", "章节一", "记录[C1]。"),), (_catalog("one", "ref-a", origin=origin),))
    missing = _project(final)
    assert "未记录" in missing.reading_markdown
    assert missing.source_display_missing[0]["fields"] == ["title", "section"]
    frozen_origin = _origin("one", page=5, table="表|1",
                            title="资料|甲\n附", section="第|二章")
    frozen = _final((("one", "章节一", "记录[C1]。"),),
                    (_catalog("one", "ref-a", origin=frozen_origin),))
    displayed = _project(frozen)
    assert "资料\\|甲 附" in displayed.reading_markdown
    assert "第\\|二章" in displayed.reading_markdown
    assert "表\\|1" in displayed.reading_markdown
    assert "记录[C1]。" in displayed.reading_markdown
    assert displayed.source_display_missing == ()
    assert displayed.source_display_hashes == ({
        "origin_sha256": canonical_sha256(frozen_origin), "display_sha256": None},)


def test_partial_published_document_discloses_frozen_missing_and_withheld_keys():
    final = _final((("one", "已纳入", "正文[C1]。"),), (_catalog("one", "ref-a"),),
                   missing=("two",), withheld=("three",))
    result = _project(final)
    assert result.section_completeness == {
        "partial": True,
        "included_section_keys": ("one",),
        "missing_section_keys": ("two",),
        "withheld_section_keys": ("three",),
        "listed_section_count": 3,
        "required_section_count": 3,
    }
    assert "原件章节情况：部分内容\n原件章节计数：已包含 1；缺失 1；未纳入 1" in result.reading_markdown
    assert "缺失章节键：two\n未纳入章节键：three" in result.reading_markdown
    assert "## 1、已纳入" in result.reading_markdown
    assert "原件要求章节数：3" in result.report_markdown
    assert "原件 partial 标记：true" in result.report_markdown
    assert result.statistics["required_section_count"] == 3
    assert result.statistics["missing_section_count"] == 1
    assert result.statistics["withheld_section_count"] == 1


@pytest.mark.parametrize("field,value", (
    ("missing_section_keys", "two"),
    ("missing_section_keys", ["one"]),
    ("withheld_section_keys", ["two", "two"]),
    ("partial", False),
))
def test_inconsistent_frozen_section_completeness_fails_closed(field, value):
    final = _final((("one", "已纳入", "正文[C1]。"),), (_catalog("one", "ref-a"),),
                   missing=("two",))
    final[field] = value
    with pytest.raises(ValueError, match="Section completeness"):
        _project(final)


def test_projection_rejects_changed_catalog_or_unverified_status():
    final = _final((("one", "章节一", "文字[C1]。"),), (_catalog("one", "ref-a"),))
    foreign = deepcopy(final["citation_projection"])
    foreign["catalog"]["entries"][0]["citation_ref"] = "ref-foreign"
    with pytest.raises(ValueError, match="differs"):
        project_reading_document(visible_payload=final, citation_projection=foreign, status_view=_status())
    with pytest.raises(ValueError, match="verified published"):
        project_reading_document(visible_payload=final,
            citation_projection=final["citation_projection"], status_view=_status(release_status=None))


def test_handwritten_published_payload_has_exact_expected_reading_bytes():
    """Final aliases and expected Markdown are fixture facts, not assembler output."""
    origin = _origin("one", page=7)
    section_catalog = _catalog("one", "stable-ref", origin=origin)
    source_content = "## 项目背景\n数值 12[C7]。\n\n| 行 | 值 |\n| --- | --- |\n| A | 12 |"
    pre_remap_payload = {
        "payload_kind": "document", "document_logical_id": "doc-handwritten",
        "included_sections": [{"section_key": "one", "visible_payload": {
            "payload_kind": "section", "section_id": "one",
            "section_title": "项目背景", "content": source_content.replace("[C7]", "[C1]"),
        }}],
        "missing_section_keys": [], "withheld_section_keys": [], "partial": False,
    }
    document_catalog = CitationAliasCatalog.build(
        scope="DOCUMENT",
        source_refs=({"section_key": "one", "section_catalog_hash": section_catalog.catalog_hash,
                      "origins": list(section_catalog.source_refs)},),
        entries=(CitationAliasEntry(alias="C7", citation_ref="stable-ref",
                                    exact_evidence_ref="evidence:one",
                                    source_identity_consistent=True, origin_refs=(origin,)),),
    )
    projection_fields = {
        "schema_version": "document_citation_projection_v1",
        "policy_version": "document-citation-display-v1",
        "source_payload_sha256": canonical_sha256(pre_remap_payload),
        "source_catalogs": [section_catalog.model_dump(mode="json")],
        "section_alias_maps": [{"section_key": "one", "mapping": {"C1": "C7"}}],
        "catalog": document_catalog.model_dump(mode="json"),
    }
    projection = DocumentCitationProjection(
        **projection_fields, projection_hash=canonical_sha256(projection_fields)
    ).model_dump(mode="json")
    final = deepcopy(pre_remap_payload)
    final["included_sections"][0]["visible_payload"]["content"] = source_content
    final["citation_projection"] = projection
    result = _project(final)
    expected = (
        "# 阅读导出版\n\n"
        "本文件是已发布原件的只读派生展示；原业务结果未重新评估。\n\n"
        "原生成情况：partial\\_success\n"
        "原质量结果：failed\n"
        "原控制动作：OUTPUT\\_BEST\\_EFFORT\n"
        "原发布结果：published\n\n"
        "原文档标题：未记录\n\n"
        "## 1、项目背景\n\n"
        "数值 12[C7]。\n\n| 行 | 值 |\n| --- | --- |\n| A | 12 |\n\n"
        "## 正文引用来源\n\n"
        "| 引用 | 来源名称 | 记录位置 | 备注 |\n"
        "| --- | --- | --- | --- |\n"
        "| \\[C7\\] | 未记录 | 第 7 页 |  |\n"
    )
    assert result.reading_markdown == expected
    assert result.title_transformations[0]["original_heading_text"] == "## 项目背景\n"
    assert result.citation_diagnostics == ()
    assert result.used_aliases == ("C7",)
    assert result.statistics["source_content_codepoints"] == len(source_content)
    assert result.statistics["reading_content_codepoints"] == len(source_content) - len("## 项目背景\n")
