# POST-BT PREPARATION: new4 accepted literal/schema controls only; old7 supplement04 preserved separately. No import/run.
# PREPARATION ONLY: TXT, NOT imported/collected/executed; separate from 40
# native-producer controls and unchanged original 14 quantitative controls.
# Existing fixture/schema forms, not actual ProjectFact producer outputs.
# Numeric-zero + declared-unit cases reuse frozen existing fields. Ambiguous
# absent-span tuple-sequence rejection cases are PENDING_ROOT_SOURCE_REVIEW:
# root must review/observe RED before choosing any minimal predicate change.
# No unit list, conversion, parser/service, qualifier vocabulary or new owner.
from hashlib import sha256
import json
import pytest
from rag.schema.prompt_evidence_candidate_v1 import build_prompt_evidence_candidate_v1
from test_support.stage_d_fixtures import retrieval_result


@pytest.mark.parametrize("value,source_span,visible,expected,oracle_status", [
    pytest.param(0, {}, "预算金额：0万元", True,
        "FROZEN_ZERO_VALUE_AND_DECLARED_UNIT", id="zero-absent-span-complete"),
    pytest.param(0, {}, "预算金额：万元", False,
        "FROZEN_ZERO_VALUE_AND_DECLARED_UNIT", id="zero-absent-span-missing-value"),
    pytest.param(0, {"text": "预算金额：0万元"}, "预算金额：0万元", True,
        "FROZEN_COMPLETE_SOURCE_SPAN", id="zero-complete-source-span"),
    pytest.param("250", None, "预算金额：250万元", True,
        "FROZEN_ABSENT_SPAN_COMPLETE_DECLARED_TUPLE", id="absent-span-complete-tuple"),
])
def test_frozen_literal_zero_and_declared_unit(tmp_path, value, source_span,
                                               visible, expected, oracle_status):
    # The fixture factory publishes authoritative child.extra fields; absent
    # span remains None/{} and is not replaced by fabricated full-span proof.
    source_text = '预算金额：0万元' if value == 0 else '预算金额：250万元'
    row = retrieval_result(text=source_text, predicate="预算金额",
                           value=value, unit="万元")
    row["child_chunk"]["extra"]["source_span"] = source_span
    row["prompt_visible_text"] = visible
    candidate = build_prompt_evidence_candidate_v1(row)
    fact = candidate.structured_evidence.fact
    assert fact is not None
    assert fact.value == value and fact.unit == "万元" and fact.predicate == "预算金额"
    assert fact.source_span == source_span
    trace = {
        "classification": "CONTROLLED_EXISTING_SCHEMA_FIXTURE_NOT_NATIVE_PRODUCER_OUTPUT",
        "oracle_status": oracle_status,
        "expected_under_source_review": expected,
        "source_child_original_fixture": row["child_chunk"],
        "actual_fact": fact.model_dump(mode="json"),
        "actual_prompt": candidate.model_dump(mode="json"),
        "visible_bytes_utf8": list(visible.encode("utf-8")),
        "visible_sha256": sha256(visible.encode("utf-8")).hexdigest(),
        "model_gateway_count": {"count": 0, "basis": "NO_MODEL_GATEWAY_IN_THIS_SCHEMA_FIXTURE"},
        "semantic_Support_Verifier_Gate_Release": "NOT_EXERCISED",
    }
    payload = (json.dumps(trace, ensure_ascii=False, sort_keys=True,
                          indent=2) + chr(10)).encode("utf-8")
    target = tmp_path / "declared-tuple-source-review-trace.json"
    with target.open("xb") as handle:
        handle.write(payload)
    print("TUPLE_SOURCE_REVIEW_TRACE", str(target), sha256(payload).hexdigest())
    # Pending cases are intended exact RED observations after root authorizes
    # test export/accepts their bounded oracle. No skip/xfail/manufactured PASS.
    assert candidate.fact_visible_in_prompt is expected