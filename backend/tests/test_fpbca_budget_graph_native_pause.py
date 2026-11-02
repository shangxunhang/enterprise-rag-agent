"""Actual factory, fixed baseline and eight Section native budget hold."""
import json
from pathlib import Path
from test_phase2_runtime_safety_closure import _run_production_fake, _execution

def test_native_fixed_24000_document_hold_has_no_terminal_or_child_mutation(tmp_path):
    observations=[];physical=[]
    def inspect_engine(engine):
        pw=engine.stage_service.target_post_write_composition
        gateway=pw.citation_grounding.gateway
        invoke=gateway.invoker.invoke
        def measured(request,model_name):
            physical.append(request.model_copy(deep=True));return invoke(request,model_name)
        gateway.invoker.invoke=measured
        execute=engine.execute
        def observed(workflow,state):
            try:return execute(workflow,state)
            finally:
                native=pw.lifecycle_service.budget_preimage(state.run_id)
                budget=engine.budget_runtime(state.task_id,state.run_id).canonical_state()
                record={'scope':'ACTUAL_FACTORY_EIGHT_SECTIONS_FIXED_24000','run':native.run.model_dump(mode='json'),'children':[r.model_dump(mode='json') for r in native.current_children],'child_sha256':native.child_preimage_sha256,'budget':budget.model_dump(mode='json'),'physical':[r.model_dump(mode='json') for r in physical]}
                observations.append(record)
                target=tmp_path/'actual-native-pause.json';assert not target.exists();target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        engine.execute=observed
    summary,factory=_run_production_fake(tmp_path/'fresh-run',run_id='bt_native_document_budget_wait',mutate_engine=inspect_engine)
    execution=_execution(summary)
    native=observations[0]
    assert execution['status']=='paused' and not execution['metadata']['workflow_complete']
    assert execution['metadata']['pause_purpose']=='BUDGET_AUTHORIZATION'
    assert execution['metadata']['human_review_required'] is False
    assert native['run']['status']=='paused' and native['run']['finished_at'] is None
    assert len(native['children'])==8 and all(r['status']=='running' for r in native['children'])
    assert native['budget']['limits']['model_tokens_total']==24000 and native['budget'].get('grant_history',[])==[]
    assert 'evaluate_document_gate' not in execution['completed_node_ids']
    assert 'finalize_workflow_failure' not in execution['metadata']['business_node_sequence']
    assert all(r.get('extra',{}).get('logical_operation_id')!=execution['metadata']['budget_wait']['refusal']['semantic_operation_ref'] for r in native['physical'])
    assert execution['error'] is None and execution['metadata']['budget_wait']['refusal']['native_denial_ref'].endswith('/result')
