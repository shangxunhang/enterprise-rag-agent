"""RC1 Gold provenance and locally synthetic Full8 business scenario contracts.

Data-only checks are deliberately not claimed as canonical M6/Gate/Release
acceptance. The production DocumentVerifier is actually invoked only for a
pair of synthetic cross-chapter Section inputs with real Section/State schemas.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "docs/full8_r04_launch_preparation_20261026_r01/32_FULL8_FAKE_BUSINESS_COVERAGE_20261028_R01"
GOLD = Path("E:/rag-agent-evaluation/MULTI_AGENT_V3_RC1_20261013/gold_dataset_v1")


def _matrix():
    return json.loads((BASE/"01_COVERAGE_MATRIX.json").read_text(encoding="utf-8"))


def _fixtures():
    return json.loads((BASE/"02_SYNTHETIC_FACT_SCENARIOS.json").read_text(encoding="utf-8"))


def test_matrix_counts_priority_and_every_scenario_has_honest_evidence_layer():
    value = _matrix()
    rows = value["scenarios"]
    assert len(rows) == 24
    assert len({row["id"] for row in rows}) == len(rows)
    assert len({row["scenario"] for row in rows}) == len(rows)
    assert set(row["priority"] for row in rows) == {0, 1, 2}
    assert sum(row["priority"] == 0 for row in rows) == 12
    assert value["dataset_summary"]["business_candidates"] == 120
    assert value["dataset_summary"]["verified_scoped_gold"] == 53
    assert value["dataset_summary"]["review_required"] == 67
    assert value["dataset_summary"]["full_native_hydrated_gold"] == 0
    assert all(row["remaining_gap"] and isinstance(row["test_references"], list)
               for row in rows)
    assert all(row["evidence_level"] != "FORMAL_BUSINESS_E2E_PASS"
               for row in rows)


def test_all_gold_case_references_exist_and_only_verified_subset_is_gold():
    if not GOLD.is_dir():
        pytest.skip("RC1 Gold original read-only assets not mounted")
    rows = _matrix()["scenarios"]
    data = _fixtures()["business_scenarios"]
    verified = [json.loads(s) for s in (GOLD/"04_GOLD_DATASET_VERIFIED.jsonl").read_text(
        encoding="utf-8-sig").splitlines() if s.strip()]
    review = [json.loads(s) for s in (GOLD/"05_GOLD_REVIEW_REQUIRED.jsonl").read_text(
        encoding="utf-8-sig").splitlines() if s.strip()]
    all_cases = {row["case_id"]: row for row in verified + review}
    assert len(verified) == 53 and len(review) == 67
    assert len(all_cases) == 120
    refs = {ref for row in rows for ref in row["gold_refs"]}
    refs.update(ref for item in data for ref in item["gold_refs"])
    refs.update(ref for item in _matrix()["synthetic_fixtures"] for ref in item["gold_refs"])
    assert refs <= set(all_cases), sorted(refs - set(all_cases))
    for ref in refs:
        record = all_cases[ref]
        assert record["gold_status"] in {"VERIFIED", "REVIEW_REQUIRED"}
        assert record["full_native_fixture_status"] == "TEST_FIXTURE_BLOCKED"
        assert record.get("actual_model_observation") == "NOT_RUN_THIS_PHASE"
    assert all(row["gold_status"] == "VERIFIED" for row in verified)
    assert all(row["gold_status"] == "REVIEW_REQUIRED" for row in review)


def test_file_test_references_have_real_production_test_symbols():
    rows = _matrix()["scenarios"]
    for item in rows:
        for identifier in item["test_references"]:
            module, _, test_name = identifier.partition("::")
            assert test_name.startswith("test_")
            target = ROOT/"backend/tests"/module
            assert target.is_file(), identifier
            source = target.read_text(encoding="utf-8-sig")
            assert "def "+test_name+"(" in source, identifier


@pytest.mark.parametrize("name", [f"SYN-BIZ-{i:03d}" for i in range(5, 17)])
def test_deterministic_business_data_has_real_predicates_not_fake_authority(name):
    data = _fixtures()
    assert data["generation_method"] == "LOCAL_DETERMINISTIC_TEST_DATA_NOT_DEEPSEEK"
    assert data["real_provider_calls"] == 0
    fixtures = {r["scenario_id"]: r for r in data["business_scenarios"]}
    assert len(fixtures) == 12
    item = fixtures[name]
    assert item["schema"] == "synthetic_business_case_v1"
    assert item["matrix_id"] in {r["id"] for r in _matrix()["scenarios"]}
    assert item["scope"]["external_business_fact"] is False
    assert item["scope"]["not_canonical_owner"] is True
    assert item["scope"]["source_authority"] == "TEST_SCENARIO_INPUT_ONLY"
    assert item["expected"]
    assert item["test_level"] in {
        "DATA_READY_OWNER_HYDRATION_PENDING",
        "NATIVE_DOCUMENT_VERIFIER_READY",
    }


@pytest.mark.parametrize("consistent", [False, True], ids=["two-section-conflict","two-section-agreement"])
def test_synthetic_two_section_numbers_use_actual_production_document_verifier(consistent):
    from apps.enterprise_document.services.agent_rag_v1.document_verifier import DocumentVerifierService
    from test_phase7_document_closure_mainline import _section, _accepted_state, _project_input
    data = _fixtures()
    case = next(item for item in data["business_scenarios"]
                if item["scenario_id"] == "SYN-BIZ-009")
    scope = case["input"]["paragraphs"]
    value = scope[0]["server_count"]
    sections = [
        _section(item["section_id"], item["title"], item["order"],
                 item["text"] if not consistent or i == 0 else
                 item["text"].replace(str(item["server_count"]), str(value)))
        for i,item in enumerate(scope)
    ]
    states = {
        item["section_id"]: _accepted_state(
            item["section_id"], item["title"], item["order"])
        for item in scope
    }
    observed = DocumentVerifierService().verify_mainline(
        sections=sections,section_states=states,project_input=_project_input())
    actual = [item for item in observed.conflicts
              if item.conflict_type == "cross_section_fact_conflict"]
    if consistent:
        assert not actual
    else:
        assert not observed.passed
        assert actual
        assert set(actual[0].section_ids) == {scope[0]["section_id"],scope[1]["section_id"]}
        assert actual[0].severity == "blocking"
