"""Assembly V2 closes the archived V1 selected-Control recovery gap."""
import json
import pytest

from core.hashing import canonical_sha256
from apps.enterprise_document.schemas.post_write.control import CorrectionLineageProjectionSchema
from apps.enterprise_document.schemas.post_write import CurrentnessState
from apps.enterprise_document.services.post_write import (
    AssemblyInputManifestBuilder, DocumentBindingStore, PostWriteEntryStore,
    SectionAssemblyEligibilityResolver,
    active_selection_from_manifest_entry,
    ArtifactUsabilityIntegrityResolver,
)
from backend.tests.test_post_write_correction_control import _fixture, _decide, _rehash_currentness


@pytest.mark.parametrize("action", ["CORRECT", "BLOCK"])
def test_distinct_exact_controls_survive_withheld_durable_manifest_reopen(tmp_path, action):
    fx = _fixture(tmp_path, quality="FAIL", repair_remaining=2)
    overrides = {}
    if action == "BLOCK":
        stale = _rehash_currentness(fx["currentness"], CurrentnessState.STALE)
        overrides = dict(fresh_currentness_proof=stale,
            artifact_usability_integrity_proof=ArtifactUsabilityIntegrityResolver(fx["artifact_store"]).resolve(
                artifact_state_ref=fx["artifact_ref"], currentness_proof=stale))
    first = _decide(fx, **overrides)
    payload = fx["lineage"].model_dump(mode="json")
    payload.pop("projection_hash")
    payload["repair_budget_revision"] += 1
    lineage = CorrectionLineageProjectionSchema(
        **payload, projection_hash=canonical_sha256(payload))
    second = _decide(fx, correction_lineage_projection=lineage, **overrides)
    assert first.as_ref() != second.as_ref()
    assert first.input_manifest.post_write_entry_ref == second.input_manifest.post_write_entry_ref
    assert first.delivery_gate_decision_ref == second.delivery_gate_decision_ref
    assert first.enforcement_action.value == second.enforcement_action.value == action
    resolver = SectionAssemblyEligibilityResolver(
        control_store=fx["control_store"], entry_store=PostWriteEntryStore(fx["database"]))
    store = DocumentBindingStore(fx["database"])
    builder = AssemblyInputManifestBuilder(store=store)
    entries = [builder.selection_entry(section_key="section-control-1",
        eligibility=resolver.resolve(section_key="section-control-1", control_decision_ref=d.as_ref()))
        for d in (first, second)]
    assert entries[0] != entries[1]
    assert entries[0].selected_section_control_decision_ref == first.as_ref()
    assert entries[1].selected_section_control_decision_ref == second.as_ref()
    assert entries[0].selected_section_control_decision_ref != entries[1].selected_section_control_decision_ref
    assert entries[0].section_assembly_candidate is None
    selections = [active_selection_from_manifest_entry(entry) for entry in entries]
    assert selections[0] != selections[1]
    assert selections[0].selected_section_control_decision_ref == first.as_ref()
    assert selections[1].selected_section_control_decision_ref == second.as_ref()
    assert selections[0].control_decision_ref is None
    manifests = [builder.build_and_persist(document_logical_id="document-logical:run-control-1:doc-control-1",
        required_section_keys=("section-control-1",), ordered_section_entries=(entry,)) for entry in entries]
    assert manifests[0] != manifests[1]
    reopened = DocumentBindingStore(fx["database"])
    for manifest_ref, d in zip(manifests, (first, second)):
        durable = reopened.resolve_assembly_manifest(manifest_ref)
        assert durable.as_ref() == manifest_ref
        assert durable.ordered_section_entries[0].selected_section_control_decision_ref == d.as_ref()
        text = json.dumps(durable.model_dump(mode="json"))
        assert d.control_decision_id in text
        assert d.control_decision_hash in text
        assert fx["control_store"].resolve_exact(d.as_ref()) == d
