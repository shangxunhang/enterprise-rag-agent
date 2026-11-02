"""Real Gateway, fixed Fake client: physical capture and noninterference."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import statistics
import time

import pytest

from contracts.base_client import BaseLLMClient
from core.diagnostics import DiagnosticConfig, DiagnosticSession, activate_diagnostics, current_diagnostics
from core.runtime.execution_control import WorkflowExecutionCancelled, WorkflowExecutionControl, activate_execution_control
from eval.full_chain.domains.runtime_reliability import read_diagnostic_prefix
from eval.full_chain.domains.generation_claim import model_representations, extract_generation_claim
from eval.full_chain.snapshot import SnapshotView
from model_gateway.model_contract import ModelProfile, ModelRole, RoutingPolicy
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter
from schemas.failure import FailureOrigin
from schemas.model import ModelRequestSchema, ModelResponseSchema, TokenUsageSchema


class FixedClient(BaseLLMClient):
    def __init__(self, model_name="fixed-1", *, unavailable=False, control=None, throws=False):
        self.model_name = model_name
        self.unavailable = unavailable
        self.control = control
        self.throws = throws
        self.calls = []

    def generate(self, request):
        self.calls.append(request.model_copy(deep=True))
        if self.throws:
            if isinstance(self.throws, BaseException):
                raise self.throws
            raise WorkflowExecutionCancelled("fixed client cancellation")
        response = ModelResponseSchema(model_call_id=request.model_call_id, task_id=request.task_id, run_id=request.run_id, model_name=self.model_name, success=not self.unavailable, content="A" if not self.unavailable else "", error_message="provider unavailable" if self.unavailable else None, failure_origin=FailureOrigin.PROVIDER_RUNTIME if self.unavailable else FailureOrigin.UNKNOWN, created_at=request.created_at, finish_reason="error" if self.unavailable else "stop", latency_ms=3, token_usage=TokenUsageSchema(prompt_tokens=7, completion_tokens=2, total_tokens=9), metadata={"Authorization": "secret-must-not-leak", "sdk_packet": {"key": "secret-must-not-leak"}})
        if self.control:
            self.control.cancel("fixed post-return cancellation")
        return response


def request():
    return ModelRequestSchema(model_call_id="logical-fixed", task_id="task-fixed", run_id="run-fixed", model_role="section_generation", prompt="visible input", messages=[{"role": "user", "content": "visible input"}], created_at="2026-10-05T00:00:00Z", extra={"api_key": "secret-must-not-leak", "headers": {"Authorization": "secret-must-not-leak"}, "llm_context_summary":{"package_id":"package-fixed","context_sha256":"hash-fixed","final_model_input_sha256":"input-hash-fixed","context_authority":"SIF-10","credentials":"secret-must-not-leak"}})


def gateway(*clients):
    profiles = [ModelProfile(profile_id="p" + str(n), model_name=c.model_name, provider="fixed") for n,c in enumerate(clients)]
    router = ModelRouter(clients[0].model_name, profiles=profiles, policies=[RoutingPolicy(role=ModelRole.SECTION_GENERATION, candidates=[p.profile_id for p in profiles])])
    result = ModelGateway(clients[0].model_name, router=router)
    for client in clients:
        result.register_client(client)
    return result


def session(tmp_path, name="capture", **kwargs):
    return DiagnosticSession(DiagnosticConfig(tmp_path / name, tmp_path, "fixture-session", flush_records=1, **kwargs))


def captured(session, tmp_path):
    session.close()
    prefix = read_diagnostic_prefix(session.path, [tmp_path])
    check_generation_reader(prefix)
    return prefix


def check_generation_reader(prefix):
    rows = model_representations(prefix["events"], capture_complete=prefix["completeness"] == "COMPLETE")
    if prefix["completeness"] != "COMPLETE":
        assert all(g["raw_state"] != "MEASURED" for g in rows["physical_attempts"])
    events = [*prefix["events"], *([prefix["seal"]] if prefix.get("seal") else [])]
    batch = extract_generation_claim(SnapshotView.from_records({"diagnostic_events": events}), subject_id="capture-fixture")
    return rows, batch


def representation(prefix, kind):
    return [e for e in prefix["events"] if e["event_type"] == kind]


def test_T02_normal_real_gateway_preserves_actual_request_raw_and_projection(tmp_path):
    client = FixedClient()
    observer = session(tmp_path)
    with observer:
        response = gateway(client).generate(request())
    prefix = captured(observer, tmp_path)
    assert prefix["completeness"] == "COMPLETE"
    rows = [representation(prefix, kind)[0] for kind in ("MODEL_ATTEMPT_REQUEST", "MODEL_CLIENT_RAW_RESPONSE", "MODEL_GATEWAY_PROJECTED_RESPONSE")]
    assert len({r["provider_attempt_id"] for r in rows}) == 1
    assert [r["provider_attempt_ordinal"] for r in rows] == [1,1,1]
    assert rows[0]["material"]["prompt"] == "visible input"
    assert rows[0]["material"]["model_name"] == client.calls[0].model_name
    assert rows[0]["material"]["extra"]["llm_context_summary"]["package_id"]=="package-fixed"
    assert rows[1]["material"]["content"] == rows[2]["material"]["content"] == response.content == "A"
    assert rows[1]["material"]["metadata"] == {}
    assert "selected_profile" in rows[2]["material"]["metadata"]
    assert all("secret-must-not-leak" not in p.read_text() for p in observer.path.parent.rglob("*.json*"))
    groups,batch = check_generation_reader(prefix)
    assert groups["physical_attempts"][0]["raw_state"] == "MEASURED"
    assert any(s.unit_id == "D05" and s.measurement_state == "MEASURED" for s in batch.stages)


def test_T02_return_after_cancel_does_not_overwrite_successful_raw(tmp_path):
    control = WorkflowExecutionControl.with_timeout(execution_id="cancel-fixture", timeout_seconds=10)
    observer = session(tmp_path)
    with observer, activate_execution_control(control):
        with pytest.raises(WorkflowExecutionCancelled):
            gateway(FixedClient(control=control)).generate(request())
    prefix = captured(observer, tmp_path)
    raw = representation(prefix,"MODEL_CLIENT_RAW_RESPONSE")[0]["material"]
    projected = representation(prefix,"MODEL_GATEWAY_PROJECTED_RESPONSE")[0]["material"]
    assert (raw["success"],raw["finish_reason"],raw["content"]) == (True,"stop","A")
    assert (projected["success"],projected["finish_reason"]) == (False,"cancelled")
    _,batch=check_generation_reader(prefix)
    assert any(f.code=="GATEWAY_POST_RETURN_PROJECTION" for f in batch.findings)


def test_T02_two_physical_fallback_keeps_both_material_chains_and_usage(tmp_path):
    disabled = gateway(FixedClient(unavailable=True),FixedClient("fixed-2"))
    off_response = disabled.generate(request())
    enabled = gateway(FixedClient(unavailable=True),FixedClient("fixed-2"))
    observer = session(tmp_path)
    with observer:
        on_response = enabled.generate(request())
    prefix = captured(observer,tmp_path)
    for kind in ("MODEL_ATTEMPT_REQUEST","MODEL_CLIENT_RAW_RESPONSE","MODEL_GATEWAY_PROJECTED_RESPONSE"):
        events = representation(prefix,kind)
        assert len(events)==2
        assert [r["provider_attempt_ordinal"] for r in events]==[1,2]
        assert len({r["provider_attempt_id"] for r in events})==2
    assert [r["material"]["success"] for r in representation(prefix,"MODEL_CLIENT_RAW_RESPONSE")]==[False,True]
    assert on_response.content == off_response.content
    assert enabled.usage_snapshot() == disabled.usage_snapshot()


def test_T02_client_control_exception_is_not_fabricated_raw(tmp_path):
    observer = session(tmp_path)
    client = FixedClient(throws=True)
    with observer, pytest.raises(WorkflowExecutionCancelled):
        gateway(client).generate(request())
    prefix=captured(observer,tmp_path)
    assert len(client.calls)==1
    assert len(representation(prefix,"MODEL_ATTEMPT_REQUEST"))==1
    assert len(representation(prefix,"MODEL_CLIENT_EXCEPTION"))==1
    assert representation(prefix,"MODEL_CLIENT_RAW_RESPONSE")==[]
    assert representation(prefix,"MODEL_CLIENT_EXCEPTION")[0]["outcome"]=="UNKNOWN"
    groups,_=check_generation_reader(prefix)
    assert groups["physical_attempts"][0]["raw_state"]=="NO_RAW_RESPONSE"


def test_T02_client_unknown_base_exception_keeps_exact_exception_without_raw(tmp_path):
    class InterruptedFixture(BaseException):
        pass
    failure=InterruptedFixture("fixed unknown")
    observer=session(tmp_path)
    with observer, pytest.raises(InterruptedFixture) as actual:
        gateway(FixedClient(throws=failure)).generate(request())
    assert actual.value is failure
    prefix=captured(observer,tmp_path)
    assert representation(prefix,"MODEL_CLIENT_RAW_RESPONSE")==[]
    assert representation(prefix,"MODEL_GATEWAY_PROJECTED_RESPONSE")==[]
    assert representation(prefix,"MODEL_CLIENT_EXCEPTION")[0]["outcome"]=="UNKNOWN"


def test_T02_raw_prefix_survives_downstream_business_commit_failure(tmp_path):
    observer=session(tmp_path)
    with pytest.raises(RuntimeError,match="downstream commit"), observer:
        result=gateway(FixedClient()).generate(request())
        assert result.success
        observer.flush()
        prefix=read_diagnostic_prefix(observer.path,[tmp_path])
        assert representation(prefix,"MODEL_CLIENT_RAW_RESPONSE")[0]["material"]["content"]=="A"
        assert prefix["completeness"]=="CAPTURE_INCOMPLETE"  # not sealed yet
        raise RuntimeError("downstream commit failed")
    assert representation(captured(observer,tmp_path),"MODEL_CLIENT_RAW_RESPONSE")[0]["material"]["content"]=="A"


@pytest.mark.parametrize("failure",["representation-cap","queue-drop","no-seal","disk-error"])
def test_T11_capture_failure_keeps_business_output_and_exposes_incomplete(tmp_path,monkeypatch,failure):
    kwargs={"max_representation_bytes":100} if failure=="representation-cap" else {}
    observer=session(tmp_path,**kwargs)
    if failure=="queue-drop":
        monkeypatch.setattr(observer,"config",replace(observer.config,max_queue_events=1))
        with observer._condition:
            observer.emit({"event_type":"fill"})
            observer.emit({"event_type":"dropped"})
    if failure=="disk-error":
        monkeypatch.setattr(observer,"_blob_root",tmp_path/"missing-parent")
    with observer:
        result=gateway(FixedClient()).generate(request())
    assert result.content=="A" and result.success
    if failure=="no-seal":
        lines=observer.path.read_bytes().splitlines(keepends=True)
        # A frozen copy of the complete prefix, simulating an unsealed hard exit.
        unsealed=tmp_path/"unsealed.jsonl"
        unsealed.write_bytes(b"".join(lines[:-1]))
        prefix=read_diagnostic_prefix(unsealed,[tmp_path])
    else:
        prefix=read_diagnostic_prefix(observer.path,[tmp_path])
    assert prefix["completeness"]=="CAPTURE_INCOMPLETE"
    groups,batch=check_generation_reader(prefix)
    assert all(g["raw_state"] != "MEASURED" for g in groups["physical_attempts"])
    if failure=="representation-cap":
        assert representation(prefix,"MODEL_CLIENT_RAW_RESPONSE")==[]
    assert observer.health()["lost_count"]>0 or failure=="no-seal"


def test_T12_disabled_has_no_IO_and_span_preserves_business_exception(tmp_path):
    root=tmp_path/"disabled"
    observer=DiagnosticSession(DiagnosticConfig(root,tmp_path,"off",enabled=False))
    with observer:
        assert current_diagnostics().enabled is False
        with pytest.raises(ValueError,match="business"):
            with observer.span("fixed","FS_API_ELAPSED"):
                raise ValueError("business")
    assert not root.exists()


def test_T12_paired_overhead_and_business_equivalence(tmp_path):
    for enabled in (False,True):
        with DiagnosticSession(DiagnosticConfig(tmp_path/("warmup-"+str(enabled)),tmp_path,"warmup",enabled=enabled)):
            gateway(FixedClient()).generate(request())
    results={False:[],True:[]}
    cpu={False:[],True:[]}
    health=[]
    semantics={False:[],True:[]}
    for repetition in range(5):
        for enabled in (False,True):
            observer=DiagnosticSession(DiagnosticConfig(tmp_path/f"overhead-{repetition}-{enabled}",tmp_path,"overhead",enabled=enabled))
            client=FixedClient()
            gw=gateway(client)
            begin=time.perf_counter(); cpu_begin=time.process_time()
            with observer:
                result=gw.generate(request())
            results[enabled].append(time.perf_counter()-begin)
            cpu[enabled].append(time.process_time()-cpu_begin)
            assert result.success and result.content=="A"
            assert gw.usage_snapshot()["logical_calls"]==1
            actual_request=client.calls[0].model_dump(mode="json")
            # Original Gateway creates a fresh physical UUID in each independent
            # replay. All other request/route/output/usage bytes must agree.
            actual_request["extra"].pop("provider_attempt_id")
            semantics[enabled].append({"request":actual_request,"response":result.model_dump(mode="json"),"usage":gw.usage_snapshot()})
            if enabled:
                health.append(observer.health())
    baseline=statistics.median(results[False])
    assert semantics[False]==semantics[True]
    increment=statistics.median(results[True])-baseline
    assert increment <= max(baseline*.1,.05)
    report={"repetitions":5,"warmups_per_mode":1,"wall_s":{str(k):v for k,v in results.items()},"cpu_s":{str(k):v for k,v in cpu.items()},"paired_median_increment_s":statistics.median([on-off for on,off in zip(results[True],results[False])]),"p95_wall_s":{str(k):sorted(v)[-1] for k,v in results.items()},"capture_health":health,"clock":"perf_counter/process_time", "T15":"PENDING_RUN_AUTHORIZATION"}
    (tmp_path/"observer_overhead_receipt.json").write_text(json.dumps(report,indent=2))


@pytest.mark.parametrize("value", [0, 65, True, False, 16.0, "32", None])
def test_T11_depth_config_rejects_noninteger_or_unbounded_limits(tmp_path, value):
    with pytest.raises(ValueError, match="max_depth.*integer from 1 to 64"):
        DiagnosticConfig(tmp_path / "invalid", tmp_path, "depth", max_depth=value)
    assert not (tmp_path / "invalid").exists()


def _deep_public_result():
    nested = {"exact_source_ref": "fixed-original-ref"}
    for _ in range(20):
        nested = {"binding": nested}
    return {"task_id": "fixed-task", "run_id": "fixed-run", "status": "partial_success",
        "terminal_outcome": {"status": "OUTPUT_BEST_EFFORT"}, "release_publication": None,
        "target_release_publication": {"manifest_ref": "fixed-manifest"},
        "deliverable_final_result": {"draft": {"sections": [
            {"section_id": str(index), "input": nested, "text": "fixed public text " * 5000}
            for index in range(8)]}}, "shutdown_diagnostics": [],
        "settings": {"credentials": "outside-public-allowlist"}}


def test_T11_default_depth_refusal_preserves_actual_mainline_result_and_source(tmp_path):
    from application.mainline_service import _observe_new_run
    from core.diagnostics import _bounded_copy
    result = _deep_public_result()
    original = json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
    source = tmp_path / "frozen_public_source.json"
    source.write_bytes(original)
    with pytest.raises(ValueError, match="depth cap"):
        _bounded_copy({"payload": result["deliverable_final_result"]}, 2 * 1024 * 1024)
    calls = []

    @_observe_new_run
    def business(self, **kwargs):
        calls.append(kwargs["user_input"])
        return result

    observer = session(tmp_path, "default-depth")
    assert observer.config.max_depth == 16
    with observer:
        returned = business(object(), user_input="fixed input")
    assert returned is result
    assert calls == ["fixed input"]
    assert json.dumps(result, ensure_ascii=False, sort_keys=True).encode() == original
    assert source.read_bytes() == original
    assert observer.health()["lost_count"] == 1
    prefix = read_diagnostic_prefix(observer.path, [tmp_path])
    assert prefix["completeness"] == "CAPTURE_INCOMPLETE"
    assert prefix["seal"]["lost_count"] == 1
    assert not any(event["event_type"] == "S-PUBLIC" for event in prefix["events"])
    assert any(event["event_type"] == "SPAN_RESULT" and event["category"] == "WORKFLOW_WALL"
        for event in prefix["events"])


def test_T12_custom_depth_captures_actual_public_wrapper_without_result_mutation(tmp_path):
    from application.mainline_service import _observe_new_run
    result = _deep_public_result()
    original = json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
    source = tmp_path / "frozen_public_source.json"
    source.write_bytes(original)
    original_hash = sha256(original).hexdigest()
    calls = []

    @_observe_new_run
    def business(self, **kwargs):
        calls.append((kwargs["user_input"], kwargs["project_input"]))
        return result

    project = {"schema_version": "fixed", "business_goal": "original"}
    for enabled in (False, True):
        observer = session(tmp_path, "custom-depth-" + str(enabled), enabled=enabled,
            max_depth=32, max_event_bytes=2*1024*1024, max_queue_bytes=8*1024*1024)
        with observer:
            returned = business(object(), user_input="fixed input", project_input=project)
        assert returned is result
        assert sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest() == original_hash
        assert source.read_bytes() == original
        assert current_diagnostics().enabled is False
        if not enabled:
            assert not (tmp_path / ("custom-depth-" + str(enabled))).exists()
            continue
        prefix = read_diagnostic_prefix(observer.path, [tmp_path])
        assert prefix["completeness"] == "COMPLETE"
        assert observer.health()["lost_count"] == prefix["seal"]["lost_count"] == 0
        public = [event for event in prefix["events"] if event["event_type"] == "S-PUBLIC"]
        assert len(public) == 1
        expected = {key: result.get(key) for key in ("task_id", "run_id", "status", "terminal_outcome",
            "release_publication", "target_release_publication", "deliverable_final_result", "shutdown_diagnostics")}
        assert public[0]["payload"] == expected
        assert public[0]["representation_kind"] == "PUBLIC_DELIVERY_PROJECTION"
        assert "settings" not in public[0]["payload"]
        assert prefix["events"][0]["caps"]["max_depth"] == 32
    assert calls == [("fixed input", project), ("fixed input", project)]


def test_T12_depth_profile_preserves_original_mainline_exception_object(tmp_path):
    from application.mainline_service import _observe_new_run
    error = RuntimeError("original business error")
    calls = []

    @_observe_new_run
    def business(self, **kwargs):
        calls.append(kwargs["user_input"])
        raise error

    for enabled in (False, True):
        observer = session(tmp_path, "error-depth-" + str(enabled), enabled=enabled, max_depth=32,
            max_event_bytes=2*1024*1024, max_queue_bytes=8*1024*1024)
        with pytest.raises(RuntimeError) as caught:
            with observer:
                business(object(), user_input="fixed failure")
        assert caught.value is error
        assert current_diagnostics().enabled is False
        if not enabled:
            assert not (tmp_path / ("error-depth-" + str(enabled))).exists()
            continue
        prefix = read_diagnostic_prefix(observer.path, [tmp_path])
        assert prefix["completeness"] == "COMPLETE"
        assert any(event["event_type"] == "P-INGRESS" and event["payload"]["phase"] == "error"
            and event["payload"]["error_type"] == "RuntimeError" for event in prefix["events"])
        assert any(event["event_type"] == "SPAN_ERROR" for event in prefix["events"])
        assert not any(event["event_type"] == "S-PUBLIC" for event in prefix["events"])
    assert calls == ["fixed failure", "fixed failure"]


def test_T11_depth_profile_reaches_event_model_pool_copy_and_segment_caps(tmp_path, monkeypatch):
    import core.diagnostics as diagnostics
    copied = []
    original_copy = diagnostics._bounded_copy

    def observed_copy(value, limit, **kwargs):
        copied.append((value, kwargs.get("max_depth")))
        return original_copy(value, limit, **kwargs)

    monkeypatch.setattr(diagnostics, "_bounded_copy", observed_copy)
    observer = session(tmp_path, "depth-copy-paths", max_depth=32)
    nested = {"leaf": "unchanged"}
    for _ in range(20):
        nested = {"nested": nested}
    with observer:
        observer.emit({"event_type": "DEEP_EVENT", "payload": nested})
        observer.capture_model("MODEL_ATTEMPT_REQUEST", request())
        observer.emit_pool({"pool_id": "fixed-depth", "declared_count": 1}, [nested])
        child = observer.allocate_segment(tmp_path / "child", "child", byte_quota=8192, event_quota=5)
    assert child.max_depth == 32
    assert all(depth == 32 for _, depth in copied)
    assert any(value == nested for value, _ in copied)  # pool item copy
    assert any(value.get("model_call_id") == "logical-fixed" for value, _ in copied)  # model material copy
    prefix = read_diagnostic_prefix(observer.path, [tmp_path])
    assert prefix["completeness"] == "COMPLETE"
    assert next(event for event in prefix["events"] if event["event_type"] == "DEEP_EVENT")["payload"] == nested
    assert next(event for event in prefix["events"] if event["event_type"] == "POOL_ITEM")["item"] == nested
    assert next(event for event in prefix["events"] if event["event_type"] == "MODEL_ATTEMPT_REQUEST")["material"]["prompt"] == "visible input"
