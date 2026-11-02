"""Frozen non-correcting eligibility and the existing interruption surface."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from apps.enterprise_document.services.agent_rag_v1.run_finalization_currentness import final_control_eligible
from apps.enterprise_document.schemas.post_write import EnforcementAction
from backend.tests.test_multi_agent_v3_release_lineage import _assessment_fixture, _final_control


def test_real_correct_control_is_not_terminal_eligible(tmp_path):
    fx=_assessment_fixture(tmp_path)
    control=_final_control(fx['post_write'],correcting=True)
    assert control.enforcement_action is EnforcementAction.CORRECT
    assert not final_control_eligible(control)


@pytest.mark.parametrize('action',[EnforcementAction.OUTPUT,EnforcementAction.OUTPUT_BEST_EFFORT,EnforcementAction.BLOCK])
def test_all_noncorr_terminal_actions_eligible_without_requested_correction(tmp_path,action):
    fx=_assessment_fixture(tmp_path)
    control=fx['final_control'].model_copy(update={'enforcement_action':action})
    assert final_control_eligible(control)


def test_executable_correction_is_excluded_even_with_noncorr_action(tmp_path):
    fx=_assessment_fixture(tmp_path)
    correcting=_final_control(fx['post_write'],correcting=True)
    assert not final_control_eligible(correcting.model_copy(update={'enforcement_action':EnforcementAction.OUTPUT}))


def test_correct_uses_existing_nonterminal_surface_no_lifecycle_transition(tmp_path,monkeypatch):
    from backend.tests.test_bca014_i8_finalization_recovery import target_fixture
    from apps.enterprise_document.services.agent_rag_v1.post_write_composition import TargetPostWriteCompositionError
    post,request=target_fixture(tmp_path/'target',monkeypatch)
    fx=_assessment_fixture(tmp_path/'control');correct=_final_control(fx['post_write'],correcting=True)
    before=post.lifecycle_service.load_run_lifecycle(request['run_id'])
    with patch.object(post.control_store,'resolve_exact',return_value=correct), \
            patch.object(post.lifecycle_service,'transition_run',side_effect=AssertionError('CORRECT transition')), \
            patch.object(post.assessment_store,'put_if_absent_or_same',side_effect=AssertionError('CORRECT Assessment')):
        with pytest.raises(TargetPostWriteCompositionError,match='correction/recovery'):
            post._commit_and_terminalize_run(run_id=request['run_id'],materialization=None,
                final_document=SimpleNamespace(control_decision_ref=correct.as_ref()),
                task_id=request['task_id'],document_id=request['document_id'],project_input=request['project_input'])
    assert post.lifecycle_service.load_run_lifecycle(request['run_id'])==before
