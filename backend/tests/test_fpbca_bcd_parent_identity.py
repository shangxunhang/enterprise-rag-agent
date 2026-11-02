from copy import deepcopy
from types import SimpleNamespace
import pytest
from rag.plugins.candidate_enrichers import ParentChildCandidateEnricher
from rag.schema.Retrieval_Result_Schema import build_retrieval_result_v2
from rag.schema.candidate import CandidateSet
from test_support.stage_d_fixtures import retrieval_result

def pair():
    row = retrieval_result(text="Budget child", representation_type="paragraph_text")
    child = deepcopy(row["child_chunk"])
    profile = deepcopy(row["metadata"]["document_profile"])
    child["document_profile"] = profile
    parent = {
        **deepcopy(child),
        "parent_chunk_id": child["parent_chunk_id"],
        "chunk_id": child["parent_chunk_id"],
        "child_chunk_ids": [child["child_chunk_id"]],
        "text": "AUTHORIZED PARENT",
    }
    parent.pop("child_chunk_id", None)
    return child, parent

@pytest.mark.parametrize('mutation',['missing_tenant','wrong_tenant','missing_kb','wrong_doc','missing_profile','wrong_source','wrong_profile','wrong_parent'])
def test_bad_parent_falls_back_to_independently_valid_child(mutation):
    child,parent=pair()
    if mutation=='missing_tenant':parent.pop('tenant_id')
    elif mutation=='wrong_tenant':parent['tenant_id']='foreign'
    elif mutation=='missing_kb':parent.pop('kb_id')
    elif mutation=='wrong_doc':parent['doc_id']='foreign'
    elif mutation=='missing_profile':parent.pop('document_profile');parent['extra'].pop('document_profile',None)
    elif mutation=='wrong_source':parent['document_profile']['source_sha256']='f'*64
    elif mutation=='wrong_profile':parent['document_profile']['authority']['issuer']='foreign'
    else:parent['parent_chunk_id']='wrong'
    result=build_retrieval_result_v2(child_chunk=child,parent_chunk=parent,rank=1,score=1)
    assert result['text']==child['text']
    assert result['parent_text']==''
    assert result['context_granularity']=='child'

def test_exact_parent_retains_utility_and_identity():
    child,parent=pair()
    result=build_retrieval_result_v2(child_chunk=child,parent_chunk=parent,rank=1,score=1)
    assert result['text']=='AUTHORIZED PARENT'

def test_missing_child_identity_is_not_repaired_from_parent():
    child,parent=pair();child.pop('tenant_id')
    with pytest.raises(ValueError):build_retrieval_result_v2(child_chunk=child,parent_chunk=parent,rank=1,score=1)

def test_whole_matched_cohort_required_for_parent_expansion():
    child,parent=pair();second=deepcopy(child);second['chunk_id']='child-second';second['tenant_id']='foreign'
    rows=[{'child_chunk':c,'child_chunk_id':c['chunk_id'],'parent_chunk_id':c['parent_chunk_id'],'fusion_score':1} for c in (child,second)]
    enricher=ParentChildCandidateEnricher(build_context={'resource_pool':SimpleNamespace(get_parent_store=lambda:SimpleNamespace(get=lambda _:parent))})
    result=enricher.enrich(CandidateSet(query='budget',source_name='test',candidates=rows))
    assert len(result.candidates)==1
    assert result.candidates[0]['parent_text']==''
    assert 'foreign' not in str(result.candidates[0]['metadata'].get('matched_child_chunks'))
