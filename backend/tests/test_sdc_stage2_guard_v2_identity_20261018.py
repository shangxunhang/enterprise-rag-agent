"""Guard identity and SQLite isolation in a fresh, controlled E: test root.

The v1 comparison is today's frozen prechange source identity, not a recovered
2026-10-15 environment. SQLite v1/v2 records below are newly created fixtures;
no historical runtime is opened, resumed, or evaluated.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.post_write import (
    RequiredDimensionConditionSchema,
    ValidationCompletionItemState,
    ValidationFactFamily,
    ValidationScope,
    ValidationSemanticState,
)
from apps.enterprise_document.services.agent_rag_v1.post_write_pipeline_identity import (
    pipeline_identity_material, semantic_pipeline_sha256, validation_run_identity,
)
from apps.enterprise_document.services.post_write import (
    FormalExecutabilityGuard,
    PostWritePersistenceIntegrityError,
    QualityDeliveryContractRegistry,
    ValidationRunFactory,
)
from apps.enterprise_document.services.post_write.gate import GateInputIntegrityError
from backend.tests.test_bca014_i6_semantic_pipeline_identity import composition
from backend.tests.test_post_write_delivery_gate import _evaluate, _gate_fixture


REPO = Path(__file__).resolve().parents[2]
FROZEN_V1 = REPO / "docs/sdc_stage2_formal_guard_v2_d1a_archive_only_20261018_r02/frozen_prechange/consistency_v1.py.txt"
FROZEN_V1_SHA256 = "e2c6c2573feb5d8e7423f098fab17b78213b0d46a3e7e3d22c88535dd3ec3c57"
V2_PRODUCER = "post-write-formal-executability:v2"
GUARD_CONTRACT = "apps.enterprise_document.services.post_write.consistency.FormalExecutabilityGuard.PRODUCER_REF"


def _frozen_v1_producer():
    source = FROZEN_V1.read_bytes()
    assert sha256(source).hexdigest() == FROZEN_V1_SHA256
    guard = next(node for node in ast.parse(source).body
                 if isinstance(node, ast.ClassDef) and node.name == "FormalExecutabilityGuard")
    declaration = next(node for node in guard.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "PRODUCER_REF"
                               for target in node.targets))
    producer = ast.literal_eval(declaration.value)
    assert producer == "post-write-formal-executability:v1"
    return producer


def _record(key, value):
    destination = os.environ.get("SDC_GUARD_V2_IDENTITY_REPORT")
    if not destination:
        return
    path = Path(destination)
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "comparison_basis": "CURRENT_PRECHANGE_V1_IDENTITY_AND_NEW_SYNTHETIC_SQLITE_FIXTURES",
        "historical_20261015_environment_restored": False,
        "frozen_v1_source_sha256": FROZEN_V1_SHA256,
        "historical_roots_opened": 0,
        "provider_calls": 0,
    }
    payload[key] = value
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _identities(scope, subject_hash, quality_contract_ref):
    assert FormalExecutabilityGuard.PRODUCER_REF == V2_PRODUCER
    owner = composition()
    with patch.object(FormalExecutabilityGuard, "PRODUCER_REF", _frozen_v1_producer()):
        v1 = pipeline_identity_material(owner, scope)
    v2 = pipeline_identity_material(owner, scope)
    normalized = deepcopy(v1)
    changed = []
    for node in normalized["composition_topology"]:
        for contract in node["contracts"]:
            if contract["contract_ref"] == GUARD_CONTRACT:
                assert contract["contract_identity"] == "post-write-formal-executability:v1"
                contract["contract_identity"] = V2_PRODUCER
                changed.append(contract["contract_ref"])
    assert changed == [GUARD_CONTRACT]
    assert normalized == v2, "Guard producer must be the only identity difference"
    pipeline_hashes = [semantic_pipeline_sha256(material) for material in (v1, v2)]
    generation = "c" * 64
    run_ids = [validation_run_identity(subject_hash=subject_hash,
        quality_contract_ref=quality_contract_ref, semantic_pipeline_sha256=pipeline_hash,
        verification_generation_sha256=generation) for pipeline_hash in pipeline_hashes]
    assert pipeline_hashes[0] != pipeline_hashes[1]
    assert run_ids[0] != run_ids[1]
    return {"scope": scope.value, "subject_hash": subject_hash,
        "quality_contract_ref": quality_contract_ref.model_dump(mode="json"),
        "verification_generation_sha256": generation,
        "v1_producer": _frozen_v1_producer(), "v2_producer": V2_PRODUCER,
        "v1_pipeline_sha256": pipeline_hashes[0], "v2_pipeline_sha256": pipeline_hashes[1],
        "v1_validation_run_id": run_ids[0], "v2_validation_run_id": run_ids[1],
        "only_changed_contract": GUARD_CONTRACT}


@pytest.mark.parametrize("scope", [ValidationScope.SECTION, ValidationScope.DOCUMENT])
def test_frozen_current_v1_and_live_v2_identity_change_only_guard(scope):
    quality = SimpleNamespace(model_dump=lambda **kwargs: {"quality": "identity-only-fixed-fixture"})
    row = _identities(scope, "a" * 64, quality)
    _record("pure_" + scope.value, row)


def _file_hashes(root):
    return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def _version_fixture(root, version):
    """Use existing lawful entry/currentness/Gate setup, changing only Run ID."""
    captured = {}
    original_register = QualityDeliveryContractRegistry.register
    original_create = ValidationRunFactory.create

    def register(registry, **kwargs):
        ref = original_register(registry, **kwargs)
        captured["quality"] = ref
        return ref

    def create(factory, **kwargs):
        run = original_create(factory, **kwargs)
        identities = _identities(ValidationScope.SECTION, run.subject_key.subject_hash,
                                 captured["quality"])
        captured["identities"] = identities
        return run.model_copy(update={"validation_run_id": identities[version + "_validation_run_id"]})

    with patch.object(QualityDeliveryContractRegistry, "register", register), \
            patch.object(ValidationRunFactory, "create", create):
        fixture = _gate_fixture(root,
            fact_states=(("fact-consistency-version-fixture", ValidationFactFamily.ARTIFACT_CONSISTENCY,
                          ValidationSemanticState.FAIL, "relation-version-fixture"),),
            completion_states=(("consistency:item-version-fixture", True,
                                ValidationCompletionItemState.FAIL, "fact-consistency-version-fixture"),),
            conditions=(RequiredDimensionConditionSchema(dimension_id="consistency",
                fact_families=(ValidationFactFamily.ARTIFACT_CONSISTENCY,)),))
    fixture["gate"] = _evaluate(fixture)
    fixture["identity_comparison"] = captured["identities"]
    return fixture


def test_new_synthetic_sqlite_versions_do_not_reuse_completion_gate_or_fact(tmp_path):
    configured_root = Path(os.environ["SDC_GUARD_V2_NATIVE_ROOT"]).resolve()
    assert configured_root.drive.casefold() == "e:"
    assert tmp_path.resolve().is_relative_to(configured_root / "pytest")
    v1_root, v2_root = tmp_path / "synthetic-v1-identity", tmp_path / "v2-identity"
    assert not v1_root.exists() and not v2_root.exists()
    v1 = _version_fixture(v1_root, "v1")
    preserved_v1 = _file_hashes(v1_root)
    v2 = _version_fixture(v2_root, "v2")
    assert v1["identity_comparison"] == v2["identity_comparison"]
    assert v1["subject_hash"] == v2["subject_hash"]
    assert v1["run_id"] != v2["run_id"]
    assert v1["completion_ref"] != v2["completion_ref"]
    assert v1["gate"].as_ref() != v2["gate"].as_ref()
    fact_id = "fact-consistency-version-fixture"
    assert v1["fact_refs"][fact_id] != v2["fact_refs"][fact_id]
    assert v2["completion"].items[0].fact_refs == (v2["fact_refs"][fact_id],)
    assert v2["gate"].validation_run_id == v2["run_id"]
    assert v2["gate"].gate_input_manifest.validation_completion_ref == v2["completion_ref"]
    assert v2["gate"].gate_input_manifest.validation_fact_refs == (v2["fact_refs"][fact_id],)
    assert v2["validation_store"].list_facts_for_run(v2["run_id"]) == (v2["facts"][fact_id],)
    before_rejections = _file_hashes(v2_root)
    with pytest.raises(GateInputIntegrityError):
        v2["service"].evaluate(validation_run_id=v2["run_id"],
            validation_completion_ref=v1["completion_ref"], quality_delivery_contract_ref=v2["contract_ref"],
            currentness_proof=v2["currentness"])
    with pytest.raises(PostWritePersistenceIntegrityError):
        v2["validation_store"].resolve_fact(v1["fact_refs"][fact_id])
    with pytest.raises(PostWritePersistenceIntegrityError):
        v2["service"].resolve_exact(v1["gate"].as_ref())
    assert _file_hashes(v2_root) == before_rejections
    assert _file_hashes(v1_root) == preserved_v1
    _record("synthetic_sqlite", {**v2["identity_comparison"],
        "comparison_level": "NEW_SYNTHETIC_SQLITE_BINDING_FIXTURES_NOT_OLD_RUNTIME_REPLAY",
        "v1_database": str(v1["database"].path), "v2_database": str(v2["database"].path),
        "v1_completion_ref": v1["completion_ref"].model_dump(mode="json"),
        "v2_completion_ref": v2["completion_ref"].model_dump(mode="json"),
        "v1_gate_ref": v1["gate"].as_ref().model_dump(mode="json"),
        "v2_gate_ref": v2["gate"].as_ref().model_dump(mode="json"),
        "v1_fact_ref": v1["fact_refs"][fact_id].model_dump(mode="json"),
        "v2_fact_ref": v2["fact_refs"][fact_id].model_dump(mode="json"),
        "quality_in_both_fixtures": "FAIL_SYNTHETIC_STORAGE_TEST",
        "cross_version_completion_fact_gate_rejections": 3,
        "rejected_input_file_changes": [], "synthetic_v1_file_changes": [],
        "document_global": "REAL_CONSUMER_PROOF_OWNED_BY_MAIN_NATIVE_TEST"})
