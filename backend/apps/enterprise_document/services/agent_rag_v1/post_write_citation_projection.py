"""Writer-bound citation integrity and Artifact-owned assembly label projection.

No lexical grounding, model call, latest registry, or canonical Evidence mutation.
The assembler chooses display labels once; validators consume the frozen mapping.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.enterprise_document.schemas.post_write.foundation import (
    ExpectedItemExecutionAccountingSchema, ExpectedItemExecutionState, StageDescriptorSchema, StagePlanSchema,
    ValidationCompletionItemSchema, ValidationCompletionItemState, ValidationIntermediateKind, ValidationScope,
    ValidatorExecutionRecordSchema, ValidatorTerminalExecutionState, ValidationRunState,
)
from apps.enterprise_document.schemas.post_write.quality import (
    CitationOccurrenceTargetSchema, CitationOccurrenceBindingState, CitationTargetRefSchema,
)
from apps.enterprise_document.services.agent_rag_v1.citation_association import (
    CitationProjectionIntegrityError, associate_selected_citations,
)
from apps.enterprise_document.services.post_write.artifact_state import ArtifactCanonicalPayloadMaterializer
from apps.enterprise_document.services.post_write.citation import CitationIntegrityValidator
from apps.enterprise_document.services.post_write.execution import ValidatorInputEnvelopeBuilder
from apps.enterprise_document.services.post_write.persistence import PostWritePersistenceIntegrityError
from apps.enterprise_document.services.scheme_writer.constants import CITATION_PATTERN
from apps.enterprise_document.services.scheme_writer.document_citation_registry import DocumentCitationRegistry
from contracts.evidence import evidence_bundle_sha256
from core.hashing import canonical_sha256
from core.runtime.execution_control import checkpoint_current_execution


PARSER_ID = "scheme-writer-bracket-citation"
PARSER_VERSION = "v1"
PROJECTION_VERSION = "document-citation-display-v1"


@dataclass(frozen=True)
class CitationMarker:
    start: int
    end: int
    token: str
    alias: str
    well_formed: bool


def scan_citation_markers(text: str) -> tuple[CitationMarker, ...]:
    """Scan the existing reserved ASCII bracket-alias syntax, keeping bad tokens.

    This is not a Markdown parser or a semantic Claim association heuristic.
    Chinese explanatory brackets are outside the authored ASCII alias syntax.
    """
    if not isinstance(text, str):
        raise TypeError("citation marker scanning requires exact text")
    result, cursor = [], 0
    while True:
        start = text.find("[", cursor)
        if start < 0:
            break
        cursor = start + 1
        closing = text.find("]", cursor)
        newline = text.find("\n", cursor)
        end = closing + 1 if closing >= 0 and (newline < 0 or closing < newline) else newline if newline >= 0 else len(text)
        token = text[start:end]
        inner = token[1:-1] if token.endswith("]") else token[1:]
        probe = inner.lstrip("[ \t\r")
        if probe and not re.match(r"[A-Za-z0-9_.:\-]", probe):
            continue
        if not probe and token not in {"[", "[]", "[ ]"}:
            continue
        valid = CITATION_PATTERN.fullmatch(token)
        result.append(CitationMarker(start, end, token, valid.group(1) if valid else inner or token, bool(valid)))
        cursor = max(start + 1, end)
    return tuple(result)


def _replace_exact_markers(text: str, mapping: dict[str, str]) -> str:
    output = text
    for marker in reversed(scan_citation_markers(text)):
        if marker.well_formed and marker.alias in mapping:
            replacement = DocumentCitationRegistry.replace_markers(marker.token, mapping)
            output = output[:marker.start] + replacement + output[marker.end:]
    return output


def _unique_origins(origins):
    entries = {canonical_sha256(item): deepcopy(item) for item in origins}
    return tuple(entries[key] for key in sorted(entries))


class CitationAliasEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    alias: str = Field(min_length=1)
    citation_ref: str = Field(min_length=1)
    exact_evidence_ref: str = Field(min_length=1)
    source_identity_consistent: bool
    origin_refs: tuple[dict, ...] = Field(min_length=1)


class CitationAliasCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["post_write_citation_alias_catalog_v1"] = "post_write_citation_alias_catalog_v1"
    scope: Literal["SECTION", "DOCUMENT"]
    source_refs: tuple[dict, ...] = Field(min_length=1)
    entries: tuple[CitationAliasEntry, ...]
    catalog_hash: str

    @model_validator(mode="after")
    def _hash(self):
        if canonical_sha256(self.model_dump(mode="json", exclude={"catalog_hash"})) != self.catalog_hash:
            raise ValueError("Citation catalog hash mismatch")
        return self

    @classmethod
    def build(cls, *, scope, source_refs, entries):
        base = {"schema_version": "post_write_citation_alias_catalog_v1", "scope": scope,
            "source_refs": list(_unique_origins(source_refs)),
            "entries": [item.model_dump(mode="json") for item in sorted(entries, key=lambda item: (item.alias, item.citation_ref, item.exact_evidence_ref))]}
        return cls(**base, catalog_hash=canonical_sha256(base))

    def groups(self):
        result = defaultdict(list)
        for item in self.entries:
            result[item.alias].append(item)
        return dict(result)


class DocumentCitationProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["document_citation_projection_v1"] = "document_citation_projection_v1"
    policy_version: Literal["document-citation-display-v1"] = PROJECTION_VERSION
    source_payload_sha256: str
    source_catalogs: tuple[CitationAliasCatalog, ...]
    section_alias_maps: tuple[dict, ...]
    catalog: CitationAliasCatalog
    projection_hash: str

    @model_validator(mode="after")
    def _hash(self):
        if canonical_sha256(self.model_dump(mode="json", exclude={"projection_hash"})) != self.projection_hash:
            raise ValueError("Document citation projection hash mismatch")
        return self


def _reserved_aliases(payload, catalogs):
    reserved = set()
    included = payload.get("included_sections")
    if not isinstance(included, list) or len(included) != len(catalogs):
        raise CitationProjectionIntegrityError("included Section/catalog cardinality mismatch")
    for section, catalog in zip(included, catalogs):
        groups = catalog.groups()
        for alias, entries in groups.items():
            if len(entries) != 1 or not entries[0].source_identity_consistent:
                reserved.add(alias)
        visible = section["visible_payload"]
        for field in ("section_title", "content"):
            for marker in scan_citation_markers(visible[field]):
                if marker.well_formed and marker.alias not in groups:
                    reserved.add(marker.alias)
    return reserved


def assemble_document_citation_projection(payload, catalogs):
    """Pure assembly-time label allocation; never called by validation/replay."""
    catalogs = tuple(CitationAliasCatalog.model_validate(item) for item in catalogs)
    reserved = _reserved_aliases(payload, catalogs)
    labels, origins, first, maps, invalid = {}, defaultdict(list), {}, [], []
    next_id = 1
    for section, catalog in zip(payload["included_sections"], catalogs):
        if catalog.scope != "SECTION":
            raise CitationProjectionIntegrityError("Document assembly requires Section catalogs")
        mapping = {}
        for alias, group in catalog.groups().items():
            if len(group) != 1 or not group[0].source_identity_consistent:
                invalid.extend(group)
                continue
            entry = group[0]
            if entry.citation_ref not in labels:
                while f"C{next_id}" in reserved:
                    next_id += 1
                labels[entry.citation_ref] = f"C{next_id}"
                next_id += 1
                first[entry.citation_ref] = entry
            origins[entry.citation_ref].extend(entry.origin_refs)
            mapping[alias] = labels[entry.citation_ref]
        maps.append({"section_key": section["section_key"], "mapping": mapping})
    entries = [CitationAliasEntry(alias=label, citation_ref=ref, exact_evidence_ref=first[ref].exact_evidence_ref,
        source_identity_consistent=True, origin_refs=_unique_origins(origins[ref])) for ref, label in labels.items()]
    entries.extend(invalid)
    catalog = CitationAliasCatalog.build(scope="DOCUMENT", source_refs=tuple(
        {"section_key": section["section_key"], "section_catalog_hash": source.catalog_hash,
         "origins": list(source.source_refs)} for section, source in zip(payload["included_sections"], catalogs)), entries=entries)
    base = {"schema_version": "document_citation_projection_v1", "policy_version": PROJECTION_VERSION,
        "source_payload_sha256": canonical_sha256(payload), "source_catalogs": [item.model_dump(mode="json") for item in catalogs],
        "section_alias_maps": maps, "catalog": catalog.model_dump(mode="json")}
    projection = DocumentCitationProjection(**base, projection_hash=canonical_sha256(base))
    output = deepcopy(payload)
    for item, mapping in zip(output["included_sections"], maps):
        for field in ("section_title", "content"):
            item["visible_payload"][field] = _replace_exact_markers(item["visible_payload"][field], mapping["mapping"])
    output["citation_projection"] = projection.model_dump(mode="json")
    return output


def validate_document_citation_projection(payload, source_payload, catalogs):
    """Verify stored aliases against exact sources; never allocate new labels."""
    projection = DocumentCitationProjection.model_validate(payload.get("citation_projection"))
    catalogs = tuple(CitationAliasCatalog.model_validate(item) for item in catalogs)
    if projection.source_payload_sha256 != canonical_sha256(source_payload) or projection.source_catalogs != catalogs:
        raise CitationProjectionIntegrityError("Document citation sources differ from frozen assembly inputs")
    if len(projection.section_alias_maps) != len(catalogs):
        raise CitationProjectionIntegrityError("Document citation maps are incomplete")
    if projection.catalog.scope != "DOCUMENT" or any(source.scope != "SECTION" for source in catalogs):
        raise CitationProjectionIntegrityError("Document/Section citation catalog scope mismatch")
    groups = projection.catalog.groups()
    reserved = _reserved_aliases(source_payload, catalogs)
    valid_doc = {alias: group[0] for alias, group in groups.items() if len(group) == 1 and group[0].source_identity_consistent}
    if reserved & set(valid_doc):
        raise CitationProjectionIntegrityError("Document remap made an original unresolved alias usable")
    expected = deepcopy(source_payload)
    used_refs, observed_origins = set(), defaultdict(list)
    first_sources, source_labels, invalid_entries = {}, {}, []
    expected_sources = []
    for item, source, map_row in zip(expected["included_sections"], catalogs, projection.section_alias_maps):
        if set(map_row) != {"section_key", "mapping"} or map_row["section_key"] != item["section_key"]:
            raise CitationProjectionIntegrityError("Document remap targets another Section")
        mapping = map_row["mapping"]
        invalid_entries.extend(entry for group in source.groups().values()
                               if len(group) != 1 or not group[0].source_identity_consistent for entry in group)
        valid_source = {alias: group[0] for alias, group in source.groups().items() if len(group) == 1 and group[0].source_identity_consistent}
        if set(mapping) != set(valid_source):
            raise CitationProjectionIntegrityError("Document remap omitted or invented a source alias")
        for alias, entry in valid_source.items():
            target = valid_doc.get(mapping[alias])
            if target is None or target.citation_ref != entry.citation_ref:
                raise CitationProjectionIntegrityError("Document citation alias maps to another stable source")
            if entry.citation_ref in source_labels and source_labels[entry.citation_ref] != mapping[alias]:
                raise CitationProjectionIntegrityError("Document stable source has multiple aliases")
            source_labels[entry.citation_ref] = mapping[alias]
            first_sources.setdefault(entry.citation_ref, entry)
            used_refs.add(target.citation_ref)
            observed_origins[target.citation_ref].extend(entry.origin_refs)
        expected_sources.append({"section_key": item["section_key"], "section_catalog_hash": source.catalog_hash, "origins": list(source.source_refs)})
        for field in ("section_title", "content"):
            item["visible_payload"][field] = _replace_exact_markers(item["visible_payload"][field], mapping)
    if used_refs != {entry.citation_ref for entry in valid_doc.values()}:
        raise CitationProjectionIntegrityError("Document catalog has extra source targets")
    for entry in valid_doc.values():
        if entry.origin_refs != _unique_origins(observed_origins[entry.citation_ref]):
            raise CitationProjectionIntegrityError("Document deduplication lost or changed origin lineage")
    expected_entries = [CitationAliasEntry(alias=source_labels[ref], citation_ref=ref,
        exact_evidence_ref=entry.exact_evidence_ref, source_identity_consistent=True,
        origin_refs=_unique_origins(observed_origins[ref])) for ref, entry in first_sources.items()]
    expected_entries.extend(invalid_entries)
    if Counter(canonical_sha256(entry.model_dump(mode="json")) for entry in expected_entries) != Counter(
            canonical_sha256(entry.model_dump(mode="json")) for entry in projection.catalog.entries):
        raise CitationProjectionIntegrityError("Document catalog entries differ from exact source Evidence/lineage")
    if projection.catalog.source_refs != _unique_origins(expected_sources):
        raise CitationProjectionIntegrityError("Document catalog source provenance mismatch")
    expected["citation_projection"] = projection.model_dump(mode="json")
    if expected != payload:
        raise CitationProjectionIntegrityError("Document changed beyond its frozen citation mapping")
    return projection.catalog


class CitationIntegritySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["citation_integrity_snapshot_v1"] = "citation_integrity_snapshot_v1"
    artifact_state_ref: dict
    validation_subject_hash: str
    parser_id: Literal["scheme-writer-bracket-citation"] = PARSER_ID
    parser_version: Literal["v1"] = PARSER_VERSION
    catalog: CitationAliasCatalog
    occurrences: tuple[dict, ...]


class TargetPostWriteCitationIntegrity:
    def __init__(self, post_write):
        self.post_write = post_write

    def section_catalog(self, *, section_artifact_ref, evidence_ref, record=None):
        post_write = self.post_write
        invocation = post_write.writer_execution_store.resolve_writer_generation_invocation(evidence_ref.writer_invocation_ref)
        if invocation.canonical_evidence_input.evidence_bundle_sha256 != evidence_ref.evidence_bundle_sha256:
            raise CitationProjectionIntegrityError("Citation catalog differs from the Writer-locked Evidence hash")
        bundle = post_write._resolve_section_evidence_snapshot(evidence_ref)
        if evidence_bundle_sha256(bundle) != evidence_ref.evidence_bundle_sha256:
            raise CitationProjectionIntegrityError("Citation catalog Evidence content hash mismatch")
        subject = evidence_ref.writer_invocation_ref.logical_execution_key.writer_subject_ref
        record = record or post_write.artifact_store.resolve_exact(section_artifact_ref)
        if record.artifact_ref != section_artifact_ref:
            raise CitationProjectionIntegrityError('Citation Section projection differs from exact Artifact')
        payload = ArtifactCanonicalPayloadMaterializer.business_payload(record.canonical_payload_ref)
        if payload.get("section_id") != subject.section_id:
            raise CitationProjectionIntegrityError("Citation catalog Artifact belongs to another Writer Section")
        binding = post_write.research_runtime.resolve_current_evidence_package_binding(run_id=subject.run_id, section_id=subject.section_id)
        package = post_write.research_runtime.validate_evidence_package_binding_current(binding, require_source_fresh=True)
        if package.evidence_bundle_binding.evidence_bundle_sha256 != "sha256:" + evidence_ref.evidence_bundle_sha256:
            raise CitationProjectionIntegrityError("Citation catalog Package does not bind the exact Writer Evidence")
        source_ref = {"run_id": subject.run_id, "section_id": subject.section_id,
            "section_artifact_ref": section_artifact_ref.model_dump(mode="json"),
            "evidence_snapshot_ref": evidence_ref.model_dump(mode="json")}
        entries = []
        for row in associate_selected_citations(package=package, bundle=bundle):
            if not row.citation_ref_present:
                raise CitationProjectionIntegrityError("canonical Citation source identity is missing")
            if not row.alias_well_formed:
                raise CitationProjectionIntegrityError("canonical Citation alias is outside the bound syntax contract")
            origin = {**source_ref, "exact_evidence_ref": row.exact_evidence_ref,
                "source_locator_ref": row.source_locator_ref,
                "source_locator": row.source_locator, "evidence_id": row.evidence_id,
                "citation_source_identity": row.citation_source_identity}
            entries.append(CitationAliasEntry(alias=row.alias, citation_ref=row.citation_ref,
                exact_evidence_ref=row.exact_evidence_ref,
                source_identity_consistent=row.source_identity_consistent, origin_refs=(origin,)))
        return CitationAliasCatalog.build(scope="SECTION", source_refs=(source_ref,), entries=entries)

    def document_catalogs(self, manifest, *, section_records=None):
        return tuple(self.section_catalog(section_artifact_ref=entry.section_assembly_candidate.section_artifact_ref,
                     evidence_ref=entry.section_assembly_candidate.section_evidence_snapshot_ref,
                     record=None if section_records is None else section_records[entry.section_key])
            for entry in manifest.ordered_section_entries if entry.section_assembly_candidate is not None)

    def assemble_projection(self, payload, manifest):
        return assemble_document_citation_projection(payload, self.document_catalogs(manifest))

    def validate_document_projection(self, payload, manifest):
        source = self.post_write.document_lifecycle.document_assembler.source_document_payload(manifest)
        return validate_document_citation_projection(payload, source, self.document_catalogs(manifest))

    def _bound_document_projection(self, payload, manifest, *, section_records=None):
        projection = DocumentCitationProjection.model_validate(payload.get("citation_projection"))
        source = self.post_write.document_lifecycle.document_assembler.source_document_payload(manifest, section_records=section_records)
        catalogs = self.document_catalogs(manifest, section_records=section_records)
        if (projection.source_payload_sha256 != canonical_sha256(source)
                or projection.source_catalogs != catalogs or projection.catalog.scope != "DOCUMENT"
                or any(catalog.scope != "SECTION" for catalog in catalogs)):
            raise CitationProjectionIntegrityError("committed Document citation sources differ")
        return projection

    def committed_assembly(self, *, artifact_id, artifact_version, manifest, expected_parent_ref,
                           record=None, section_records=None):
        """Resolve only the requested immutable assembly version; no latest lookup."""
        store = self.post_write.artifact_store
        row = None
        if record is None:
            with store.database.read_transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM artifact_states WHERE artifact_id=? AND artifact_version=?",
                    (artifact_id, artifact_version),
                ).fetchone()
        if record is None and row is None:
            lifecycle = self.post_write.lifecycle_service
            run_ids = {entry.section_assembly_candidate.section_evidence_snapshot_ref.writer_invocation_ref
                       .logical_execution_key.writer_subject_ref.run_id
                       for entry in manifest.ordered_section_entries if entry.section_assembly_candidate is not None}
            if lifecycle is not None and any(lifecycle.load_run_lifecycle(run_id).status.value != "running" for run_id in run_ids):
                raise CitationProjectionIntegrityError("terminal Run is missing committed Citation assembly")
            return None
        record = record or store.resolve_exact(store._row_to_record(row).artifact_ref)
        if record.artifact_ref.artifact_id != artifact_id or record.artifact_ref.artifact_version != artifact_version:
            raise CitationProjectionIntegrityError('committed assembly exact Artifact identity differs')
        if (record.parent_artifact_ref != expected_parent_ref
                or record.origin.assembly_input_manifest_ref != manifest.manifest_id):
            raise CitationProjectionIntegrityError("committed Citation assembly lineage differs")
        payload = ArtifactCanonicalPayloadMaterializer.business_payload(record.canonical_payload_ref)
        self._bound_document_projection(payload, manifest, section_records=section_records)
        return record.artifact_ref

    def _verify_snapshot_sources(self, snapshot, *, artifact_ref, scope, subject_hash,
                                 source_sections, evidence_snapshot_ref=None, assembly_input_manifest_ref=None):
        if (snapshot.artifact_state_ref != artifact_ref.model_dump(mode="json")
                or snapshot.validation_subject_hash != subject_hash or snapshot.catalog.scope != ValidationScope(scope).value
                or snapshot.parser_id != PARSER_ID or snapshot.parser_version != PARSER_VERSION):
            raise CitationProjectionIntegrityError("committed Citation snapshot identity/version differs")
        lifecycle = self.post_write.lifecycle_service
        if lifecycle is not None:
            for source in source_sections:
                lineage = source.active_execution_lineage
                if lineage is None:
                    raise CitationProjectionIntegrityError("Citation source lineage is missing")
                current = lifecycle.resolve_active_lineage(lineage.run_id, lineage.attempt_scope_ref)
                if (current.attempt_id != lineage.attempt_id or current.input_snapshot_version != lineage.input_snapshot_version
                        or current.attempt_lifecycle_revision != lineage.attempt_lifecycle_revision
                        or current.attempt_status.value != "running" or current.run_status.value not in {"running", "finalizing", "success"}):
                    raise CitationProjectionIntegrityError("Citation source lineage is stale")
        record = self.post_write.artifact_store.resolve_exact(artifact_ref)
        payload = ArtifactCanonicalPayloadMaterializer.business_payload(record.canonical_payload_ref)
        if ValidationScope(scope) is ValidationScope.SECTION:
            if evidence_snapshot_ref is None or len(source_sections) != 1:
                raise CitationProjectionIntegrityError("Section Citation input is incomplete")
            source = source_sections[0]
            if (source.writer_generation_invocation_ref != evidence_snapshot_ref.writer_invocation_ref
                    or payload.get("content") != source.section.content
                    or payload.get("section_title") != source.section.section_title):
                raise CitationProjectionIntegrityError("committed Citation Writer/text differs")
            catalog = self.section_catalog(section_artifact_ref=artifact_ref, evidence_ref=evidence_snapshot_ref)
        else:
            if assembly_input_manifest_ref is None:
                raise CitationProjectionIntegrityError("Document Citation input is incomplete")
            manifest = self.post_write.binding_store.resolve_assembly_manifest(assembly_input_manifest_ref)
            candidates = [entry.section_assembly_candidate for entry in manifest.ordered_section_entries
                          if entry.section_assembly_candidate is not None]
            writers = {source.writer_generation_invocation_ref: source for source in source_sections}
            if (len(writers) != len(source_sections) or len(candidates) != len(source_sections)
                    or {candidate.section_evidence_snapshot_ref.writer_invocation_ref for candidate in candidates} != set(writers)):
                raise CitationProjectionIntegrityError("committed Citation Document Section set differs")
            for candidate in candidates:
                source = writers[candidate.section_evidence_snapshot_ref.writer_invocation_ref]
                section_record = self.post_write.artifact_store.resolve_exact(candidate.section_artifact_ref)
                section_payload = ArtifactCanonicalPayloadMaterializer.business_payload(section_record.canonical_payload_ref)
                if (section_payload.get("section_id") != source.section.section_id
                        or section_payload.get("section_title") != source.section.section_title
                        or section_payload.get("content") != source.section.content):
                    raise CitationProjectionIntegrityError("committed Citation Document source text differs")
            catalog = self._bound_document_projection(payload, manifest).catalog
        if catalog != snapshot.catalog:
            raise CitationProjectionIntegrityError("committed Citation catalog differs from exact sources")

    def _restore_execution(self, *, validation_run_id, subject_hash, artifact_ref, scope, source_sections,
                           evidence_snapshot_ref=None, assembly_input_manifest_ref=None):
        store = self.post_write.validation_store
        item_ref = "citation:integrity:" + artifact_ref.artifact_hash
        plan = self.stage_plan()
        run = store.resolve_run_exact(validation_run_id)
        if run.subject_key.subject_hash != subject_hash or run.execution_state not in {ValidationRunState.RUNNING, ValidationRunState.COMPLETED}:
            raise CitationProjectionIntegrityError("Citation ValidationRun subject/state differs")
        record = store.find_validator_execution_for_item(validation_run_id=validation_run_id,
            validator_id=plan.validator_id, expected_item_ref=item_ref)
        if record is None:
            if run.execution_state is ValidationRunState.COMPLETED:
                raise CitationProjectionIntegrityError("completed ValidationRun is missing Citation execution")
            lifecycle = self.post_write.lifecycle_service
            if lifecycle is not None and any(source.active_execution_lineage is None
                    or lifecycle.load_run_lifecycle(source.active_execution_lineage.run_id).status.value != "running"
                    for source in source_sections):
                raise CitationProjectionIntegrityError("terminal Run is missing Citation execution")
            if self.post_write.claim_quality_projection is not None and self.post_write.claim_quality_projection.is_terminal_replay(source_sections):
                raise CitationProjectionIntegrityError("terminal Run is missing Citation execution")
            # A partially committed input is not permission to materialize a new input.
            with store.database.read_transaction() as connection:
                pending = connection.execute(
                    "SELECT event_id FROM validation_execution_events WHERE validation_run_id=? "
                    "AND event_kind='INTERMEDIATE_COMMITTED' AND json_extract(event_json, '$.payload.target_ref')=?",
                    (validation_run_id, item_ref),
                ).fetchone()
            if pending is not None:
                raise CitationProjectionIntegrityError("incomplete committed Citation execution requires exact recovery; refusing redispatch")
            return None
        if (record.validation_run_id != validation_run_id or record.subject_hash != subject_hash
                or record.validator_id != plan.validator_id or record.validator_version != plan.validator_version
                or record.stage_plan_ref != plan.stage_plan_ref or record.stage_plan_version != plan.stage_plan_version
                or record.terminal_execution_state is not ValidatorTerminalExecutionState.COMPLETED
                or len(record.intermediate_refs) != 1 or len(record.expected_item_accounting) != 1):
            raise CitationProjectionIntegrityError("committed Citation execution identity/version differs")
        intermediate = record.intermediate_refs[0]
        if intermediate.validation_run_id != validation_run_id or intermediate.intermediate_kind is not ValidationIntermediateKind.TARGET_ACCOUNTING:
            raise CitationProjectionIntegrityError("committed Citation intermediate binding differs")
        snapshot = self.post_write.validation_intermediate_runtime.resolve_typed(intermediate, CitationIntegritySnapshot)
        self._verify_snapshot_sources(snapshot, artifact_ref=artifact_ref, scope=scope, subject_hash=subject_hash,
            source_sections=source_sections, evidence_snapshot_ref=evidence_snapshot_ref,
            assembly_input_manifest_ref=assembly_input_manifest_ref)
        provenance = ("citation-integrity-snapshot://sha256/" + canonical_sha256(snapshot.model_dump(mode="json")),)
        envelope = ValidatorInputEnvelopeBuilder(validation_view_resolver=self.post_write.validation_view_resolver).build(
            validation_run_id=validation_run_id, subject_hash=subject_hash, artifact_state_ref=artifact_ref,
            stage_plan=plan, applicable_expected_validation_item_refs=(item_ref,), exact_authority_refs=provenance)
        accounting = record.expected_item_accounting[0]
        expected_ref = "validator-execution:citation-integrity:" + canonical_sha256({"input_hash": envelope.input_hash, "item_ref": item_ref})
        if (record.input_hash != envelope.input_hash or record.validator_execution_ref != expected_ref
                or accounting.expected_validation_item_ref != item_ref
                or accounting.execution_state is not ExpectedItemExecutionState.COMPLETED
                or accounting.fact_refs != record.produced_fact_refs or accounting.provenance_refs != provenance):
            raise CitationProjectionIntegrityError("committed Citation execution input/accounting differs")
        facts = tuple(store.resolve_fact(ref) for ref in record.produced_fact_refs)
        targets = {row["target"]["occurrence_ref"] for row in snapshot.occurrences}
        if (bool(targets) != bool(facts) or any(fact.validation_run_id != validation_run_id or fact.subject_hash != subject_hash
                or fact.family.value != "CITATION_INTEGRITY" or fact.target_ref not in targets
                or fact.payload.get("alias_catalog_ref") != "citation-alias-catalog://sha256/" + snapshot.catalog.catalog_hash
                for fact in facts)):
            raise CitationProjectionIntegrityError("committed Citation Fact source binding differs")
        return snapshot, record.produced_fact_refs, facts

    def read_committed_inputs(self, **arguments):
        """Downstream consumer seam; never authorizes scanning or rematerialization."""
        restored = self._restore_execution(**arguments)
        if restored is None:
            raise CitationProjectionIntegrityError("Grounding requires committed Citation Integrity inputs")
        return restored

    def validate_committed_document(self, payload, manifest, *, validation_run_id, subject_hash, artifact_ref, source_sections):
        restored = self._restore_execution(validation_run_id=validation_run_id, subject_hash=subject_hash,
            artifact_ref=artifact_ref, scope=ValidationScope.DOCUMENT, source_sections=source_sections,
            assembly_input_manifest_ref=manifest.as_ref())
        if restored is None:
            raise CitationProjectionIntegrityError("Document consumer requires committed Citation execution")
        return restored[0].catalog

    def resolve_snapshot(self, *, artifact_ref, scope, subject_hash, source_sections, evidence_snapshot_ref=None, assembly_input_manifest_ref=None):
        post_write = self.post_write
        record = post_write.artifact_store.resolve_exact(artifact_ref)
        payload = ArtifactCanonicalPayloadMaterializer.business_payload(record.canonical_payload_ref)
        scope = ValidationScope(scope)
        if scope is ValidationScope.SECTION:
            if evidence_snapshot_ref is None or len(source_sections) != 1:
                raise CitationProjectionIntegrityError("Section Citation input is incomplete")
            catalog = self.section_catalog(section_artifact_ref=artifact_ref, evidence_ref=evidence_snapshot_ref)
            source = source_sections[0]
            if payload.get("content") != source.section.content or payload.get("section_title") != source.section.section_title:
                raise CitationProjectionIntegrityError("Section Citation text differs from actual Artifact")
            locations = [(source.section.section_id, field, payload[field]) for field in ("section_title", "content")]
        elif scope is ValidationScope.DOCUMENT:
            if assembly_input_manifest_ref is None:
                raise CitationProjectionIntegrityError("Document Citation input is incomplete")
            manifest = post_write.binding_store.resolve_assembly_manifest(assembly_input_manifest_ref)
            catalog = self.validate_document_projection(payload, manifest)
            locations = [(item["section_key"], field, item["visible_payload"][field]) for item in payload["included_sections"] for field in ("section_title", "content")]
        else:
            raise CitationProjectionIntegrityError("unsupported Citation validation scope")
        occurrences = []
        for section_id, field, text in locations:
            for ordinal, marker in enumerate(scan_citation_markers(text)):
                source_span = {"section_id": section_id, "field": field, "start": marker.start, "end": marker.end, "ordinal": ordinal}
                identity = {"artifact_state_ref": artifact_ref.model_dump(mode="json"), "parser_id": PARSER_ID,
                    "parser_version": PARSER_VERSION, "span": source_span, "token": marker.token}
                digest = canonical_sha256(identity)
                target = CitationOccurrenceTargetSchema(occurrence_ref="citation-occurrence://sha256/" + digest,
                    artifact_state_ref=artifact_ref, validation_subject_hash=subject_hash, citation_alias=marker.alias,
                    source_span_ref="citation-span://sha256/" + canonical_sha256(source_span), occurrence_hash=digest)
                occurrences.append({"target": target.model_dump(mode="json"), "token": marker.token, "well_formed": marker.well_formed, "span": source_span})
        return CitationIntegritySnapshot(artifact_state_ref=artifact_ref.model_dump(mode="json"), validation_subject_hash=subject_hash,
            catalog=catalog, occurrences=tuple(occurrences))

    @staticmethod
    def facts(snapshot, *, validation_run_id, subject_hash):
        facts, groups = [], snapshot.catalog.groups()
        catalog_ref = "citation-alias-catalog://sha256/" + snapshot.catalog.catalog_hash
        for observed in snapshot.occurrences:
            occurrence = CitationOccurrenceTargetSchema.model_validate(observed["target"])
            valid_syntax = observed["well_formed"]
            facts.append(CitationIntegrityValidator.project_rule(validation_run_id=validation_run_id, subject_hash=subject_hash,
                occurrence=occurrence, rule_id="PW-R-CIT-I-01", alias_catalog_ref=catalog_ref,
                binding_state=CitationOccurrenceBindingState.VALID if valid_syntax else CitationOccurrenceBindingState.INVALID,
                reason_codes=("WELL_FORMED" if valid_syntax else "MALFORMED",)))
            if not valid_syntax:
                continue
            group = groups.get(occurrence.citation_alias, [])
            target = None
            if len(group) == 1:
                entry = group[0]
                origins = [origin for origin in entry.origin_refs if origin.get("section_id") == observed["span"]["section_id"]]
                exact_evidence_ref = origins[0].get("exact_evidence_ref", entry.exact_evidence_ref) if origins else entry.exact_evidence_ref
                target_material = {"citation_ref": entry.citation_ref, "exact_evidence_ref": exact_evidence_ref,
                    "catalog_ref": catalog_ref, "origins": [origin for origin in entry.origin_refs]}
                digest = canonical_sha256(target_material)
                target = CitationTargetRefSchema(citation_target_ref="citation-target://sha256/" + digest,
                    exact_evidence_ref=exact_evidence_ref, target_hash=digest)
            facts.append(CitationIntegrityValidator.project_rule(validation_run_id=validation_run_id, subject_hash=subject_hash,
                occurrence=occurrence, rule_id="PW-R-CIT-I-02", alias_catalog_ref=catalog_ref,
                binding_state=CitationOccurrenceBindingState.VALID if target else CitationOccurrenceBindingState.INVALID,
                citation_target_ref=target, reason_codes=("RESOLVED_UNIQUE" if target else "UNKNOWN_ALIAS" if not group else "AMBIGUOUS_ALIAS",)))
            if target is None:
                continue
            consistent = group[0].source_identity_consistent
            facts.append(CitationIntegrityValidator.project_rule(validation_run_id=validation_run_id, subject_hash=subject_hash,
                occurrence=occurrence, rule_id="PW-R-CIT-I-03", alias_catalog_ref=catalog_ref,
                binding_state=CitationOccurrenceBindingState.VALID if consistent else CitationOccurrenceBindingState.INVALID,
                citation_target_ref=target if consistent else None,
                reason_codes=("EXACT_WRITER_SOURCE_LINEAGE" if consistent else "CITATION_SOURCE_IDENTITY_MISMATCH",)))
        return tuple(facts)

    @staticmethod
    def stage_plan():
        stage = StageDescriptorSchema(stage_ref="citation-integrity-rules-stage:v1", stage_version="v1", sequence_index=0,
            stage_input_schema_ref="schema://citation-integrity-snapshot/v1", stage_output_schema_ref="schema://citation-integrity-rule-fact/v1",
            execution_policy_ref="policy://citation-integrity-rules/v1", execution_policy_version="v1", timeout_seconds=10, max_attempts=1)
        base = {"schema_version": "validator_stage_plan_v1", "stage_plan_ref": "stage-plan:citation-integrity-rules:v1",
            "validator_id": CitationIntegrityValidator.VALIDATOR_ID, "validator_version": "v2", "stage_plan_version": "v1", "stages": [stage.model_dump(mode="json")]}
        return StagePlanSchema(**base, stage_plan_hash=canonical_sha256(base))

    def complete_item(self, *, validation_run_id, subject_hash, artifact_ref, scope, source_sections, evidence_snapshot_ref=None, assembly_input_manifest_ref=None):
        post_write = self.post_write
        arguments = dict(artifact_ref=artifact_ref, scope=scope, subject_hash=subject_hash, source_sections=source_sections,
            evidence_snapshot_ref=evidence_snapshot_ref, assembly_input_manifest_ref=assembly_input_manifest_ref)
        restored = self._restore_execution(validation_run_id=validation_run_id, **arguments)
        item_ref = "citation:integrity:" + artifact_ref.artifact_hash
        if restored is not None:
            snapshot, fact_refs, facts = restored
        else:
            snapshot = self.resolve_snapshot(**arguments)
            self._verify_snapshot_sources(snapshot, **arguments)
            source_hash = canonical_sha256(snapshot.model_dump(mode="json"))
            facts = self.facts(snapshot, validation_run_id=validation_run_id, subject_hash=subject_hash)
            plan = self.stage_plan()
            provenance = ("citation-integrity-snapshot://sha256/" + source_hash,)
            envelope = ValidatorInputEnvelopeBuilder(validation_view_resolver=post_write.validation_view_resolver).build(
                validation_run_id=validation_run_id, subject_hash=subject_hash, artifact_state_ref=artifact_ref,
                stage_plan=plan, applicable_expected_validation_item_refs=(item_ref,), exact_authority_refs=provenance)
            execution_ref = "validator-execution:citation-integrity:" + canonical_sha256({"input_hash": envelope.input_hash, "item_ref": item_ref})
            checkpoint_current_execution()
            intermediate = post_write.validation_intermediate_runtime.commit(validation_run_id=validation_run_id,
                producer_ref=plan.validator_id, target_ref=item_ref, typed_intermediate=snapshot,
                intermediate_kind=ValidationIntermediateKind.TARGET_ACCOUNTING, occurred_at_epoch=0.0)
            fact_refs = tuple(post_write.validation_store.put_fact_if_absent_or_same(fact) for fact in facts)
            self._verify_snapshot_sources(snapshot, **arguments)
            with post_write.database.read_transaction() as connection:
                sequence = post_write.validation_store._next_event_sequence(connection, validation_run_id)
            event = post_write.validation_store._event(validation_run_id=validation_run_id,
                event_id="validator-terminal:citation-integrity:" + canonical_sha256(execution_ref), sequence=sequence,
                event_kind="VALIDATOR_COMPLETED", payload={"validator_execution_ref": execution_ref}, occurred_at_epoch=0.0)
            accounting = ExpectedItemExecutionAccountingSchema(expected_validation_item_ref=item_ref,
                execution_state=ExpectedItemExecutionState.COMPLETED, fact_refs=fact_refs, provenance_refs=provenance)
            base = {"schema_version": "validator_execution_record_v1", "validator_execution_ref": execution_ref,
                "validation_run_id": validation_run_id, "subject_hash": subject_hash, "validator_id": plan.validator_id,
                "validator_version": plan.validator_version, "stage_plan_ref": plan.stage_plan_ref, "stage_plan_version": plan.stage_plan_version,
                "input_hash": envelope.input_hash, "terminal_event_id": event.event_id, "terminal_execution_state": "COMPLETED",
                "expected_item_accounting": [accounting.model_dump(mode="json")], "produced_fact_refs": [ref.model_dump(mode="json") for ref in fact_refs],
                "intermediate_refs": [intermediate.model_dump(mode="json")]}
            post_write.validation_store.finalize_validator_execution(event,
                ValidatorExecutionRecordSchema(**base, record_sha256=canonical_sha256(base)), stage_plan=plan, input_envelope=envelope)
        states = {fact.semantic_state.value for fact in facts}
        state = ValidationCompletionItemState.NOT_APPLICABLE if not facts else ValidationCompletionItemState.FAIL if "FAIL" in states else ValidationCompletionItemState.INDETERMINATE if "INDETERMINATE" in states else ValidationCompletionItemState.PASS
        return ValidationCompletionItemSchema(expected_validation_item_ref=item_ref, required=True, state=state, fact_refs=fact_refs)
