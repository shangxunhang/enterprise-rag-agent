"""Shared deterministic fixture support for Gate V2 Pre-write Step12 tests.

The helpers in this module intentionally do not call Gate business calculators to
manufacture expected truth.  Invariant-bearing identities are caller supplied.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import threading
from typing import Any, Callable, Iterable

from apps.enterprise_document.schemas.pre_write_admission import (
    EvidenceCriterionSchema,
    EvidenceRequirementSchema,
    EvidenceRequirementsSnapshotSchema,
    WriterAdmissionDecisionRefSchema,
)
from apps.enterprise_document.schemas.pre_write_binding import (
    WriterGateBindingRefSchema,
    WriterSubjectRefSchema,
)
from apps.enterprise_document.schemas.pre_write_control import (
    PreWriteRouteDecisionRefSchema,
)
from apps.enterprise_document.schemas.pre_write_writer import (
    WriterLogicalExecutionKey,
)
from schemas.rag import EvidenceBundleSchema, RAGContextSchema


@dataclass(frozen=True)
class GateV2IdentityScenario:
    """One explicit identity set for stale/currentness/concurrency invariants."""

    task_id: str
    run_id: str
    document_id: str
    section_id: str
    write_instance_id: str

    @property
    def writer_subject_ref(self) -> WriterSubjectRefSchema:
        return writer_subject(
            run_id=self.run_id,
            document_id=self.document_id,
            section_id=self.section_id,
        )


def writer_subject(
    *,
    run_id: str,
    document_id: str,
    section_id: str,
) -> WriterSubjectRefSchema:
    """Build a WriterSubjectRef only from explicit caller-owned identity."""

    return WriterSubjectRefSchema(
        run_id=run_id,
        document_id=document_id,
        section_id=section_id,
    )


def evidence_requirement(
    *,
    requirement_id: str,
    criteria: Iterable[tuple[str, str]],
) -> EvidenceRequirementSchema:
    """Build one frozen Requirement from explicit criterion ids/text."""

    return EvidenceRequirementSchema(
        requirement_id=requirement_id,
        criteria=tuple(
            EvidenceCriterionSchema(criterion_id=criterion_id, criterion=text)
            for criterion_id, text in criteria
        ),
    )


def requirements_snapshot(
    *,
    source_profile_ref: str,
    source_task_ref: str,
    requirements_sha256: str,
    requirements: Iterable[EvidenceRequirementSchema],
) -> EvidenceRequirementsSnapshotSchema:
    """Build a snapshot without computing the expected digest for the caller."""

    return EvidenceRequirementsSnapshotSchema(
        requirements_sha256=requirements_sha256,
        source_profile_ref=source_profile_ref,
        source_task_ref=source_task_ref,
        requirements=tuple(requirements),
    )


def empty_evidence_bundle(*, query: str) -> EvidenceBundleSchema:
    """Build a canonical empty-Evidence handoff for tests where evidence is irrelevant."""

    return EvidenceBundleSchema(
        query=query,
        context=RAGContextSchema(context_text="", used_context_chars=0),
    )


def admission_decision_ref(*, decision_id: str) -> WriterAdmissionDecisionRefSchema:
    return WriterAdmissionDecisionRefSchema(decision_id=decision_id)


def binding_ref(*, binding_id: str) -> WriterGateBindingRefSchema:
    return WriterGateBindingRefSchema(binding_id=binding_id)


def route_decision_ref(*, route_decision_id: str) -> PreWriteRouteDecisionRefSchema:
    return PreWriteRouteDecisionRefSchema(route_decision_id=route_decision_id)


def writer_logical_execution_key(
    *,
    writer_subject_ref: WriterSubjectRefSchema,
    write_instance_id: str,
    route_decision_ref: PreWriteRouteDecisionRefSchema,
) -> WriterLogicalExecutionKey:
    return WriterLogicalExecutionKey(
        writer_subject_ref=writer_subject_ref,
        write_instance_id=write_instance_id,
        route_decision_ref=route_decision_ref,
    )


def stdlib_sha256_json(value: Any) -> str:
    """Independent test-side SHA-256 helper; never delegates to production hashing."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class CountingRetrievalProvider:
    """Deterministic retrieval double with an independent physical-call counter."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.call_count = 0
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        self.calls.append((args, dict(kwargs)))
        return self.response


class CountingWriterModel:
    """Deterministic Writer double with an independent physical-call counter."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.call_count = 0
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        self.calls.append((args, dict(kwargs)))
        return self.response


class FaultInjectingCheckpointBoundary:
    """Small checkpoint-boundary adapter used to inject one explicit failure."""

    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.call_count = 0

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.call_count += 1
        raise self.failure


class FaultInjectingTraceSink:
    """Small Trace sink double used to inject one explicit persistence failure."""

    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.call_count = 0

    def emit(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.call_count += 1
        raise self.failure


class TwoWorkerBarrier:
    """Barrier helper that makes two contenders reach a claim boundary together."""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._barrier = threading.Barrier(2)
        self.timeout_seconds = timeout_seconds

    def wait(self) -> int:
        return self._barrier.wait(timeout=self.timeout_seconds)


def sqlite_temp_paths(root: Path) -> dict[str, Path]:
    """Return the three frozen SQLite domain paths under an explicit temp root."""

    runs = root / "runs"
    return {
        "checkpoint": runs / "langgraph_checkpoints.sqlite3",
        "execution": runs / "execution_control.sqlite3",
        "trace": runs / "run_trace.sqlite3",
    }


def run_two_contenders(
    *,
    first: Callable[[], Any],
    second: Callable[[], Any],
) -> tuple[Any, Any]:
    """Run two already-explicit contender callables; business expectations stay outside."""

    results: list[Any] = [None, None]
    errors: list[BaseException | None] = [None, None]

    def invoke(index: int, call: Callable[[], Any]) -> None:
        try:
            results[index] = call()
        except BaseException as exc:  # test helper must preserve the exact injected failure
            errors[index] = exc

    threads = (
        threading.Thread(target=invoke, args=(0, first)),
        threading.Thread(target=invoke, args=(1, second)),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors[0] is not None:
        raise errors[0]
    if errors[1] is not None:
        raise errors[1]
    return results[0], results[1]


__all__ = [
    "CountingRetrievalProvider",
    "CountingWriterModel",
    "FaultInjectingCheckpointBoundary",
    "FaultInjectingTraceSink",
    "GateV2IdentityScenario",
    "TwoWorkerBarrier",
    "admission_decision_ref",
    "binding_ref",
    "empty_evidence_bundle",
    "evidence_requirement",
    "requirements_snapshot",
    "route_decision_ref",
    "run_two_contenders",
    "sqlite_temp_paths",
    "stdlib_sha256_json",
    "writer_logical_execution_key",
    "writer_subject",
]
