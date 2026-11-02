"""348 production semantic quality policy population and lineage acceptance."""
from pathlib import Path

import pytest
import yaml

from apps.enterprise_document.config.quality_delivery import (
    SemanticQualityDeliveryConfig,
    load_semantic_quality_delivery_config,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.services.agent_rag_v1.post_write_composition import (
    TargetPostWriteComposition,
)
from apps.enterprise_document.services.post_write.persistence import (
    PostWriteSqliteDatabase,
    QualityDeliveryContractStore,
    RequirementStore,
)
from apps.enterprise_document.services.post_write.policy import (
    QualityDeliveryContractRegistry,
)
from apps.enterprise_document.services.post_write.requirements import (
    RequirementMaterializer,
)
from core.hashing import canonical_sha256


CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "enterprise_document"
    / "config"
    / "quality_delivery_v1.yaml"
)


def test_production_semantic_quality_config_is_hash_bound_and_global_applicable():
    config = load_semantic_quality_delivery_config(CONFIG_PATH)
    assert config.config_id == "quality-delivery-semantic-default"
    assert config.config_version == "v1"
    assert config.consistency_procedure.document_global_applicability == "APPLICABLE"
    assert config.consistency_procedure.applicability_reason == (
        "PRODUCTION_DOCUMENT_GLOBAL_CONSISTENCY_REQUIRED"
    )
    assert [
        item.target_ref
        for item in config.consistency_procedure.document_global_selectors
    ] == [
        "consistency-obligation://document-global/all-included-assertions/v1"
    ]
    payload = config.model_dump(mode="json", exclude={"config_hash"})
    assert canonical_sha256(payload) == config.config_hash


def test_semantic_quality_config_tamper_without_hash_change_fails_closed(tmp_path):
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["consistency_procedure"]["applicability_reason"] = "tampered"
    path = tmp_path / "quality_delivery_v1.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="config hash"):
        load_semantic_quality_delivery_config(path)


def test_config_lowers_to_exact_consistency_requirement_source():
    config = load_semantic_quality_delivery_config(CONFIG_PATH)
    entry = config.requirement_entry()
    assert entry.requirement_kind == "consistency_procedure"
    assert entry.statement == config.source_ref
    assert set(entry.constraints) == {"consistency_procedure"}
    assert entry.requirement_id == config.requirement_id
    assert config.config_hash in config.source_ref


def test_contract_population_points_to_exact_materialized_config_requirement(tmp_path):
    config = load_semantic_quality_delivery_config(CONFIG_PATH)
    database = PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")
    requirement_store = RequirementStore(database)
    requirement_ref = RequirementMaterializer(requirement_store).materialize(
        semantic_contract_version="quality-config-lineage-v1",
        source_business_ref=config.source_ref,
        source_task_ref="task://quality-config-lineage",
        requirements=(config.requirement_entry(),),
    )
    procedure = config.procedure_input(requirement_ref=requirement_ref)
    assert procedure.applicability_source_refs[0].requirement_ref == requirement_ref
    assert procedure.applicability_source_refs[0].requirement_id == config.requirement_id
    snapshot = requirement_store.resolve_exact(requirement_ref)
    source = next(
        item for item in snapshot.requirements
        if item.requirement_id == config.requirement_id
    )
    assert source.statement == config.source_ref


def test_same_contract_identity_cannot_silently_accept_changed_semantic_policy(tmp_path):
    config = load_semantic_quality_delivery_config(CONFIG_PATH)
    database = PostWriteSqliteDatabase(tmp_path / "post_write.sqlite3")
    requirement_ref = RequirementMaterializer(RequirementStore(database)).materialize(
        semantic_contract_version="quality-config-contract-v1",
        source_business_ref=config.source_ref,
        source_task_ref="task://quality-config-contract",
        requirements=(config.requirement_entry(),),
    )
    registry = QualityDeliveryContractRegistry(QualityDeliveryContractStore(database))
    from apps.enterprise_document.schemas.post_write.gate import RequiredDimensionConditionSchema

    registry.register(
        contract_id="quality://same-id",
        contract_version="v1",
        required_dimensions=(RequiredDimensionConditionSchema(dimension_id="consistency"),),
        consistency_procedure_input=config.procedure_input(
            requirement_ref=requirement_ref
        ),
    )
    changed_declaration = config.consistency_procedure.model_copy(
        update={"applicability_reason": "changed semantic policy"}
    )
    changed_payload = {
        "schema_version": config.schema_version,
        "config_id": config.config_id,
        "config_version": config.config_version,
        "consistency_procedure": changed_declaration.model_dump(mode="json"),
    }
    changed = SemanticQualityDeliveryConfig(
        **changed_payload,
        config_hash=canonical_sha256(changed_payload),
    )
    changed_ref = RequirementMaterializer(RequirementStore(database)).materialize(
        semantic_contract_version="quality-config-contract-v1",
        source_business_ref=changed.source_ref,
        source_task_ref="task://quality-config-contract",
        requirements=(changed.requirement_entry(),),
    )
    with pytest.raises(Exception):
        registry.register(
            contract_id="quality://same-id",
            contract_version="v1",
            required_dimensions=(RequiredDimensionConditionSchema(dimension_id="consistency"),),
            consistency_procedure_input=changed.procedure_input(
                requirement_ref=changed_ref
            ),
        )


def test_cons_348_16_does_not_expand_project_input_schema():
    fields = set(ProjectInputSchema.model_fields)
    assert "consistency_procedure" not in fields
    assert "global_consistency" not in fields
    assert "consistency_requirements" not in fields


def test_target_composition_default_config_path_is_production_file():
    source = Path(
        __import__(
            "apps.enterprise_document.services.agent_rag_v1.post_write_composition",
            fromlist=["__file__"],
        ).__file__
    ).read_text(encoding="utf-8")
    assert '"quality_delivery_v1.yaml"' in source
    assert "load_semantic_quality_delivery_config" in source
