import pytest
from rag.schema.prompt_evidence_candidate_v1 import build_prompt_evidence_candidate_v1
from test_support.stage_d_fixtures import retrieval_result

@pytest.mark.parametrize('source,visible,value,unit,expected',[
 ('预算金额：250万元','预算金额：250','250','万元',False),
 ('预算金额：250万元','预算金额：250万元','250','万元',True),
 ('预算金额：约250万元','预算金额：250万元','250','万元',False),
 ('预算金额：约250万元','预算金额：约250万元','250','万元',True),
 ('预算金额：250万元','预算金额：2.5百万元','250','万元',False),
 ('预算金额：２５０万元','预算金额: 250 万元','250','万元',True),
 ('预算金额：250万元','预算金额：250万元','250万元','万元',True),
 ('预算金额：250','预算金额：250','250','万元',False),
])
def test_complete_declared_source_witness(source,visible,value,unit,expected):
    row=retrieval_result(text=source,value=value,unit=unit)
    row['prompt_visible_text']=visible
    candidate=build_prompt_evidence_candidate_v1(row)
    assert candidate.fact_visible_in_prompt is expected

def test_generated_delivery_qualifier_not_required_in_source_span():
    row=retrieval_result(text='预算金额：250万元',value='250',fact_status='estimated')
    row['prompt_visible_text']='预算金额：250万元'
    assert build_prompt_evidence_candidate_v1(row).fact_visible_in_prompt is True

def test_different_candidates_do_not_reconstruct_split_unit():
    row=retrieval_result(text='预算金额：250万元',value='250')
    row['prompt_visible_text']='预算金额：250'
    assert build_prompt_evidence_candidate_v1(row).fact_visible_in_prompt is False

@pytest.mark.parametrize('visible,expected',[
 ('项目: 软件开发\n金额: 约250万元',True),
 ('项目: 软件开发\n金额: 250万元',False),
 ('项目: 软件开发\n金额: 约250',False),
])
def test_native_table_json_value_uses_complete_rendered_source_witness(visible,expected):
    import json
    value=json.dumps({'项目':'软件开发','金额':'约250万元'},ensure_ascii=False,sort_keys=True,separators=(',',':'))
    row=retrieval_result(text='项目: 软件开发\n金额: 约250万元',value=value,predicate='table_row')
    row['prompt_visible_text']=visible
    assert build_prompt_evidence_candidate_v1(row).fact_visible_in_prompt is expected

def test_component_fallback_without_source_span_requires_declared_unit():
    row=retrieval_result(text='预算金额：250万元',value='250')
    row['child_chunk']['extra']['source_span']={}
    row['prompt_visible_text']='预算金额：250'
    assert build_prompt_evidence_candidate_v1(row).fact_visible_in_prompt is False
