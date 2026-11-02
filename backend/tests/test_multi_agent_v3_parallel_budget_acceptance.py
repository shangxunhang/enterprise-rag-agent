"""W15-S10: prove parallel success and scarce admission under explicit startup policies."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from threading import Barrier, Lock

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.config.grounded_generation import GroundedGenerationPolicyLoader
from apps.enterprise_document.services.agent_rag_v1.budget_manager import CanonicalBudgetCapacityExceeded
from apps.enterprise_document.services.agent_rag_v1.model_tool_budget_runtime import CanonicalInvocationBudgetAdapter
from core.config import get_settings
from model_gateway.model_contract import ModelRole
from test_multi_agent_v3_workflow_recovery import (
    _W13InspectingFactory,
    _W13TwoSectionNormalization,
    _w13_two_section_project_input,
    _w15_parallel_budget_policy_file,
    test_w13_two_section_parallel_target_synchronizes_one_completion_and_release as _assert_parallel_success,
)
import test_multi_agent_v3_workflow_recovery as workflow_fixtures


_POLICY = Path(__file__).resolve().parents[1] / "apps/enterprise_document/config/grounded_generation_v1.yaml"


@pytest.fixture
def capacity_workload(monkeypatch):
    """Declare real input demand; do not depend on redundant Evidence metadata."""
    original = workflow_fixtures._w13_two_section_project_input

    def project(task_id):
        raw = original(task_id).model_dump(mode="json")
        # Author the workload in the canonical task instructions, not metadata.
        raw["user_query"] = (
            "生成 Wave13 双章节并行验证文档。分别完成约定的章节A和章节B，只写当前章节正文，保持章节边界，不新增章节。"
            "采用正式、清晰、可执行的中文表述，围绕建设目标、实施安排、责任边界和后续验收组织内容，避免空泛口号。"
            "写作依据限于当前章节获准使用的事实和证据；准确呈现已知信息，不从其他章节草稿、历史生成结果或模型记忆补充项目事实。"
            "对缺失的信息明确说明待确认事项及其对本章节建议的影响，保持已知部分可用，不把单项缺口扩展为整个项目尚未确认。"
            "如果现有材料相互冲突，保留冲突和来源差异，不自行决定哪个说法为真；采用条件式建议表达可选实施路径。"
            "建议和事实采用不同措辞：已经明确的项目事实可以直接陈述，拟议建设动作说明前提、执行步骤和适用范围。"
            "任何行业惯例只可作为参考建议，不能写成客户已经采纳的安排；避免未经证据支持的规模、性能、合规性或采购承诺。"
            "合理连接前后段落，先描述当前约束，再说明行动和验收方式，使读者能够识别每一项建议对应的实际问题。"
            "遵守输入中已存在的人工处理边界，对需要人工或外部专业工具完成的工作只描述交接安排，不声称系统已经完成。"
            "引用遵循当前章节已有的要求和获准别名；没有允许的别名时不要创造引用，不展示内部事实、声明、运行或制品标识。"
            "保持表述自然且有层次，避免重复罗列所有全局缺口；只在当前章节解释相关限制及其对结论的影响。"
            "使用已获准的章节计划组织正文，既不扩展兄弟章节，也不凭空新增技术前提；最终输出可由现有验证流程核对。"
        )
        return type(original(task_id)).model_validate(raw)

    # Both the formal input and normalization use the same authored source, before
    # immutable Context construction, hashing and real Budget reservation.
    monkeypatch.setattr(workflow_fixtures, "_w13_two_section_project_input", project)
    monkeypatch.setitem(globals(), "_w13_two_section_project_input", project)


class _OverlappingAdmissions:
    """Hold real reservations until both admission attempts finish; no timing sleeps."""

    def __init__(self, monkeypatch):
        self.records = []
        self.active_tokens = []
        self.lock = Lock()
        self.barrier = Barrier(2, timeout=10)
        original = CanonicalInvocationBudgetAdapter.reserve_provider_attempt

        def observe(adapter, request):
            if request.model_role != ModelRole.SECTION_GENERATION.value:
                return original(adapter, request)
            allowance = adapter.provider_token_allowance(request)
            try:
                reservation = original(adapter, request)
            except Exception as error:
                with self.lock:
                    self.records.append((request, allowance, None, error, adapter.budget_manager))
                self.barrier.wait()
                raise
            with self.lock:
                self.records.append((request, allowance, reservation, None, adapter.budget_manager))
                self.active_tokens.append(adapter.budget_manager.canonical_state().active_reserved.model_tokens_total)
            # Neither real provider can start before the other admission attempt.
            self.barrier.wait()
            return reservation

        monkeypatch.setattr(CanonicalInvocationBudgetAdapter, "reserve_provider_attempt", observe)


def test_headroom_policy_changes_only_explicit_test_capacity_and_identity(tmp_path):
    before = _POLICY.read_bytes()
    baseline = GroundedGenerationPolicyLoader().load(_POLICY).model_dump(mode="json")
    path = _w15_parallel_budget_policy_file(tmp_path)
    configured = GroundedGenerationPolicyLoader().load(path).model_dump(mode="json")
    assert baseline["canonical_budget"]["model_tokens_total"] == 24000
    assert configured["canonical_budget"]["model_tokens_total"] == 64000
    assert configured["canonical_budget"]["policy_ref"] == "budget-policy://multi-agent-v3/test-w15-parallel-headroom-v1"
    for field in ("model_tokens_total", "policy_ref"):
        configured["canonical_budget"][field] = baseline["canonical_budget"][field]
    assert configured == baseline
    assert _POLICY.read_bytes() == before


@pytest.mark.parametrize("repetition", range(3))
def test_sufficient_startup_policy_supports_two_overlapping_real_writer_reservations(tmp_path, monkeypatch, repetition, capacity_workload):
    overlap = _OverlappingAdmissions(monkeypatch)
    _assert_parallel_success(tmp_path / str(repetition))
    assert len(overlap.records) == 2
    assert all(error is None and reservation for _, _, reservation, error, _ in overlap.records)
    allowances = [allowance for _, allowance, _, _, _ in overlap.records]
    assert 24000 < sum(allowances) <= 64000
    assert max(overlap.active_tokens) == sum(allowances)
    state = overlap.records[0][-1].canonical_state()
    assert state.limits.model_tokens_total == 64000
    assert state.active_reserved.model_tokens_total == 0
    assert state.used.true_retry_attempts == 0
    assert len({request.model_call_id for request, *_ in overlap.records}) == 2


@pytest.mark.parametrize("repetition", range(3))
def test_default_policy_rejects_overlapping_capacity_before_second_provider(tmp_path, monkeypatch, repetition, capacity_workload):
    before = _POLICY.read_bytes()
    root = tmp_path / str(repetition)
    for key, value in {
        "USE_REAL_RAG_TOOL": "false", "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default", "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false", "SECTION_MAX_CONCURRENCY": "2",
        "GROUNDED_GENERATION_POLICY_FILE": str(_POLICY),
    }.items():
        monkeypatch.setenv(key, value)
    settings = replace(
        get_settings(), data_root=root, default_model_name="fake_llm",
        supervisor_model_name="fake_llm", enable_llm_routing=False,
        trace_enabled=True, data_capture_enabled=True,
    )
    factory = _W13InspectingFactory(_W13TwoSectionNormalization(), force_parallel=True)
    overlap = _OverlappingAdmissions(monkeypatch)
    task_id, run_id = "task_w15_parallel_scarce", "run_w15_parallel_scarce"
    summary = MainlineApplicationService(supervisor_factory=factory).run(
        project_root=Path(__file__).resolve().parents[2],
        user_input=_w13_two_section_project_input(task_id).user_query, task_id=task_id, run_id=run_id,
        output_root=root, clean_existing=True, settings=settings,
        project_input=workflow_fixtures._w13_current_new_task_project_input(_w13_two_section_project_input(task_id)).model_dump(mode="json"),
        allow_demo_defaults=False, enable_agent_self_rag=False,
    )
    assert len(overlap.records) == 2
    admitted = [record for record in overlap.records if record[3] is None]
    denied = [record for record in overlap.records if record[3] is not None]
    assert len(admitted) == len(denied) == 1
    error = denied[0][3]
    assert isinstance(error, CanonicalBudgetCapacityExceeded)
    assert error.resource == "model_tokens_total" and error.limit == 24000
    assert error.required > error.remaining
    state = factory.engines[0].budget_runtime(task_id, run_id).canonical_state()
    assert state.limits.model_tokens_total == 24000
    assert state.used.true_retry_attempts == 0
    assert state.active_reserved.model_tokens_total == 0
    assert summary["status"] == "failed"
    assert summary["deliverable_final_result"] is None
    assert summary.get("release_publication") is None
    assert summary.get("target_release_publication") is None
    terminal = json.loads((root / "tasks" / f"{task_id}_state.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert terminal["error"]["error_type"] == "CanonicalBudgetCapacityExceeded"
    assert terminal["error"]["message"] == str(error)
    assert list((root / "runs" / "target_release").rglob("*.json")) == []
    # Count exact Writer calls, not the independent successful Planner call.
    events = [json.loads(line) for line in (root / "runs" / f"{run_id}_trace.jsonl").read_text(encoding="utf-8").splitlines()]
    completed = [event for event in events if event.get("event_type") == "model_finished" and event.get("status") == "success"]
    writer_ids = {record[0].model_call_id for record in overlap.records}
    completed_writers = [event for event in completed if event.get("call_id") in writer_ids]
    assert len(completed_writers) == 1
    assert completed_writers[0]["call_id"] == admitted[0][0].model_call_id
    assert all(event.get("call_id") != denied[0][0].model_call_id for event in completed)
    completed_planners = [event for event in completed if (event.get("lineage") or {}).get("call_purpose") == "planner_fact_requirements"]
    assert len(completed_planners) == 1
    completed_extractions = [event for event in completed if (event.get("lineage") or {}).get("call_purpose") == "claim_materialization"]
    assert len(completed_extractions) == 1
    assert completed_extractions[0]["lineage"]["section_id"] == admitted[0][0].extra["section_id"]
    completed_criteria = [event for event in completed if event.get("caller") == "WriterAdmissionFactAssessor"
        and str(event.get("call_id", "")).startswith("model_call_pre_write_criterion_")]
    assert len(completed_criteria) == 2
    assert len(completed) == len(completed_writers) + len(completed_planners) + len(completed_extractions) + len(completed_criteria)
    assert _POLICY.read_bytes() == before
