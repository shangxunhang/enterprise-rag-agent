"""Post-write A offline contracts; controlled GOLD is not model semantic proof.

Run only through the preserved tools/run_mechanical.py, group v2. V1 receipts
come from this round's synthetic V1 fixtures, never from business history.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from apps.enterprise_document.schemas.agent_rag_v1.paragraphs import ParagraphBindingSchema
from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimOperationContextSchema
from apps.enterprise_document.services.agent_rag_v1 import claim_materialization_runtime as cm
from apps.enterprise_document.services.agent_rag_v1.artifact_runtime import ArtifactRuntime
from apps.enterprise_document.services.agent_rag_v1.claim_verification_runtime import ClaimVerificationRuntime
from apps.enterprise_document.services.agent_rag_v1.lifecycle_runtime import RunAttemptLifecycleService
from apps.enterprise_document.services.agent_rag_v1.paragraph_runtime import ParagraphRuntime
from core.hashing import canonical_sha256
from schemas.context import ContextPackageSchema
import test_multi_agent_v3_claim_verification_runtime as claim_runtime_fixtures


pytestmark = pytest.mark.v2
ISOLATION_ROOT = Path("E:/rag-agent-evaluation/POSTWRITE_RUNTIME_ISOLATION_20261011T080600Z")


def pytest_collection_modifyitems(items):
    """Let the fixed runner select the existing directed regressions via -m v2."""
    allowed = {
        "test_multi_agent_v3_claim_materialization_mainline.py",
        "test_bca014_i5_verifier_project_input_basis.py",
        "test_bca014_i5_verifier_v2_semantics.py",
        "test_multi_agent_v3_requirement_sources.py",
        "test_multi_agent_v3_faithfulness_fth03.py",
        "test_multi_agent_v3_claim_support_runtime.py",
        "test_bca014_verifier_reply_ref_normalization.py",
    }
    for item in items:
        if Path(item.path).name in allowed:
            item.add_marker(pytest.mark.v2)


@pytest.fixture
def isolation(request, monkeypatch):
    """Reuse the existing runner's exact-source and network-denial fixture."""
    name = "postwrite_a_existing_isolation_tests"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ISOLATION_ROOT / "mechanical/test_isolation.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    module = sys.modules[name]
    module._offline_and_exact_source.__wrapped__(request, monkeypatch)
    return module


def _not_called(*_args, **_kwargs):
    raise AssertionError("Replay must not rebuild instructions, dispatch, or invoke a truth owner")


def _restore_fixture(root):
    """Open existing owners without rerunning the fixture's claim_new_run."""
    upstream = root / "paragraph/upstream"
    lifecycle = RunAttemptLifecycleService(upstream / "lifecycle.jsonl")
    artifact = ArtifactRuntime(upstream / "artifact-runtime.jsonl", lifecycle_service=lifecycle)
    paragraphs = ParagraphRuntime(
        artifact_runtime=artifact, authorization_validator=_not_called,
        source_validator=_not_called, manifest_authorization_validator=_not_called,
        manifest_ledger_path=root / "paragraph/paragraph-manifest.jsonl",
    )
    runtime = ClaimVerificationRuntime(
        paragraph_runtime=paragraphs, binding_source_validator=_not_called,
        ledger_path=root / "claim-verification.jsonl",
    )
    reference, = (item for item in artifact.artifact_store.list_references()
                  if item.artifact_type == "claim_extraction_input")
    receipt = artifact.artifact_store.get_json(
        reference.artifact_ref, task_id=reference.task_id, run_id=reference.run_id,
    )
    material = receipt["input_material"]
    context = ClaimOperationContextSchema(
        active_lineage=lifecycle.resolve_active_lineage(reference.run_id, "section:" + reference.section_id),
        authorization_ref=material["authorization_ref"], operation_purpose="claim-records",
    )
    gateway = SimpleNamespace(calls=[], generate=_not_called, routing_capabilities=_not_called)
    extractor = cm.TargetClaimExtractionRuntime(claim_runtime=runtime, model_gateway=gateway)
    bindings = tuple(ParagraphBindingSchema.model_validate(item) for item in material["paragraph_bindings"])
    return SimpleNamespace(extractor=extractor, runtime=runtime, gateway=gateway,
                           task_id=reference.task_id, context=context, bindings=bindings, receipt=receipt)


@pytest.mark.parametrize("state", ["completed", "unknown"])
def test_postwrite_a_v1_context_replays_under_v2_without_dispatch(tmp_path, monkeypatch, isolation, state):
    fixture_root = Path(os.environ["POSTWRITE_A_V1_FIXTURES"]).resolve()
    assert fixture_root.name == "fixtures" and fixture_root.parent.name.startswith("v1_")
    assert isolation._is_beneath(fixture_root, ISOLATION_ROOT / "mechanical")
    matches = [path for path in fixture_root.glob("*/preserved-v1-" + state)
               if not path.parent.name.endswith("current")]
    assert len(matches) == 1
    source = matches[0]
    source_before = isolation._tree_bytes(source)
    copied = tmp_path / ("copied-synthetic-v1-" + state)
    shutil.copytree(source, copied)
    restored = _restore_fixture(copied)
    package = ContextPackageSchema.model_validate(restored.receipt["context_package"])
    assert package.instruction_binding.role_instruction_version == "v1"
    assert package.instruction_binding.role_instruction_ref.endswith("/v1")
    before = isolation._tree_bytes(copied)
    monkeypatch.setattr(cm.TargetClaimExtractionRuntime, "_package", _not_called)
    kwargs = dict(task_id=restored.task_id, bindings=restored.bindings, operation_context=restored.context)
    if state == "completed":
        result = restored.extractor.extract_and_commit(**kwargs)
        assert result.records
        assert all(restored.runtime.resolve_claim(record.claim_ref) == record for record in result.records)
    else:
        with pytest.raises(cm.ClaimExtractionError, match="no automatic redispatch"):
            restored.extractor.extract_and_commit(**kwargs)
    assert restored.gateway.calls == []
    assert isolation._tree_bytes(copied) == before
    assert isolation._tree_bytes(source) == source_before
    entry = isolation._load_entry()
    with pytest.raises(ValueError, match="rejects historical or unreserved"):
        entry.preflight_existing(task_id=restored.task_id, run_id=restored.context.active_lineage.run_id)


# These are injected model outputs. They prove exact representation, identity,
# and conservative accounting only; every case still needs real-model acceptance.
GOLD_CASES = (
    ("pure-commitment", "本方案将遵守不编造参数的要求", "NO_APPLICABLE_FACTUAL_CLAIM", None),
    ("completed-status", "所有技术参数已经核实", "FACTUAL_CLAIMS_MATERIALIZED", "current_project_fact"),
    ("engineering-guarantee", "建成后保证任何灾难业务不中断", "FACTUAL_CLAIMS_MATERIALIZED", "technical_specification"),
    ("mixed-procurement", "严格遵守要求，已采购50台服务器", "FACTUAL_CLAIMS_MATERIALIZED", "current_project_quantity"),
    ("external-standard", "某技术标准第N条规定某指标", "FACTUAL_CLAIMS_MATERIALIZED", "policy_requirement"),
    ("conditional-plan", "若审批通过，拟配置20台", "FACTUAL_CLAIMS_MATERIALIZED", "current_project_configuration"),
    ("unsupported-inference", "已有20台，因此必然节省30%", "FACTUAL_CLAIMS_MATERIALIZED", "current_project_fact"),
    ("uncertain-mixed", "本方案将遵守要求，参数核实状态尚待确认", "INDETERMINATE", "current_project_fact"),
)


@pytest.mark.parametrize("case,text,state,claim_type", GOLD_CASES, ids=[item[0] for item in GOLD_CASES])
def test_postwrite_a_controlled_gold_preserves_boundaries_and_v2_request(
    tmp_path, monkeypatch, isolation, case, text, state, claim_type,
):
    original = claim_runtime_fixtures._paragraph_candidate

    def candidate(*args, **kwargs):
        return original(*args, **{**kwargs, "text": text})

    monkeypatch.setattr(claim_runtime_fixtures, "_paragraph_candidate", candidate)
    task_id, run_id = isolation._identity_pair()
    environment = isolation._unit_environment(tmp_path / case, monkeypatch, task_id=task_id, run_id=run_id)
    environment.gateway.output = {"units": [{
        "unit_id": environment.units[0]["unit_id"], "accounting_state": state,
        "claims": [] if claim_type is None else [{"quote": text, "occurrence_index": 0, "claim_type": claim_type}],
    }]}
    result = isolation._extract(environment)
    receipt, package = isolation._claim_receipt(environment)
    request, = environment.gateway.calls
    role = request.messages[1]["content"]
    binding = package.instruction_binding
    assert binding.role_instruction_ref == "instruction://claim-domain/role/v3"
    assert binding.role_instruction_version == request.extra["prompt_version"] == "v3"
    assert canonical_sha256(role) == binding.role_instruction_sha256
    assert "INDETERMINATE" in role and "RequirementAdherence" in role
    final_input = {"input_kind": "MESSAGES", "messages": request.messages}
    assert package.model_input.final_input_material.inline_final_input_payload == final_input
    assert canonical_sha256(final_input) == package.model_input.final_model_input_sha256
    assert request.extra["llm_context_summary"] == {
        "managed": True, "package_id": package.package_ref.context_package_id,
        "context_sha256": package.model_input.final_model_input_sha256,
    }
    fingerprint = canonical_sha256(receipt["input_material"])
    assert receipt["input_fingerprint"] == fingerprint
    assert receipt["model_call_id"] == request.model_call_id == "model_call_claim_" + fingerprint
    assert (request.task_id, request.run_id) == (task_id, run_id)
    assert package.invocation_subject.run_id == run_id
    assert package.invocation_subject.invocation_id == "claim-extraction://sha256/" + fingerprint
    result_ref, = (item for item in environment.extractor.store.list_references()
                   if item.artifact_type == "claim_extraction_result")
    result_receipt = environment.extractor.store.get_json(result_ref.artifact_ref, task_id=task_id, run_id=run_id)
    assert result_receipt["model_call_id"] == request.model_call_id
    assert result_receipt["input_fingerprint"] == fingerprint
    assert result.coverage_accounting.accounting_state.value == state
    if claim_type is not None:
        assert tuple(record.text_span.text for record in result.records) == (text,)
        assert all(environment.runtime.resolve_claim_head(record.claim_ref) is None for record in result.records)
    else:
        assert result.records == ()
    # Full GOLD quotes preserve the plan condition, procurement status/quantity,
    # engineering guarantee and the unsupported 30% consequence verbatim.
    before = isolation._tree_bytes(tmp_path / case)
    replay = isolation._extract(environment)
    assert replay.records == result.records
    assert len(environment.gateway.calls) == 1
    assert isolation._tree_bytes(tmp_path / case) == before


def test_postwrite_a_pending_intent_before_dispatch_never_redispatches(tmp_path, monkeypatch, isolation):
    task_id, run_id = isolation._identity_pair()
    root = tmp_path / "pending-before-dispatch"
    environment = isolation._unit_environment(root, monkeypatch, task_id=task_id, run_id=run_id)
    checkpoints = 0

    def stop_after_intent():
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 2:
            raise SystemExit("synthetic stop after intent, before physical dispatch")

    monkeypatch.setattr(cm, "checkpoint_current_execution", stop_after_intent)
    with pytest.raises(SystemExit, match="before physical dispatch"):
        isolation._extract(environment)
    assert environment.gateway.calls == []
    receipt, package = isolation._claim_receipt(environment)
    assert package.instruction_binding.role_instruction_version == "v3"
    assert receipt["model_call_id"]
    assert not any(item.artifact_type == "claim_extraction_result" for item in environment.extractor.store.list_references())
    before = isolation._tree_bytes(root)
    monkeypatch.setattr(cm, "checkpoint_current_execution", lambda: None)
    monkeypatch.setattr(cm.TargetClaimExtractionRuntime, "_package", _not_called)
    with pytest.raises(cm.ClaimExtractionError, match="no automatic redispatch"):
        isolation._extract(environment)
    assert environment.gateway.calls == []
    assert isolation._tree_bytes(root) == before


def test_postwrite_a_fixed_v2_entry_rejects_unstarted_synthetic_identity(tmp_path, monkeypatch, isolation):
    entry = isolation._load_entry()
    roots = (Path(entry.OUTPUT_ROOT), Path(entry.CONFIG["rag_root"]))
    before = {str(root): tuple(sorted(path.name for path in root.iterdir())) if root.exists() else () for root in roots}
    task_id, run_id = isolation._identity_pair()
    environment = isolation._unit_environment(tmp_path / "synthetic-unstarted", monkeypatch, task_id=task_id, run_id=run_id)
    synthetic_before = isolation._tree_bytes(tmp_path / "synthetic-unstarted")
    project = tmp_path / "unstarted-project-input.json"
    project.write_text(json.dumps({"task_id": task_id}), encoding="utf-8")
    with pytest.raises(ValueError, match="rejects historical or unreserved"):
        entry.preflight_new(project_input_file=project, run_id=run_id)
    for action in ("preflight-existing", "recover"):
        with pytest.raises(ValueError, match="rejects historical or unreserved"):
            entry.main([action, "--task-id", task_id, "--run-id", run_id])
    assert environment.gateway.calls == []
    assert isolation._tree_bytes(tmp_path / "synthetic-unstarted") == synthetic_before
    assert {str(root): tuple(sorted(path.name for path in root.iterdir())) if root.exists() else () for root in roots} == before
