"""Frozen V2.1 Writer authority: governed prose once, raw retained by exact refs.

Producer tests use current chunkers, governance, packing and Bundle mapping.
Schema-boundary tests are separately marked as unowned input, not producer facts.
"""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.research import EvidencePackageSelectionSchema
from apps.enterprise_document.schemas.pre_write_admission import EvidenceCriterionSchema, EvidenceRequirementSchema
from apps.enterprise_document.services.scheme_writer.writer_admission import EvidenceRequirementsMaterializer
from apps.enterprise_document.services.agent_rag_v1 import writer_evidence_projection as projection
from core.hashing import canonical_sha256
from rag.catalog.document_profile import DocumentProfileSchema
from rag.context.context_packer import ContextPacker
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from rag.plugins.chunkers.project_fact_record import ProjectFactRecordChunker
from rag.plugins.chunkers.template_section import TemplateSectionChunker
from rag.plugins.chunkers.policy_article_clause import PolicyArticleClauseChunker
from rag.plugins.chunkers.table_row_record import TableRowRecordChunker
from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.schema.structured_evidence import build_structured_evidence
from schemas.rag import RAGToolInputSchema, RetrievalAccessScopeSchema, EvidenceRequirementsSchema, RAGEvidenceAssessmentSchema
from test_stage_c_structured_chunkers import _unit
from test_support.stage_d_fixtures import document_profile
from test_multi_agent_v3_writer_input_boundary import r1_mainline
from apps.enterprise_document.services.agent_rag_v1.writer_context import TargetWriterContextComposer, TargetWriterContextIntegrityError
from schemas.context import ContextPackageSchema

RAW = "FPBCA_V21_SOURCE_BODY_must_stay_canonical_a190f0c8"
TASK_TOPIC = "Task-owned independent writing requirement b8c91237"


class _Dump:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **kwargs):
        return deepcopy(self.value)


def _catalog(profile):
    payload = {k:deepcopy(profile[k]) for k in ["schema_version","document_id","document_type",
        "evidence_role","kb_id","tenant_id","authority","content_capabilities","parsing"]}
    payload["project_relation"] = {"relation":profile["project_relation"],"project_id":profile["project_id"]}
    payload["classification"] = {"method":"manual_seed","confidence":1.0,"reviewed":True}
    return DocumentProfileSchema.model_validate(payload)


def _record(kind, texts, *, doc_id=None, rank=1, technical=False, source_name=None):
    classes = {"fact":ProjectFactRecordChunker,"template":TemplateSectionChunker,
               "policy":PolicyArticleClauseChunker,"table":TableRowRecordChunker}
    doc_id = doc_id or "doc-v21-"+kind
    if kind == "template":
        profile=document_profile(document_id=doc_id,kb_id="template_style_kb",document_type="document_template",
            evidence_role="template_style",project_relation="general",project_id=None,
            allowed_claim_types=["template_structure","style_guidance"])
    elif kind == "policy":
        profile=document_profile(document_id=doc_id,kb_id="policy_standard_kb",document_type="policy_standard",
            evidence_role="policy_standard",project_relation="general",project_id=None,allowed_claim_types=["policy_requirement"])
    elif technical:
        profile=document_profile(document_id=doc_id,document_type="technical_whitepaper",evidence_role="technical_reference",
            project_relation="other_project",project_id="other-project-"+str(rank),allowed_claim_types=["technical_specification"],
            forbidden_claim_types=["current_project_cost"])
    else:
        profile=document_profile(document_id=doc_id,document_type="current_project_requirement",evidence_role="project_fact",
            allowed_claim_types=["current_project_cost","current_project_fact"])
    profile["parsing"]["chunk_strategy"]=classes[kind].STRATEGY
    profile["parsing"]["parser_strategy"]={"fact":"project_fact_extraction","template":"template_document",
        "policy":"policy_clause","table":"structured_table"}[kind]
    _catalog(profile)
    units=[]
    for index,text in enumerate(texts,1):
        unit=_unit(document_id=doc_id,strategy=classes[kind].STRATEGY,order=index,text=text,
            kb_id=profile["kb_id"],evidence_role=profile["evidence_role"],unit_type="table" if kind=="table" else "paragraph")
        unit["extra"]["document_profile"]=profile
        if source_name is not None:
            unit["source_name"]=source_name
        units.append(unit)
    chunked=classes[kind](parent_chunk_size=5000,child_chunk_size=5000).chunk_records(units)
    child=next(c for c in chunked.children if RAW in json.dumps(c,ensure_ascii=False)) if any(RAW in x for x in texts) or source_name else chunked.children[-1]
    parent=next(p for p in chunked.parents if p["parent_chunk_id"]==child["parent_chunk_id"])
    result=build_retrieval_result_v2(child_chunk=child,parent_chunk=parent,rank=rank,score=.99-rank*.01)
    return result


def _bundle(records, *, query="预算金额", budget=100, topics=None):
    request=RAGToolInputSchema(task_id="task-fpbca-v21",run_id="run-fpbca-v21",agent_name="ResearchAgent",query=query,
        access_scope=RetrievalAccessScopeSchema(tenant_id="default",authorized_kb_ids=["project_fact_kb","policy_standard_kb","template_style_kb"]),
        max_context_chars=12000,max_context_items=3,retrieval_trace_id="trace-fpbca-v21",retrieval_scope="section",
        evidence_requirements=EvidenceRequirementsSchema.from_topics(section_id="investment",section_title="独立验证",
            section_type="general",topics=topics or [query]))
    invocation=RAGRequestMapper().map(request)
    gov=OnlineEvidenceGovernanceV2().evaluate(query=query,results=records,runtime_context=invocation.payload["extra_metadata"])
    for record,assessment in zip(records,gov.item_assessments):
        record["metadata"]["evidence_assessment_v2"]=assessment.model_dump(mode="json")
    packed=ContextPacker(packing_strategy="default",include_metadata=False,max_context_chars=12000).pack(records,token_budget=budget)
    return RAGResultMapper().map(request=request,invocation=invocation,
        raw_result={"data":{"retrieval_results":records,"context_pack":packed.to_dict()}},latency_ms=0,
        rag_project_root="D:/MyCode/rag-agent-shared",service_name="fpbca_v21_directed_test")


def _project(bundle, **owner):
    package=SimpleNamespace(selections=[EvidencePackageSelectionSchema(evidence_id=eid,requirement_ids=("REQ1",))
        for eid in bundle.selected_evidence_ids],evidence_bundle_binding=_Dump({"handle":"immutable","sha256":canonical_sha256(bundle.model_dump(mode="json"))}))
    return projection.project_writer_evidence(binding=_Dump({"current_version":"v1"}),package=package,bundle=bundle,
        context_text=bundle.context.context_text,citations=bundle.citations,**owner)


def _resolve(value,pointer):
    for part in pointer.lstrip("/").split("/"):
        part=part.replace("~1","/").replace("~0","~")
        value=value[int(part)] if isinstance(value,list) else value[part]
    return value


def _assert_refs(value,canonical):
    if isinstance(value,dict):
        if "bundle_json_pointer" in value:
            assert canonical_sha256(_resolve(canonical,value["bundle_json_pointer"]))==value["sha256"]
        for child in value.values():
            _assert_refs(child,canonical)
    elif isinstance(value,list):
        for child in value:
            _assert_refs(child,canonical)


@pytest.mark.parametrize("kind,texts,query,source_name",[
    ("fact",["说明："+"预算金额说明。"*100,"预算金额："+RAW+"万元。"],"预算金额",None),
    ("template",["一、格式要求","应采用"+"统一章节组织形式。"*100+RAW+"格式。"],"格式要求",None),
    ("template",["一、字段要求","应包含："+"必填字段。"*150+RAW],"字段要求",None),
    ("template",["一、占位验证","{{"+RAW+"}}"],"占位验证",None),
    ("template",["一、"+"章节"*55+RAW,"应采用统一格式。"],"统一格式",None),
    ("policy",["第一章 约束","第一条 应符合规定","应符合规定。"*100],"应符合规定",RAW),
    ("policy",["第一章 "+RAW,"第一条 应符合规定","应符合规定。"*100],"应符合规定",None),
    ("table",["|设备|参数|\n|---|---|\n|服务器|"+"合法参数说明。"*100+RAW+"|"],"设备参数",None),
])
def test_actual_producer_hidden_structured_semantics_stay_canonical(kind,texts,query,source_name):
    bundle=_bundle([_record(kind,texts,source_name=source_name)],query=query)
    canonical=bundle.model_dump(mode="json")
    before=canonical_sha256(canonical)
    assert bundle.selected_evidence_ids==["E1"]
    assert RAW in json.dumps(canonical,ensure_ascii=False)
    assert RAW not in bundle.context.context_text
    result=_project(bundle)
    # Outer source-title policy is a separate B finding. This checks the named
    # structured/assessment channels, not a policy change to catalog title.
    assert RAW not in json.dumps(result["source_constraints"],ensure_ascii=False)
    assert RAW not in json.dumps(result["assessment"],ensure_ascii=False)
    assert result["context_text"]==bundle.context.context_text
    _assert_refs(result,canonical)
    assert before==canonical_sha256(bundle.model_dump(mode="json"))


@pytest.mark.parametrize("kind,texts,query",[
    ("fact",["预算金额："+RAW+"万元。"],"预算金额"),
    ("template",["一、格式要求","编制说明："+RAW],"格式要求"),
    ("policy",["第一章 约束","第一条 应符合规定","应符合规定："+RAW],"应符合规定"),
    ("table",["|设备|参数|\n|---|---|\n|服务器|"+RAW+"|"],"设备参数"),
])
def test_visible_legitimate_body_has_one_governed_authority(kind,texts,query):
    bundle=_bundle([_record(kind,texts)],query=query,budget=4000)
    assert RAW in bundle.context.context_text
    result=_project(bundle)
    assert result["context_text"]==bundle.context.context_text
    assert json.dumps(result,ensure_ascii=False).count(RAW)==1


def _conflict_bundle(visible=False):
    first=_record("fact",["预算金额：100万元。"],doc_id="conflict-source-1",rank=1,technical=not visible)
    second_text=["预算金额："+RAW+"万元。"] if visible else ["说明："+"预算金额说明。"*100,"预算金额："+RAW+"万元。"]
    second=_record("fact",second_text,doc_id="conflict-source-2",rank=2,technical=not visible)
    return _bundle([first,second],budget=4000 if visible else 1000)


def test_producer_bound_two_selected_hidden_conflict_value_is_not_writer_body():
    bundle=_conflict_bundle()
    assert bundle.selected_evidence_ids==["E1","E2"]
    assert bundle.assessment.status.value=="conflicted"
    assert [x["fact_visible_in_prompt"] for x in bundle.assessment.details["evidence_governance_v2"]["prompt_evidence_candidates"]]==[False,False]
    assert RAW in json.dumps(bundle.assessment.conflicts,ensure_ascii=False)
    assert RAW not in bundle.context.context_text
    result=_project(bundle)
    assert RAW not in json.dumps(result,ensure_ascii=False)


def test_conflict_typed_state_and_exact_full_refs_survive_without_raw_values():
    bundle=_conflict_bundle()
    result=_project(bundle)
    view=result["assessment"]
    assert view["status"]=="conflicted"
    assert len(view["conflicts"])==len(bundle.assessment.conflicts)==1
    for index,(safe,raw) in enumerate(zip(view["conflicts"],bundle.assessment.conflicts)):
        assert safe["claim_type"]==raw["claim_type"]
        # V2.2 retains source-derived participant IDs in the exact canonical
        # conflict ref, rather than admitting them as a second string channel.
        assert "evidence_ids" not in safe
        assert safe["conflict_ref"]=={"bundle_json_pointer":f"/assessment/conflicts/{index}","sha256":canonical_sha256(raw)}
        assert not {"topic","description","candidate_values"}&set(safe)
    _assert_refs(result,bundle.model_dump(mode="json"))


def test_visible_conflict_values_are_context_only():
    bundle=_conflict_bundle(visible=True)
    assert RAW in bundle.context.context_text
    result=_project(bundle)
    assert RAW not in json.dumps(result["assessment"],ensure_ascii=False)
    assert json.dumps(result,ensure_ascii=False).count(RAW)==1


@pytest.mark.parametrize("path",[
    ("representation_type",),("original_strategy",),("actual_strategy",),("fact","record_id"),
    ("fact","fact_category"),("fact","source_span","field"),("fact","source_span","section_id"),
    ("fact","source_span","unit_order_start"),("fallback","reason"),("fallback","strategy"),
    ("table","table_id"),("policy","chapter_id"),("policy","article_id"),("policy","clause_id"),
])
def test_schema_accepted_arbitrary_strings_are_not_producer_safe_state(path):
    raw={"schema_version":"structured_evidence_v1","representation_type":"fact_record", "original_strategy":"fact_record",
        "actual_strategy":"fact_record","fact":{"fact_category":"cost","fact_status":"estimated",
        "source_span":{"unit_order_start":7,"unit_order_end":7,"text":RAW}},"fallback":{"used":False}}
    value=raw
    for part in path[:-1]:
        value=value.setdefault(part,{})
    value[path[-1]]=RAW
    result=projection._structured(raw,"/structured")
    assert RAW not in json.dumps(result,ensure_ascii=False)
    assert result["source_ref"]["sha256"]==canonical_sha256(raw)


def test_safe_numeric_location_and_typed_status_preserved():
    raw={"schema_version":"structured_evidence_v1","representation_type":"fact_record","original_strategy":"fact_record",
        "actual_strategy":"fact_record","fact":{"fact_category":"cost","fact_status":"estimated",
        "value":RAW,"source_span":{"unit_order_start":7,"unit_order_end":7,"page":3,"text":RAW,
        "field":RAW,"section_id":RAW}},"fallback":{"used":False}}
    result=projection._structured(raw,"/structured")
    assert result["fact"]["fact_status"]=="estimated"
    assert result["fact"]["fact_category"]=="cost"
    assert result["fact"]["source_span"]=={"unit_order_start":7,"unit_order_end":7,"page":3}
    assert result["fact"]["source_span_ref"]["sha256"]==canonical_sha256(raw["fact"]["source_span"])


def test_unowned_public_semantic_strings_and_judge_ids_are_opaque():
    bundle=_bundle([_record("fact",["预算金额：100万元。"])] ,budget=4000,topics=[RAW])
    # This is an explicitly unowned schema-boundary attack. It is not the
    # producer-bound conflict fixture above and supplies no Task/Profile owner.
    assessment=RAGEvidenceAssessmentSchema(required_topics=[RAW],covered_topics=[RAW],
        required_qualifiers=[RAW],reason_codes=[RAW],judge_name=RAW,judge_version=RAW,prompt_evidence_source=RAW)
    bundle=bundle.model_copy(update={"assessment":assessment},deep=True)
    result=_project(bundle)
    assert RAW not in json.dumps(result["assessment"],ensure_ascii=False)
    assert result["assessment"]["assessment_ref"]["sha256"]==canonical_sha256(assessment.model_dump(mode="json"))


def test_item_assessment_and_nested_usage_unknown_strings_are_not_copied():
    bundle=_bundle([_record("fact",["预算金额：100万元。"])] ,budget=4000)
    details=deepcopy(bundle.assessment.details)
    row=details["evidence_governance_v2"]["item_assessments"][0]
    row["supported_topics"]=[RAW]
    row["reason_codes"]=[RAW]
    row["metadata"]["required_qualifiers"]=[RAW]
    row["metadata"]["fact_claim_policies"]={"current_project_cost":{"eligible":True,"certainty_level":RAW,
        "required_qualifiers":[RAW],"reason_codes":[RAW],"raw_nested":{"body":RAW}}}
    row["metadata"]["fact_visible_in_prompt"]={"raw":RAW}
    bundle=bundle.model_copy(update={"assessment":bundle.assessment.model_copy(update={"details":details},deep=True)},deep=True)
    result=_project(bundle)
    assert RAW not in json.dumps(result["source_constraints"]["item_assessments"],ensure_ascii=False)
    _assert_refs(result,bundle.model_dump(mode="json"))


def test_known_closed_qualifiers_and_reason_codes_remain_useful():
    bundle=_bundle([_record("fact",["预算金额：100万元。"])] ,budget=4000)
    result=_project(bundle)
    assert result["assessment"]["required_qualifiers"]==["根据现有估算材料","最终金额以审批或采购结果为准"]
    assert "fact_status_estimated" in result["assessment"]["reason_codes"]
    assert result["assessment"]["judge_name"]=="document_profile_gate_v2"
    assert result["assessment"]["judge_version"]=="stage_d_v1"
    assert result["assessment"]["prompt_evidence_source"]=="context_pack_selected_results"


def test_exact_profile_task_snapshot_allows_task_owned_topics():
    snapshot=EvidenceRequirementsMaterializer.materialize(source_profile_ref="profile://independent/v1",
        source_task_ref="task://independent/v1",requirements=(EvidenceRequirementSchema(requirement_id="REQ1",
        criteria=(EvidenceCriterionSchema(criterion_id="CRITERION1",criterion=TASK_TOPIC),)),))
    bundle=_bundle([_record("fact",["预算金额：100万元。"])] ,budget=4000,topics=[TASK_TOPIC])
    result=_project(bundle,requirement_snapshot=snapshot,requirement_ids=("REQ1",))
    assert result["assessment"]["required_topics"]==[TASK_TOPIC]
    assert result["assessment"]["covered_topics"]+result["assessment"]["missing_topics"]==[TASK_TOPIC]
    assert result["assessment"]["requirement_owner"]["requirements_sha256"]==snapshot.requirements_sha256


def test_free_bundle_requirement_strings_do_not_manufacture_owner():
    bundle=_bundle([_record("fact",["预算金额：100万元。"])] ,budget=4000,topics=[TASK_TOPIC])
    result=_project(bundle)
    assert TASK_TOPIC not in json.dumps(result["assessment"],ensure_ascii=False)


def test_new_compact_identity_names_the_new_semantic_shape():
    assert (projection.PROJECTION_SCHEMA_ID,projection.PROJECTION_SCHEMA_VERSION)==("writer_evidence_package_compact_v5","v5")


def _stale_projection_identity(package, schema_id, schema_version):
    """Rebind a schema-valid package; preserve locked payload and source owners.

    Only the representation identity differs. All materialization mirroring,
    semantic hashes and package ID are recomputed, so a generic integrity error
    cannot substitute for the required current representation version check.
    """
    selected = tuple(item.model_copy(update={"projection_schema_id":schema_id,
        "projection_schema_version":schema_version},deep=True)
        if item.candidate_id=="writer-current-evidence-package" else item
        for item in package.selected_materializations)
    staged=package.model_copy(update={"selected_materializations":selected,
        "model_input":package.model_input.model_copy(update={"ordered_context_materializations":selected},deep=True)},deep=True)
    fingerprint=canonical_sha256(staged.semantic_preimage())
    package_id="ctxpkg_"+canonical_sha256({"contract":"context_package_v1",
        "invocation_subject":staged.invocation_subject.model_dump(mode="json"),
        "context_semantic_fingerprint":fingerprint})
    staged=staged.model_copy(update={"context_semantic_fingerprint":fingerprint,
        "package_ref":staged.package_ref.model_copy(update={"context_package_id":package_id})},deep=True)
    return ContextPackageSchema.model_validate(staged.model_dump(mode="json"))


@pytest.fixture(scope="module",params=("normal","fallback","target3000"))
def actual_predispatch_currentness(request,tmp_path_factory):
    """Observe the original guards while the actual lifecycle is still active.

    The reusable live Factory fixture returns after workflow completion. Testing
    current reuse at that later time would only prove terminal source staleness.
    This wrapper records checks inside its existing predispatch observer instead;
    it neither changes owner state nor suppresses a currentness/admission guard.
    """
    original_validate=TargetWriterContextComposer.validate_pre_model_call
    original_final=TargetWriterContextComposer.final_messages
    observations=[]

    def observe(composer,package,*,project_input):
        current=original_validate(composer,package,project_input=project_input)
        seen={}
        # Restore the original guard during nested final_messages calls; avoid
        # recursively invoking this observation wrapper, without bypassing it.
        with patch.object(TargetWriterContextComposer,"validate_pre_model_call",original_validate):
            for schema_id,schema_version in (
                (f"writer_evidence_package_compact_{identity}", version)
                for identity in ("v3", "v4", "v5") for version in ("v3", "v4", "v5")
                if (identity, version) != ("v5", "v5")
            ):
                stale=_stale_projection_identity(current,schema_id,schema_version)
                assert stale.model_input.final_input_material==current.model_input.final_input_material
                for reader,method in (("validate_pre_model_call",original_validate),("final_messages",original_final)):
                    try:
                        method(composer,stale,project_input=project_input)
                    except TargetWriterContextIntegrityError as exc:
                        seen[(schema_id,schema_version,reader)]=str(exc)
                    else:
                        seen[(schema_id,schema_version,reader)]=None
            fresh=original_validate(composer,current,project_input=project_input)
            fresh_messages=original_final(composer,fresh,project_input=project_input)
        observations.append((seen,fresh,fresh_messages))
        return current

    with patch.object(TargetWriterContextComposer,"validate_pre_model_call",observe):
        result=r1_mainline.__wrapped__(request,tmp_path_factory)
    assert observations
    return observations,result


@pytest.mark.parametrize("schema_id,schema_version",[
    (f"writer_evidence_package_compact_{identity}", version)
    for identity in ("v3", "v4", "v5") for version in ("v3", "v4", "v5")
    if (identity, version) != ("v5", "v5")
])
@pytest.mark.parametrize("reader",["validate_pre_model_call","final_messages"])
def test_actual_current_context_rejects_stale_or_partial_projection_identity(actual_predispatch_currentness,schema_id,schema_version,reader):
    observations,_=actual_predispatch_currentness
    assert all("locked Evidence projection" in (seen[(schema_id,schema_version,reader)] or "")
        for seen,_,_ in observations)


def test_actual_current_context_accepts_fresh_v5_and_keeps_locked_input(actual_predispatch_currentness):
    observations,result=actual_predispatch_currentness
    request=result[1]
    for _,current,messages in observations:
        evidence=next(item for item in current.selected_materializations if item.candidate_id=="writer-current-evidence-package")
        assert (evidence.projection_schema_id,evidence.projection_schema_version)==("writer_evidence_package_compact_v5","v5")
        assert messages==request.messages


@pytest.mark.parametrize("kind,key,value",[
    ("fact","subject",RAW),("fact","predicate",RAW),("fact","value",{"nested":RAW}),("fact","unit",RAW),
    ("table","table_title",RAW),("table","header",[RAW]),("table","cells",[RAW]),
    ("table","normalized_fields",{"field":RAW}),("table","inherited_cells",{"inherited":RAW}),
    ("table","merged_cell_relations",[{"body":RAW}]),("table","calculation_record",{"body":RAW}),
    ("policy","policy_name",RAW),("policy","chapter_title",RAW),("policy","article_title",RAW),
    ("policy","clause_path",[RAW]),
    ("template","section_path",[RAW]),("template","section_title",RAW),("template","required_fields",[RAW]),
    ("template","placeholder_fields",[RAW]),("template","style_hints",[RAW]),
])
def test_supplemental_every_structured_body_family_is_opaque(kind,key,value):
    # Explicit schema-boundary negatives supplement the live producer controls.
    raw={"schema_version":"structured_evidence_v1","representation_type":
        {"fact":"fact_record","table":"table_row","policy":"policy_clause","template":"template_section"}[kind],
        kind:{key:value},"fallback":{"used":False}}
    before=canonical_sha256(raw)
    result=projection._structured(raw,"/structured")
    assert RAW not in json.dumps(result,ensure_ascii=False)
    assert result[kind]["support_ref"]["sha256"]==canonical_sha256(raw[kind])
    assert result["source_ref"]["sha256"]==before==canonical_sha256(raw)


@pytest.mark.parametrize("attack",["hash","unknown_id","duplicate_ids","empty_ids","ids_without_snapshot"])
def test_supplemental_requirement_owner_rejects_unbound_or_inexact_selection(attack):
    snapshot=EvidenceRequirementsMaterializer.materialize(source_profile_ref="profile://independent/v1",
        source_task_ref="task://independent/v1",requirements=(EvidenceRequirementSchema(requirement_id="REQ1",
        criteria=(EvidenceCriterionSchema(criterion_id="CRITERION1",criterion=TASK_TOPIC),)),))
    ids=("REQ1",)
    if attack=="hash":
        snapshot=snapshot.model_copy(update={"requirements_sha256":"0"*64})
    elif attack=="unknown_id":
        ids=("UNKNOWN",)
    elif attack=="duplicate_ids":
        ids=("REQ1","REQ1")
    elif attack=="empty_ids":
        ids=()
    else:
        snapshot=None
    with pytest.raises(ValueError,match="Writer requirement"):
        projection._requirement_owner(snapshot,ids)


def test_supplemental_multiple_task_criteria_match_research_topic_projection():
    snapshot=EvidenceRequirementsMaterializer.materialize(source_profile_ref="profile://independent/v1",
        source_task_ref="task://independent/v1",requirements=(EvidenceRequirementSchema(requirement_id="REQ1",criteria=(
        EvidenceCriterionSchema(criterion_id="C1",criterion="  "+TASK_TOPIC+"  "),
        EvidenceCriterionSchema(criterion_id="C2",criterion="Additional exact Task criterion"))),))
    topics,owner=projection._requirement_owner(snapshot,("REQ1",))
    assert topics==frozenset({TASK_TOPIC+"；Additional exact Task criterion"})
    assert owner=={"requirements_sha256":snapshot.requirements_sha256,"requirement_ids":["REQ1"]}
