"""New-task fixture conversion cannot change production historical ingress policy."""
import pytest
from apps.enterprise_document.services.project_input_normalization.input_contract import (
    normalize_new_project_input, read_saved_project_input,
)
import test_multi_agent_v3_workflow_recovery as workflow


@pytest.mark.parametrize("helper", [workflow._w13_single_section_project_input,
                                   workflow._w13_two_section_project_input])
def test_original_v2_saved_read_and_strict_new_task_rejection_remain(helper):
    project = helper("i10-fixture-boundary")
    raw = project.model_dump(mode="json")
    assert raw["schema_version"] == "project_input_v2"
    assert read_saved_project_input(raw).model_dump(mode="json") == raw
    with pytest.raises(ValueError, match="caller-approved conversion"):
        normalize_new_project_input(raw, strict=True)
    current = workflow._w13_current_new_task_project_input(project)
    assert current.schema_version == "project_input_v3"
    assert normalize_new_project_input(current.model_dump(mode="json"), strict=True) == current
    assert project.model_dump(mode="json") == raw


def test_explicit_v1_new_task_is_still_rejected():
    raw = workflow._w13_single_section_project_input("i10-v1").model_dump(mode="json")
    raw["schema_version"] = "project_input_v1"
    with pytest.raises(ValueError, match="caller-approved conversion"):
        normalize_new_project_input(raw, strict=True)
