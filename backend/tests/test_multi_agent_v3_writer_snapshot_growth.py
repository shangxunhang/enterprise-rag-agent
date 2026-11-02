"""W15-S07 structural regression; full application acceptance remains Stage F."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.enterprise_document.services.scheme_writer.writer_invocation import _unified_snapshot


class _Dump:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def model_dump(self, **_kwargs):
        self.calls += 1
        return self.value


def _snapshot(previous, *, target):
    recovery = SimpleNamespace(
        prompt_id="test-prompt", prompt_version="v1", initial_prompt_text="locked prompt",
        context_package_id="context-1", context_sha256="a" * 64,
        writer_evidence_semantics_json="{}", structured_writing_facts_json="[]",
        citation_ids=[], evidence_bundle_sha256="b" * 64,
    )
    prompt = SimpleNamespace(technical_recovery_context=recovery, context_package=_Dump({"id": "prompt-context"}))
    package = _Dump({"id": "canonical-package", "authorized_sources": ["artifact://exact-version"]})
    return _unified_snapshot(
        shared_state=SimpleNamespace(task_id="task", run_id="run"),
        project_input=_Dump({"task_id": "task"}),
        writer_subject_ref=SimpleNamespace(document_id="document", section_id="section"),
        section_title="current", section_order=1, structured_facts=[],
        previous_sections=previous, target_prompt=prompt,
        target_context_package=package if target else None,
    )


@pytest.mark.parametrize("count", (0, 1, 8, 32))
def test_target_snapshot_never_serializes_raw_prior_writer_history(count):
    # Simulate nested old invocations, not a real data fixture or provider result.
    previous = [_Dump({"input": {"writer_generation_invocation": {"previous_sections": ["retired"]}}}) for _ in range(count)]
    snapshot = _snapshot(previous, target=True)
    assert all(item.calls == 0 for item in previous)
    assert snapshot["previous_sections"] == []
    assert snapshot["target_context_package"]["authorized_sources"] == ["artifact://exact-version"]
    assert snapshot == _snapshot([], target=True)
    assert snapshot["target_prompt_lock"]["initial_prompt_text"] == "locked prompt"


def test_without_target_package_retains_existing_compatibility_snapshot():
    previous = [_Dump({"section_id": "old", "input": {"historical": True}})]
    snapshot = _snapshot(previous, target=False)
    assert previous[0].calls == 1
    assert snapshot["previous_sections"] == [{"section_id": "old", "input": {"historical": True}}]
    assert "target_context_package" not in snapshot
