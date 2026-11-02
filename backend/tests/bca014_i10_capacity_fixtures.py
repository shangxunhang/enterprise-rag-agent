"""Explicit startup capacity for fake-only native publication fixture proofs."""
import json
from pathlib import Path
import yaml


def explicit_fake_startup_policy(output_root: Path,run_id: str):
    default_path=Path(__file__).resolve().parents[1]/'apps/enterprise_document/config/grounded_generation_v1.yaml'
    original=default_path.read_bytes()
    policy=yaml.safe_load(original)
    assert policy['canonical_budget']['logical_model_calls']==44
    assert policy['canonical_budget']['model_tokens_total']==24000
    policy['canonical_budget']['model_tokens_total']=64000
    output_root.mkdir(parents=True,exist_ok=True)
    profile=output_root/f'explicit_fake_{run_id}_policy.json'
    assert not profile.exists(),'A startup fixture never replaces a prior Run policy'
    profile.write_text(json.dumps(policy),encoding='utf-8')
    return profile,default_path,original


def assert_native_publication(summary,engine):
    assert summary['deliverable_final_result'] is not None
    assert summary['target_release_publication'] is not None
    state=engine.budget_runtime(summary['task_id'],summary['run_id']).canonical_state()
    assert state.limits.logical_model_calls==44
    assert state.limits.model_tokens_total==64000
    assert state.used.logical_model_calls==43
    assert all(value==0 for value in state.active_reserved.model_dump().values())
