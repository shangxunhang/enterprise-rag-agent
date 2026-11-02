"""Read-only Target review-draft access from the *existing* validated output.

Review-draft availability is not quality approval or a publication authority.
This service never writes Artifacts, re-evaluates semantic Facts, or overrides Gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.persistence.safe_paths import SafeExternalIdentifier, SafePathResolver

from apps.enterprise_document.schemas.post_write.control import (
    DocumentPostWriteControlInputManifestSchema,
)
from apps.enterprise_document.schemas.post_write.document import (
    AssemblySelectionState,
)
from apps.enterprise_document.schemas.post_write.foundation import (
    ArtifactScope,
    ArtifactStateRefSchema,
    EnforcementAction,
    OverallQualityState,
    PostWriteControlDecisionRefSchema,
)
from apps.enterprise_document.schemas.post_write.currentness import CurrentnessState
from apps.enterprise_document.services.post_write.artifact_state import (
    ArtifactCanonicalPayloadMaterializer,
    ArtifactStateIntegrityError,
)
from apps.enterprise_document.services.post_write.persistence import (
    PostWritePersistenceIntegrityError,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import (
    ArtifactIntegrityError,
)
from apps.enterprise_document.services.post_write.currentness import (
    CurrentnessIntegrityError,
)
from apps.enterprise_document.services.post_write.control import (
    ArtifactUsabilityIntegrityState,
)
from apps.enterprise_document.services.post_write.currentness import (
    ActiveComparisonMode,
)


class ReviewDraftUnavailable(ValueError):
    """An exact, complete, current and conforming review document is unavailable."""


@dataclass(frozen=True)
class TargetReviewDraft:
    """Transient projection over canonical owners; no new persisted authority."""

    document_artifact_ref: ArtifactStateRefSchema
    output_binding: Any
    validated_output_bytes: bytes
    editable_review_text: str
    quality_state: OverallQualityState
    control_action: EnforcementAction
    dimensions: tuple[tuple[str, str], ...]
    blocking_fact_ids: tuple[str, ...]
    warning_fact_ids: tuple[str, ...]
    unresolved_reason_refs: tuple[str, ...]

    @property
    def formal_quality_eligible(self) -> bool:
        """Quality prerequisite only; *not* human formal-release authorization."""
        return (
            self.quality_state is OverallQualityState.PASS
            and self.control_action is EnforcementAction.OUTPUT
            and not self.blocking_fact_ids
            and not self.unresolved_reason_refs
        )

    def as_result_reference(self) -> dict[str, Any]:
        """Small client-facing read receipt; never put document bytes in Run State."""
        return {
            "document_artifact_ref": self.document_artifact_ref.model_dump(mode="json"),
            "validated_output_ref": self.output_binding.output_artifact_ref.model_dump(
                mode="json"
            ),
            "output_format": self.output_binding.output_format,
            "output_payload_sha256": self.output_binding.output_payload_sha256,
            "quality_state": self.quality_state.value,
            "control_action": self.control_action.value,
            "formal_quality_eligible": self.formal_quality_eligible,
            "formal_release_authorized": False,
            "requires_human_confirmation": True,
            "dimensions": [
                {"dimension_id": key, "state": state}
                for key, state in self.dimensions
            ],
            "blocking_fact_ids": list(self.blocking_fact_ids),
            "warning_fact_ids": list(self.warning_fact_ids),
            "unresolved_reason_refs": list(self.unresolved_reason_refs),
        }


class TargetReviewDraftReader:
    """Validate immutable output for review independently of PublishedRelease."""

    def __init__(self, post_write: Any) -> None:
        self.post_write = post_write

    def read(
        self,
        *,
        document_artifact_ref: ArtifactStateRefSchema,
        control_decision_ref: PostWriteControlDecisionRefSchema,
        task_id: str,
        run_id: str,
    ) -> TargetReviewDraft:
        try:
            return self._read_exact(
                document_artifact_ref=document_artifact_ref,
                control_decision_ref=control_decision_ref,
                task_id=task_id,
                run_id=run_id,
            )
        except (
            PostWritePersistenceIntegrityError,
            ArtifactIntegrityError,
            CurrentnessIntegrityError,
            FileNotFoundError,
        ) as exc:
            raise ReviewDraftUnavailable(
                "exact review draft integrity or currentness check failed"
            ) from exc

    def _read_exact(
        self,
        *,
        document_artifact_ref: ArtifactStateRefSchema,
        control_decision_ref: PostWriteControlDecisionRefSchema,
        task_id: str,
        run_id: str,
    ) -> TargetReviewDraft:
        artifact_ref = ArtifactStateRefSchema.model_validate(document_artifact_ref)
        control_ref = PostWriteControlDecisionRefSchema.model_validate(
            control_decision_ref
        )
        if artifact_ref.artifact_scope is not ArtifactScope.DOCUMENT:
            raise ReviewDraftUnavailable("review draft requires DOCUMENT Artifact")
        post_write = self.post_write
        control = post_write.control_store.resolve_exact(control_ref)
        if not isinstance(
            control.input_manifest, DocumentPostWriteControlInputManifestSchema
        ):
            raise ReviewDraftUnavailable("review draft requires DOCUMENT Control")
        if (
            control.input_manifest.artifact_state_ref != artifact_ref
            or control.as_ref() != control_ref
        ):
            raise ReviewDraftUnavailable("review draft Control/Artifact mismatch")
        if control.enforcement_action not in {
            EnforcementAction.OUTPUT,
            EnforcementAction.OUTPUT_BEST_EFFORT,
        }:
            raise ReviewDraftUnavailable("review draft is withheld by Control")

        record = post_write.artifact_store.resolve_exact(artifact_ref)
        head = post_write.artifact_store.resolve_current(artifact_ref.artifact_id)
        if head.artifact_ref != artifact_ref:
            raise ReviewDraftUnavailable("review draft is not the current Artifact")
        proof = control.fresh_currentness_proof
        if (
            proof.currentness_state is not CurrentnessState.CURRENT
            or proof.active_projection.artifact_state_ref != artifact_ref
        ):
            raise ReviewDraftUnavailable("review draft has no current binding")
        fresh = post_write.currentness_resolver.prove(
            historical_entry_ref=control.input_manifest.post_write_entry_ref,
            active_projection=proof.active_projection,
            comparison_mode=ActiveComparisonMode.EXPECTED_BINDING_IDENTITY,
        )
        if (
            fresh.currentness_state is not CurrentnessState.CURRENT
            or fresh.expected_active_entry_identity
            != proof.expected_active_entry_identity
        ):
            raise ReviewDraftUnavailable("review draft binding changed or is stale")
        usability = post_write.usability_resolver.resolve(
            artifact_state_ref=artifact_ref, currentness_proof=fresh
        )
        if usability.state is not ArtifactUsabilityIntegrityState.TRUSTED_USABLE:
            raise ReviewDraftUnavailable("review draft Artifact is not trusted")

        gate = post_write.gate_store.resolve_exact(control.delivery_gate_decision_ref)
        if (
            gate.gate_input_manifest.artifact_state_ref != artifact_ref
            or gate.overall_quality_state != control.quality_state_passthrough
            or gate.as_ref() != control.delivery_gate_decision_ref
        ):
            raise ReviewDraftUnavailable("review draft quality authority mismatch")

        # A conformance proof binds the rendered output to its visible
        # projection. It does not replace the independent Assembly Manifest
        # membership obligation: reject duplicates, missing Sections and
        # cross-section substitution even if a renderer is mocked as conforming.
        manifest_ref = control.input_manifest.assembly_input_manifest_ref
        if proof.active_projection.assembly_input_manifest_ref != manifest_ref:
            raise ReviewDraftUnavailable("review draft active Assembly binding differs")
        manifest = post_write.binding_store.resolve_assembly_manifest(manifest_ref)
        required = tuple(manifest.required_section_keys)
        entries = tuple(manifest.ordered_section_entries)
        if (
            not required
            or len(required) != len(set(required))
            or tuple(item.section_key for item in entries) != required
            or any(
                item.selection_state is not AssemblySelectionState.INCLUDED
                or item.section_assembly_candidate is None
                for item in entries
            )
        ):
            raise ReviewDraftUnavailable(
                "review draft Assembly Manifest is not a complete included set"
            )
        visible = ArtifactCanonicalPayloadMaterializer.business_payload(
            record.canonical_payload_ref
        )
        sections = visible.get("included_sections")
        if (
            visible.get("payload_kind") != "document"
            or visible.get("document_logical_id") != manifest.document_logical_id
            or not isinstance(sections, list)
            or len(sections) != len(required)
            or visible.get("partial")
            or visible.get("missing_section_keys")
            or visible.get("withheld_section_keys")
        ):
            raise ReviewDraftUnavailable(
                "review draft is missing document Sections; not a complete draft"
            )
        for expected, entry in zip(required, sections):
            if (
                not isinstance(entry, dict)
                or entry.get("section_key") != expected
                or not isinstance(entry.get("visible_payload"), dict)
                or entry["visible_payload"].get("section_id") != expected
            ):
                raise ReviewDraftUnavailable(
                    "review draft Sections differ from required Assembly membership"
                )
        output_bindings = tuple(record.validated_output_bindings)
        if len(output_bindings) != 1:
            raise ReviewDraftUnavailable(
                "review draft output format must be unambiguous"
            )
        binding = output_bindings[0]
        if binding.output_format not in {"markdown", "json"}:
            raise ReviewDraftUnavailable("unsupported review draft output format")
        if (
            binding.output_artifact_ref.task_id != task_id
            or binding.output_artifact_ref.run_id != run_id
        ):
            raise ReviewDraftUnavailable("review draft output belongs to another Run")
        try:
            post_write.output_conformance_verifier.resolve_bound_conforming(
                record=record, output_binding=binding
            )
        except ArtifactStateIntegrityError as exc:
            raise ReviewDraftUnavailable("review draft output is not conforming") from exc
        raw = post_write.output_store.get_bytes(
            binding.output_artifact_ref.artifact_ref,
            task_id=task_id,
            run_id=run_id,
            input_snapshot_version=binding.output_artifact_ref.input_snapshot_version,
        )
        if sha256(raw).hexdigest() != binding.output_payload_sha256:
            raise ReviewDraftUnavailable("review draft output checksum mismatch")

        text_sections = []
        for index, entry in enumerate(sections, start=1):
            section = entry.get("visible_payload")
            if not isinstance(section, dict) or section.get("payload_kind") != "section":
                raise ReviewDraftUnavailable("review draft Section is malformed")
            title, section_id, content = (
                str(section.get("section_title") or "").strip(),
                str(section.get("section_id") or "").strip(),
                str(section.get("content") or ""),
            )
            if not title or not section_id:
                raise ReviewDraftUnavailable("review draft Section identity is missing")
            text_sections.append(f"{index}、{title}\n{content}")

        return TargetReviewDraft(
            document_artifact_ref=artifact_ref,
            output_binding=binding,
            validated_output_bytes=raw,
            editable_review_text="\n\n".join(text_sections),
            quality_state=gate.overall_quality_state,
            control_action=control.enforcement_action,
            dimensions=tuple(
                (d.dimension_id, d.state.value)
                for d in gate.dimension_result_projections
            ),
            blocking_fact_ids=tuple(f.fact_id for f in gate.blocking_fact_refs),
            warning_fact_ids=tuple(f.fact_id for f in gate.warning_fact_refs),
            unresolved_reason_refs=tuple(gate.unresolved_reason_refs),
        )


class TargetReviewDraftLocalExporter:
    """Run-scoped editable copy, never a formal Release or new Quality Owner.

    The canonical validated output stays in the existing output_store.
    Markdown's opaque machine header is removed only in this derivative copy;
    its checksum is separate from the canonical output checksum.
    """

    def __init__(self, runs_dir: str | Path) -> None:
        self.paths = SafePathResolver(Path(runs_dir) / "review_drafts")

    def export(self, *, draft: TargetReviewDraft, task_id: str,
               run_id: str) -> dict[str, str]:
        safe_task = SafeExternalIdentifier.validate(task_id, field_name="task_id")
        safe_run = SafeExternalIdentifier.validate(run_id, field_name="run_id")
        if (
            draft.output_binding.output_artifact_ref.task_id != safe_task
            or draft.output_binding.output_artifact_ref.run_id != safe_run
        ):
            raise ReviewDraftUnavailable("review copy request is foreign to the Run")
        output_format = draft.output_binding.output_format
        if output_format == "markdown":
            content = (draft.editable_review_text + "\n").encode("utf-8")
            suffix = ".md"
        elif output_format == "json":
            content = draft.validated_output_bytes
            suffix = ".json"
        else:
            raise ReviewDraftUnavailable("review copy has unsupported output format")
        artifact_hash = draft.document_artifact_ref.artifact_hash
        filename = f"review-draft-v{draft.document_artifact_ref.artifact_version}-{artifact_hash}{suffix}"
        folder = self.paths.resolve(safe_task, safe_run)
        target = self.paths.resolve(safe_task, safe_run, filename)
        digest = sha256(content).hexdigest()
        folder.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or sha256(target.read_bytes()).hexdigest() != digest:
                raise ReviewDraftUnavailable(
                    "existing review copy differs from current validated Artifact"
                )
        else:
            temporary = self.paths.resolve(
                safe_task, safe_run, f".{filename}.{uuid4().hex}.tmp"
            )
            try:
                with temporary.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Only a derived run-local copy is written, never the canonical
                # output or an existing release/publication artifact.
                if target.exists():
                    if sha256(target.read_bytes()).hexdigest() != digest:
                        raise ReviewDraftUnavailable(
                            "concurrent review copy differs from current Artifact"
                        )
                else:
                    os.replace(temporary, target)
            except OSError as exc:
                raise ReviewDraftUnavailable(
                    "review copy could not be written to local output"
                ) from exc
            finally:
                if temporary.exists():
                    temporary.unlink()
        if sha256(target.read_bytes()).hexdigest() != digest:
            raise ReviewDraftUnavailable("review copy post-write checksum mismatch")
        return {
            "review_file_path": str(target),
            "review_file_sha256": digest,
            "review_copy_format": output_format,
            "source_validated_output_sha256": draft.output_binding.output_payload_sha256,
            "delivery_kind": "HUMAN_REVIEW_COPY_NOT_FORMAL_RELEASE",
        }


__all__ = [
    "ReviewDraftUnavailable",
    "TargetReviewDraft",
    "TargetReviewDraftReader",
    "TargetReviewDraftLocalExporter",
]
