"""Deterministic projection of live StagePlans, explicit contracts and topology.

No source bytes, paths, object identities, data generation or durable state.
"""
import inspect
from pydantic import BaseModel
from core.hashing import canonical_sha256
from . import post_write_requirement_adherence as requirement
from . import post_write_citation_projection as integrity
from . import post_write_coverage_composition as coverage
from . import post_write_claim_projection as claims
from . import post_write_faithfulness_composition as certainty
from . import post_write_consistency_composition as consistency
from . import post_write_citation_grounding as grounding
from apps.enterprise_document.services.post_write import coverage as structural
from apps.enterprise_document.services.post_write import relevancy

PIPELINE_IDENTITY_CONTRACT = "post-write-semantic-pipeline-identity-v1"


def explicit_contract_identities(module, owner):
    """Read existing first-class strings and typed schema version declarations.

    The imported Fact projectors and intermediate schema declarations are part
    of the selected producer's semantic contract. Names are stable identifiers,
    never runtime repr/id or filesystem locations.
    """
    values = {}
    def defaults(prefix, callable_value):
        if not callable(callable_value):
            return
        try:
            parameters = inspect.signature(callable_value).parameters
        except (ValueError, TypeError):
            return
        for name, parameter in parameters.items():
            if isinstance(parameter.default, str) and ("contract" in name or "version" in name):
                values[prefix + name] = parameter.default

    def constants(prefix, namespace):
        for name, value in namespace.items():
            if isinstance(value, str) and name.isupper() and (
                    "CONTRACT" in name or "VERSION" in name or "POLICY" in name
                    or name in {"PRODUCER_REF", "PARSER_ID", "VALIDATOR_ID"}):
                values[prefix + name] = value
    constants(module.__name__ + ".", vars(module))
    constants(type(owner).__name__ + "." if not isinstance(owner, type) else owner.__name__ + ".", vars(owner if isinstance(owner, type) else type(owner)))
    for name, cls in sorted(vars(module).items()):
        if not isinstance(cls, type):
            continue
        constants(cls.__module__ + "." + cls.__name__ + ".", vars(cls))
        for method_name in sorted(name for name in vars(cls) if not name.startswith("_")):
            defaults(cls.__module__ + "." + cls.__name__ + "." + method_name + ".", getattr(cls, method_name))
        if issubclass(cls, BaseModel):
            field = cls.model_fields.get("schema_version")
            if field is not None and isinstance(field.default, str):
                values[cls.__module__ + "." + cls.__name__ + ".schema_version"] = field.default
    if module is requirement:
        constants(relevancy.__name__ + ".", vars(relevancy))
    return tuple({"contract_ref": name, "contract_identity": value} for name, value in sorted(values.items()))


def pipeline_identity_material(composition, scope):
    """Match _complete_validation execution and Completion insertion topology."""
    nodes = []
    def node(role, owner, module, dimension, placement, dependencies=(), plan=None):
        stage_plan = plan if plan is not None else owner.stage_plan()
        nodes.append(dict(role=role, dimension=dimension, required=True,
            completion_placement=placement, depends_on=tuple(dependencies),
            validator_identity={key: getattr(stage_plan, key) for key in (
                "validator_id", "validator_version", "stage_plan_ref", "stage_plan_version", "stage_plan_hash")},
            contracts=explicit_contract_identities(module, owner),
            stage_contracts=tuple(dict(stage_input_schema_ref=stage.stage_input_schema_ref,
                stage_output_schema_ref=stage.stage_output_schema_ref,
                execution_policy_ref=stage.execution_policy_ref,
                execution_policy_version=stage.execution_policy_version) for stage in stage_plan.stages)))
    if composition.requirement_adherence is not None:
        node("requirement_adherence", composition.requirement_adherence, requirement, "relevancy", "required_dimension")
    node("citation_integrity", composition.citation_integrity, integrity, "citation", "extra:citation_item")
    if composition.coverage_composition is not None:
        node("coverage_composition", composition.coverage_composition, coverage, "coverage", "required_dimension", ("citation_integrity",))
    elif scope.value == "DOCUMENT":
        node("structural_coverage", structural.CoverageProjector, structural, "coverage", "required_dimension",
            plan=composition._coverage_stage_plan())
    if composition.claim_quality_projection is not None:
        node("claim_quality_projection", composition.claim_quality_projection, claims, "faithfulness", "extra:projected_items")
        if composition.faithfulness_fth03 is None or composition.citation_grounding is None:
            raise ValueError("Claim pipeline requires FTH-03 and Citation Grounding")
        node("faithfulness_fth03", composition.faithfulness_fth03, certainty, "faithfulness", "required_dimension", ("claim_quality_projection",))
    if composition.consistency_composition is not None:
        node("consistency_composition", composition.consistency_composition, consistency, "consistency", "required_dimension",
            ("claim_quality_projection",) if composition.claim_quality_projection is not None else ())
    if composition.claim_quality_projection is not None:
        node("citation_grounding", composition.citation_grounding, grounding, "citation", "required_dimension", ("citation_integrity", "claim_quality_projection"))
    return dict(pipeline_identity_contract_version=PIPELINE_IDENTITY_CONTRACT,
        required_dimensions=tuple(composition.REQUIRED_DIMENSIONS),
        ordered_validator_identities=tuple(node["validator_identity"] for node in nodes),
        composition_topology=tuple(nodes),
        completion_order=tuple(("required_dimension", dimension) for dimension in composition.REQUIRED_DIMENSIONS)
            + tuple(("extra", node["role"]) for node in nodes if node["completion_placement"] == "extra:projected_items")
            + (("extra", "citation_integrity"),))


def semantic_pipeline_sha256(material):
    return canonical_sha256(material)


def validation_run_identity(*, subject_hash, quality_contract_ref, semantic_pipeline_sha256, verification_generation_sha256):
    return "validation_run://sha256/" + canonical_sha256(dict(subject_hash=subject_hash,
        quality_contract_ref=quality_contract_ref.model_dump(mode="json"),
        semantic_pipeline_sha256=semantic_pipeline_sha256,
        verification_generation_sha256=verification_generation_sha256))
