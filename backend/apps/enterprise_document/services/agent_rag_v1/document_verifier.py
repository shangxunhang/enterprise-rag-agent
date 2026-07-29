"""跨章节 Claim 一致性与 Excel 权威事实验证。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any, Iterable

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ClaimLifecycleStatus,
    ClaimSchema,
    ClaimVerificationStatus,
    ConflictRepairPlanSchema,
    DocumentConflictSchema,
    DocumentVerificationResultSchema,
    FactSnapshotSchema,
    MultiAgentSharedStateSchema,
    RepairAction,
    RepairTaskSchema,
)


class DocumentVerifierService:
    _HARD_TYPES = {
        "device",
        "model",
        "specification",
        "quantity",
        "amount",
        "formula",
        "calculation_result",
        "construction_scope",
    }

    def verify(
        self,
        *,
        state: MultiAgentSharedStateSchema,
        claims: Iterable[ClaimSchema],
        fact_snapshot: FactSnapshotSchema,
        mode: str = "full_logic",
        changed_section_ids: set[str] | None = None,
    ) -> DocumentVerificationResultSchema:
        if mode not in {"full_logic", "incremental_logic"}:
            raise ValueError("unsupported document verification mode")
        if fact_snapshot.snapshot_version != state.state_meta.input_snapshot_version:
            raise ValueError("document verifier FactSnapshot is stale")
        checked = self._checked_sections(
            state,
            mode=mode,
            changed_section_ids=changed_section_ids or set(),
        )
        active_claims = [
            claim
            for claim in claims
            if claim.lifecycle_status == ClaimLifecycleStatus.ACTIVE
        ]
        relevant_fact_ids = {
            str(claim.metadata.get("candidate_fact_id"))
            for claim in active_claims
            if claim.section_id in checked
            and claim.metadata.get("candidate_fact_id")
        }
        relevant_claims = [
            claim
            for claim in active_claims
            if claim.section_id in checked
            or str(claim.metadata.get("candidate_fact_id")) in relevant_fact_ids
        ]
        conflicts: list[DocumentConflictSchema] = []
        for claim in relevant_claims:
            if (
                claim.claim_type in self._HARD_TYPES
                and claim.verification_status
                in {
                    ClaimVerificationStatus.CONFLICTED,
                    ClaimVerificationStatus.UNSUPPORTED,
                    ClaimVerificationStatus.REJECTED,
                }
            ):
                conflicts.append(
                    self._conflict(
                        conflict_type="authoritative_fact_conflict",
                        section_ids=[claim.section_id],
                        target_section_ids=[claim.section_id],
                        claim_ids=[claim.claim_id],
                        authoritative_fact_ref=claim.metadata.get("source_ref"),
                        message=(
                            f"{claim.claim_type} 未通过权威事实校验"
                        ),
                    )
                )
            elif (
                claim.claim_type in self._HARD_TYPES
                and not claim.source_bindings
            ):
                conflicts.append(
                    self._conflict(
                        conflict_type="claim_binding_missing",
                        section_ids=[claim.section_id],
                        target_section_ids=[claim.section_id],
                        claim_ids=[claim.claim_id],
                        authoritative_fact_ref=claim.metadata.get("source_ref"),
                        message=f"{claim.claim_type} 缺少必需来源绑定",
                    )
                )

        by_fact: dict[tuple[str, str], list[ClaimSchema]] = {}
        for claim in relevant_claims:
            fact_id = str(claim.metadata.get("candidate_fact_id") or "")
            if fact_id:
                by_fact.setdefault((fact_id, claim.claim_type), []).append(claim)
        for (_fact_id, claim_type), group in sorted(by_fact.items()):
            values = {self._normalized_value(claim.normalized_value) for claim in group}
            if len(values) <= 1:
                continue
            authoritative = group[0].metadata.get("authoritative_value")
            target_sections = sorted(
                {
                    claim.section_id
                    for claim in group
                    if not self._equals(claim.normalized_value, authoritative)
                }
            )
            conflicts.append(
                self._conflict(
                    conflict_type="cross_section_fact_conflict",
                    section_ids=sorted({claim.section_id for claim in group}),
                    target_section_ids=(
                        target_sections
                        or sorted({claim.section_id for claim in group})
                    ),
                    claim_ids=[claim.claim_id for claim in group],
                    authoritative_fact_ref=group[0].metadata.get("source_ref"),
                    message=(
                        f"跨章节 {claim_type} 声明不一致，必须以 Excel 为准"
                    ),
                )
            )

        conflicts = self._deduplicate(conflicts)
        repair_tasks: list[RepairTaskSchema] = []
        previous_repair_id = None
        for index, conflict in enumerate(
            sorted(conflicts, key=self._conflict_priority),
            start=1,
        ):
            repair_id = f"repair_{index:03d}_{conflict.conflict_id[-8:]}"
            repair_tasks.append(
                RepairTaskSchema(
                    repair_id=repair_id,
                    priority=index,
                    conflict_ids=[conflict.conflict_id],
                    target_section_ids=conflict.target_section_ids,
                    action=(
                        RepairAction.RESEARCH_MORE
                        if conflict.conflict_type == "claim_binding_missing"
                        else RepairAction.REWRITE
                    ),
                    authoritative_fact_refs=(
                        [conflict.authoritative_fact_ref]
                        if conflict.authoritative_fact_ref
                        else []
                    ),
                    prerequisite_repair_ids=(
                        [previous_repair_id] if previous_repair_id else []
                    ),
                )
            )
            previous_repair_id = repair_id
        return DocumentVerificationResultSchema(
            mode=mode,
            checked_section_ids=sorted(checked),
            conflicts=conflicts,
            repair_plan=ConflictRepairPlanSchema(repair_tasks=repair_tasks),
            passed=not conflicts,
        )

    @staticmethod
    def _checked_sections(
        state: MultiAgentSharedStateSchema,
        *,
        mode: str,
        changed_section_ids: set[str],
    ) -> set[str]:
        if mode == "full_logic":
            return set(state.section_states)
        unknown = changed_section_ids - set(state.section_states)
        if unknown:
            raise ValueError(
                "incremental verification has unknown sections: "
                + ", ".join(sorted(unknown))
            )
        checked = set(changed_section_ids)
        for section_id, section in state.section_states.items():
            references = set(section.execution.hard_dependencies) | set(
                section.execution.consistency_references
            )
            if references & changed_section_ids:
                checked.add(section_id)
            if section_id in changed_section_ids:
                checked.update(section.execution.hard_dependencies)
        return checked

    @staticmethod
    def _conflict(
        *,
        conflict_type: str,
        section_ids: list[str],
        target_section_ids: list[str],
        claim_ids: list[str],
        authoritative_fact_ref: Any,
        message: str,
    ) -> DocumentConflictSchema:
        raw = (
            f"{conflict_type}:{','.join(sorted(section_ids))}:"
            f"{','.join(sorted(claim_ids))}"
        )
        return DocumentConflictSchema(
            conflict_id=(
                "conflict_"
                + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
            ),
            conflict_type=conflict_type,
            severity="blocking",
            section_ids=sorted(set(section_ids)),
            target_section_ids=sorted(set(target_section_ids)),
            claim_ids=sorted(set(claim_ids)),
            authoritative_fact_ref=(
                str(authoritative_fact_ref)
                if authoritative_fact_ref
                else None
            ),
            message=message,
        )

    @staticmethod
    def _deduplicate(
        conflicts: list[DocumentConflictSchema],
    ) -> list[DocumentConflictSchema]:
        by_id = {conflict.conflict_id: conflict for conflict in conflicts}
        return [by_id[key] for key in sorted(by_id)]

    @staticmethod
    def _conflict_priority(conflict: DocumentConflictSchema) -> tuple[int, str]:
        priority = {
            "authoritative_fact_conflict": 1,
            "claim_binding_missing": 2,
            "cross_section_fact_conflict": 3,
        }.get(conflict.conflict_type, 10)
        return priority, conflict.conflict_id

    @staticmethod
    def _normalized_value(value: Any) -> str:
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        return str(value).strip().casefold()

    @staticmethod
    def _equals(left: Any, right: Any) -> bool:
        if right is None:
            return False
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except (InvalidOperation, ValueError):
            return str(left).strip().casefold() == str(right).strip().casefold()
