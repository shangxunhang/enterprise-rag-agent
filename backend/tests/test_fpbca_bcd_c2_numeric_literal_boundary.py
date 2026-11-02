"""TXT ONLY: one accepted existing-schema literal control; no native-A claim.

Export only after root C2 test gate to the new independent target
backend/tests/test_fpbca_bcd_c2_numeric_literal_boundary.py. Preserve original58.
No cross-sentence, tuple, unit conversion, complete numeric grammar, or source
producer mutation is tested or implied by this one directed contract control.
"""
from pathlib import Path
from hashlib import sha256
import inspect
import json

import rag.schema.prompt_evidence_candidate_v1 as visibility_owner
from test_support.stage_d_fixtures import retrieval_result


def test_declared_250_is_not_visible_as_part_of_1250_in_existing_schema(tmp_path):
    row = retrieval_result(text="预算金额：250万元", predicate="预算金额",
                           value="250", unit="万元")
    # Explicit existing-schema fixture. This absent native-span shape is NOT
    # attributed to ProjectFactRecordChunker or to a lawful B4 prefix producer.
    row["child_chunk"]["extra"]["source_span"] = None
    row["prompt_visible_text"] = "预算金额：1250万元"
    result = visibility_owner.build_prompt_evidence_candidate_v1(row)
    fact = result.structured_evidence.fact
    owner_file = Path(visibility_owner.__file__).resolve()
    native_source_point = {
        "path": str(owner_file), "sha256": sha256(owner_file.read_bytes()).hexdigest(),
        "function": "_fact_visible", "line": inspect.getsourcelines(visibility_owner._fact_visible)[1],
    }
    trace = {
        "classification": "EXISTING_SCHEMA_NUMERIC_LITERAL_CONTRACT_ONLY_NOT_NATIVE_PROJECTFACT_PIPELINE",
        "authority": "Frozen C2 normalized declared numeric value, without conversion; root accepted ONLY this one literal negative",
        "expected_fact_visibility": False,
        "input_fixture": row,
        "actual_fact": None if fact is None else fact.model_dump(mode="json"),
        "actual_owner_result": result.model_dump(mode="json"),
        "native_source_point": native_source_point,
        "model_gateway_support_verifier_gate_release": "OUTSIDE_THIS_DIRECTED_CONTRACT_CONTROL",
        "complete_numeric_grammar": "NOT_CLAIMED",
        "cross_sentence_tuple_semantics": "NOT_AUTHORIZED_NOT_EXERCISED",
    }
    payload = (json.dumps(trace, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    target = tmp_path / "c2-numeric-literal-250-vs-1250-contract-trace.json"
    with target.open("xb") as stream:
        stream.write(payload)
    print("C2_LITERAL_CONTRACT_TRACE", str(target), sha256(payload).hexdigest())
    assert fact is not None and fact.value == "250" and fact.unit == "万元"
    assert fact.predicate == "预算金额" and fact.source_span is None
    assert result.prompt_visible_text == "预算金额：1250万元"
    assert result.fact_visible_in_prompt is False
