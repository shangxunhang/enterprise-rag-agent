from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from apps.enterprise_document.runtime.agent_rag_v1 import (
    MultiAgentApplicationService,
    MultiAgentProjectInputSchema,
)


pytestmark = pytest.mark.recovery_e2e


def test_writer_recovers_from_checkpoint_and_progress_events(
    tmp_path: Path,
) -> None:
    result = MultiAgentApplicationService().run(
        MultiAgentProjectInputSchema(
            scenario="crash-recovery",
            output_dir=str(tmp_path),
            task_id="recovery-e2e",
            run_id="run-001",
        )
    )
    records = [
        json.loads(line)
        for line in Path(result.writer_event_log_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    compute_paragraphs = [
        record["paragraph"]["paragraph_id"]
        for record in records
        if record.get("section_id") == "compute"
        and "paragraph" in record
    ]

    assert result.task_status.value == "success"
    assert result.trace.recovery_performed is True
    assert result.trace.checkpoint_event_recovery is True
    assert result.trace.reused_paragraph_ids == ["p001", "p002"]
    assert Counter(compute_paragraphs) == {
        "p001": 1,
        "p002": 1,
        "p003": 1,
    }
    assert (
        result.final_state.section_states["compute"].writing.expected_paragraph_ids
        == ["p001", "p002", "p003"]
    )
    assert Path(result.document_artifact_path).is_file()
