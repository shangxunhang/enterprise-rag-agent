"""Small evaluation-domain contracts, separate from production schemas."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping


class MeasurementState(str, Enum):
    MEASURED = "MEASURED"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "N/A"
    UNMEASURED = "UNMEASURED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"


class EvidenceAvailability(str, Enum):
    PERSISTED = "PERSISTED"
    RECONSTRUCTABLE = "DETERMINISTICALLY_RECONSTRUCTABLE"
    NOT_RECORDED = "NOT_RECORDED"


def canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class EvaluationRunIdentity:
    evaluation_id: str
    subject_id: str
    production_run_id: str | None = None
    source_snapshot_id: str = "unknown"
    source_segment: str = "unknown"
    fingerprints: Mapping[str, Any] = field(default_factory=dict)
    mode: str = "OBSERVE"


@dataclass(frozen=True)
class EvaluationEvidenceRef:
    locator: str
    owner: str = "unknown"
    family: str = "unknown"
    schema_version: str = "unknown"
    pointer: str = ""
    raw_sha256: str | None = None
    semantic_sha256: str | None = None
    semantic_algorithm: str | None = None
    snapshot_id: str = "unknown"
    availability: str = "PERSISTED"
    access: str = "READABLE"
    integrity: str = "UNKNOWN"
    completeness: str = "UNKNOWN"
    as_of: str | None = None


@dataclass(frozen=True)
class StageObservation:
    unit_id: str
    subject_id: str
    operation_id: str | None = None
    source_segment: str = "unknown"
    implementation_version: str = "unknown"
    scope: str = "unknown"
    query: str | None = None
    round: int | None = None
    attempt: str | None = None
    enabled: bool | None = None
    applicable: bool | None = None
    triggered: bool | None = None
    executed: bool | None = None
    expected_execution: bool | None = None
    expectation_basis: str | None = None
    observed_execution_status: str = "UNKNOWN"
    observed_quality_status: str = "UNKNOWN"
    observed_control_action: str | None = None
    measurement_state: str = "UNMEASURED"
    evidence_refs: tuple[EvaluationEvidenceRef, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    identity_quality: str = "EVALUATION_LOCAL"

    @property
    def key(self) -> str:
        return canonical_hash([self.unit_id, self.subject_id, self.source_segment,
            self.operation_id, self.implementation_version, self.scope, self.query,
            self.round, self.attempt])


@dataclass(frozen=True)
class EdgeObservation:
    edge_id: str
    subject_id: str
    source_key: str | None = None
    target_key: str | None = None
    source_refs: tuple[EvaluationEvidenceRef, ...] = ()
    target_refs: tuple[EvaluationEvidenceRef, ...] = ()
    allowed_transformation: str = "IDENTITY"
    measurement_state: str = "UNMEASURED"
    assertion_result: bool | None = None
    availability: str = "NOT_RECORDED"
    completeness: str = "UNKNOWN"
    reason: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    unit_id: str = "unknown"
    scope: str = "unknown"
    value: Any = None
    numerator: int | float | None = None
    denominator: int | float | None = None
    measurement_state: str = "UNMEASURED"
    assertion_result: bool | None = None
    reason: str = ""
    version: str = "v1"
    calculator_ref: str = "pure"
    reference_ref: str | None = None
    input_completeness: str = "UNKNOWN"
    sample_counts: Mapping[str, int] = field(default_factory=dict)
    representation: str = "ACTUAL_OBSERVATION"


@dataclass(frozen=True)
class Finding:
    code: str
    subject_id: str = "unknown"
    message: str = ""
    unit_id: str | None = None
    edge_id: str | None = None
    evidence_refs: tuple[EvaluationEvidenceRef, ...] = ()
    evidence_level: str = "SYMPTOM"
    candidate_causes: tuple[str, ...] = ()
    counterevidence: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    issue_ref: str | None = None
    badcase_ref: str | None = None
    origin: str = "evaluation"


FailureAttribution = Finding


@dataclass
class ObservationBatch:
    domain_id: str = "unknown"
    stages: list[StageObservation] = field(default_factory=list)
    edges: list[EdgeObservation] = field(default_factory=list)
    evidence: list[EvaluationEvidenceRef] = field(default_factory=list)
    metrics: list[MetricResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvaluationReport:
    identity: EvaluationRunIdentity
    execution_coverage: Mapping[str, Any]
    stage_quality: tuple[StageObservation, ...] = ()
    edge_fidelity: tuple[EdgeObservation, ...] = ()
    metrics: tuple[MetricResult, ...] = ()
    release_close: Mapping[str, Any] = field(default_factory=dict)
    performance_cost: Mapping[str, Any] = field(default_factory=dict)
    comparison: Mapping[str, Any] = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()
    badcases: tuple[Mapping[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    diagnostic_readiness: str = "NOT_ESTABLISHED"
    reference_review: Mapping[str, str] = field(default_factory=dict)
    schema_version: str = "full_chain_evaluation_report_v1"

    def to_dict(self) -> dict[str, Any]:
        data = {item.name: asdict(self.identity) if item.name == 'identity' else getattr(self, item.name)
                for item in fields(self)}
        for name in ('stage_quality', 'edge_fidelity'):
            data[name] = [{item.name: _report_payload(getattr(row, item.name),
                          getattr(row, 'evidence_refs', ()) or getattr(row, 'source_refs', ()) or getattr(row, 'target_refs', ())) if item.name == 'payload'
                          else _report_value(getattr(row, item.name)) for item in fields(row)}
                          for row in getattr(self, name)]
        data['metrics'] = [asdict(row) for row in self.metrics]
        data['findings'] = [{item.name: _report_value(getattr(row, item.name)) for item in fields(row)}
                            for row in self.findings]
        return data


def _report_value(value):
    if isinstance(value, tuple):
        if len(value) > 64 and all(isinstance(item, EvaluationEvidenceRef) for item in value):
            return _report_evidence_refs(value)
        return [_report_value(item) for item in value]
    if hasattr(value, '__dataclass_fields__'):
        return asdict(value)
    return value


def _report_evidence_refs(refs):
    """Losslessly encode long ordered equal-metadata /integer pointer runs.

    Each range still binds the exact original file hash and selection. Short,
    irregular or differently bound references retain their original encoding.
    Evaluation observations and all original source bytes remain unchanged.
    """
    result=[]
    group=None
    def flush():
        nonlocal group
        if group is None:
            return
        if group['count']==1:
            result.append(dict(group['metadata'],pointer='/'+str(group['start'])))
        else:
            group['digest'].update(b']')
            result.append(dict(group['metadata'],pointer='',record_selection={
                'representation':'EXACT_ORDERED_ORIGINAL_POINTER_RANGE',
                'start':group['start'],'end_exclusive':group['end']+1,
                'count':group['count'],'original_refs_sha256':group['digest'].hexdigest()}))
        group=None
    for ref in refs:
        original=asdict(ref)
        pointer=original['pointer']
        suffix=pointer[1:] if isinstance(pointer,str) and pointer.startswith('/') else ''
        if not suffix.isascii() or not suffix.isdigit() or pointer!='/'+str(int(suffix)):
            flush(); result.append(original); continue
        index=int(suffix)
        metadata={key:value for key,value in original.items() if key!='pointer'}
        encoded=json.dumps(original,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
        if group is not None and group['metadata']==metadata and index==group['end']+1:
            group['digest'].update(b','+encoded)
            group['end']=index; group['count']+=1
        else:
            flush()
            group={'metadata':metadata,'start':index,'end':index,'count':1,'digest':sha256(b'['+encoded)}
    flush()
    return result


def _report_payload(payload, evidence_refs=()):
    """Large material stays at exact source refs; in-memory observations stay raw."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(encoded) <= 4096 or not evidence_refs:
        return dict(payload)
    summary = {'representation': 'EVALUATION_PAYLOAD_SUMMARY', 'full_payload_sha256': sha256(encoded).hexdigest(),
               'full_payload_bytes': len(encoded), 'full_payload_field_count': len(payload),
               'material_access': 'EXACT_STAGE_OR_EDGE_EVIDENCE_REFS', 'elided_material_is_not_empty': True}
    for key in ('event_type', 'evidence_key', 'category', 'operation_ref', 'span_id', 'sequence',
                'session_id', 'segment_id', 'provider_attempt_id', 'model_call_id', 'model_role',
                'record_origin', 'phase', 'complete', 'completeness'):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool, type(None))) and (not isinstance(value, str) or len(value) <= 256):
            if key in payload:
                summary[key] = value
    for key, value in payload.items():
        if isinstance(value, (list, tuple)):
            summary.setdefault('collection_counts', {})[key] = len(value)
    return summary


def execution_measurement(*, enabled=None, applicable=None, executed=None,
                          evidence_complete=False, technical_failure=False) -> str:
    if enabled is False or applicable is False:
        return "N/A"
    if technical_failure:
        return "TECHNICAL_FAILURE"
    if executed is False and evidence_complete:
        return "NOT_RUN"
    if executed is True and evidence_complete:
        return "MEASURED"
    return "UNMEASURED"
