"""Minimal local closure proofs for BCD independent acceptance.

These controls are deliberately narrow:
- V08: current legal Template evidence reaches the actual local Writer ModelRequest
  while the raw template example remains absent.
- REC-C: capture owner survives a real child-process reopen and exact replay.
- REC-W: the production Writer SQLite owner is cold-read from a separate process
  without redispatch or store mutation.

No real Provider, network, external PostgreSQL, or production source mutation is used.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
from uuid import uuid4

import pytest

from application.mainline_service import MainlineApplicationService
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterLogicalExecutionKey,
)
from core.config import get_settings
from core.hashing import canonical_sha256
from model_gateway.fake_llm_client import FakeLLMClient
from rag.context.context_packer import ContextPacker
from rag.evidence.governance_v2 import OnlineEvidenceGovernanceV2
from rag.mapping.request_mapper import RAGRequestMapper
from rag.mapping.result_mapper import RAGResultMapper
from rag.services.rag_service import ObservedRAGService
from schemas.rag import EvidenceBundleSchema, RetrievalAccessScopeSchema
from schemas.evidence_common import EvidenceStatus
from schemas.status import ExecutionStatus

from backend.tests import test_fpbca_bcd_c2_native_producer_visibility as c2
from backend.tests.test_fpbca_bcd_reconciliation import native, _persist
from backend.tests.test_multi_agent_v3_writer_input_boundary import _project_input
import backend.tests.test_multi_agent_v3_workflow_recovery as workflow_tests


ROOT = Path(__file__).resolve().parents[2]


def _child_bootstrap() -> str:
    return f"""
import importlib.util
import sys
from pathlib import Path
ROOT = Path({str(ROOT)!r})
spec = importlib.util.spec_from_file_location("source_bootstrap", ROOT / "source_bootstrap.py")
module = importlib.util.module_from_spec(spec)
sys.modules["source_bootstrap"] = module
assert spec.loader is not None
spec.loader.exec_module(module)
module.bootstrap_source_root(ROOT)
"""


def _run_child(code: str) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _child_bootstrap() + "\n" + code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert process.returncode == 0, (
        f"child failed rc={process.returncode}\nSTDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
    )
    return process


def _current_template_bundle(request) -> EvidenceBundleSchema:
    """Build one current legal Template bundle with the frozen C2 producer path."""

    produced = c2._produce("template", "dense")
    source_scope = request.access_scope
    authorized = list(source_scope.authorized_kb_ids) if source_scope is not None else []
    for kb_id in ("template_style_kb", "project_fact_kb", "policy_standard_kb"):
        if kb_id not in authorized:
            authorized.append(kb_id)
    fixture_request = request.model_copy(
        update={
            "access_scope": RetrievalAccessScopeSchema(
                tenant_id=source_scope.tenant_id if source_scope is not None else "default",
                authorized_kb_ids=authorized,
            )
        },
        deep=True,
    )
    invocation = RAGRequestMapper().map(fixture_request)
    rows = [deepcopy(produced["result"])]
    initial = OnlineEvidenceGovernanceV2().evaluate(
        query=produced["query"],
        results=rows,
        runtime_context=invocation.payload["extra_metadata"],
    )
    accepted = []
    for row, assessment in zip(rows, initial.item_assessments):
        row["metadata"]["evidence_assessment_v2"] = assessment.model_dump(mode="json")
        if assessment.eligibility:
            accepted.append(row)
    assert accepted, "current Template producer did not reach the governed Packer"

    packer = ContextPacker(
        max_context_chars=int(request.max_context_chars or 30000),
        max_items=1,
        packing_strategy="default",
        include_metadata=False,
    )
    prepared = packer.prepare_projection(accepted)
    packed = packer.pack([], prepared_projection=prepared, token_budget=100000)
    assert packed.selected_results, "current legal Template projection was not selected"

    bundle = RAGResultMapper().map(
        request=fixture_request,
        invocation=invocation,
        raw_result={
            "data": {
                "retrieval_results": rows,
                "context_pack": packed.to_dict(),
            }
        },
        latency_ms=0,
        rag_project_root=str(ROOT),
        service_name="fpbca-local-closure-template-v08",
    )

    # This is the same explicit test-fixture semantic assertion used by FakeRAG:
    # the test owns sufficiency, while production still owns projection/currentness.
    topics = list(request.evidence_requirements.required_topics)
    assessment = bundle.assessment.model_copy(
        update={
            "assessment_state": "completed",
            "status": EvidenceStatus.SUFFICIENT,
            "coverage_ratio": 1.0,
            "required_topics": topics,
            "covered_topics": topics,
            "missing_topics": [],
            "evidence_available": True,
            "authoritative_evidence_present": False,
            "historical_or_reference_evidence_present": True,
            "prompt_evidence_source": "fpbca_current_template_fixture",
        },
        deep=True,
    )
    item_assessments = [
        item.model_copy(
            update={
                "supported_topics": topics,
                "reason_codes": list(item.reason_codes) + ["fpbca_current_template_fixture"],
            },
            deep=True,
        )
        for item in bundle.item_assessments
    ]
    raw = bundle.model_dump(mode="json")
    raw.update(
        {
            "task_id": request.task_id,
            "run_id": request.run_id,
            "query": request.query,
            "retrieval_trace_id": request.retrieval_trace_id,
            "retrieval_scope": request.retrieval_scope,
            "evidence_requirements": request.evidence_requirements.model_dump(mode="json"),
            "status": ExecutionStatus.SUCCESS.value,
            "assessment": assessment.model_dump(mode="json"),
            "item_assessments": [item.model_dump(mode="json") for item in item_assessments],
        }
    )
    current = EvidenceBundleSchema.model_validate(raw)
    assert c2.RAW_EXAMPLE not in current.context.context_text
    assert current.selected_evidence_ids
    return current


def test_v08_current_template_reaches_actual_writer_model_request(tmp_path, monkeypatch):
    task_id = "task-fpbca-v08-current-template"
    run_id = "run-fpbca-v08-current-template"
    project = _project_input(task_id)
    runtime_root = Path("E:/rag-agent-tmp") / ("fpbca-v08-local-" + uuid4().hex)
    runtime_root.mkdir(parents=True, exist_ok=False)
    requests = []
    bundles = []
    original_generate = FakeLLMClient.generate

    class _TemplateRAGService:
        def retrieve(self, request):
            bundle = _current_template_bundle(request)
            bundles.append(bundle)
            return bundle

        def close(self):
            return None

    def build_template_rag(_rag_factory, _options, trace=None, **_kwargs):
        return ObservedRAGService(_TemplateRAGService(), trace)

    def capture_generate(client, request):
        if request.extra.get("call_purpose") == "scheme_section_generation":
            requests.append(request.model_copy(deep=True))
        return original_generate(client, request)

    settings = replace(
        get_settings(),
        data_root=runtime_root,
        default_model_name="fake_llm",
        supervisor_model_name="fake_llm",
        enable_llm_routing=False,
        trace_enabled=True,
        data_capture_enabled=True,
        gate_enforcement_mode="strict",
        gate_recovery_rounds_limit=0,
    )
    environment = {
        "USE_REAL_RAG_TOOL": "false",
        "FAKE_RAG_SCENARIO": "default",
        "FAKE_LLM_SCENARIO": "default",
        "ENABLE_AGENT_SELF_RAG": "false",
        "ENABLE_SEMANTIC_GATE": "false",
    }
    factory = workflow_tests._W13InspectingFactory()

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(
            patch.object(
                workflow_tests,
                "_w13_single_section_project_input",
                lambda _task_id: project,
            )
        )
        stack.enter_context(patch.object(type(factory.rag_factory), "build", build_template_rag))
        stack.enter_context(patch.object(FakeLLMClient, "generate", capture_generate))
        try:
            summary = MainlineApplicationService(supervisor_factory=factory).run(
                project_root=ROOT,
                user_input=project.user_query,
                run_id=run_id,
                task_id=task_id,
                output_root=runtime_root,
                clean_existing=True,
                settings=settings,
                project_input=project.model_dump(mode="json"),
                allow_demo_defaults=False,
                enable_agent_self_rag=False,
            )

            assert bundles, summary.get("error_message") or summary
            assert len(requests) == 1, summary
            request = requests[0]
            message_text = "\n".join(item["content"] for item in request.messages)
            visible_template = bundles[0].context.context_text.strip()
            assert visible_template
            assert c2.TAIL in visible_template
            assert c2.TAIL in message_text
            assert c2.RAW_EXAMPLE not in visible_template
            assert c2.RAW_EXAMPLE not in message_text
            assert request.extra.get("call_purpose") == "scheme_section_generation"
        finally:
            shutil.rmtree(runtime_root, ignore_errors=True)


def test_rec_c_capture_exact_replay_survives_process_reopen(tmp_path):
    path = tmp_path / "capture-process-reopen.jsonl"
    record = {
        "capture_id": "fpbca-rec-c-process-reopen",
        "payload": {"value": 1, "scope": "local-only"},
    }
    code = f"""
import json
import os
from pathlib import Path
from rag.data_capture.jsonl_writer import JsonlWriter
from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink
path = Path({str(path)!r})
record = json.loads({json.dumps(json.dumps(record, ensure_ascii=False))})
JsonlWriter(path).write_once(record, id_field="capture_id")
rows = CrashSafeJsonlRecordSink(path).read_records()
print(json.dumps({{"pid": os.getpid(), "count": len(rows), "rows": rows}}, ensure_ascii=False))
"""
    first = _run_child(code)
    first_payload = json.loads(first.stdout.strip().splitlines()[-1])
    assert first_payload["count"] == 1
    before = path.read_bytes()

    second = _run_child(code)
    second_payload = json.loads(second.stdout.strip().splitlines()[-1])
    assert second_payload["count"] == 1
    assert second_payload["rows"] == [record]
    assert path.read_bytes() == before


def test_rec_w_writer_result_survives_process_reopen_without_redispatch(native):
    result, ref, _generation_ref = _persist(native)
    before = native["path"].read_bytes()
    key_json = json.dumps(native["key"].model_dump(mode="json"), ensure_ascii=False)
    ref_json = json.dumps(ref.model_dump(mode="json"), ensure_ascii=False)
    code = f"""
import json
import os
from core.hashing import canonical_sha256
from core.runtime.pre_write_execution_store import SQLitePreWriteExecutionStore
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterExecutionRefSchema,
    WriterLogicalExecutionKey,
)
store = SQLitePreWriteExecutionStore({str(native["path"] )!r})
key = WriterLogicalExecutionKey.model_validate(json.loads({key_json!r}))
ref = WriterExecutionRefSchema.model_validate(json.loads({ref_json!r}))
record = store.get_writer_execution(key)
result = store.resolve_writer_execution(ref)
assert record is not None and result is not None
print(json.dumps({{
    "pid": os.getpid(),
    "state": record.state.value,
    "semantic_fingerprint": record.semantic_fingerprint,
    "result_sha256": canonical_sha256(result.model_dump(mode="json")),
}}, ensure_ascii=False))
"""
    child = _run_child(code)
    payload = json.loads(child.stdout.strip().splitlines()[-1])
    assert payload["state"].lower() == "succeeded"
    assert payload["semantic_fingerprint"] == native["fingerprint"]
    assert payload["result_sha256"] == ref.result_sha256
    assert payload["result_sha256"] == canonical_sha256(result.model_dump(mode="json"))
    assert native["path"].read_bytes() == before
