"""SRCREV-03 Option A: one exact release through the final three reading files.

The release fixture is built independently in the source-adapter tests. Expected
reading facts below are handwritten from that fixture, never copied from the
exporter's own projection or manifest builder.
"""

from __future__ import annotations

import base64
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from application.reading_export.delivery import (
    MANIFEST_NAME,
    READING_NAME,
    REPORT_NAME,
    DeliveryConflict,
)
from application.reading_export.exporter import export_reading_markdown
from application.reading_export.source import TerminalOutcomeReceiptRequired
from application.mainline_service import MainlineApplicationService
from application.terminal_outcome_schema import TerminalOutcomeResultV1
from agent.supervisor_agent import SupervisorAgent
from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from apps.enterprise_document.services.post_write.persistence import PostWriteSqliteDatabase
from apps.enterprise_document.services.post_write.release import PostWriteReleasePublicationService
from core.hashing import canonical_sha256
from export_reading_markdown import main as cli_main
from test_src_rev_03_readonly_source_adapter import (
    DOCUMENT_ID,
    DOCUMENT_LOGICAL_ID,
    build_release_fixture,
)


RICH_SECTIONS = (
    (
        "first", "section-background", "项目背景",
        "## 项目背景\n计划部署 12 台服务器[C1]。\n\n"
        "| 型号 | 数量 |\n| --- | ---: |\n| A | 12 |\n\n"
        "```python\nprint(12)\n```\n\n"
        "待核对 [C999]、[C 1]、[fact_run_hw]。",
    ),
    (
        "second", "section-plan", "实施方案",
        "## 1、实施方案\n按原文执行[C1]。\n\n### 真正的小标题\n数字 3.14 不变。",
    ),
)


def _inventory(root: Path) -> tuple[tuple[str, int | None, str | None], ...]:
    """Include directories, so a new empty release-store directory is visible."""
    rows = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            rows.append((relative, None, None))
        else:
            raw = path.read_bytes()
            rows.append((relative, len(raw), sha256(raw).hexdigest()))
    return tuple(sorted(rows))


def _outcome_with(fixture, **changes) -> TerminalOutcomeResultV1:
    raw = fixture.terminal_outcome.model_dump(mode="json")
    raw.update(changes)
    raw.pop("result_id")
    raw["result_id"] = "sha256:" + canonical_sha256(raw)
    return TerminalOutcomeResultV1.model_validate(raw)


@pytest.mark.component_integration
def test_exact_release_to_three_files_is_read_only_and_repeatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = build_release_fixture(tmp_path / "source")
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    output_dir = output_parent / "reading"
    before_tree = _inventory(fixture.release_root)
    before_outcome = fixture.terminal_outcome.model_dump(mode="json")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("SRCREV-03 opened SQLite or constructed a business owner")

    # Patch only after building the independent input files. The operation under
    # test must read those existing files and must not initialize a service.
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", forbidden)
        patch.setattr(FileArtifactStore, "__init__", forbidden)
        patch.setattr(PostWriteSqliteDatabase, "__init__", forbidden)
        patch.setattr(PostWriteReleasePublicationService, "__init__", forbidden)
        patch.setattr(TargetPostWriteComposition, "__init__", forbidden)
        patch.setattr(MainlineApplicationService, "__init__", forbidden)
        patch.setattr(SupervisorAgent, "__init__", forbidden)
        first = export_reading_markdown(
            target_release_root=fixture.release_root,
            release_id=fixture.release_id,
            terminal_outcome=fixture.terminal_outcome,
            output_dir=output_dir,
        )
        second = export_reading_markdown(
            target_release_root=fixture.release_root,
            release_id=fixture.release_id,
            terminal_outcome=fixture.terminal_outcome,
            output_dir=output_dir,
        )

    assert first.status == "NEW"
    assert second.status == "REUSE"
    assert second.export_identity == first.export_identity
    assert second.file_sha256s == first.file_sha256s
    assert first.final_dir == output_dir
    assert first.source_release_id == fixture.release_id
    assert first.terminal_outcome_result_id == fixture.terminal_outcome.result_id
    assert {item.name for item in output_dir.iterdir()} == {
        READING_NAME, REPORT_NAME, MANIFEST_NAME,
    }
    files = {name: (output_dir / name).read_bytes()
             for name in (READING_NAME, REPORT_NAME, MANIFEST_NAME)}
    assert dict(first.file_sha256s) == {
        name: sha256(raw).hexdigest() for name, raw in files.items()
    }
    assert _inventory(fixture.release_root) == before_tree
    assert fixture.terminal_outcome.model_dump(mode="json") == before_outcome
    assert all(not path.name.endswith((".lock", ".tmp", "-wal", "-shm", "-journal"))
               for path in fixture.release_root.rglob("*"))

    reading = files[READING_NAME].decode("utf-8")
    report = files[REPORT_NAME].decode("utf-8")
    manifest = json.loads(files[MANIFEST_NAME].decode("utf-8"))
    assert reading.startswith("# 阅读导出版\n\n")
    assert "<!-- target-document-visible-v1:" in fixture.payload_bytes.decode("utf-8")
    assert "<!-- target-document-visible-v1:" not in reading
    assert "原生成情况：success\n原质量结果：passed\n原控制动作：OUTPUT\n原发布结果：published" in reading
    assert "## 1、Scope\n\n\nExact body [C1]." in reading
    assert reading.count("## Scope") == 0
    assert "| \\[C1\\] | 未记录 | 第 3 页 |  |" in reading
    assert "## 正文引用来源" in reading
    assert "标题变换" in report and "抑制一次" in report
    assert "TerminalOutcome checkpoint provenance：未由 exporter 独立验证" in report

    source = manifest["source"]
    assert manifest["export_identity"] == first.export_identity
    assert manifest["schema_version"] == "src-rev-03-export-manifest-v1"
    assert source["publication_release_id"] == fixture.release_id
    assert DOCUMENT_ID != DOCUMENT_LOGICAL_ID
    assert source["document_id"] == DOCUMENT_ID
    assert source["document_logical_id"] == DOCUMENT_LOGICAL_ID
    assert source["document_id"] == (
        fixture.proof.release_time_currentness_proof.active_projection.document_id
    )
    assert source["document_logical_id"] == fixture.manifest.document_logical_id
    assert source["release_manifest_payload_sha256"] == fixture.manifest.manifest_payload_sha256
    assert source["release_input_proof_sha256"] == fixture.proof.proof_sha256
    assert source["release_payload_sha256"] == sha256(fixture.payload_bytes).hexdigest()
    assert source["document_artifact_ref"] == fixture.manifest.document_artifact_ref.model_dump(mode="json")
    assert source["validated_output_binding_sha256"] == fixture.manifest.validated_output_binding_sha256
    assert source["renderer_contract_version"] == "target-markdown-renderer-v1"
    assert source["terminal_outcome_result_id"] == fixture.terminal_outcome.result_id
    assert source["terminal_outcome_source"] == "caller_supplied_exact_receipt"
    encoded_metadata = fixture.payload_bytes.split(b" -->", 1)[0].split(b":", 1)[1]
    expected_visible = json.loads(base64.b64decode(encoded_metadata, validate=True))
    assert source["document_citation_projection_hash"] == expected_visible["citation_projection"]["projection_hash"]
    assert manifest["ordered_section_ids"] == ["section-1"]
    assert manifest["statistics"] == {
        "source_output_bytes": len(fixture.payload_bytes),
        "source_content_codepoints": len("## Scope\n\nExact body [C1]."),
        "reading_content_codepoints": len("\nExact body [C1]."),
        "reading_file_utf8_bytes": len(files[READING_NAME]),
        "actual_section_count": 1,
        "missing_section_count": 0,
        "withheld_section_count": 0,
        "listed_section_count": 1,
        "required_section_count": 1,
    }
    assert manifest["title_transformations"][0]["source_character_range"] == {
        "start": 0, "end": len("## Scope\n"),
    }
    assert {row["path"] for row in manifest["files"]} == {READING_NAME, REPORT_NAME}
    assert {row["path"]: (row["bytes"], row["sha256"]) for row in manifest["files"]} == {
        name: (len(files[name]), sha256(files[name]).hexdigest())
        for name in (READING_NAME, REPORT_NAME)
    }
    assert "manifest_sha256" not in manifest


@pytest.mark.component_integration
def test_raw_document_id_is_not_parsed_from_logical_id(tmp_path: Path) -> None:
    logical_id_with_decoy_raw = "document-logical:run-src03-fixture:decoy-document"
    fixture = build_release_fixture(
        tmp_path / "source", document_logical_id=logical_id_with_decoy_raw,
    )
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    receipt = export_reading_markdown(
        target_release_root=fixture.release_root, release_id=fixture.release_id,
        terminal_outcome=fixture.terminal_outcome,
        output_dir=output_parent / "reading",
    )
    manifest = json.loads((receipt.final_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert receipt.status == "NEW"
    assert manifest["source"]["document_id"] == DOCUMENT_ID
    assert manifest["source"]["document_logical_id"] == logical_id_with_decoy_raw


@pytest.mark.component_integration
def test_best_effort_fail_with_later_failure_preserves_rich_markdown_and_citations(
    tmp_path: Path,
) -> None:
    fixture = build_release_fixture(
        tmp_path / "source", sections=RICH_SECTIONS,
        control_action="OUTPUT_BEST_EFFORT", quality_state="FAIL",
        terminal_overrides={"status": "failed", "execution_status": "failed"},
    )
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    before = _inventory(fixture.release_root)
    receipt = export_reading_markdown(
        target_release_root=fixture.release_root, release_id=fixture.release_id,
        terminal_outcome=fixture.terminal_outcome,
        output_dir=output_parent / "reading",
    )
    reading = (receipt.final_dir / READING_NAME).read_text(encoding="utf-8")
    report = (receipt.final_dir / REPORT_NAME).read_text(encoding="utf-8")
    manifest = json.loads((receipt.final_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert receipt.status == "NEW"
    assert reading == (
        "# 阅读导出版\n\n"
        "本文件是已发布原件的只读派生展示；原业务结果未重新评估。\n\n"
        "原生成情况：failed\n"
        "原质量结果：failed\n"
        "原控制动作：OUTPUT\\_BEST\\_EFFORT\n"
        "原发布结果：published\n\n"
        "原文档标题：未记录\n\n"
        "## 1、项目背景\n\n"
        "计划部署 12 台服务器[C1]。\n\n"
        "| 型号 | 数量 |\n| --- | ---: |\n| A | 12 |\n\n"
        "```python\nprint(12)\n```\n\n"
        "待核对 [C999]、[C 1]、[fact_run_hw]。\n\n"
        "## 2、实施方案\n\n"
        "## 1、实施方案\n按原文执行[C1]。\n\n"
        "### 真正的小标题\n数字 3.14 不变。\n\n"
        "## 正文引用来源\n\n"
        "| 引用 | 来源名称 | 记录位置 | 备注 |\n"
        "| --- | --- | --- | --- |\n"
        "| \\[C1\\] | 未记录 | 第 3 页 |  |\n"
        "| \\[C1\\] | 未记录 | 第 4 页 |  |\n"
    )
    assert {item["code"] for item in manifest["citation_diagnostics"]} == {
        "UNKNOWN_ALIAS", "MALFORMED_MARKER", "INTERNAL_FACT_MARKER",
    }
    for marker, escaped in (
        ("[C999]", "\\[C999\\]"),
        ("[C 1]", "\\[C 1\\]"),
        ("[fact_run_hw]", "\\[fact\\_run\\_hw\\]"),
    ):
        assert marker in reading and escaped in report
    assert "original Control action：`OUTPUT\\_BEST\\_EFFORT`" in report
    assert "original Quality state：`FAIL`" in report
    assert "第 3–4 页" not in reading
    assert manifest["statistics"] == {
        "source_output_bytes": len(fixture.payload_bytes),
        "source_content_codepoints": len(RICH_SECTIONS[0][3]) + len(RICH_SECTIONS[1][3]),
        "reading_content_codepoints": len(RICH_SECTIONS[0][3]) - len("## 项目背景\n") + len(RICH_SECTIONS[1][3]),
        "reading_file_utf8_bytes": len(reading.encode("utf-8")),
        "actual_section_count": 2,
        "missing_section_count": 0,
        "withheld_section_count": 0,
        "listed_section_count": 2,
        "required_section_count": 2,
    }
    assert [row["section_id"] for row in manifest["title_transformations"]] == [
        "section-background", "section-plan",
    ]
    assert [row["transformed"] for row in manifest["title_transformations"]] == [True, False]
    assert manifest["source"]["terminal_outcome_result_id"] == fixture.terminal_outcome.result_id
    assert _inventory(fixture.release_root) == before


@pytest.mark.component_integration
def test_partial_publication_discloses_missing_and_withheld_section_keys(tmp_path: Path) -> None:
    fixture = build_release_fixture(
        tmp_path / "source", control_action="OUTPUT_BEST_EFFORT", quality_state="FAIL",
        missing_section_keys=("section-not-generated",),
        withheld_section_keys=("section-not-released",), partial=True,
    )
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    before = _inventory(fixture.release_root)
    receipt = export_reading_markdown(
        target_release_root=fixture.release_root, release_id=fixture.release_id,
        terminal_outcome=fixture.terminal_outcome,
        output_dir=output_parent / "partial",
    )
    reading = (receipt.final_dir / READING_NAME).read_text(encoding="utf-8")
    report = (receipt.final_dir / REPORT_NAME).read_text(encoding="utf-8")
    manifest = json.loads((receipt.final_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert receipt.status == "NEW"
    assert "原生成情况：partial\\_success" in reading
    assert "原质量结果：failed" in reading
    assert (
        "原件章节情况：部分内容\n"
        "原件章节计数：已包含 1；缺失 1；未纳入 1\n"
        "缺失章节键：section-not-generated\n"
        "未纳入章节键：section-not-released"
    ) in reading
    assert "原件要求章节数：3" in report
    assert "缺失章节键：section-not-generated" in report
    assert "未纳入章节键：section-not-released" in report
    assert reading.count("## 1、Scope") == 1
    assert "## 2、" not in reading
    assert manifest["statistics"]["actual_section_count"] == 1
    assert manifest["statistics"]["missing_section_count"] == 1
    assert manifest["statistics"]["withheld_section_count"] == 1
    assert manifest["statistics"]["listed_section_count"] == 3
    assert manifest["statistics"]["required_section_count"] == 3
    assert manifest["section_completeness"] == {
        "partial": True,
        "included_section_keys": ["one"],
        "missing_section_keys": ["section-not-generated"],
        "withheld_section_keys": ["section-not-released"],
        "listed_section_count": 3,
        "required_section_count": 3,
    }
    assert _inventory(fixture.release_root) == before


@pytest.mark.component_integration
def test_later_execution_failure_is_rendered_from_exact_receipt(tmp_path: Path) -> None:
    fixture = build_release_fixture(tmp_path / "source")
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    later = _outcome_with(fixture, status="failed", execution_status="failed",
                          phase="persist_deliverable")
    receipt = export_reading_markdown(
        target_release_root=fixture.release_root, release_id=fixture.release_id,
        terminal_outcome=later, output_dir=output_parent / "later",
    )
    reading = (receipt.final_dir / READING_NAME).read_text(encoding="utf-8")
    manifest = json.loads((receipt.final_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert receipt.status == "NEW"
    assert "原生成情况：failed\n原质量结果：passed\n原控制动作：OUTPUT\n原发布结果：published" in reading
    assert manifest["source"]["terminal_outcome_result_id"] == later.result_id
    assert later.result_id != fixture.terminal_outcome.result_id
    assert receipt.export_identity != export_reading_markdown(
        target_release_root=fixture.release_root, release_id=fixture.release_id,
        terminal_outcome=fixture.terminal_outcome, output_dir=output_parent / "normal",
    ).export_identity


@pytest.mark.component_integration
def test_missing_receipt_and_conflicting_final_fail_without_source_change(tmp_path: Path) -> None:
    fixture = build_release_fixture(tmp_path / "source")
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    final = output_parent / "reading"
    before = _inventory(fixture.release_root)
    with pytest.raises(TerminalOutcomeReceiptRequired, match="TERMINAL_OUTCOME_RECEIPT_REQUIRED"):
        export_reading_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id,
                                terminal_outcome=None, output_dir=final)
    assert not final.exists()
    final.mkdir()
    (final / "stray.txt").write_bytes(b"another export")
    with pytest.raises(DeliveryConflict):
        export_reading_markdown(target_release_root=fixture.release_root,
                                release_id=fixture.release_id,
                                terminal_outcome=fixture.terminal_outcome, output_dir=final)
    assert (final / "stray.txt").read_bytes() == b"another export"
    assert _inventory(fixture.release_root) == before


@pytest.mark.component_integration
def test_exporter_mid_delivery_failure_leaves_no_final_or_source_changes(tmp_path: Path) -> None:
    fixture = build_release_fixture(tmp_path / "source")
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    final = output_parent / "reading"
    before = _inventory(fixture.release_root)

    def fail_after_report(stage: str) -> None:
        if stage == f"after_write:{REPORT_NAME}":
            raise RuntimeError("injected export failure")

    with pytest.raises(RuntimeError, match="injected export failure"):
        export_reading_markdown(
            target_release_root=fixture.release_root, release_id=fixture.release_id,
            terminal_outcome=fixture.terminal_outcome, output_dir=final,
            fault_injector=fail_after_report,
        )
    assert not final.exists()
    assert list(output_parent.glob(".src03-stage-*")) == []
    assert _inventory(fixture.release_root) == before


@pytest.mark.component_integration
def test_cli_requires_explicit_receipt_and_reports_local_admin_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = build_release_fixture(tmp_path / "source")
    output_parent = tmp_path / "output"
    output_parent.mkdir()
    final = output_parent / "reading"
    arguments = [
        "--target-release-root", str(fixture.release_root),
        "--release-id", fixture.release_id,
        "--output-dir", str(final),
    ]
    with pytest.raises(SystemExit) as missing:
        cli_main(arguments)
    assert missing.value.code == 2
    assert "TERMINAL_OUTCOME_RECEIPT_REQUIRED" in capsys.readouterr().err
    assert not final.exists()

    corrupt_json = tmp_path / "corrupt-terminal-outcome.json"
    corrupt_receipt = fixture.terminal_outcome.model_dump(mode="json")
    corrupt_receipt["result_id"] = "sha256:" + "0" * 64
    corrupt_json.write_text(json.dumps(corrupt_receipt), encoding="utf-8")
    assert cli_main([*arguments, "--terminal-outcome-json", str(corrupt_json)]) == 2
    assert "result" in capsys.readouterr().err.lower()
    assert not final.exists()

    terminal_json = tmp_path / "terminal-outcome.json"
    terminal_json.write_text(
        json.dumps(fixture.terminal_outcome.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    assert cli_main([*arguments, "--terminal-outcome-json", str(terminal_json)]) == 0
    operation = json.loads(capsys.readouterr().out)
    assert operation["status"] == "NEW"
    assert operation["source_release_id"] == fixture.release_id
    assert operation["terminal_outcome_result_id"] == fixture.terminal_outcome.result_id
    assert operation["terminal_outcome_source"] == "caller_supplied_exact_receipt"
    assert operation["authorization_boundary"] == "local_admin"
    assert operation["checkpoint_provenance"] == "not_independently_verified"
    assert operation["files_sha256"] == {
        name: sha256((final / name).read_bytes()).hexdigest()
        for name in (READING_NAME, REPORT_NAME, MANIFEST_NAME)
    }
    assert sha256((final / MANIFEST_NAME).read_bytes()).hexdigest() == operation["manifest_sha256_external"]
    assert terminal_json.read_text(encoding="utf-8") == json.dumps(
        fixture.terminal_outcome.model_dump(mode="json"), ensure_ascii=False
    )
