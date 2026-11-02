"""Opt-in fixed-root D1-A entry; no production routing authority is added."""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import uuid
from unittest.mock import patch

import pytest

from application.mainline_service import MainlineApplicationService
from application.existing_run_authorization import ExistingRunAuthorizationError
from apps.enterprise_document.services.agent_rag_v1.disk_guard import DiskBudgetGuard
from apps.enterprise_document.services.agent_rag_v1 import writer_review_copy as wc
from core.config import get_settings
from model_gateway.fake_llm_client import FakeLLMClient
from test_writer_review_copy_c3_production_20261017_r03 import protected_directory


def _save(name, value):
    destination = Path(os.environ['GUARD_V2_EVIDENCE_DIR']) / name
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def _tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        if p.is_file() else 'DIRECTORY' for p in sorted(root.rglob('*'))}


@dataclass(frozen=True)
class _FixedNativeIngress:
    """One opted-in test invocation; callers cannot choose a data root/project."""
    root: Path
    task_id: str
    run_id: str
    project: dict

    def run(self, service, *, task_id, run_id, settings):
        if (task_id, run_id) != (self.task_id, self.run_id):
            raise ExistingRunAuthorizationError('fixed new-run identity rejected')
        if settings.data_root.resolve() != self.root.resolve():
            raise ExistingRunAuthorizationError('fixed new-run root rejected')
        return service.run(project_root=Path(__file__).resolve().parents[2],
            task_id=self.task_id, run_id=self.run_id, user_input=self.project['user_query'],
            output_root=self.root, clean_existing=False, settings=settings,
            project_input=self.project, allow_demo_defaults=False, enable_agent_self_rag=False)


def _new_root(label):
    parent = Path(os.environ['GUARD_V2_NATIVE_ROOT'])
    assert parent.is_absolute() and parent.drive.lower() == 'e:'
    assert parent.parent.name == 'rag-agent-guard-v2-native'
    uuid.UUID(parent.name)
    return protected_directory(parent / (label + '-' + uuid.uuid4().hex))


def test_phase_a_fixed_new_root_rejects_old_cross_unknown_without_writes():
    root = _new_root('admission')
    # Directory provisioning is setup, before request-side-effect measurement.
    (root / 'tasks').mkdir()
    trusted = wc._wc_trusted_windows_sids()
    wc._wc_require_trusted_directory(root, output_root=root, trusted_sids=trusted)
    settings = replace(get_settings(), data_root=root, default_model_name='fake_llm',
        supervisor_model_name='fake_llm', enable_llm_routing=False)
    guard = DiskBudgetGuard.from_settings(settings)
    measurements = guard.assert_new_durable_run_allowed((root,), 'guard-v2-native-new-run')
    task, run = 'task-guard-v2-' + uuid.uuid4().hex, 'run-guard-v2-' + uuid.uuid4().hex
    ingress = _FixedNativeIngress(root, task, run, {'task_id': task, 'user_query': '固定新测试输入'})
    class DenyFactory:
        builds = 0
        def build(self, **kwargs):
            self.builds += 1
            raise AssertionError('rejection reached owner/model assembly')
    factory = DenyFactory()
    service = MainlineApplicationService(supervisor_factory=factory)
    identities = [
        ('task-rc1-ch1-review-real-20261015-v1', 'rc1-ch1-review-real-20261015-v1'),
        ('task-rc1-ch1-review-real-20261015-v2', 'rc1-ch1-review-real-20261015-v2'),
        (task, 'cross-run-' + uuid.uuid4().hex),
        ('cross-task-' + uuid.uuid4().hex, run),
        ('task-unknown-' + uuid.uuid4().hex, 'run-unknown-' + uuid.uuid4().hex),
    ]
    before = _tree(root)
    rows = []
    with patch.object(FakeLLMClient, 'generate', side_effect=AssertionError('model dispatched')) as model:
        for old_task, old_run in identities:
            with pytest.raises(ExistingRunAuthorizationError):
                ingress.run(service, task_id=old_task, run_id=old_run, settings=settings)
            # Existing production authorizer is exercised only against this new root.
            with pytest.raises(ExistingRunAuthorizationError):
                service.recover_run(project_root=Path(__file__).resolve().parents[2],
                    task_id=old_task, run_id=old_run, output_root=root, settings=settings)
            assert _tree(root) == before
            rows.append({'task_id': old_task, 'run_id': old_run, 'rejected': True,
                'new_root_tree_unchanged': True})
        with pytest.raises(ExistingRunAuthorizationError):
            ingress.run(service, task_id=task, run_id=run,
                settings=replace(settings, data_root=root.parent))
        assert model.call_count == 0
    assert factory.builds == 0 and _tree(root) == before
    _save('PHASE_A_ISOLATION.json', {'verdict': 'D1A_ARCHIVE_ONLY_ISOLATION_READY',
        'output_root': str(root), 'task_id': task, 'run_id': run,
        'root_is_protected_dacl': True, 'sif13_unmodified': [
            {'measurement': asdict(m), 'policy': asdict(p)} for m, p in measurements],
        'identities': rows, 'factory_builds': factory.builds, 'model_calls': 0,
        'before_tree': before, 'after_tree': _tree(root),
        'budget_reserves': 0, 'checkpoint_postwrite_fact_release_writes': 0,
        'old_roots_passed_to_service': [], 'general_version_router_claimed': False})
