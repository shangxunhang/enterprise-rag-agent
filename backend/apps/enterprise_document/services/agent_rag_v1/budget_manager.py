"""Canonical Multi-Agent V3 Budget authority plus legacy compatibility mode.

Target callers use BudgetManager.canonical and durable JSONL state.
The historical constructor remains available only so the current pre-cutover
Workflow can keep using its projection/local-guard semantics until the shared
composition-root barrier.

The dated diagnostics below are historical, including their BLOCKED verdicts.
Current integrated acceptance and issue closure are recorded in
docs/implementation_routes_20260816/MULTI_AGENT/350_351_STEP7_INTEGRATED_CLOSURE_20260912.md.

INT-PERF-01 (2026-09-12, Step7 350+351 bounded correction; MAJOR, OPEN):
The corrected REQUIRED eight-section fixture exceeds the frozen 120s deadline.
Fresh pre-change evidence: E:/rag-agent-tmp/step7-performance-1789223933627/
profile-before.json and profile-before-tmp/canonical-metrics.json. Test elapsed
127.540s; canonical elapsed 126.439s; main Run 40/44 unique logical calls,
zero retries/active reservations; Budget reconstruction 701 calls / 44.751s.
This extends INT-VERIFY-05; the earlier 134.228s failure remains historical.
Impact: combined integration blocked by redundant physical reconstruction.
Decision: deterministic implementation correction; no contract/user decision.

Safety proof recorded before implementation:
* Repeated operation: parse every committed JSONL event, validate its schema,
  derived accounting and transition, and rebuild all task/run account states.
* Only an EXACT unchanged complete-line prefix is reusable. A revision number,
  file size, mtime, final balance or caller projection is never authority.
* Every access still reads the whole durable log through its existing torn-tail
  recovery, under the existing thread/process locks. Compare the entire prefix;
  rewrite, truncation or replacement invalidates reuse and forces full replay.
  External appends validate every new event and the prefix/suffix transition.
* Retain only private, reconstructible parsed state on the existing manager;
  publish the prefix only after successful validation, and deep-copy returned
  accounts so mutable projections/settlements cannot alter verified state.
  Restart starts empty. Append/fsync/readback, CAS, deadline checks, reservation
  identity/accounting and zero-write terminal replay remain unchanged.
* Existing-mechanism search is in existing-mechanisms.txt beside the evidence:
  supervisor_factory already reuses managers by task/run; CrashSafeJsonlEventLog
  owns recovery/reads/appends; document_lifecycle has operation-local exact
  bundle reuse, not a Budget checkpoint. No reusable Budget prefix mechanism
  was found. Memory's connection factory retains per-transaction close; no
  pool or Claim/Verifier/currentness change is justified for this correction.
* Protection: frozen budget_runtime (CAS, accounting, deadline, reopen,
  projection isolation, idempotent replay), budget_contention, model_tool_runtime
  and parallel_budget_acceptance; full frozen 350/351 and combined gates follow.
  External test_budget_prefix_probe.py additionally covers same-size/mtime
  historical corruption, suffix corruption, external append, restart, truncation,
  torn-tail recovery, mutable settlement isolation and zero-write replay.
  Baseline probe: 7/7 PASS. Expected saving: most of the 44.751s parsing/replay
  cost, while preserving full physical reads and all business obligations.
Verification status: pending focused semantics, stable normal, resource and
exact frozen regression. Limits remain 44 calls / 120s; 352 is excluded.

INT-PERF-N01 (NOTE, resolved): the external Python 3.10 metrics reader initially
rejected UTC 'Z'; normalize to '+00:00'. Fresh metrics above use the corrected
reader. Raw FakeLLM counts include eight separate criterion-assessment Runs;
main-Run logical accounting is taken from canonical reservations, not that total.

INT-PERF-N02 (NOTE, resolved harness defect): external task.py shadowed backend's
task package during the first focused collection (zero tests executed). Renamed
the external driver gate_driver.py; retain focused/execute-receipt.json as the
invalid harness receipt and use a fresh destination for valid semantic evidence.

Final bounded decision (same date): INT-PERF-01 remains BLOCKED; INT-VERIFY-04/05
are not closed. Frozen focused semantics 54/54 PASS; external integrity probes
8/8 PASS, including invalid appended suffix/recovery without cache advancement.
Budget reconstruction falls to 743 calls / 5.645627s (87.38% less inclusive time,
despite the now-complete gate). No logical/authority/44-call/120s change.
Three fresh complete normal runs, canonical start through terminal run_finished:
  Run 1 (profiled): 120.245031s, headroom -0.245031s, pytest 121.300770s.
  Run 2:           110.947050s, headroom  9.052950s, pytest 112.137787s.
  Run 3:           112.207603s, headroom  7.792397s, pytest 113.337189s.
All three test assertions PASS; each main Run consumes 43 unique logical calls,
no retries, no active reservations, provider ordinal 1, and partial_success with
the complete Document Gate/consistency/terminal result. Assertion PASS is not a
stable-performance PASS: one full terminal path exceeds 120s and observed spread
is larger than the two remaining margins. No fourth normal run is used.

Remaining measured work (inclusive, overlapping; do not sum): Document Gate
46.422s in Run 1 / 42.866s in Run 2 / 42.794s in Run 3; Run 1 PostgreSQL connects
320 / 13.638796s, Claim ledger reads 1910 / 11.981456s, Verifier material checks
88 / 9.708640s, terminal Memory reconcile 9.160556s.
Rejected candidate evidence: claim-reuse-feasibility.json measures 100 reads of
the completed Claim ledger: reconstruction 0.820265s versus full read plus an
isolated deep copy 1.556009s. This safe snapshot approach is slower; exposing its
mutable models would not preserve caller isolation. No Claim change was made.
No safe currentness-window proof was established for caching Verifier or Memory
source observations. Their independent checks were retained. Memory's existing
connection_factory seam can supply connections, but _transaction closes each;
the repository has no owned reusable pool/reset/disposal lifecycle to enable.
Choosing a new transport ownership/lifecycle design needs a separate bounded
decision under AGENTS.md, before implementation. Do not infer that business
architecture or the frozen obligations must change, or that all possible
performance improvements have been disproved by this bounded investigation.

Status: STEP7_350_351_COMBINED_PERFORMANCE_CORRECTION_BLOCKED.
Resource, fresh frozen 324/480/7 and the separate combined currentness/recovery
and UNSUPPORTED gate are NOT RUN: stable normal is their unmet prerequisite.
Historical frozen passes do not certify this production candidate. No commits;
retain this candidate and the byte-identical pre-existing combined test diff
(SHA256 3310546f68d0e488268d6294a1d481102f0e0a1ef789c533da84894b276bcca0).
Final report beside the evidence: final-report.json, SHA256
38f6374ef0d3ca1b447157931035f35677efc759556d49d0f714d39bd522b20e.
HEAD remains cfafbfe62f42630764642bd89418f63e410f8081; 352 NOT INTEGRATED.
STOP; do not enter Resource/full regression, 352, Repair, Research-More or Step8.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import RLock
from typing import Callable, Sequence

from apps.enterprise_document.schemas.agent_rag_v1 import (
    CANONICAL_BUDGET_DIMENSIONS,
    BudgetReservationRefSchema,
    BudgetReservationSchema,
    BudgetSettlementSchema,
    BudgetSnapshotSchema,
    BudgetVectorSchema,
    CanonicalBudgetStateSchema,
    CanonicalBudgetVectorSchema,
    LegacyBudgetReservationSchema,
    TaskBudgetLimitsSchema,
)
from apps.enterprise_document.schemas.agent_rag_v1.budgets import (
    CanonicalBudgetAdmissionDenialSchema,
    CanonicalBudgetTopupAuthorizationSchema,
    CanonicalBudgetTopupGrantSchema,
    CanonicalBudgetTopupRequestSchema,
    canonical_budget_topup_consumption_key,
    canonical_budget_topup_fingerprint,
)
from core.persistence.crash_safe_jsonl import (
    CrashSafeJsonlEventLog,
    canonical_json_sha256,
)
from core.persistence.interprocess_lock import InterProcessFileLock


class TaskBudgetExhausted(RuntimeError):
    def __init__(self, resource: str, limit: int) -> None:
        self.resource = resource
        self.limit = limit
        super().__init__(f"task budget exhausted: {resource} limit={limit}")


_RESOURCE_LIMIT_FIELDS = {
    "model_calls": "max_model_calls",
    "tool_calls": "max_tool_calls",
    "total_replans": "max_total_replans",
    "total_rewrites": "max_total_rewrites",
    "document_repair_tasks": "max_document_repair_tasks",
}


def _legacy_add(
    left: BudgetVectorSchema,
    right: BudgetVectorSchema,
) -> BudgetVectorSchema:
    return BudgetVectorSchema(
        **{
            field: getattr(left, field) + getattr(right, field)
            for field in _RESOURCE_LIMIT_FIELDS
        }
    )


def _legacy_subtract(
    left: BudgetVectorSchema,
    right: BudgetVectorSchema,
) -> BudgetVectorSchema:
    values = {
        field: getattr(left, field) - getattr(right, field)
        for field in _RESOURCE_LIMIT_FIELDS
    }
    if any(value < 0 for value in values.values()):
        raise ValueError("budget vector subtraction cannot become negative")
    return BudgetVectorSchema(**values)


class CanonicalBudgetIntegrityError(ValueError):
    """Persisted Budget history or semantic identity is contradictory."""


class CanonicalBudgetRevisionConflict(CanonicalBudgetIntegrityError):
    """A caller attempted to mutate a stale canonical Budget revision."""


class CanonicalBudgetIdentityConflict(CanonicalBudgetIntegrityError):
    """The same replay-stable reservation ID has a different fingerprint."""


class CanonicalBudgetCapacityExceeded(RuntimeError):
    """The requested operation cannot fit in remaining canonical capacity."""

    def __init__(
        self,
        resource: str,
        *,
        limit: int,
        remaining: int,
        required: int,
    ) -> None:
        self.resource = resource
        self.limit = int(limit)
        self.remaining = int(remaining)
        self.required = int(required)
        self.native_denial: CanonicalBudgetAdmissionDenialSchema | None = None
        super().__init__(
            "canonical budget capacity exceeded: "
            f"{resource} required={required} remaining={remaining} limit={limit}"
        )


class CanonicalBudgetDraining(RuntimeError):
    """New resource-consuming work is forbidden after explicit draining."""

    def __init__(self) -> None:
        super().__init__("canonical budget is draining; new expensive work denied")


class CanonicalBudgetTopupRejected(RuntimeError):
    """A budget decision grants neither capacity nor production terminal state."""

    def __init__(self, decision: str, reason: str) -> None:
        self.decision = decision
        self.reason = reason
        super().__init__(f"canonical budget top-up {decision}: {reason}")


class CanonicalBudgetDeadlineExceeded(RuntimeError):
    """The durable Run/workflow deadline already elapsed."""

    def __init__(self, deadline_at: datetime) -> None:
        self.deadline_at = deadline_at
        super().__init__(
            f"canonical budget deadline exhausted: {deadline_at.isoformat()}"
        )


_CANONICAL_EVENT_SCHEMA_VERSION = "canonical_budget_event_v1"
_CANONICAL_TOPUP_EVENT_SCHEMA_VERSION = "canonical_budget_event_v2"
_MAX_TOPUP_VALUE = (1 << 63) - 1
_CANONICAL_EVENT_TYPES = {
    "account_created", "reservation_created", "reservation_consumed",
    "reservation_released", "draining_started", "topup_granted",
}


def _canonical_zero() -> CanonicalBudgetVectorSchema:
    return CanonicalBudgetVectorSchema()


def _canonical_add(
    left: CanonicalBudgetVectorSchema,
    right: CanonicalBudgetVectorSchema,
) -> CanonicalBudgetVectorSchema:
    return CanonicalBudgetVectorSchema(
        **{
            name: int(getattr(left, name)) + int(getattr(right, name))
            for name in CANONICAL_BUDGET_DIMENSIONS
        }
    )


def _canonical_subtract(
    left: CanonicalBudgetVectorSchema,
    right: CanonicalBudgetVectorSchema,
) -> CanonicalBudgetVectorSchema:
    values = {
        name: int(getattr(left, name)) - int(getattr(right, name))
        for name in CANONICAL_BUDGET_DIMENSIONS
    }
    if any(value < 0 for value in values.values()):
        raise CanonicalBudgetIntegrityError(
            "canonical Budget vector subtraction cannot become negative"
        )
    return CanonicalBudgetVectorSchema(**values)


def _canonical_remaining(
    *,
    limits: CanonicalBudgetVectorSchema,
    used: CanonicalBudgetVectorSchema,
    active_reserved: CanonicalBudgetVectorSchema,
) -> CanonicalBudgetVectorSchema:
    return CanonicalBudgetVectorSchema(
        **{
            name: max(
                0,
                int(getattr(limits, name))
                - int(getattr(used, name))
                - int(getattr(active_reserved, name)),
            )
            for name in CANONICAL_BUDGET_DIMENSIONS
        }
    )


def _canonical_exhausted_dimensions(
    *,
    limits: CanonicalBudgetVectorSchema,
    used: CanonicalBudgetVectorSchema,
    active_reserved: CanonicalBudgetVectorSchema,
) -> list[str]:
    return [
        name
        for name in CANONICAL_BUDGET_DIMENSIONS
        if int(getattr(used, name)) + int(getattr(active_reserved, name))
        >= int(getattr(limits, name))
    ]


def _canonical_state_with_derived(
    *,
    task_id: str,
    run_id: str,
    policy_ref: str,
    policy_version: str,
    limits: CanonicalBudgetVectorSchema,
    used: CanonicalBudgetVectorSchema,
    active_reserved: CanonicalBudgetVectorSchema,
    draining: bool,
    revision: int,
    started_at: datetime,
    deadline_at: datetime,
    reservations: list[BudgetReservationSchema],
    grant_history: list[CanonicalBudgetTopupGrantSchema] | None = None,
) -> CanonicalBudgetStateSchema:
    history = grant_history or []
    effective = CanonicalBudgetVectorSchema(**{
        name: int(getattr(limits, name)) + sum(int(getattr(grant.request.delta, name)) for grant in history)
        for name in CANONICAL_BUDGET_DIMENSIONS
    })
    return CanonicalBudgetStateSchema(
        schema_version="canonical_budget_state_v2" if history else "canonical_budget_state_v1",
        task_id=task_id,
        run_id=run_id,
        policy_ref=policy_ref,
        policy_version=policy_version,
        limits=limits,
        used=used,
        active_reserved=active_reserved,
        remaining=_canonical_remaining(
            limits=effective,
            used=used,
            active_reserved=active_reserved,
        ),
        exhausted_dimensions=_canonical_exhausted_dimensions(
            limits=effective,
            used=used,
            active_reserved=active_reserved,
        ),
        draining=draining,
        revision=revision,
        started_at=started_at,
        deadline_at=deadline_at,
        reservations=reservations,
        grant_history=history,
    )


class BudgetManager:
    """Single canonical Budget owner in Target mode.

    BudgetManager(...) without canonical construction is the pre-existing
    compatibility mode. Target code MUST use BudgetManager.canonical so state
    is durable, replay-safe, scoped to one task/run account and revision-CAS
    guarded.
    """

    _LOCKS_GUARD = RLock()
    _PATH_LOCKS: dict[str, RLock] = {}

    def __init__(
        self,
        *,
        task_id: str,
        limits: TaskBudgetLimitsSchema,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.task_id = str(task_id).strip()
        if not self.task_id:
            raise ValueError("task_id is required")
        self.limits = TaskBudgetLimitsSchema.model_validate(limits)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.started_at = self._now()
        self._reserved = BudgetVectorSchema()
        self._used = BudgetVectorSchema()
        self._reservations: dict[str, LegacyBudgetReservationSchema] = {}
        self._draining = False
        self._lock = RLock()

        self._canonical_mode = False
        self.run_commit_guard = None
        self.run_id: str | None = None
        self.canonical_limits: CanonicalBudgetVectorSchema | None = None
        self.policy_ref: str | None = None
        self.policy_version: str | None = None
        self.max_elapsed_seconds: int | None = None
        self.storage_path: Path | None = None
        self._canonical_log: CrashSafeJsonlEventLog | None = None
        self._canonical_lock_path: Path | None = None

    @classmethod
    def canonical(
        cls,
        *,
        task_id: str,
        run_id: str,
        limits: CanonicalBudgetVectorSchema,
        max_elapsed_seconds: int,
        storage_path: str | Path,
        policy_ref: str = "budget-policy://multi-agent-v3/default",
        policy_version: str = "v1",
        clock: Callable[[], datetime] | None = None,
        run_commit_guard=None,
        require_run_commit_guard: bool = False,
        require_existing_account: bool = False,
    ) -> "BudgetManager":
        """Open or create one durable canonical task/run Budget account.

        Read-only consumers may require the exact account to exist already.
        Missing storage or account then fails without creating an account.
        Elapsed-time policy remains recorded for diagnostics; finite capacity
        and explicit draining govern admission.
        """

        normalized_task = str(task_id or "").strip()
        normalized_run = str(run_id or "").strip()
        normalized_policy_ref = str(policy_ref or "").strip()
        normalized_policy_version = str(policy_version or "").strip()
        if not normalized_task or not normalized_run:
            raise ValueError("canonical Budget requires task_id and run_id")
        if not normalized_policy_ref or not normalized_policy_version:
            raise ValueError("canonical Budget policy ref/version are required")
        if int(max_elapsed_seconds) <= 0:
            raise ValueError("max_elapsed_seconds must be positive")
        if require_existing_account and not Path(storage_path).is_file():
            raise CanonicalBudgetIntegrityError("exact canonical Budget account does not exist")

        obj = cls.__new__(cls)
        if require_run_commit_guard and not callable(run_commit_guard):
            raise ValueError("Target canonical Budget requires a Run commit guard")
        obj.run_commit_guard = run_commit_guard
        obj.require_run_commit_guard = require_run_commit_guard
        obj.task_id = normalized_task
        obj.run_id = normalized_run
        obj.canonical_limits = CanonicalBudgetVectorSchema.model_validate(limits)
        obj.policy_ref = normalized_policy_ref
        obj.policy_version = normalized_policy_version
        obj.max_elapsed_seconds = int(max_elapsed_seconds)
        obj.clock = clock or (lambda: datetime.now(timezone.utc))
        obj.storage_path = Path(storage_path)
        obj._canonical_log = CrashSafeJsonlEventLog(obj.storage_path)
        obj._canonical_verified_lines: list[str] = []
        obj._canonical_verified_accounts: dict[
            tuple[str, str], CanonicalBudgetStateSchema
        ] = {}
        obj._canonical_lock_path = obj.storage_path.with_suffix(
            obj.storage_path.suffix + ".lock"
        )
        lock_key = str(obj.storage_path.resolve())
        with cls._LOCKS_GUARD:
            obj._lock = cls._PATH_LOCKS.setdefault(lock_key, RLock())

        obj._canonical_mode = True
        obj.limits = None
        obj.started_at = None
        obj._reserved = BudgetVectorSchema()
        obj._used = BudgetVectorSchema()
        obj._reservations = {}
        obj._draining = False

        obj._ensure_canonical_account(require_existing_account=require_existing_account)
        return obj

    def _now(self) -> datetime:
        value = self.clock()
        if not isinstance(value, datetime):
            raise TypeError("Budget clock must return datetime")
        if value.tzinfo is None:
            raise ValueError("Budget clock must return timezone-aware datetime")
        return value

    @staticmethod
    def _serialize_canonical_event(
        *,
        event_type: str,
        state: CanonicalBudgetStateSchema,
    ) -> str:
        frame = {
                "schema_version": (_CANONICAL_TOPUP_EVENT_SCHEMA_VERSION
                                   if state.schema_version == "canonical_budget_state_v2"
                                   else _CANONICAL_EVENT_SCHEMA_VERSION),
                "event_type": event_type,
                "task_id": state.task_id,
                "run_id": state.run_id,
                "state": state.model_dump(mode="json", round_trip=True),
            }
        if state.schema_version == "canonical_budget_state_v2":
            frame["event_sha256"] = canonical_json_sha256(frame)
        return json.dumps(
            frame,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _reservation_map(
        state: CanonicalBudgetStateSchema,
    ) -> dict[str, BudgetReservationSchema]:
        return {
            item.reservation_ref.reservation_id: item
            for item in state.reservations
        }

    @classmethod
    def _validate_canonical_transition(
        cls,
        previous: CanonicalBudgetStateSchema,
        current: CanonicalBudgetStateSchema,
        event_type: str | None = None,
    ) -> None:
        if current.revision != previous.revision + 1:
            raise CanonicalBudgetIntegrityError(
                "canonical Budget revisions must be contiguous"
            )
        for field_name in (
            "task_id",
            "run_id",
            "policy_ref",
            "policy_version",
            "limits",
            "started_at",
            "deadline_at",
        ):
            if getattr(current, field_name) != getattr(previous, field_name):
                raise CanonicalBudgetIntegrityError(
                    f"canonical Budget immutable field changed: {field_name}"
                )
        for name in CANONICAL_BUDGET_DIMENSIONS:
            if int(getattr(current.used, name)) < int(getattr(previous.used, name)):
                raise CanonicalBudgetIntegrityError(
                    "canonical Budget used counters cannot decrease"
                )
        if previous.draining and not current.draining:
            raise CanonicalBudgetIntegrityError(
                "canonical Budget draining cannot be cleared"
            )

        old_history, new_history = previous.grant_history, current.grant_history
        if new_history[:len(old_history)] != old_history:
            raise CanonicalBudgetIntegrityError("canonical Budget cannot delete or change grants")
        added = new_history[len(old_history):]
        if event_type == "topup_granted":
            if len(added) != 1:
                raise CanonicalBudgetIntegrityError("top-up event must append exactly one grant")
            if (current.used != previous.used or current.active_reserved != previous.active_reserved
                    or current.reservations != previous.reservations or current.draining != previous.draining):
                raise CanonicalBudgetIntegrityError("top-up event cannot alter accounting or reservations")
            grant = added[0]
            if grant.created_against_budget_revision != previous.revision or grant.resulting_budget_revision != current.revision:
                raise CanonicalBudgetIntegrityError("top-up grant revision is not the native transition")
            try:
                cls._validate_topup_authority(previous, grant.request, grant.authorization, grant.accepted_at)
            except (ValueError, CanonicalBudgetTopupRejected, CanonicalBudgetDraining) as exc:
                raise CanonicalBudgetIntegrityError("committed top-up authorization invalid") from exc
        elif added:
            raise CanonicalBudgetIntegrityError("ordinary Budget event cannot add a grant")
        if old_history and current.schema_version != "canonical_budget_state_v2":
            raise CanonicalBudgetIntegrityError("grant-bearing Budget cannot downgrade state version")

        previous_rows = cls._reservation_map(previous)
        current_rows = cls._reservation_map(current)
        if not set(previous_rows).issubset(current_rows):
            raise CanonicalBudgetIntegrityError(
                "canonical Budget history cannot delete reservations"
            )
        for reservation_id, old in previous_rows.items():
            new = current_rows[reservation_id]
            for field_name in (
                "reservation_ref",
                "requested",
                "semantic_fingerprint",
                "created_against_budget_revision",
                "policy_ref",
                "policy_version",
                "created_at",
            ):
                if getattr(old, field_name) != getattr(new, field_name):
                    raise CanonicalBudgetIntegrityError(
                        "canonical Budget reservation immutable field changed: "
                        f"{reservation_id}/{field_name}"
                    )
            if old.status != "active" and new != old:
                raise CanonicalBudgetIntegrityError(
                    "terminal canonical Budget reservation is immutable"
                )
            if old.status == "active" and new.status not in {
                "active",
                "consumed",
                "released",
            }:
                raise CanonicalBudgetIntegrityError(
                    "invalid canonical Budget reservation transition"
                )

    @staticmethod
    def _verify_derived_state(state: CanonicalBudgetStateSchema) -> None:
        expected_remaining = _canonical_remaining(
            limits=state.effective_limits,
            used=state.used,
            active_reserved=state.active_reserved,
        )
        if state.remaining != expected_remaining:
            raise CanonicalBudgetIntegrityError(
                "canonical Budget remaining projection is inconsistent"
            )
        expected_exhausted = _canonical_exhausted_dimensions(
            limits=state.effective_limits,
            used=state.used,
            active_reserved=state.active_reserved,
        )
        if state.exhausted_dimensions != expected_exhausted:
            raise CanonicalBudgetIntegrityError(
                "canonical Budget exhausted projection is inconsistent"
            )

        active_sum = _canonical_zero()
        for reservation in state.reservations:
            if reservation.status == "active":
                if reservation.reservation_ref.parent_reservation_id is not None:
                    raise CanonicalBudgetIntegrityError(
                        "parent reservation runtime is not active in Step 3.2"
                    )
                active_sum = _canonical_add(active_sum, reservation.requested)
        if active_sum != state.active_reserved:
            raise CanonicalBudgetIntegrityError(
                "canonical Budget active_reserved does not match ACTIVE records"
            )
        if state.schema_version == "canonical_budget_state_v2":
            actual_sum = _canonical_zero()
            for reservation in state.reservations:
                if reservation.status == "consumed":
                    actual_sum = _canonical_add(actual_sum, reservation.actual)
            if actual_sum != state.used:
                raise CanonicalBudgetIntegrityError("canonical Budget used differs from native settlements")

    def _load_canonical_accounts_unlocked(
        self,
    ) -> dict[tuple[str, str], CanonicalBudgetStateSchema]:
        assert self._canonical_log is not None
        lines = self._canonical_log.read_complete_lines()
        prefix_length = len(self._canonical_verified_lines)
        if lines[:prefix_length] == self._canonical_verified_lines:
            accounts = dict(self._canonical_verified_accounts)
        else:
            # Content, not metadata, proves reuse. Revalidate changed history.
            self._canonical_verified_lines = []
            self._canonical_verified_accounts = {}
            prefix_length = 0
            accounts = {}
        for line_number, line in enumerate(
            lines[prefix_length:],
            start=prefix_length + 1,
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception as exc:
                raise CanonicalBudgetIntegrityError(
                    f"invalid canonical Budget JSON at "
                    f"{self.storage_path}:{line_number}"
                ) from exc
            if not isinstance(payload, dict):
                raise CanonicalBudgetIntegrityError(
                    "canonical Budget event must be a JSON object"
                )
            version = payload.get("schema_version")
            if version not in {_CANONICAL_EVENT_SCHEMA_VERSION, _CANONICAL_TOPUP_EVENT_SCHEMA_VERSION}:
                raise CanonicalBudgetIntegrityError(
                    "unknown canonical Budget event schema version"
                )
            raw_state = payload.get("state")
            if not isinstance(raw_state, dict):
                raise CanonicalBudgetIntegrityError(
                    "canonical Budget event state is required"
                )
            if version == _CANONICAL_TOPUP_EVENT_SCHEMA_VERSION:
                if set(payload) != {"schema_version", "event_type", "task_id", "run_id", "state", "event_sha256"}:
                    raise CanonicalBudgetIntegrityError("unknown v2 Budget event fields")
                digest = canonical_json_sha256({name: value for name, value in payload.items() if name != "event_sha256"})
                if payload["event_sha256"] != digest:
                    raise CanonicalBudgetIntegrityError("committed v2 Budget event hash mismatch")
                if payload.get("event_type") not in _CANONICAL_EVENT_TYPES or raw_state.get("schema_version") != "canonical_budget_state_v2":
                    raise CanonicalBudgetIntegrityError("invalid v2 Budget event/state kind")
            elif raw_state.get("schema_version", "canonical_budget_state_v1") != "canonical_budget_state_v1" or "grant_history" in raw_state:
                raise CanonicalBudgetIntegrityError("v1 Budget event cannot carry additive grants")
            try:
                state = CanonicalBudgetStateSchema.model_validate(raw_state)
            except Exception as exc:
                raise CanonicalBudgetIntegrityError(
                    f"invalid canonical Budget state at "
                    f"{self.storage_path}:{line_number}"
                ) from exc
            if payload.get("task_id") != state.task_id or payload.get(
                "run_id"
            ) != state.run_id:
                raise CanonicalBudgetIntegrityError(
                    "canonical Budget event identity does not match state"
                )
            self._verify_derived_state(state)
            key = (state.task_id, state.run_id)
            previous = accounts.get(key)
            if previous is None:
                if state.revision != 0 or state.grant_history:
                    raise CanonicalBudgetIntegrityError(
                        "first canonical Budget revision must be zero"
                    )
            else:
                self._validate_canonical_transition(previous, state, payload.get("event_type"))
            accounts[key] = state
        # Cache only a fully validated prefix. Never expose its mutable models.
        self._canonical_verified_lines = lines
        self._canonical_verified_accounts = accounts
        return {key: state.model_copy(deep=True) for key, state in accounts.items()}

    def _canonical_current_unlocked(self) -> CanonicalBudgetStateSchema:
        accounts = self._load_canonical_accounts_unlocked()
        key = (self.task_id, str(self.run_id))
        state = accounts.get(key)
        if state is None:
            raise KeyError(
                f"canonical Budget account not found: {self.task_id}/{self.run_id}"
            )
        return state

    def _append_canonical_state_unlocked(
        self,
        *,
        event_type: str,
        state: CanonicalBudgetStateSchema,
    ) -> None:
        assert self._canonical_log is not None
        self._canonical_log.append_serialized(
            self._serialize_canonical_event(
                event_type=event_type,
                state=state,
            )
        )
        verified = self._canonical_current_unlocked()
        if verified != state:
            raise OSError("canonical Budget durable verification failed")

    def _ensure_canonical_account(self, *, require_existing_account: bool = False) -> None:
        assert self._canonical_mode
        assert self._canonical_lock_path is not None
        assert self.canonical_limits is not None
        assert self.policy_ref is not None
        assert self.policy_version is not None
        assert self.max_elapsed_seconds is not None

        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            accounts = self._load_canonical_accounts_unlocked()
            key = (self.task_id, str(self.run_id))
            existing = accounts.get(key)
            if existing is not None:
                if existing.limits != self.canonical_limits:
                    raise CanonicalBudgetIntegrityError(
                        "canonical Budget reopen changed immutable limits"
                    )
                if existing.policy_ref != self.policy_ref or (
                    existing.policy_version != self.policy_version
                ):
                    raise CanonicalBudgetIntegrityError(
                        "canonical Budget reopen changed policy identity"
                    )
                configured_elapsed = int(self.max_elapsed_seconds)
                persisted_elapsed = int(
                    (existing.deadline_at - existing.started_at).total_seconds()
                )
                if persisted_elapsed != configured_elapsed:
                    raise CanonicalBudgetIntegrityError(
                        "canonical Budget reopen changed immutable deadline policy"
                    )
                return

            if require_existing_account:
                raise CanonicalBudgetIntegrityError("exact canonical Budget account does not exist")
            now = self._now()
            if self.run_commit_guard is not None:
                self.run_commit_guard(str(self.run_id))
            state = _canonical_state_with_derived(
                task_id=self.task_id,
                run_id=str(self.run_id),
                policy_ref=self.policy_ref,
                policy_version=self.policy_version,
                limits=self.canonical_limits,
                used=_canonical_zero(),
                active_reserved=_canonical_zero(),
                draining=False,
                revision=0,
                started_at=now,
                deadline_at=now + timedelta(seconds=self.max_elapsed_seconds),
                reservations=[],
            )
            self._append_canonical_state_unlocked(
                event_type="account_created",
                state=state,
            )

    def canonical_state(self) -> CanonicalBudgetStateSchema:
        """Load canonical truth only from the durable owner log."""

        if not self._canonical_mode:
            raise RuntimeError(
                "canonical_state is unavailable in legacy compatibility mode"
            )
        assert self._canonical_lock_path is not None
        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            return self._canonical_current_unlocked().model_copy(deep=True)

    def resolve_state_revision(self, revision: int, expected_sha256: str | None = None) -> CanonicalBudgetStateSchema:
        """Passively select a validated historical native admission preimage."""
        if not self._canonical_mode or type(revision) is not int or revision < 0:
            raise CanonicalBudgetIntegrityError("exact canonical Budget revision is required")
        assert self._canonical_lock_path is not None
        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            self._load_canonical_accounts_unlocked()
            for line in self._canonical_verified_lines:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if payload.get("task_id") != self.task_id or payload.get("run_id") != self.run_id:
                    continue
                state = CanonicalBudgetStateSchema.model_validate(payload["state"])
                if state.revision == revision:
                    actual = canonical_json_sha256(state.model_dump(mode="json"))
                    if expected_sha256 is not None and actual != expected_sha256:
                        raise CanonicalBudgetIntegrityError("native Budget historical state hash mismatch")
                    return state.model_copy(deep=True)
            raise CanonicalBudgetIntegrityError("exact native Budget revision absent")

    def resolve_topup_grant(self, grant_ref: str, expected_sha256: str | None = None) -> CanonicalBudgetTopupGrantSchema:
        """Read one exact accepted native grant without re-authorizing a debit."""
        if not self._canonical_mode or not isinstance(grant_ref, str) or not grant_ref.strip():
            raise CanonicalBudgetIntegrityError("exact canonical Budget grant ref is required")
        assert self._canonical_lock_path is not None
        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            state = self._canonical_current_unlocked()
            matches = [grant for grant in state.grant_history if grant.grant_ref == grant_ref]
            if len(matches) != 1:
                raise CanonicalBudgetIntegrityError("exact native Budget grant missing or ambiguous")
            grant = matches[0]
            if expected_sha256 is not None and grant.grant_sha256 != expected_sha256:
                raise CanonicalBudgetIntegrityError("native Budget grant hash mismatch")
            return grant.model_copy(deep=True)

    @staticmethod
    def _validate_topup_authority(state, request, authorization, accepted_at):
        if state.draining:
            raise CanonicalBudgetDraining()
        if authorization.decision != "APPROVED":
            raise CanonicalBudgetTopupRejected("DENIED", "trusted command denied this top-up")
        if authorization.revoked or (authorization.expires_at is not None and accepted_at >= authorization.expires_at):
            raise CanonicalBudgetTopupRejected("REJECTED", "authorization revoked or expired")
        for name in ("authorization_ref", "authorization_sha256", "task_id", "run_id",
                     "canonical_request_ref", "canonical_request_sha256", "semantic_operation_ref",
                     "semantic_input_sha256", "artifact_version", "baseline_policy_sha256"):
            if getattr(request, name) != getattr(authorization, name):
                raise CanonicalBudgetTopupRejected("REJECTED", f"authorization binding differs: {name}")
        if request.task_id != state.task_id or request.run_id != state.run_id:
            raise CanonicalBudgetTopupRejected("REJECTED", "authorization belongs to another Budget account")
        policy_hash = canonical_json_sha256({"policy_ref": state.policy_ref, "policy_version": state.policy_version,
                                           "limits": state.limits.model_dump(mode="json")})
        if request.baseline_policy_sha256 != policy_hash:
            raise CanonicalBudgetTopupRejected("REJECTED", "immutable baseline policy hash differs")
        if authorization.authorization_kind == "SINGLE_USE":
            if (authorization.single_use_wait_event_ref != request.wait_event_ref
                    or authorization.single_use_native_denial_sha256 != request.native_denial_sha256
                    or authorization.single_use_child_preimage_sha256 != request.child_preimage_sha256):
                raise CanonicalBudgetTopupRejected("REJECTED", "single-use authorization changed its native refusal")
        same_root = [grant for grant in state.grant_history
                     if grant.authorization.authorization_ref == authorization.authorization_ref
                     and grant.authorization.authorization_sha256 == authorization.authorization_sha256]
        if any(grant.authorization != authorization for grant in same_root):
            raise CanonicalBudgetIdentityConflict("immutable authorization root changed")
        root_debited = _canonical_zero()
        for grant in same_root:
            root_debited = _canonical_add(root_debited, grant.request.delta)
        after_effective = _canonical_add(state.effective_limits, request.delta)
        after_root_debit = _canonical_add(root_debited, request.delta)
        after_remaining = _canonical_remaining(limits=after_effective, used=state.used, active_reserved=state.active_reserved)
        for name in CANONICAL_BUDGET_DIMENSIONS:
            if (getattr(after_effective, name) > _MAX_TOPUP_VALUE
                    or getattr(after_effective, name) > getattr(authorization.cumulative_ceiling, name)
                    or getattr(after_root_debit, name) > getattr(authorization.authorized_delta, name)):
                raise CanonicalBudgetTopupRejected("LIMIT_REACHED", f"finite authorization ceiling exceeded: {name}")
            if getattr(after_remaining, name) < getattr(request.required, name):
                raise CanonicalBudgetTopupRejected("LIMIT_REACHED", f"authorized finite delta still insufficient: {name}")

    def apply_topup(self, request, *, authorization_resolver, paused_authorization_fence) -> CanonicalBudgetTopupGrantSchema:
        """Consume a trusted finite authorization once under Budget→Lifecycle locks."""
        if not self._canonical_mode:
            raise RuntimeError("top-up is canonical Budget only")
        raw = request.model_dump(mode="json") if isinstance(request, CanonicalBudgetTopupRequestSchema) else request
        bound = CanonicalBudgetTopupRequestSchema.model_validate(raw)
        if not callable(authorization_resolver) or not callable(paused_authorization_fence):
            raise CanonicalBudgetTopupRejected("REJECTED", "trusted authorization resolver and PAUSED owner fence required")
        assert self._canonical_lock_path is not None
        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            state = self._canonical_current_unlocked()
            for grant in state.grant_history:
                if grant.request.approval_id == bound.approval_id:
                    if canonical_budget_topup_fingerprint(bound, grant.authorization) != grant.semantic_fingerprint:
                        raise CanonicalBudgetIdentityConflict("top-up client id changed semantics")
                    return grant.model_copy(deep=True)
            authorization = authorization_resolver(bound.model_copy(deep=True))
            if authorization is None:
                raise CanonicalBudgetTopupRejected("PENDING_AUTHORIZATION", "no trusted authorization exists")
            raw_auth = (authorization.model_dump(mode="json")
                        if isinstance(authorization, CanonicalBudgetTopupAuthorizationSchema) else authorization)
            try:
                authority = CanonicalBudgetTopupAuthorizationSchema.model_validate(raw_auth)
            except (ValueError, TypeError) as exc:
                raise CanonicalBudgetTopupRejected("REJECTED", "invalid trusted authorization carrier") from exc
            consumption_key = canonical_budget_topup_consumption_key(bound, authority)
            fingerprint = canonical_budget_topup_fingerprint(bound, authority)
            for grant in state.grant_history:
                if grant.authorization_consumption_key == consumption_key:
                    if grant.semantic_fingerprint != fingerprint:
                        raise CanonicalBudgetIdentityConflict("authorization consumption key changed debit semantics")
                    return grant.model_copy(deep=True)
            self._validate_expected_revision(state, bound.expected_budget_revision)
            now = self._now()
            self._validate_topup_authority(state, bound, authority, now)
            with paused_authorization_fence(**{
                    name: getattr(bound, name) for name in (
                        "task_id", "run_id", "expected_lifecycle_revision", "wait_event_ref",
                        "wait_event_sha256", "child_preimage_sha256", "semantic_operation_ref",
                        "semantic_input_sha256", "artifact_version")
            }):
                grant = CanonicalBudgetTopupGrantSchema(
                    request=bound.model_copy(deep=True), authorization=authority.model_copy(deep=True),
                    authorization_consumption_key=consumption_key, semantic_fingerprint=fingerprint,
                    created_against_budget_revision=state.revision,
                    resulting_budget_revision=state.revision + 1, accepted_at=now,
                )
                next_state = _canonical_state_with_derived(
                    task_id=state.task_id, run_id=state.run_id, policy_ref=state.policy_ref,
                    policy_version=state.policy_version, limits=state.limits, used=state.used,
                    active_reserved=state.active_reserved, draining=state.draining,
                    revision=state.revision + 1, started_at=state.started_at, deadline_at=state.deadline_at,
                    reservations=state.reservations, grant_history=[*state.grant_history, grant],
                )
                self._append_canonical_state_unlocked(event_type="topup_granted", state=next_state)
                return grant.model_copy(deep=True)

    def _validate_expected_revision(
        self,
        state: CanonicalBudgetStateSchema,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is None:
            raise CanonicalBudgetRevisionConflict(
                "canonical Budget mutation requires expected_revision CAS"
            )
        if int(expected_revision) != state.revision:
            raise CanonicalBudgetRevisionConflict(
                "stale canonical Budget revision: "
                f"expected={expected_revision} current={state.revision}"
            )

    def _validate_reservation_ref(
        self,
        ref: BudgetReservationRefSchema,
    ) -> BudgetReservationRefSchema:
        normalized = BudgetReservationRefSchema.model_validate(ref)
        if normalized.task_id != self.task_id:
            raise CanonicalBudgetIntegrityError(
                "reservation task_id does not match canonical account"
            )
        if normalized.run_id != self.run_id:
            raise CanonicalBudgetIntegrityError(
                "reservation run_id does not match canonical account"
            )
        if normalized.parent_reservation_id is not None:
            raise CanonicalBudgetIntegrityError(
                "parent reservation/slice runtime is deferred; "
                "do not double-reserve canonical Run capacity"
            )
        return normalized

    def _reservation_fingerprint(
        self,
        *,
        ref: BudgetReservationRefSchema,
        requested: CanonicalBudgetVectorSchema,
    ) -> str:
        return canonical_json_sha256(
            {
                "run_id": ref.run_id,
                "task_id": ref.task_id,
                "reservation_kind": ref.reservation_kind,
                "owner_occurrence_ref": ref.owner_occurrence_ref,
                "scope_ref": ref.scope_ref,
                "operation_ref": ref.operation_ref,
                "attempt_id": ref.attempt_id,
                "parent_reservation_id": ref.parent_reservation_id,
                "requested": requested.model_dump(mode="json"),
                "policy_ref": self.policy_ref,
                "policy_version": self.policy_version,
            }
        )

    @staticmethod
    def _settlement_fingerprint(
        *,
        reservation: BudgetReservationSchema,
        status: str,
        actual: CanonicalBudgetVectorSchema,
    ) -> str:
        return canonical_json_sha256(
            {
                "reservation_id": reservation.reservation_ref.reservation_id,
                "reservation_fingerprint": reservation.semantic_fingerprint,
                "status": status,
                "actual": actual.model_dump(mode="json"),
            }
        )

    def reserve(
        self,
        reservation: str | BudgetReservationRefSchema,
        requested: BudgetVectorSchema | CanonicalBudgetVectorSchema,
        *,
        expected_revision: int | None = None,
    ) -> LegacyBudgetReservationSchema | BudgetReservationSchema:
        if not self._canonical_mode:
            if not isinstance(reservation, str):
                raise TypeError("legacy Budget reserve requires reservation_id string")
            return self._legacy_reserve(
                reservation,
                BudgetVectorSchema.model_validate(requested),
            )

        if isinstance(reservation, str):
            raise CanonicalBudgetIntegrityError(
                "canonical reserve requires BudgetReservationRefSchema; "
                "invocation/trace IDs cannot substitute semantic identity"
            )
        ref = self._validate_reservation_ref(reservation)
        vector = CanonicalBudgetVectorSchema.model_validate(requested)
        if vector.is_zero():
            raise ValueError("canonical Budget reservation vector cannot be zero")
        assert self._canonical_lock_path is not None

        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            state = self._canonical_current_unlocked()
            if self.run_commit_guard is not None:
                self.run_commit_guard(str(self.run_id))
            reservation_id = ref.reservation_id
            fingerprint = self._reservation_fingerprint(
                ref=ref,
                requested=vector,
            )
            existing = self._reservation_map(state).get(reservation_id)
            if existing is not None:
                if existing.semantic_fingerprint != fingerprint:
                    raise CanonicalBudgetIdentityConflict(
                        "budget reservation id reused with different fingerprint"
                    )
                return existing.model_copy(deep=True)

            self._validate_expected_revision(state, expected_revision)
            if state.draining:
                raise CanonicalBudgetDraining()

            for name in CANONICAL_BUDGET_DIMENSIONS:
                required = int(getattr(vector, name))
                if required <= 0:
                    continue
                remaining = int(getattr(state.remaining, name))
                if remaining < required:
                    error = CanonicalBudgetCapacityExceeded(
                        name,
                        limit=int(getattr(state.effective_limits, name)),
                        remaining=remaining,
                        required=required,
                    )
                    error.native_denial = CanonicalBudgetAdmissionDenialSchema(
                        task_id=state.task_id, run_id=state.run_id,
                        budget_revision=state.revision,
                        budget_state_sha256=canonical_json_sha256(state.model_dump(mode="json")),
                        reservation_ref=ref.model_copy(deep=True), requested=vector.model_copy(deep=True),
                        denied_dimension=name, required=required, remaining=remaining,
                        baseline_limits=state.limits.model_copy(deep=True),
                        effective_limits=state.effective_limits,
                        available=state.remaining.model_copy(deep=True), draining=state.draining,
                    )
                    raise error

            now = self._now()
            record = BudgetReservationSchema(
                reservation_ref=ref,
                requested=vector,
                semantic_fingerprint=fingerprint,
                status="active",
                created_against_budget_revision=state.revision,
                policy_ref=str(self.policy_ref),
                policy_version=str(self.policy_version),
                created_at=now,
                updated_at=now,
            )
            next_state = _canonical_state_with_derived(
                task_id=state.task_id,
                run_id=state.run_id,
                policy_ref=state.policy_ref,
                policy_version=state.policy_version,
                limits=state.limits,
                used=state.used,
                active_reserved=_canonical_add(
                    state.active_reserved,
                    vector,
                ),
                draining=state.draining,
                revision=state.revision + 1,
                started_at=state.started_at,
                deadline_at=state.deadline_at,
                reservations=[*state.reservations, record],
                grant_history=state.grant_history,
            )
            self._append_canonical_state_unlocked(
                event_type="reservation_created",
                state=next_state,
            )
            return record.model_copy(deep=True)

    def resolve_reservation(
        self,
        reservation_id: str,
    ) -> BudgetReservationSchema | LegacyBudgetReservationSchema:
        normalized = str(reservation_id or "").strip()
        if not normalized:
            raise ValueError("reservation_id is required")
        if not self._canonical_mode:
            existing = self._reservations.get(normalized)
            if existing is None:
                raise KeyError(normalized)
            return existing.model_copy(deep=True)

        assert self._canonical_lock_path is not None
        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            state = self._canonical_current_unlocked()
            existing = self._reservation_map(state).get(normalized)
            if existing is None:
                raise KeyError(normalized)
            return existing.model_copy(deep=True)

    def read_state_with_reservations(
        self,
        reservation_ids: Sequence[str],
    ) -> tuple[CanonicalBudgetStateSchema, tuple[BudgetReservationSchema, ...]]:
        """Read one validated canonical observation with independent record copies.

        This does not create accounts or append Budget events. Existing JSONL
        torn-tail recovery and coordination-lock initialization still apply.
        Later IDs are resolved in order so earlier lookup/integrity errors keep
        their precedence over malformed later IDs.
        """
        if not self._canonical_mode:
            raise RuntimeError("read_state_with_reservations is canonical Budget only")
        if (not isinstance(reservation_ids, Sequence)
                or isinstance(reservation_ids, (str, bytes)) or not reservation_ids):
            raise ValueError("reservation_ids must be a non-empty sequence")
        first = str(reservation_ids[0] or "").strip()
        if not first:
            raise ValueError("reservation_id is required")

        assert self._canonical_lock_path is not None
        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            state = self._canonical_current_unlocked()
            reservations = self._reservation_map(state)
            selected = []
            for position, reservation_id in enumerate(reservation_ids):
                normalized = first if position == 0 else str(reservation_id or "").strip()
                if not normalized:
                    raise ValueError("reservation_id is required")
                existing = reservations.get(normalized)
                if existing is None:
                    raise KeyError(normalized)
                selected.append(existing.model_copy(deep=True))
            return state.model_copy(deep=True), tuple(selected)

    def consume(
        self,
        reservation_id: str,
        actual: CanonicalBudgetVectorSchema,
        *,
        expected_revision: int | None,
    ) -> BudgetSettlementSchema:
        """Convert one ACTIVE reservation into durable actual usage exactly once."""

        if not self._canonical_mode:
            raise RuntimeError("consume is canonical Budget only")
        normalized = str(reservation_id or "").strip()
        if not normalized:
            raise ValueError("reservation_id is required")
        actual_vector = CanonicalBudgetVectorSchema.model_validate(actual)
        assert self._canonical_lock_path is not None

        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            state = self._canonical_current_unlocked()
            if self.run_commit_guard is not None:
                self.run_commit_guard(str(self.run_id))
            record = self._reservation_map(state).get(normalized)
            if record is None:
                raise KeyError(normalized)
            settlement_fingerprint = self._settlement_fingerprint(
                reservation=record,
                status="consumed",
                actual=actual_vector,
            )

            if record.status == "consumed":
                existing_fingerprint = self._settlement_fingerprint(
                    reservation=record,
                    status="consumed",
                    actual=record.actual,
                )
                if existing_fingerprint != settlement_fingerprint:
                    raise CanonicalBudgetIdentityConflict(
                        "duplicate consume changed actual usage"
                    )
                assert record.terminal_budget_revision is not None
                return BudgetSettlementSchema(
                    reservation_id=normalized,
                    status="consumed",
                    actual=record.actual,
                    semantic_fingerprint=existing_fingerprint,
                    settled_against_budget_revision=record.terminal_budget_revision - 1,
                    terminal_budget_revision=record.terminal_budget_revision,
                    settled_at=record.updated_at,
                )
            if record.status == "released":
                raise CanonicalBudgetIntegrityError(
                    "released reservation cannot be consumed"
                )

            self._validate_expected_revision(state, expected_revision)
            now = self._now()
            terminal_revision = state.revision + 1
            updated = record.model_copy(
                update={
                    "status": "consumed",
                    "actual": actual_vector,
                    "terminal_budget_revision": terminal_revision,
                    "updated_at": now,
                }
            )
            next_state = _canonical_state_with_derived(
                task_id=state.task_id,
                run_id=state.run_id,
                policy_ref=state.policy_ref,
                policy_version=state.policy_version,
                limits=state.limits,
                used=_canonical_add(state.used, actual_vector),
                active_reserved=_canonical_subtract(
                    state.active_reserved,
                    record.requested,
                ),
                draining=state.draining,
                revision=terminal_revision,
                started_at=state.started_at,
                deadline_at=state.deadline_at,
                reservations=[
                    updated
                    if item.reservation_ref.reservation_id == normalized
                    else item
                    for item in state.reservations
                ],
                grant_history=state.grant_history,
            )
            self._append_canonical_state_unlocked(
                event_type="reservation_consumed",
                state=next_state,
            )
            return BudgetSettlementSchema(
                reservation_id=normalized,
                status="consumed",
                actual=actual_vector,
                semantic_fingerprint=settlement_fingerprint,
                settled_against_budget_revision=state.revision,
                terminal_budget_revision=terminal_revision,
                settled_at=now,
            )

    def release(
        self,
        reservation_id: str,
        *,
        expected_revision: int | None = None,
    ) -> LegacyBudgetReservationSchema | BudgetSettlementSchema:
        if not self._canonical_mode:
            return self._legacy_release(reservation_id)

        normalized = str(reservation_id or "").strip()
        if not normalized:
            raise ValueError("reservation_id is required")
        assert self._canonical_lock_path is not None
        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            state = self._canonical_current_unlocked()
            if self.run_commit_guard is not None:
                self.run_commit_guard(str(self.run_id))
            record = self._reservation_map(state).get(normalized)
            if record is None:
                raise KeyError(normalized)
            zero = _canonical_zero()
            settlement_fingerprint = self._settlement_fingerprint(
                reservation=record,
                status="released",
                actual=zero,
            )
            if record.status == "released":
                assert record.terminal_budget_revision is not None
                return BudgetSettlementSchema(
                    reservation_id=normalized,
                    status="released",
                    actual=zero,
                    semantic_fingerprint=settlement_fingerprint,
                    settled_against_budget_revision=record.terminal_budget_revision - 1,
                    terminal_budget_revision=record.terminal_budget_revision,
                    settled_at=record.updated_at,
                )
            if record.status == "consumed":
                raise CanonicalBudgetIntegrityError(
                    "consumed reservation cannot be released"
                )

            self._validate_expected_revision(state, expected_revision)
            now = self._now()
            terminal_revision = state.revision + 1
            updated = record.model_copy(
                update={
                    "status": "released",
                    "actual": zero,
                    "terminal_budget_revision": terminal_revision,
                    "updated_at": now,
                }
            )
            next_state = _canonical_state_with_derived(
                task_id=state.task_id,
                run_id=state.run_id,
                policy_ref=state.policy_ref,
                policy_version=state.policy_version,
                limits=state.limits,
                used=state.used,
                active_reserved=_canonical_subtract(
                    state.active_reserved,
                    record.requested,
                ),
                draining=state.draining,
                revision=terminal_revision,
                started_at=state.started_at,
                deadline_at=state.deadline_at,
                reservations=[
                    updated
                    if item.reservation_ref.reservation_id == normalized
                    else item
                    for item in state.reservations
                ],
                grant_history=state.grant_history,
            )
            self._append_canonical_state_unlocked(
                event_type="reservation_released",
                state=next_state,
            )
            return BudgetSettlementSchema(
                reservation_id=normalized,
                status="released",
                actual=zero,
                semantic_fingerprint=settlement_fingerprint,
                settled_against_budget_revision=state.revision,
                terminal_budget_revision=terminal_revision,
                settled_at=now,
            )

    def settle(
        self,
        reservation_id: str,
        actual: BudgetVectorSchema | CanonicalBudgetVectorSchema,
        *,
        expected_revision: int | None = None,
    ) -> LegacyBudgetReservationSchema | BudgetSettlementSchema:
        """Compatibility alias; canonical settlement delegates to consume."""

        if self._canonical_mode:
            return self.consume(
                reservation_id,
                CanonicalBudgetVectorSchema.model_validate(actual),
                expected_revision=expected_revision,
            )
        return self._legacy_settle(
            reservation_id,
            BudgetVectorSchema.model_validate(actual),
        )

    def start_draining(
        self,
        *,
        expected_revision: int | None = None,
    ) -> CanonicalBudgetStateSchema | None:
        if not self._canonical_mode:
            with self._lock:
                self._draining = True
            return None

        assert self._canonical_lock_path is not None
        with self._lock, InterProcessFileLock(self._canonical_lock_path):
            state = self._canonical_current_unlocked()
            if self.run_commit_guard is not None:
                self.run_commit_guard(str(self.run_id))
            if state.draining:
                return state.model_copy(deep=True)
            self._validate_expected_revision(state, expected_revision)
            next_state = _canonical_state_with_derived(
                task_id=state.task_id,
                run_id=state.run_id,
                policy_ref=state.policy_ref,
                policy_version=state.policy_version,
                limits=state.limits,
                used=state.used,
                active_reserved=state.active_reserved,
                draining=True,
                revision=state.revision + 1,
                started_at=state.started_at,
                deadline_at=state.deadline_at,
                reservations=state.reservations,
                grant_history=state.grant_history,
            )
            self._append_canonical_state_unlocked(
                event_type="draining_started",
                state=next_state,
            )
            return next_state.model_copy(deep=True)

    def admit_next_model_provider_attempt(
        self,
        reservation_ref: BudgetReservationRefSchema,
        *,
        token_allowance: int,
        expected_revision: int,
    ) -> BudgetReservationSchema:
        """Reserve token capacity only; Model routing stays separately owned."""

        if int(token_allowance) <= 0:
            raise ValueError("token_allowance must be positive")
        result = self.reserve(
            reservation_ref,
            CanonicalBudgetVectorSchema(
                model_tokens_total=int(token_allowance)
            ),
            expected_revision=expected_revision,
        )
        assert isinstance(result, BudgetReservationSchema)
        return result

    def admit_tool_attempt(
        self,
        reservation_ref: BudgetReservationRefSchema,
        *,
        expected_revision: int,
    ) -> BudgetReservationSchema:
        """Reserve one logical Tool operation; physical retry reuses this ID."""

        result = self.reserve(
            reservation_ref,
            CanonicalBudgetVectorSchema(logical_tool_calls=1),
            expected_revision=expected_revision,
        )
        assert isinstance(result, BudgetReservationSchema)
        return result

    def _legacy_reserve(
        self,
        reservation_id: str,
        requested: BudgetVectorSchema,
    ) -> LegacyBudgetReservationSchema:
        with self._lock:
            existing = self._reservations.get(reservation_id)
            if existing is not None:
                if existing.requested != requested:
                    raise ValueError(
                        "budget reservation id reused with different request"
                    )
                return existing.model_copy(deep=True)
            if self._draining:
                raise TaskBudgetExhausted("draining", 0)
            candidate = _legacy_add(
                _legacy_add(self._used, self._reserved),
                requested,
            )
            for resource, limit_field in _RESOURCE_LIMIT_FIELDS.items():
                limit = int(getattr(self.limits, limit_field))
                if getattr(candidate, resource) > limit:
                    self._draining = True
                    raise TaskBudgetExhausted(resource, limit)
            record = LegacyBudgetReservationSchema(
                reservation_id=reservation_id,
                task_id=self.task_id,
                requested=requested,
            )
            self._reserved = _legacy_add(self._reserved, requested)
            self._reservations[reservation_id] = record
            return record.model_copy(deep=True)

    def _legacy_settle(
        self,
        reservation_id: str,
        actual: BudgetVectorSchema,
    ) -> LegacyBudgetReservationSchema:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation.status == "settled":
                if reservation.settled != actual:
                    raise ValueError(
                        "settled reservation cannot change actual usage"
                    )
                return reservation.model_copy(deep=True)
            if reservation.status != "active":
                raise ValueError("released reservation cannot be settled")
            for resource in _RESOURCE_LIMIT_FIELDS:
                if getattr(actual, resource) > getattr(
                    reservation.requested,
                    resource,
                ):
                    raise ValueError(
                        f"actual usage exceeds reservation: {resource}"
                    )
            self._reserved = _legacy_subtract(
                self._reserved,
                reservation.requested,
            )
            self._used = _legacy_add(self._used, actual)
            reservation.settled = actual
            reservation.status = "settled"
            return reservation.model_copy(deep=True)

    def _legacy_release(
        self,
        reservation_id: str,
    ) -> LegacyBudgetReservationSchema:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise KeyError(reservation_id)
            if reservation.status == "active":
                self._reserved = _legacy_subtract(
                    self._reserved,
                    reservation.requested,
                )
                reservation.status = "released"
            return reservation.model_copy(deep=True)

    def snapshot(self) -> BudgetSnapshotSchema | CanonicalBudgetStateSchema:
        """Legacy snapshot is projection; Target snapshot is canonical state."""

        if self._canonical_mode:
            return self.canonical_state()

        with self._lock:
            elapsed = max(
                0.0,
                (self._now() - self.started_at).total_seconds(),
            )
            return BudgetSnapshotSchema(
                task_id=self.task_id,
                limits=self.limits,
                reserved=self._reserved,
                used=self._used,
                draining=self._draining,
                elapsed_seconds=elapsed,
            )
