from __future__ import annotations

from typing import Any

from rag.plugins.chunkers.heading_parent_paragraph_child import (
    HeadingParagraphChunker,
)
from rag.plugins.chunkers.policy_article_clause import PolicyArticleClauseChunker
from rag.plugins.chunkers.project_fact_record import (
    ProjectFactRecordChunker,
    infer_fact_status,
)
from rag.plugins.chunkers.table_row_record import (
    TableRowRecordChunker,
    parse_structured_table,
)
from rag.plugins.chunkers.template_section import TemplateSectionChunker


def _profile(
    *,
    document_id: str,
    strategy: str,
    kb_id: str,
    evidence_role: str,
    authority_level: str = "reference",
    source_document_form: str = "construction_scheme",
    allowed: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "document_profile_v1",
        "document_id": document_id,
        "source_sha256": "a" * 64,
        "document_type": "document_template"
        if strategy == "template_section"
        else "historical_construction_scheme",
        "evidence_role": evidence_role,
        "kb_id": kb_id,
        "tenant_id": "default",
        "project_relation": "general"
        if strategy in {"policy_article_clause", "template_section"}
        else "other_project",
        "project_id": None
        if strategy in {"policy_article_clause", "template_section"}
        else "other-project",
        "authority": {
            "level": authority_level,
            "issuer": "测试发布主体",
            "version": "V1",
            "effective_date": "2026-01-01",
            "expiration_date": None,
        },
        "content_capabilities": {
            "allowed_claim_types": allowed
            or ["general_method", "reference_case"],
            "forbidden_claim_types": [
                "current_project_fact",
                "current_project_cost",
            ],
        },
        "parsing": {
            "parser_strategy": "hierarchical_document",
            "chunk_strategy": strategy,
            "fallback_chunk_strategy": "fixed_window_fallback",
        },
        "classification": {
            "method": "hybrid_review",
            "confidence": 1.0,
            "reviewed": True,
            "reviewed_by": "stage-c-test",
            "reviewed_at": "2026-08-03T19:30:00+08:00",
            "reason_codes": ["test_fixture"],
        },
        "source_document_form": source_document_form,
        "taxonomy_mapping": None,
        "should_index": True,
        "is_gold_reference": False,
        "profile_sha256": "b" * 64,
        "catalog_sha256": "c" * 64,
    }


def _unit(
    *,
    document_id: str,
    strategy: str,
    order: int,
    text: str,
    unit_type: str = "paragraph",
    kb_id: str = "historical_case_kb",
    evidence_role: str = "historical_case",
    extra: dict[str, Any] | None = None,
    allowed: list[str] | None = None,
) -> dict[str, Any]:
    profile = _profile(
        document_id=document_id,
        strategy=strategy,
        kb_id=kb_id,
        evidence_role=evidence_role,
        source_document_form=(
            "template" if strategy == "template_section" else "construction_scheme"
        ),
        allowed=allowed,
    )
    payload_extra = dict(extra or {})
    payload_extra["document_profile"] = profile
    return {
        "schema_version": "cleaned_text_unit_v1",
        "unit_id": f"{document_id}_u{order:04d}",
        "doc_id": document_id,
        "tenant_id": "default",
        "kb_id": kb_id,
        "file_id": f"{document_id}.docx",
        "source_type": "document",
        "source_uri": f"D:/raw/{document_id}.docx",
        "source_name": f"{document_id}.docx",
        "source_format": "docx",
        "batch_id": "stage-c-test",
        "title": f"{document_id} title",
        "section": None,
        "section_level": None,
        "page_start": order,
        "page_end": order,
        "unit_type": unit_type,
        "unit_order": order,
        "text": text,
        "text_length": len(text),
        "language": "zh",
        "quality_score": 1.0,
        "quality_flags": [],
        "cleaning_version": "test-v1",
        "extra": payload_extra,
    }


def test_heading_parent_paragraph_child_preserves_section_boundaries() -> None:
    rows = [
        _unit(document_id="doc-heading", strategy="heading_parent_paragraph_child", order=1, text="第一章 总体方案", unit_type="title"),
        _unit(document_id="doc-heading", strategy="heading_parent_paragraph_child", order=2, text="本章正文。"),
        _unit(document_id="doc-heading", strategy="heading_parent_paragraph_child", order=3, text="第二章 建设内容", unit_type="title"),
        _unit(document_id="doc-heading", strategy="heading_parent_paragraph_child", order=4, text="本节建设正文。"),
    ]
    result = HeadingParagraphChunker().chunk_records(rows)

    assert len(result.parents) == 2
    assert len(result.children) == 2
    assert "本章正文" in result.parents[0]["text"]
    assert "本节建设正文" not in result.parents[0]["text"]
    first_extra = result.children[0]["extra"]
    assert first_extra["heading_path"] == ["第一章 总体方案"]
    assert first_extra["heading_level"] == 2
    assert first_extra["source_page_start"] == 1
    assert first_extra["source_page_end"] == 2


def test_policy_chunker_filters_toc_and_preserves_article_clause_path() -> None:
    rows = [
        _unit(document_id="doc-policy", strategy="policy_article_clause", order=1, text="目录", kb_id="policy_standard_kb", evidence_role="policy_standard"),
        _unit(document_id="doc-policy", strategy="policy_article_clause", order=2, text="第一章 总则 ........ 1", kb_id="policy_standard_kb", evidence_role="policy_standard"),
        _unit(document_id="doc-policy", strategy="policy_article_clause", order=3, text="第一章 总则", unit_type="title", kb_id="policy_standard_kb", evidence_role="policy_standard"),
        _unit(document_id="doc-policy", strategy="policy_article_clause", order=4, text="第一条 适用范围", unit_type="title", kb_id="policy_standard_kb", evidence_role="policy_standard"),
        _unit(document_id="doc-policy", strategy="policy_article_clause", order=5, text="本办法适用于测试范围。", kb_id="policy_standard_kb", evidence_role="policy_standard"),
        _unit(document_id="doc-policy", strategy="policy_article_clause", order=6, text="（一）申请人应提交材料。", unit_type="list", kb_id="policy_standard_kb", evidence_role="policy_standard"),
        _unit(document_id="doc-policy", strategy="policy_article_clause", order=7, text="第二条 审核要求", unit_type="title", kb_id="policy_standard_kb", evidence_role="policy_standard"),
        _unit(document_id="doc-policy", strategy="policy_article_clause", order=8, text="审核机关应依法办理。", kb_id="policy_standard_kb", evidence_role="policy_standard"),
    ]
    result = PolicyArticleClauseChunker().chunk_records(rows)

    assert len(result.parents) == 2
    assert all(
        child["extra"]["type_aware_chunking"]["representation_type"]
        == "policy_clause"
        for child in result.children
    )
    assert all(child["extra"]["is_table_of_contents"] is False for child in result.children)
    assert all("........ 1" not in child["text"] for child in result.children)
    article_ids = {child["extra"]["article_id"] for child in result.children}
    assert article_ids == {"article_一", "article_二"}
    assert any(child["extra"]["clause_id"] == "（一）" for child in result.children)
    assert not any(
        "第二条" in child["text"] and "第一条" in child["text"]
        for child in result.children
    )


def test_policy_chunker_discloses_fallback_when_structure_is_absent() -> None:
    row = _unit(
        document_id="doc-policy-fallback",
        strategy="policy_article_clause",
        order=1,
        text="本文件说明一般工作原则，但没有条款编号。",
        kb_id="policy_standard_kb",
        evidence_role="policy_standard",
    )
    result = PolicyArticleClauseChunker().chunk_records([row])
    chunking = result.children[0]["extra"]["type_aware_chunking"]
    assert chunking["fallback_used"] is True
    assert chunking["fallback_reason"] == "no_policy_clause_structure"
    assert chunking["actual_strategy"] == "paragraph_text_evidence"
    assert chunking["representation_type"] == "text_evidence"


def test_structured_table_binds_headers_rows_and_merged_cells() -> None:
    payload = {
        "column_count": 3,
        "row_count": 4,
        "merged_cells": [
            {"row": 3, "column": 1, "status": "merged_or_unavailable"}
        ],
        "rows": [
            ["设备", "数量", "单位"],
            ["服务器", "4", "台"],
            ["", "2", "台"],
            ["交换机", "8", "台"],
        ],
    }
    header, rows = parse_structured_table(payload)
    assert header == ["设备", "数量", "单位"]
    assert len(rows) == 3
    assert rows[1].normalized_fields["设备"] == "服务器"
    assert rows[1].inherited_cells == {"设备": "服务器"}

    unit = _unit(
        document_id="doc-table",
        strategy="table_row_record",
        order=1,
        text="| 设备 | 数量 | 单位 |",
        unit_type="table",
        extra={
            "structured_table": payload,
            "structured_table_asset": {
                "uri": "tables/doc-table.json",
                "sha256": "d" * 64,
            },
        },
    )
    result = TableRowRecordChunker().chunk_records([unit])
    assert len(result.children) == 3
    assert [child["extra"]["row_index"] for child in result.children] == [2, 3, 4]
    assert all(
        child["extra"]["type_aware_chunking"]["representation_type"]
        == "table_row"
        for child in result.children
    )
    assert all(child["extra"]["type_aware_chunking"]["fallback_used"] is False for child in result.children)


def test_co2_calculation_becomes_source_grounded_table_row() -> None:
    texts = [
        "一、年CO₂排放量",
        "项目新建38台设备，总共功耗14.2KW，碳排放因子为0.5857（kgCO₂/kWh）。",
        "=124392 kWh",
        "≈72.86 吨CO₂",
    ]
    rows = [
        _unit(document_id="doc-co2", strategy="table_row_record", order=index, text=text)
        for index, text in enumerate(texts, start=1)
    ]
    result = TableRowRecordChunker().chunk_records(rows)
    child = result.children[0]
    assert child["extra"]["normalized_fields"] == {
        "device_count": "38",
        "total_power_kw": "14.2",
        "emission_factor_kg_co2_per_kwh": "0.5857",
        "annual_electricity_kwh": "124392",
        "annual_co2_tons": "72.86",
    }
    assert child["extra"]["type_aware_chunking"]["fallback_used"] is False


def test_fact_status_never_confuses_proposed_estimated_and_confirmed() -> None:
    reference_profile = _profile(
        document_id="doc-fact",
        strategy="fact_record",
        kb_id="project_fact_kb",
        evidence_role="project_fact",
        authority_level="reference",
        source_document_form="feasibility_study",
    )
    assert infer_fact_status("拟建设4台服务器", reference_profile) == "proposed"
    assert infer_fact_status("估算投资500万元", reference_profile) == "estimated"
    assert infer_fact_status("最终确认投资500万元", reference_profile) == "confirmed"
    assert infer_fact_status("建设4台服务器", reference_profile) == "proposed"
    assert infer_fact_status(
        "项目建设单位（盖章）：陕西省交通运输厅",
        reference_profile,
        predicate="项目建设单位（盖章）",
        fact_category="general",
    ) == "unknown"
    assert infer_fact_status(
        "回传用户已经确认告知书",
        reference_profile,
        predicate="table_row",
        fact_category="general",
    ) == "unknown"
    assert infer_fact_status(
        "由成交供应商支付代理服务费",
        reference_profile,
        predicate="table_row",
        fact_category="general",
    ) == "unknown"
    assert infer_fact_status(
        "编制日期：2025年05月",
        reference_profile,
        predicate="编制日期",
        fact_category="schedule",
    ) == "unknown"
    assert infer_fact_status(
        "质量管理计划：说明如何实施质量方针",
        reference_profile,
        predicate="质量管理计划",
        fact_category="schedule",
    ) == "unknown"


def test_fact_chunker_falls_back_without_inventing_value() -> None:
    row = _unit(
        document_id="doc-fact-fallback",
        strategy="fact_record",
        order=1,
        text="本节描述总体背景，不包含可验证键值、编号需求或结构化表格。",
        kb_id="project_fact_kb",
        evidence_role="project_fact",
    )
    result = ProjectFactRecordChunker().chunk_records([row])
    child = result.children[0]
    assert child["extra"]["type_aware_chunking"]["fallback_used"] is True
    assert child["extra"]["type_aware_chunking"]["fallback_reason"] == "unsupported_fact_structure"
    assert child["extra"]["value"] is None
    assert child["extra"]["type_aware_chunking"]["representation_type"] == "text_evidence"


def test_template_sections_keep_placeholders_and_never_gain_fact_claims() -> None:
    allowed = ["template_structure", "style_guidance"]
    rows = [
        _unit(
            document_id="doc-template",
            strategy="template_section",
            order=1,
            text="第一章 项目概况",
            unit_type="title",
            kb_id="template_style_kb",
            evidence_role="template_style",
            allowed=allowed,
        ),
        _unit(
            document_id="doc-template",
            strategy="template_section",
            order=2,
            text="项目名称：{{project_name}}\n必填：建设单位\n示例投资100万元。正文采用宋体。",
            kb_id="template_style_kb",
            evidence_role="template_style",
            allowed=allowed,
        ),
    ]
    result = TemplateSectionChunker().chunk_records(rows)
    child = result.children[0]
    assert child["kb_id"] == "template_style_kb"
    assert child["extra"]["document_profile"]["evidence_role"] == "template_style"
    assert set(child["extra"]["template_claim_types"]) == set(allowed)
    assert "project_name" in child["extra"]["placeholder_fields"]
    assert child["extra"]["is_example_content"] is True
    assert "100万元" in child["extra"]["example_values"]
    assert "current_project_fact" not in child["extra"]["template_claim_types"]
