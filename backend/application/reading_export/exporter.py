"""Orchestrate SRCREV-03's derived reading export from one exact release.

The source adapter proves the immutable release file chain and cross-binds the
caller-supplied TerminalOutcome. This module only projects display bytes and
delivers the three derived files outside the business store.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable

from application.reading_export.delivery import (
    MANIFEST_NAME,
    READING_NAME,
    REPORT_NAME,
    deliver,
)
from application.reading_export.projection import (
    READING_RULE_VERSION,
    project_reading_document,
)
from application.reading_export.source import load_published_markdown
from application.terminal_outcome_schema import TerminalOutcomeResultV1
from core.hashing import canonical_sha256


EXPORT_MANIFEST_VERSION = "src-rev-03-export-manifest-v1"
TERMINAL_OUTCOME_SOURCE = "caller_supplied_exact_receipt"
SOURCE_DISPLAY_STATE = "unavailable_in_release_chain"
_OPTIONS = {"format": "markdown", "source_display": "release_projection_only"}


@dataclass(frozen=True)
class ReadingExportReceipt:
    status: str
    final_dir: Path
    export_identity: str
    file_sha256s: tuple[tuple[str, str], ...]
    source_release_id: str
    terminal_outcome_result_id: str
    terminal_outcome_source: str = TERMINAL_OUTCOME_SOURCE
    authorization_boundary: str = "not_independently_verified"
    checkpoint_provenance: str = "not_independently_verified"


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _report_field(value: object) -> str:
    """Keep exporter-authored Markdown fields on one inert text line."""
    return (str(value).replace("\\", "\\\\").replace("\r", " ")
            .replace("\n", " ").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("`", "\\`").replace("|", "\\|")
            .replace("[", "\\[").replace("]", "\\]").replace("*", "\\*")
            .replace("_", "\\_").replace("#", "\\#"))


def _source_identity(package) -> dict:
    manifest = package.manifest
    active_projection = package.proof.release_time_currentness_proof.active_projection
    projection = package.citation_projection
    projection_hash = (projection.projection_hash if hasattr(projection, "projection_hash")
                       else projection["projection_hash"])
    if package.terminal_outcome_source != TERMINAL_OUTCOME_SOURCE:
        raise ValueError("unsupported TerminalOutcome receipt source")
    return {
        "task_id": manifest.task_id,
        "run_id": manifest.run_id,
        "document_id": active_projection.document_id,
        "document_logical_id": manifest.document_logical_id,
        "document_artifact_ref": manifest.document_artifact_ref.model_dump(mode="json"),
        "publication_release_id": manifest.release_id,
        "release_manifest_artifact_ref": package.release_manifest_artifact.artifact_ref,
        "release_manifest_payload_sha256": manifest.manifest_payload_sha256,
        "release_input_proof_sha256": manifest.release_input_proof_sha256,
        "release_payload_sha256": manifest.release_payload_sha256,
        "validated_output_binding_sha256": manifest.validated_output_binding_sha256,
        "output_format": manifest.output_format,
        "renderer_contract_version": manifest.renderer_contract_version,
        "document_citation_projection_hash": projection_hash,
        "terminal_outcome_result_id": package.terminal_outcome_result_id,
        "terminal_outcome_source": TERMINAL_OUTCOME_SOURCE,
        "source_display_state": SOURCE_DISPLAY_STATE,
    }


def export_reading_markdown(
    *,
    target_release_root: Path,
    release_id: str,
    terminal_outcome: TerminalOutcomeResultV1 | dict | None,
    output_dir: Path,
    terminal_receipt_path: Path | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> ReadingExportReceipt:
    """Read an exact publication and deliver its deterministic reading copy.

    `terminal_receipt_path` only protects a CLI input file from destination
    overlap. The terminal receipt's provenance remains the caller's duty.
    """
    root = Path(target_release_root)
    package = load_published_markdown(
        target_release_root=root,
        release_id=release_id,
        terminal_outcome=terminal_outcome,
    )
    projection = project_reading_document(
        visible_payload=package.visible_payload,
        citation_projection=package.visible_payload["citation_projection"],
        status_view=package.status_view,
    )
    identity_source = _source_identity(package)
    identity_material = {
        "source": identity_source,
        "export_rule_version": READING_RULE_VERSION,
        "options": _OPTIONS,
        "source_display_hashes": list(projection.source_display_hashes),
    }
    export_identity = "reading-export://sha256/" + canonical_sha256(identity_material)

    reading_bytes = projection.reading_markdown.encode("utf-8")
    reading_sha256 = sha256(reading_bytes).hexdigest()
    report_lines = [
        "# EXPORT_REPORT",
        "",
        "本报告只记录阅读派生与输入绑定，不重新判定原业务质量。",
        "",
        f"- export identity：`{_report_field(export_identity)}`",
        f"- export rule version：`{_report_field(READING_RULE_VERSION)}`",
        f"- publication release_id：`{_report_field(package.manifest.release_id)}`",
        f"- release manifest SHA-256：`{package.manifest.manifest_payload_sha256}`",
        f"- release proof SHA-256：`{package.manifest.release_input_proof_sha256}`",
        f"- source output SHA-256：`{package.manifest.release_payload_sha256}`",
        f"- source output UTF-8 bytes：{len(package.payload_bytes)}",
        f"- Artifact ID/version/hash：`{_report_field(package.manifest.document_artifact_ref.artifact_id)}` / "
        f"{package.manifest.document_artifact_ref.artifact_version} / "
        f"`{package.manifest.document_artifact_ref.artifact_hash}`",
        f"- original Control action：`{_report_field(package.status_view['control_action'])}`",
        f"- original Quality state：`{_report_field(package.manifest.quality_state_passthrough.value)}`",
        f"- TerminalOutcome result_id：`{_report_field(package.terminal_outcome_result_id)}`",
        f"- terminal outcome source：`{TERMINAL_OUTCOME_SOURCE}`",
        "- TerminalOutcome checkpoint provenance：未由 exporter 独立验证",
        "- authorization boundary：exporter 未独立核验；应用内调用方负责自身授权，独立 CLI 为 local_admin",
        f"- status projection version：`{_report_field(package.status_view['status_projection_version'])}`",
        f"- reading SHA-256：`{reading_sha256}`",
        "- source display state：发布 projection 中未记录的名称/位置保持“未记录”",
        "",
        projection.report_markdown.rstrip("\n"),
        "",
    ]
    report_bytes = "\n".join(report_lines).encode("utf-8")
    source_output_bytes = len(package.payload_bytes)
    statistics = {"source_output_bytes": source_output_bytes, **projection.statistics}
    manifest_value = {
        "schema_version": EXPORT_MANIFEST_VERSION,
        "export_identity": export_identity,
        "export_rule_version": READING_RULE_VERSION,
        "options": dict(_OPTIONS),
        "source": identity_source,
        "status_projection_version": package.status_view["status_projection_version"],
        "ordered_section_ids": [item["section_id"] for item in projection.title_transformations],
        "statistics": statistics,
        "section_completeness": dict(projection.section_completeness),
        "title_transformations": list(projection.title_transformations),
        "citation_diagnostics": list(projection.citation_diagnostics),
        "source_display_missing": list(projection.source_display_missing),
        "source_display_hashes": list(projection.source_display_hashes),
        "files": [
            {"path": READING_NAME, "bytes": len(reading_bytes), "sha256": reading_sha256},
            {"path": REPORT_NAME, "bytes": len(report_bytes), "sha256": sha256(report_bytes).hexdigest()},
        ],
    }
    manifest_bytes = _json_bytes(manifest_value)
    forbidden = [root.resolve(strict=True).parent]
    if terminal_receipt_path is not None:
        forbidden.append(Path(terminal_receipt_path).resolve(strict=True))
    delivered = deliver(
        Path(output_dir),
        {READING_NAME: reading_bytes, REPORT_NAME: report_bytes,
         MANIFEST_NAME: manifest_bytes},
        export_identity,
        forbidden_roots=forbidden,
        fault_injector=fault_injector,
    )
    return ReadingExportReceipt(
        status=delivered.status,
        final_dir=delivered.final_dir,
        export_identity=export_identity,
        file_sha256s=delivered.file_sha256s,
        source_release_id=package.manifest.release_id,
        terminal_outcome_result_id=package.terminal_outcome_result_id,
    )
