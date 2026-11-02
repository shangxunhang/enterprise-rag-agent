"""Opt-in offline attribution of an upstream fixture failure, not mainline PASS.

The old helper is substituted only in memory; no checkout, historical run or
production file is changed. Enable with CH1_RUN_ATTRIBUTION_PROBE=1.
"""
import json
import os
import socket
from unittest.mock import patch

import pytest

from apps.enterprise_document.services.agent_rag_v1.post_write_consistency_composition import TargetPostWriteConsistency
import test_multi_agent_v3_consistency_mainline as mainline


def _previous_reason_only(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), (list, tuple)):
        return payload
    rows = []
    for row in payload["results"]:
        codes = row.get("reason_codes") if isinstance(row, dict) else None
        if isinstance(codes, (list, tuple)) and all(isinstance(code, str) for code in codes) and len(codes) == len(set(codes)):
            row = {**row, "reason_codes": sorted(codes)}
        rows.append(row)
    return {**payload, "results": rows}


@pytest.mark.skipif(os.environ.get("CH1_RUN_ATTRIBUTION_PROBE") != "1", reason="explicit offline failure-attribution probe only")
@pytest.mark.parametrize("implementation", ["previous_reason_only", "current_role_sort"])
def test_upstream_fixture_failure_occurs_before_consistency_parser(tmp_path, monkeypatch, implementation):
    def denied(*args, **kwargs):
        raise AssertionError("Attribution probe may not contact any network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    selected = _previous_reason_only if implementation == "previous_reason_only" else TargetPostWriteConsistency._normalize_reason_order
    observed = []
    original_run = mainline.MainlineApplicationService.run

    def capture_run(self, *args, **kwargs):
        result = original_run(self, *args, **kwargs)
        observed.append(result)
        return result

    with patch.object(TargetPostWriteConsistency, "_normalize_reason_order", wraps=selected) as normalizer, \
         patch.object(TargetPostWriteConsistency, "_parse_reply", wraps=TargetPostWriteConsistency._parse_reply) as ordinary, \
         patch.object(TargetPostWriteConsistency, "_parse_shared_reply", wraps=TargetPostWriteConsistency._parse_shared_reply) as shared, \
         patch.object(mainline.MainlineApplicationService, "run", capture_run):
        with pytest.raises(AssertionError, match="Wave13 produced no top-level payload for latest"):
            mainline._run(tmp_path / implementation, "NO_MATERIAL")
        assert normalizer.call_count == ordinary.call_count == shared.call_count == 0
    assert len(observed) == 1
    result = observed[0]
    summary = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    print(json.dumps({"implementation": implementation,
                      "same_upstream_failure_reproduced": True,
                      "normalizer_calls": normalizer.call_count,
                      "ordinary_parser_calls": ordinary.call_count,
                      "shared_parser_calls": shared.call_count,
                      "mainline_acceptance_pass": False,
                      "fixture_summary": summary}, ensure_ascii=True, default=str))
