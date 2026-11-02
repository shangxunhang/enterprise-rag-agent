"""Opt-in, network-free regression against immutable real CH1 evidence.

Set CH1_FROZEN_OBSERVED_CALLS to the original observed_calls.jsonl. The file is
only read and its exact SHA is required; no Provider or historical store writes.
These checks establish wire/schema behavior, not semantic or Quality PASS.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket

import pytest
from pydantic import ValidationError

from apps.enterprise_document.services.agent_rag_v1.post_write_consistency_composition import (
    ConsistencyRelationReply,
    TargetPostWriteConsistency,
)

RAW_SHA = "c84a91c5b3c1d6ca8a1eb0aa2a95958495e32ed70b37c9ed7119d7f6cc56d7d8"
TARGET = "backend/apps/enterprise_document/services/agent_rag_v1/post_write_consistency_composition.py"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Frozen CH1 tests must never access a network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture(scope="module")
def frozen_path():
    value = os.environ.get("CH1_FROZEN_OBSERVED_CALLS")
    if not value:
        pytest.skip("requires explicitly selected immutable real CH1 evidence")
    path = Path(value)
    assert path.is_file(), "explicit frozen fixture is missing"
    assert _sha(path) == RAW_SHA, "not the authorized immutable CH1 run"
    return path


@pytest.mark.parametrize("sample,relations,reorders", [(0, 8, 5), (1, 24, 2)])
def test_frozen_real_response_sort_only_strict_schema(frozen_path, sample, relations, reorders):
    events = [json.loads(line) for line in frozen_path.read_text(encoding="utf-8").splitlines() if line]
    requests = [row["request"] for row in events if row.get("event") == "request_started"
                and row.get("request", {}).get("extra", {}).get("call_purpose") == "artifact_consistency"]
    responses = {row["model_call_id"]: row["response"] for row in events
                 if row.get("event") == "response_received"}
    assert len(requests) == 2
    request = requests[sample]
    raw = responses[request["model_call_id"]]["content"]
    payload = json.loads(raw)
    before = deepcopy(payload)
    assert len(payload["results"]) == relations
    assert sum(row["role_bindings"] != sorted(row["role_bindings"]) for row in payload["results"]) == reorders
    # Freeze the previous reason-only boundary independently of the new helper.
    previous = deepcopy(payload)
    for row in previous["results"]:
        values = row.get("reason_codes")
        if isinstance(values, list) and all(isinstance(v, str) for v in values) and len(values) == len(set(values)):
            row["reason_codes"] = sorted(values)
    with pytest.raises(ValidationError, match="role_bindings must be canonical ordered-unique"):
        ConsistencyRelationReply.model_validate(previous)

    normalized = TargetPostWriteConsistency._normalize_reason_order(payload)
    assert normalized == TargetPostWriteConsistency._normalize_reason_order(normalized)
    reply = ConsistencyRelationReply.model_validate(normalized)
    assert len(reply.results) == relations
    for original, updated in zip(previous["results"], normalized["results"]):
        values = original["role_bindings"]
        assert isinstance(values, list) and all(isinstance(v, str) for v in values)
        assert len(set(values)) == len(values)
        assert updated == {**original, "role_bindings": sorted(values)}
    assert payload == before
    assert _sha(frozen_path) == RAW_SHA
    print(json.dumps({"sample": sample + 1, "model_call_id": request["model_call_id"],
                      "relations": relations, "reordered_roles": reorders,
                      "original_schema": "REJECT", "normalized_schema": "PASS",
                      "semantic_or_gate_pass_claimed": False, "raw_sha": RAW_SHA}))


def test_frozen_history_and_other_source_hashes_preserved(frozen_path):
    business = frozen_path.parent
    root = business.parent.parent
    manifest = json.loads((business.parent / "audit/SOURCE_MANIFEST_BEFORE.json").read_text(encoding="utf-8-sig"))
    repo = Path(__file__).resolve().parents[2]
    assert len(manifest) == 1164
    differences = sorted(relative for relative, digest in manifest.items()
                         if not (repo / relative).is_file() or _sha(repo / relative) != digest)
    # The frozen manifest includes tests as well as production source.
    assert differences == sorted([TARGET, "backend/tests/test_opt_011_consistency_reply_contract.py"]), differences
    assert manifest[TARGET] == "d23552ad4a8128c152cfbf91b79357d93bb8f5d0dce58b71aaa7e649b3928076"
    expected = {
        "observed_calls.jsonl": RAW_SHA,
        "execution.json": "855deca43d7d29c59442bd6389cb7b44e2cffdb59caabc619f46181a023383a8",
        "runs/claim_verification.jsonl": "fe3ec82790c48dd92d965130e8990ed400b6615b052b94f10508a28afed82526",
        "runs/claim_support.jsonl": "a0146d78531f0b09c386e3350dab74ac47fe02e4de419d631716fc4b6d14fb2b",
        "runs/post_write.sqlite3": "c84f61d3b7d8472fc6b9166f0e73f2849aa3ffe6fbe0ae3a2dcff2d3701f9b7a",
    }
    assert {name: _sha(business / name) for name in expected} == expected
    old = root.parent / "CH1_REAL_E2E_20261010_V1/campaign/business-runtime/observed_calls.jsonl"
    assert _sha(old) == "9569937877611bc6de4defab3de7ea02f7989028dff0463516ab0095aa1f75dc"
    claim = repo / "backend/apps/enterprise_document/services/agent_rag_v1/claim_materialization_runtime.py"
    assert _sha(claim) == "c62924bb1b22cddd7392f7e6c7e90104de41696ab1d3cdf75d737a6807910536"
    print(json.dumps({"source_manifest_count": len(manifest), "changed_source_files": differences,
                      "historical_hashes_unchanged": expected, "old_raw_unchanged": True}))
