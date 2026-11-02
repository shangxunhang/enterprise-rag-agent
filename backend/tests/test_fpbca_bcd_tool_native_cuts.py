"""DRAFT ONLY; future backend/tests/test_fpbca_bcd_tool_native_cuts.py.

Source-bound to sealed d2_tool_publication_memory_native_plan_04.  Not imported,
parsed, collected or executed during drafting.  Future execution requires the
parent D2 gate, trusted D3 ingress and a fresh frozen dependency epoch.

Only actual Q1/Q2 registrations are used.  Backends, cuts and producer
attestations are authored local test evidence.  _classify_passive is TEST-OWNED,
not a production Tool resolver, new result schema, owner, no-effect authority,
dispatch lease, retry permission or Model/business-correctness certification.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import threading
from typing import Any

import pytest

from apps.enterprise_document.runtime.agent_rag_v1.contracts import (
    MultiAgentProjectInputSchema,
    RetrievalResponseSchema,
)
from apps.enterprise_document.runtime.agent_rag_v1.runtime_factory import RuntimeFactory
from apps.enterprise_document.schemas.agent_rag_v1 import (
    NormalizedToolResultSchema,
    ToolResultStatus,
)
from apps.enterprise_document.services.agent_rag_v1 import (
    BudgetManager,
    CanonicalInvocationBudgetAdapter,
    JsonlToolExecutionStore,
    ToolGateway,
)
from apps.enterprise_document.services.agent_rag_v1.artifact_store import FileArtifactStore
from apps.enterprise_document.services.scheme_writer.agent_integration.section_agent_ports import (
    MainlineResearchToolGateway,
)
from apps.enterprise_document.services.scheme_writer.evidence_service import SchemeEvidenceService
from contracts.evidence import evidence_bundle_sha256
from core.runtime.execution_control import (
    WorkflowExecutionCancelled,
    WorkflowExecutionControl,
    activate_execution_control,
)
from rag.store.evidence_bundle_store import EvidenceBundleSnapshotStore
from backend.tests.test_multi_agent_v3_research_tool_bridge import (
    _arguments,
    _budget_manager,
    _request,
    _round_ref,
)
from backend.tests.test_rag_interface_v2_phase5_id_namespace import (
    _bundle,
    _gateway_context,
)


TOOL = "knowledge.retrieve"
Q1 = "mainline_evidence_service_v1"
Q2 = "standalone-v1"
INDETERMINATE = "INDETERMINATE"
RECOVERED = "RECOVERED_DURABLE_RESULT"


class _LocalCut(BaseException):
    """A local loss cut bypasses existing ordinary Exception error fallbacks."""


class _ObservationIncomplete(BaseException):
    """Harness observation failure; it does not cancel canonical owners."""


class _ProofMismatch(ValueError):
    pass


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _ProofMismatch(reason)


@dataclass(frozen=True)
class _ProducerProof:
    # Plain, test-owned immutable bytes captured from the actual producer.
    # None of these fields is a new public production ref or result contract.
    origin_json: bytes
    row_json: bytes
    source_json: bytes
    native_prefix: bytes

    def receipt(self) -> dict[str, Any]:
        return {
            "authority": "TEST_OWNED_ACTUAL_PRODUCER_ATTESTATION",
            "origin": json.loads(self.origin_json),
            "origin_sha256": _sha(self.origin_json),
            "normalized_result": json.loads(self.row_json),
            "normalized_result_sha256": _sha(self.row_json),
            "source": json.loads(self.source_json),
            "source_sha256": _sha(self.source_json),
            "native_prefix_hex": self.native_prefix.hex(),
            "native_prefix_sha256": _sha(self.native_prefix),
        }


class _CountedBackend:
    """The sole authored backend port; actual handler/evidence code stays real."""

    def __init__(self, case: "_Case", response: Any) -> None:
        self.case = case
        self.response = response

    def retrieve(self, request: Any) -> Any:
        if self.case.cut == "backend_before":
            self.case.bump("backend_before_cuts")
            raise _LocalCut("before authored backend entry")
        self.case.bump("backend_entries")
        payload = request.model_dump(mode="json")
        with self.case.lock:
            self.case.backend_requests.append(payload)
        result = self.response.model_copy(deep=True)
        self.case.thread_capture.backend_request = copy.deepcopy(payload)
        self.case.thread_capture.backend_response_sha256 = _sha(
            _json_bytes(result.model_dump(mode="json"))
        )
        self.case.bump("backend_returns")
        return result


class _Case:
    def __init__(self, root: Path, registration: str, cut: str | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True)
        self.registration = registration
        self.cut = cut
        self.context = _gateway_context()
        self.round_ref = _round_ref() if registration == Q1 else None
        self.arguments = (
            _arguments(self.round_ref)
            if registration == Q1
            else {"query_topics": ["local native tool cut"], "research_round": 1}
        )
        self.tool_log = root / "normalized-tool.jsonl"
        self.raw_root = root / "raw-artifacts"
        self.bundle_root = root / "bundle-snapshots"
        self.raw_store = FileArtifactStore(self.raw_root)
        self.snapshot_store = EvidenceBundleSnapshotStore(self.bundle_root)
        self.manager = _budget_manager(root) if registration == Q1 else None
        self.lock = threading.Lock()
        self.thread_capture = threading.local()
        self.counters: dict[str, int] = {}
        self.backend_requests: list[dict[str, Any]] = []
        self.producer_candidates: list[dict[str, Any]] = []
        self.durable_proofs: list[_ProducerProof] = []
        self.source_capture: dict[str, Any] = {}
        self.gateways: list[ToolGateway] = []
        self.miss_barrier: threading.Barrier | None = None
        self.response = (
            _bundle("d2_native_q1", selected_id="E_D2_NATIVE_Q1")
            if registration == Q1
            else RetrievalResponseSchema(
                status="sufficient",
                evidence_ids=["E_D2_NATIVE_Q2"],
                supported_topics=["local native tool cut"],
                source_refs=["source://local/d2-native-q2"],
            )
        )
        self.backend = _CountedBackend(self, self.response)
        self._instrument_native_sources()
        if registration == Q1:
            self.evidence_service = SchemeEvidenceService(
                rag_service=self.backend,
                agent_name="d2-native-test-research",
                evidence_bundle_store=self.snapshot_store,
            )
            self.bridge = MainlineResearchToolGateway(
                evidence_service=self.evidence_service,
                canonical_gateway_provider=self._gateway,
            )
        else:
            self.evidence_service = None
            self.bridge = None
            self.project_input = MultiAgentProjectInputSchema(
                task_id=self.context.task_id,
                run_id=self.context.run_id,
                scenario="success",
                output_dir=str(root / "unused-archived-runtime-output"),
            )
        # Actual existing pure operation/fingerprint derivation BEFORE effects.
        self.original_origin = self.origin(self.context)
        self.original_origin_json = _json_bytes(self.original_origin)

    def bump(self, key: str) -> None:
        with self.lock:
            self.counters[key] = self.counters.get(key, 0) + 1

    def counts(self) -> dict[str, int]:
        with self.lock:
            return dict(self.counters)

    def origin(self, context: Any) -> dict[str, Any]:
        call_id = (
            f"{context.invocation_id}:tool:001:{TOOL}"
            if self.registration == Q1
            else "legacy_call_d2_native_q2"
        )
        operation_id = (
            MainlineResearchToolGateway._canonical_tool_operation_ref(
                round_ref=self.round_ref, tool_name=TOOL, call_slot=1
            ).logical_operation_id
            if self.registration == Q1
            else call_id
        )
        return {
            "registration": self.registration,
            "context": context.model_dump(mode="json"),
            "arguments": copy.deepcopy(self.arguments),
            "call_id": call_id,
            "operation_id": operation_id,
            "fingerprint": ToolGateway._fingerprint(
                tool_name=TOOL,
                tool_version=self.registration,
                arguments=self.arguments,
                input_snapshot_version=context.input_snapshot_version,
            ),
            "research_round_ref": (
                self.round_ref.model_dump(mode="json") if self.round_ref else None
            ),
            "call_slot": 1 if self.round_ref else None,
            "binding_scope": (
                "REAL_Q1_ROUND_PLUS_TEST_PRODUCER_CONTEXT"
                if self.round_ref
                else "LEGACY_CALL_KEY_PLUS_TEST_PRODUCER_CONTEXT_ONLY"
            ),
        }

    def _instrument_native_sources(self) -> None:
        original_snapshot = self.snapshot_store.persist

        def persist(bundle: Any) -> Any:
            self.bump("snapshot_persist_attempts")
            ref = original_snapshot(bundle)
            path = self.snapshot_store._path_for_handle(ref.handle)
            capture = {
                "kind": "REAL_Q1_BUNDLE_SNAPSHOT",
                "handle": ref.handle,
                "snapshot_hash": ref.evidence_bundle_sha256,
                "contract_sha256": evidence_bundle_sha256(bundle),
                "bundle_task_id": bundle.task_id,
                "bundle_run_id": bundle.run_id,
                "accepted_ids": list(bundle.selected_evidence_ids),
                "bundle_json_sha256": _sha(_json_bytes(bundle.model_dump(mode="json"))),
                "envelope_sha256": _sha(path.read_bytes()),
            }
            with self.lock:
                self.source_capture = capture
            self.bump("snapshot_persist_returns")
            return ref

        self.snapshot_store.persist = persist
        original_raw = self.raw_store.put_json

        def put_raw(value: Any, **kwargs: Any) -> Any:
            self.bump("raw_put_attempts")
            reference = original_raw(value, **kwargs)
            path = self.raw_store._path_for_ref(reference.artifact_ref)
            capture = {
                "kind": "REAL_Q2_TOOL_RAW_ARTIFACT",
                "reference": reference.model_dump(mode="json"),
                "content_sha256": _sha(_json_bytes(value)),
                "content": copy.deepcopy(value),
                "envelope_sha256": _sha(path.read_bytes()),
            }
            with self.lock:
                self.source_capture = capture
            self.bump("raw_put_returns")
            return reference

        self.raw_store.put_json = put_raw
        if self.manager is not None:
            for name in ("reserve", "consume", "release"):
                original = getattr(self.manager, name)

                def call(*args: Any, _name=name, _original=original, **kwargs: Any) -> Any:
                    self.bump(f"budget_{_name}_attempts")
                    result = _original(*args, **kwargs)
                    self.bump(f"budget_{_name}_returns")
                    return result

                setattr(self.manager, name, call)

    def _gateway(self, context: Any) -> ToolGateway:
        # Each invocation gets an actual gateway and reopened native Tool store.
        store = JsonlToolExecutionStore(self.tool_log)
        original_get, original_put = store.get, store.put
        origin_json = _json_bytes(self.origin(context))

        def get(key: str) -> NormalizedToolResultSchema:
            try:
                return original_get(key)
            except KeyError:
                if self.miss_barrier is not None:
                    self.bump("native_initial_misses")
                    try:
                        # Outside the native read lock: no dispatch lease is added.
                        self.miss_barrier.wait(timeout=10)
                    except threading.BrokenBarrierError as exc:
                        raise _ObservationIncomplete("two native misses not observed") from exc
                raise

        def put(result: NormalizedToolResultSchema) -> bool:
            self.bump("tool_put_attempts")
            row_json = _json_bytes(result.model_dump(mode="json"))
            with self.lock:
                source = copy.deepcopy(self.source_capture)
                source["actual_backend_request"] = copy.deepcopy(
                    self.thread_capture.backend_request
                )
                source["actual_backend_response_sha256"] = (
                    self.thread_capture.backend_response_sha256
                )
                source_json = _json_bytes(source)
                candidate = {
                    "origin": json.loads(origin_json),
                    "row": json.loads(row_json),
                    "source": json.loads(source_json),
                }
                self.producer_candidates.append(candidate)
            if self.cut == "native_put_before":
                raise _LocalCut("backend returned; before native Tool result put")
            try:
                inserted = original_put(result)
            except BaseException as exc:
                self.bump("tool_put_failures")
                with self.lock:
                    candidate["native_exception_observation"] = type(exc).__name__
                    candidate["native_exception_message"] = str(exc)
                raise
            self.bump("tool_put_returns")
            if inserted:
                self.bump("tool_put_inserted")
            proof = _ProducerProof(
                origin_json, row_json, source_json, self.tool_log.read_bytes()
            )
            with self.lock:
                self.durable_proofs.append(proof)
            if self.cut == "native_put_durable_ack_lost":
                raise _LocalCut("native Tool append/readback returned; response lost")
            return inserted

        store.get, store.put = get, put

        def authorization(ctx: Any, registration: Any, arguments: Any) -> None:
            # Test injection only; not a Supervisor grant/currentness proof.
            assert ctx.task_id == self.context.task_id
            assert ctx.run_id == self.context.run_id
            assert ctx.section_id == self.context.section_id
            assert registration.name == TOOL
            assert registration.version == Q1
            assert dict(arguments) == self.arguments

        def currentness(ctx: Any) -> None:
            assert ctx.run_id == self.context.run_id
            assert ctx.attempt_id == self.context.attempt_id
            assert ctx.input_snapshot_version == self.context.input_snapshot_version

        gateway = ToolGateway(
            execution_store=store,
            artifact_store=self.raw_store,
            budget_runtime=(
                CanonicalInvocationBudgetAdapter(self.manager) if self.manager else None
            ),
            authorization_validator=authorization if self.registration == Q1 else None,
            currentness_validator=currentness if self.registration == Q1 else None,
        )
        with self.lock:
            self.gateways.append(gateway)
        return gateway

    def invoke(self, context: Any | None = None) -> NormalizedToolResultSchema:
        context = self.context if context is None else context
        if self.registration == Q1:
            request = _request(self.round_ref)
            with self.bridge.bind(request):
                result = self.bridge.session(context).call(TOOL, self.arguments)
            assert request.execution is not None
            return result
        gateway = self._gateway(context)
        gateway.register(
            name=TOOL,
            version=Q2,
            handler=RuntimeFactory._retrieval_handler(self.project_input, self.backend),
            max_retries=0,
        )
        # Exact archived registration shape: call_id-only, no canonical Budget.
        return gateway.invoke(
            context,
            call_id=self.origin(context)["call_id"],
            tool_name=TOOL,
            arguments=self.arguments,
        )

    def semantic_files(self) -> dict[str, bytes]:
        # Lock sidecars/directories are recorded separately; not semantic rows.
        paths = [self.tool_log, self.root / "budget.jsonl"]
        paths += list(self.raw_root.rglob("*.json"))
        paths += list(self.bundle_root.rglob("*.json"))
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in paths if path.is_file()
        }

    def winner_proof(self) -> _ProducerProof:
        actual = JsonlToolExecutionStore(self.tool_log).get(
            self.original_origin["operation_id"]
        )
        row = _json_bytes(actual.model_dump(mode="json"))
        with self.lock:
            matches = [proof for proof in self.durable_proofs if proof.row_json == row]
        assert len(matches) == 1, "must bind the actual persisted producer; do not pick an alias"
        return matches[0]


def _classify_passive(
    case: _Case,
    requested_origin: dict[str, Any],
    proof: _ProducerProof | None,
) -> dict[str, Any]:
    """TEST-OWNED classification: actual cold reads, exact producer proof only."""
    try:
        native = JsonlToolExecutionStore(case.tool_log).get(requested_origin["operation_id"])
        _require(proof is not None, "producer attestation absent")
        _require(_json_bytes(requested_origin) == proof.origin_json, "original caller/Round drift")
        row = native.model_dump(mode="json")
        _require(_json_bytes(row) == proof.row_json, "full normalized producer tuple drift")
        _require(case.tool_log.read_bytes() == proof.native_prefix, "committed prefix drift")
        context = requested_origin["context"]
        _require(row["call_id"] == requested_origin["call_id"], "original call alias drift")
        _require(row["tool_operation_id"] == requested_origin["operation_id"], "operation drift")
        _require(row["tool_semantic_fingerprint"] == requested_origin["fingerprint"], "FP drift")
        provenance = row["provenance"]
        _require(provenance["tool_name"] == TOOL, "tool name drift")
        _require(provenance["tool_version"] == requested_origin["registration"], "version drift")
        _require(provenance["input_fingerprint"] == requested_origin["fingerprint"], "input FP drift")
        _require(provenance["input_snapshot_version"] == context["input_snapshot_version"], "snapshot drift")
        source = json.loads(proof.source_json)
        backend_request = source["actual_backend_request"]
        _require(backend_request["task_id"] == context["task_id"], "backend producer Task drift")
        _require(backend_request["run_id"] == context["run_id"], "backend producer Run drift")
        if case.registration == Q1:
            round_ref = requested_origin["research_round_ref"]
            _require(round_ref["run_id"] == context["run_id"], "Round Run mismatch")
            _require(round_ref["attempt_id"] == context["attempt_id"], "Round Attempt mismatch")
            _require(round_ref["section_id"] == context["section_id"], "Round section mismatch")
            _require(requested_origin["call_slot"] == 1, "original call slot mismatch")
            _require(source.get("kind") == "REAL_Q1_BUNDLE_SNAPSHOT", "Bundle proof absent")
            _require(backend_request["query"] == requested_origin["arguments"]["query"], "actual RAG query drift")
            _require(backend_request["retrieval_scope"] == requested_origin["arguments"]["scope"], "actual RAG scope drift")
            _require(provenance["raw_artifact_ref"] is None, "actual Q1 produced no Tool raw")
            data = row["data"]
            _require(data["evidence_bundle_handle"] == source["handle"], "Bundle handle drift")
            _require(data["evidence_contract_sha256"] == source["contract_sha256"], "Bundle SHA drift")
            _require(data["accepted_evidence_ids"] == source["accepted_ids"], "accepted IDs drift")
            snapshots = EvidenceBundleSnapshotStore(case.bundle_root)
            bundle = snapshots.resolve(source["handle"])
            ref = snapshots.resolve_ref(source["handle"])
            _require(bundle.task_id == context["task_id"], "Bundle Task mismatch")
            _require(bundle.run_id == context["run_id"], "Bundle Run mismatch")
            _require(bundle.task_id == source["bundle_task_id"], "source Task drift")
            _require(bundle.run_id == source["bundle_run_id"], "source Run drift")
            _require(ref.evidence_bundle_sha256 == source["snapshot_hash"], "native snapshot ref SHA drift")
            _require(evidence_bundle_sha256(bundle) == source["contract_sha256"], "contract SHA drift")
            _require(list(bundle.selected_evidence_ids) == source["accepted_ids"], "source IDs drift")
            _require(_sha(_json_bytes(bundle.model_dump(mode="json"))) == source["bundle_json_sha256"], "source full tuple drift")
            _require(source["actual_backend_response_sha256"] == source["bundle_json_sha256"], "actual backend/Bundle producer drift")
            _require(_sha(snapshots._path_for_handle(ref.handle).read_bytes()) == source["envelope_sha256"], "snapshot envelope drift")
        else:
            _require(source.get("kind") == "REAL_Q2_TOOL_RAW_ARTIFACT", "raw proof absent")
            _require(backend_request["section_id"] == context["section_id"], "actual legacy section drift")
            _require(backend_request["query_topics"] == requested_origin["arguments"]["query_topics"], "actual legacy topics drift")
            _require(backend_request["research_round"] == requested_origin["arguments"]["research_round"], "actual legacy round drift")
            raw = FileArtifactStore(case.raw_root)
            original_ref = source["reference"]
            _require(provenance["raw_artifact_ref"] == original_ref["artifact_ref"], "raw ref drift")
            reference, content = raw.get_bytes_with_reference(
                original_ref["artifact_ref"],
                task_id=context["task_id"],
                section_id=context["section_id"],
                attempt_id=context["attempt_id"],
                input_snapshot_version=context["input_snapshot_version"],
            )
            # Actual gateway did not pass run_id when publishing the Tool raw.
            # Original Run is test producer context, not a fabricated raw field.
            _require(reference.run_id is None, "actual Q2 raw Run contract drift")
            _require(reference.model_dump(mode="json") == original_ref, "full raw reference drift")
            _require(_sha(content) == source["content_sha256"], "raw content SHA drift")
            _require(source["actual_backend_response_sha256"] == source["content_sha256"], "actual backend/raw producer drift")
            _require(json.loads(content) == source["content"], "raw full payload drift")
            _require(row["data"] == source["content"], "normalized/raw tuple drift")
            _require(_sha(raw._path_for_ref(reference.artifact_ref).read_bytes()) == source["envelope_sha256"], "raw envelope drift")
        return {
            "authority": "TEST_OWNED_NOT_PRODUCTION_TOOL_RECOVERY",
            "disposition": RECOVERED,
            "original_status": row["status"],
            "result": row,
            "source_sha256": _sha(proof.source_json),
        }
    except (KeyError, ValueError, FileNotFoundError) as exc:
        # AssertionError/forbidden-effect failures deliberately are NOT caught.
        # No native positive no-effect proof exists merely because get is absent.
        return {
            "authority": "TEST_OWNED_NOT_PRODUCTION_TOOL_RECOVERY",
            "disposition": INDETERMINATE,
            "native_exception": type(exc).__name__,
            "reason": str(exc),
        }


def _forbid_passive_effects(monkeypatch: Any, case: _Case) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("passive read attempted invoke, Budget or semantic mutation")

    for owner, names in (
        (ToolGateway, ("invoke",)),
        (MainlineResearchToolGateway, ("_restore_execution_from_canonical_result",)),
        (SchemeEvidenceService, ("retrieve", "canonical_bundle_binding")),
        (JsonlToolExecutionStore, ("put",)),
        (EvidenceBundleSnapshotStore, ("persist",)),
        (FileArtifactStore, ("put_json", "put_bytes", "delete_exact")),
        (BudgetManager, ("reserve", "consume", "release")),
        (CanonicalInvocationBudgetAdapter, ("reserve_tool_logical_call", "settle_tool_logical_call")),
        (_CountedBackend, ("retrieve",)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    # Instance wrappers exist for counts; forbid those too, rather than relying
    # on class monkeypatches to replace a pre-existing instance attribute.
    monkeypatch.setattr(case.raw_store, "put_json", forbidden)
    monkeypatch.setattr(case.snapshot_store, "persist", forbidden)
    if case.manager is not None:
        for name in ("reserve", "consume", "release"):
            monkeypatch.setattr(case.manager, name, forbidden)
    for gateway in case.gateways:
        monkeypatch.setattr(gateway, "invoke", forbidden)
        monkeypatch.setattr(gateway.execution_store, "put", forbidden)


def _canceled_passive(
    monkeypatch: Any, case: _Case, origin: dict[str, Any], proof: _ProducerProof | None
) -> dict[str, Any]:
    callbacks: list[str] = []
    control = WorkflowExecutionControl.with_timeout(
        execution_id="d2-tool-passive-canceled", timeout_seconds=60
    )
    control.defer_external_commit(lambda: callbacks.append("committed"), description="revoked-local")
    assert control.cancel("explicit test cancellation")
    before_counts = case.counts()
    with monkeypatch.context() as patch:
        _forbid_passive_effects(patch, case)
        with activate_execution_control(control):
            result = _classify_passive(case, origin, proof)
            with pytest.raises(WorkflowExecutionCancelled):
                control.checkpoint()
            with pytest.raises(WorkflowExecutionCancelled):
                control.defer_external_commit(lambda: callbacks.append("late"), description="late")
            with pytest.raises(WorkflowExecutionCancelled):
                control.finalize_external_commits(attempt_succeeded=True)
    assert callbacks == []
    assert control.is_cancelled
    assert case.counts() == before_counts
    result["cancellation"] = {
        "explicit_cancel_retained": True,
        "checkpoint_revoked": True,
        "defer_revoked": True,
        "finalize_revoked": True,
        "callback_count": 0,
    }
    return result


def _project_origins() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    origins: dict[str, str] = {}
    prefixes = ("apps", "core", "contracts", "rag", "schemas", "agent", "backend")
    for name, module in tuple(sys.modules.items()):
        if name.split(".", 1)[0] not in prefixes or module is None:
            continue
        filename = getattr(module, "__file__", None)
        if filename:
            actual = Path(filename).resolve()
            assert actual.is_relative_to(root), (name, str(actual), str(root))
            origins[name] = str(actual)
        for location in getattr(module, "__path__", ()):
            actual = Path(location).resolve()
            assert actual.is_relative_to(root), (name, str(actual), str(root))
    return origins


def _receipt(
    tmp_path: Path, case: _Case, *, observation: dict[str, Any],
    before: dict[str, bytes], after: dict[str, bytes], phase: str = "final",
) -> None:
    # The per-test artifact records physical facts, not a formal D2 row verdict.
    payload = {
        "classification": "LOCAL_AUTHORED_BACKEND_ACTUAL_NATIVE_OWNERS_TEST_EVIDENCE",
        "registration": case.registration,
        "cut": case.cut,
        "original_origin": case.original_origin,
        "original_origin_sha256": _sha(case.original_origin_json),
        "counts": case.counts(),
        "backend_requests": case.backend_requests,
        "producer_candidates": case.producer_candidates,
        "producer_proofs": [proof.receipt() for proof in case.durable_proofs],
        "observation": observation,
        "before": {name: {"sha256": _sha(data), "hex": data.hex()} for name, data in before.items()},
        "after": {name: {"sha256": _sha(data), "hex": data.hex()} for name, data in after.items()},
        "filesystem_note": "Native reads may create lock sidecars or truncate only an uncommitted suffix; neither is a semantic append.",
        "project_module_origins": _project_origins(),
        "model_calls": 0,
        "provider_calls": 0,
        "production_tool_adapter_added": False,
        "new_dispatch_fence": False,
        "formal_D2_row_verdict": "PARENT_FROZEN_RECEIPT_REQUIRED",
    }
    path = tmp_path / f"tool-native-observation-{phase}.json"
    assert not path.exists(), "test observation artifacts are create-once"
    path.write_bytes(_json_bytes(payload) + b"\n")


@pytest.mark.parametrize("registration", [Q1, Q2], ids=["Q1", "Q2"])
@pytest.mark.parametrize(
    "cut", ["backend_before", "native_put_before", "native_put_durable_ack_lost"]
)
def test_actual_tool_loss_cuts_cold_canceled_passive(tmp_path, monkeypatch, registration, cut):
    case = _Case(tmp_path / "native", registration, cut)
    with pytest.raises(_LocalCut):
        case.invoke()
    before = case.semantic_files()
    proof = case.durable_proofs[0] if case.durable_proofs else None
    result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
    after = case.semantic_files()
    _receipt(tmp_path, case, observation=result, before=before, after=after)
    assert after == before
    expected = RECOVERED if cut == "native_put_durable_ack_lost" else INDETERMINATE
    assert result["disposition"] == expected
    assert result["disposition"] != "PROVEN_NO_EFFECT"
    counts = case.counts()
    entered = 0 if cut == "backend_before" else 1
    assert counts.get("backend_entries", 0) == entered
    assert counts.get("backend_returns", 0) == entered
    assert counts.get("tool_put_attempts", 0) == entered
    assert counts.get("tool_put_inserted", 0) == int(cut == "native_put_durable_ack_lost")
    if registration == Q1:
        assert counts.get("snapshot_persist_returns", 0) == 2 * entered
        assert counts.get("raw_put_attempts", 0) == 0
        # Physical handler not entered is NOT a native semantic no-effect proof.
        assert case.manager.canonical_state().used.logical_tool_calls == 1
    else:
        assert counts.get("raw_put_returns", 0) == entered
        assert case.manager is None
        if proof:
            assert json.loads(proof.source_json)["reference"]["run_id"] is None
    if proof:
        assert result["result"] == json.loads(proof.row_json)
        assert result["original_status"] == result["result"]["status"]


@pytest.mark.parametrize("registration", [Q1, Q2], ids=["Q1", "Q2"])
@pytest.mark.parametrize("field", ["task_id", "run_id", "attempt_id", "snapshot", "operation", "fingerprint", "version", "arguments", "call_id", "Round_or_legacy_binding"])
def test_passive_original_producer_binding_drift_is_indeterminate(tmp_path, monkeypatch, registration, field):
    case = _Case(tmp_path / "native", registration)
    case.invoke()
    proof = case.winner_proof()
    requested = copy.deepcopy(case.original_origin)
    if field in {"task_id", "run_id", "attempt_id"}:
        requested["context"][field] += "_other"
    elif field == "snapshot":
        requested["context"]["input_snapshot_version"] += "_other"
    elif field == "operation":
        requested["operation_id"] += "_other"
    elif field == "fingerprint":
        requested["fingerprint"] = "a" * 64
    elif field == "version":
        requested["registration"] += "_other"
    elif field == "arguments":
        requested["arguments"]["authored_wrong_argument"] = True
    elif field == "call_id":
        requested["call_id"] += "_other"
    elif registration == Q1:
        requested["research_round_ref"]["attempt_id"] += "_other"
    else:
        requested["binding_scope"] = "UNPROVEN_TARGET_BINDING"
    before = case.semantic_files()
    result = _canceled_passive(monkeypatch, case, requested, proof)
    after = case.semantic_files()
    _receipt(tmp_path, case, observation={"attack": field, **result}, before=before, after=after)
    assert result["disposition"] == INDETERMINATE
    assert after == before


@pytest.mark.parametrize("registration", [Q1, Q2], ids=["Q1", "Q2"])
@pytest.mark.parametrize("field", ["call_id", "fingerprint", "version", "snapshot", "source_refs", "raw_or_bundle_ref", "data_ids", "status", "physical_refs", "budget_refs", "quality", "data_type", "error"])
def test_passive_full_native_tuple_drift_is_indeterminate(tmp_path, monkeypatch, registration, field):
    case = _Case(tmp_path / "native", registration)
    case.invoke()
    proof = case.winner_proof()
    row = json.loads(proof.row_json)
    if field == "call_id":
        row["call_id"] += "_other"
    elif field == "fingerprint":
        row["tool_semantic_fingerprint"] = "b" * 64
    elif field == "version":
        row["provenance"]["tool_version"] += "_other"
    elif field == "snapshot":
        row["provenance"]["input_snapshot_version"] += "_other"
    elif field == "source_refs":
        row["provenance"]["source_refs"] = ["source://authored-wrong-proof"]
    elif field == "raw_or_bundle_ref":
        if registration == Q1:
            row["data"]["evidence_bundle_handle"] += "_other"
        else:
            row["provenance"]["raw_artifact_ref"] += "_other"
    elif field == "data_ids":
        row["data"]["accepted_evidence_ids" if registration == Q1 else "evidence_ids"] = ["E_WRONG"]
    elif field == "status":
        row["status"] = ToolResultStatus.PARTIAL.value
    elif field == "physical_refs":
        row["physical_attempt_refs"] = ["tool_attempt:authored-wrong-proof"]
    elif field == "budget_refs":
        # Q2 has no Budget.  Adding a token to data still verifies whole payload;
        # never invent a canonical reservation schema for the archived caller.
        if registration == Q1:
            row["budget_reservation_ref"] = None
            row["budget_settlement"] = None
        else:
            row["data"]["unproven_budget_authority"] = True
    elif field == "quality":
        row["quality"]["warnings"] = ["authored drift"]
    elif field == "error":
        row["status"] = ToolResultStatus.PERMANENT_ERROR.value
        row["error"]["error_code"] = "D2_AUTHORED_ERROR_TUPLE_DRIFT"
    else:
        row["data_type"] += "_other"
    # Explicit isolated fixture corruption; original producer bytes remain in proof.
    attacked = NormalizedToolResultSchema.model_validate(row)
    case.tool_log.write_bytes(_json_bytes(attacked.model_dump(mode="json")) + b"\n")
    before = case.semantic_files()
    result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
    after = case.semantic_files()
    _receipt(tmp_path, case, observation={"attack": field, **result}, before=before, after=after)
    assert result["disposition"] == INDETERMINATE
    assert after == before


@pytest.mark.parametrize("registration", [Q1, Q2], ids=["Q1", "Q2"])
@pytest.mark.parametrize("attack", ["missing", "complete_corruption"])
def test_passive_source_absence_or_corruption_is_indeterminate(tmp_path, monkeypatch, registration, attack):
    case = _Case(tmp_path / "native", registration)
    case.invoke()
    proof = case.winner_proof()
    source = json.loads(proof.source_json)
    path = (
        case.snapshot_store._path_for_handle(source["handle"])
        if registration == Q1
        else case.raw_store._path_for_ref(source["reference"]["artifact_ref"])
    )
    assert path.resolve().is_relative_to(case.root.resolve())
    original_bytes = path.read_bytes()
    if attack == "missing":
        path.unlink()  # Only this test-owned isolated source, never history/evidence.
    else:
        envelope = json.loads(original_bytes)
        if registration == Q1:
            envelope["bundle"]["run_id"] += "_other"
        else:
            envelope["content_base64"] = base64.b64encode(b"authored corrupt bytes").decode("ascii")
        path.write_bytes(_json_bytes(envelope) + b"\n")
    before = case.semantic_files()
    result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
    after = case.semantic_files()
    _receipt(tmp_path, case, observation={"attack": attack, "original_source_hex": original_bytes.hex(), **result}, before=before, after=after)
    assert result["disposition"] == INDETERMINATE
    assert after == before


@pytest.mark.parametrize("registration", [Q1, Q2], ids=["Q1", "Q2"])
@pytest.mark.parametrize("frame", ["torn", "complete_json_corrupt", "complete_utf8_corrupt", "duplicate_identity", "invalid_schema"])
def test_tool_native_torn_prefix_and_complete_frame_controls(tmp_path, monkeypatch, registration, frame):
    case = _Case(tmp_path / "native", registration)
    case.invoke()
    proof = case.winner_proof()
    if frame == "torn":
        case.tool_log.write_bytes(proof.native_prefix + b'{"uncommitted":"\xe4')
    elif frame == "complete_json_corrupt":
        case.tool_log.write_bytes(proof.native_prefix + b'{"broken":\n')
    elif frame == "complete_utf8_corrupt":
        case.tool_log.write_bytes(proof.native_prefix + b'{"bad":"\xff"}\n')
    elif frame == "duplicate_identity":
        case.tool_log.write_bytes(proof.native_prefix + proof.row_json + b"\n")
    else:
        bad = json.loads(proof.row_json)
        del bad["provenance"]
        case.tool_log.write_bytes(_json_bytes(bad) + b"\n")
    before = case.semantic_files()
    result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
    after = case.semantic_files()
    _receipt(tmp_path, case, observation={"frame": frame, "transport_tail_truncated": frame == "torn", **result}, before=before, after=after)
    if frame == "torn":
        assert result["disposition"] == RECOVERED
        assert after["normalized-tool.jsonl"] == proof.native_prefix
        assert before["normalized-tool.jsonl"].startswith(proof.native_prefix)
        assert {key: value for key, value in before.items() if key != "normalized-tool.jsonl"} == {
            key: value for key, value in after.items() if key != "normalized-tool.jsonl"
        }
    else:
        assert result["disposition"] == INDETERMINATE
        assert after == before


def test_q1_active_replay_counts_real_projection_persist_separately(tmp_path, monkeypatch):
    case = _Case(tmp_path / "native", Q1)
    first = case.invoke()
    proof = case.winner_proof()
    before = case.semantic_files()
    counts_before = case.counts()
    alias = case.context.model_copy(
        update={"invocation_id": "d2_active_replay_alias", "execution_id": "d2_active_replay_execution"},
        deep=True,
    )
    replay = case.invoke(alias)
    counts_after = case.counts()
    _receipt(tmp_path, case, observation={"active_counts_before": counts_before, "active_counts_after": counts_after, "first": first.model_dump(mode="json"), "replay": replay.model_dump(mode="json")}, before=before, after=case.semantic_files(), phase="active_preassertions")
    assert replay == first
    assert counts_after["backend_entries"] == counts_before["backend_entries"] == 1
    assert counts_after["backend_returns"] == counts_before["backend_returns"] == 1
    assert counts_after["tool_put_attempts"] == counts_before["tool_put_attempts"] == 1
    assert counts_after["snapshot_persist_attempts"] == counts_before["snapshot_persist_attempts"] + 1
    assert counts_after["snapshot_persist_returns"] == counts_before["snapshot_persist_returns"] + 1
    assert counts_after.get("budget_reserve_attempts") == counts_before.get("budget_reserve_attempts")
    assert counts_after.get("budget_consume_attempts") == counts_before.get("budget_consume_attempts")
    assert case.manager.canonical_state().used.logical_tool_calls == 1
    assert case.semantic_files() == before
    passive = _canceled_passive(monkeypatch, case, case.original_origin, proof)
    assert passive["disposition"] == RECOVERED
    _receipt(tmp_path, case, observation={"active_replay_has_real_persist_call": True, "active_counts_before": counts_before, "active_counts_after": counts_after, "passive": passive}, before=before, after=case.semantic_files())


@pytest.mark.parametrize("backend_status", ["partial", "empty"])
def test_q2_native_non_success_disposition_is_retained(tmp_path, monkeypatch, backend_status):
    case = _Case(tmp_path / "native", Q2, "native_put_durable_ack_lost")
    # Existing actual handler maps authored typed response status, not a synthetic
    # SUCCESS row.  No retired runtime or new production classifier is built.
    case.backend.response = RetrievalResponseSchema(
        status=backend_status,
        evidence_ids=["E_D2_PARTIAL"] if backend_status == "partial" else [],
        source_refs=["source://local/d2-partial"] if backend_status == "partial" else [],
    )
    with pytest.raises(_LocalCut):
        case.invoke()
    proof = case.winner_proof()
    before = case.semantic_files()
    result = _canceled_passive(monkeypatch, case, case.original_origin, proof)
    after = case.semantic_files()
    _receipt(tmp_path, case, observation={"backend_status": backend_status, **result}, before=before, after=after)
    assert result["disposition"] == RECOVERED
    assert result["original_status"] == backend_status
    assert result["result"]["status"] == backend_status
    assert result["result"]["status"] != ToolResultStatus.SUCCESS.value
    assert before == after


@pytest.mark.parametrize("registration", [Q1, Q2], ids=["Q1", "Q2"])
def test_actual_two_gateway_native_miss_concurrent_observer_counts(tmp_path, monkeypatch, registration):
    case = _Case(tmp_path / "native", registration)
    case.miss_barrier = threading.Barrier(2)
    outcomes: list[dict[str, Any]] = []
    outcome_lock = threading.Lock()

    def worker(index: int) -> None:
        context = case.context.model_copy(
            update={"invocation_id": f"d2_native_concurrent_{index}", "execution_id": f"d2_native_worker_{index}"},
            deep=True,
        )
        try:
            returned = case.invoke(context)
            outcome = {"worker": index, "return": returned.model_dump(mode="json")}
        except BaseException as exc:
            outcome = {"worker": index, "exception": type(exc).__name__, "message": str(exc)}
        with outcome_lock:
            outcomes.append(outcome)

    workers = [threading.Thread(target=worker, args=(index,), name=f"d2-local-tool-{index}") for index in (1, 2)]
    try:
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(timeout=15)
    finally:
        case.miss_barrier.abort()
        for thread in workers:
            if thread.is_alive():
                thread.join(timeout=5)
    worker_incomplete = any(thread.is_alive() for thread in workers)
    counts = case.counts()
    before = case.semantic_files()
    # Two observers deliberately passed actual native absence before dispatch.
    # Assert that LOCAL physical observation; never impose physical count1 as Q policy.
    observation = {"worker_incomplete": worker_incomplete, "worker_outcomes": outcomes, "counts": counts, "oracle": "CONTROLLED_TWO_NATIVE_MISSES; NO_GLOBAL_AT_MOST_ONCE_DISPATCH_CLAIM"}
    _receipt(tmp_path, case, observation=observation, before=before, after=case.semantic_files(), phase="concurrent_preassertions")
    if worker_incomplete or len(outcomes) != 2:
        _receipt(tmp_path, case, observation={"disposition": "INCOMPLETE", **observation}, before=before, after=case.semantic_files())
        pytest.fail("bounded harness observation incomplete; no canonical cancellation/retry")
    assert counts.get("native_initial_misses", 0) == 2
    assert counts.get("backend_entries", 0) == 2
    assert counts.get("backend_returns", 0) == 2
    assert counts.get("tool_put_attempts", 0) == 2
    rows = JsonlToolExecutionStore(case.tool_log).list()
    assert len(rows) == 1
    proof = case.winner_proof()
    # A native ValueError collision after actual handler calls is an observation,
    # not typed STALE, a no-effect proof, or permission to retry the losing call.
    returns = [item for item in outcomes if "return" in item]
    errors = [item for item in outcomes if "exception" in item]
    assert len(returns) == 1
    assert len(errors) == 1 and errors[0]["exception"] == "ValueError"
    assert returns[0]["return"] == rows[0].model_dump(mode="json")
    assert len(rows[0].physical_attempt_refs) == 1
    if registration == Q1:
        assert case.manager.canonical_state().used.logical_tool_calls == 1
        assert counts.get("snapshot_persist_returns", 0) == 4
        assert counts.get("raw_put_attempts", 0) == 0
    else:
        assert case.manager is None
        assert counts.get("raw_put_returns", 0) == 2
    passive = _canceled_passive(monkeypatch, case, json.loads(proof.origin_json), proof)
    after = case.semantic_files()
    _receipt(tmp_path, case, observation={"passive": passive, **observation}, before=before, after=after)
    assert passive["disposition"] == RECOVERED
    assert after == before
