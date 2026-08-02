"""跨章节 Claim 一致性与 Excel 权威事实验证。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any, Iterable, Mapping, TYPE_CHECKING

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
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer.document import SchemeSectionSchema
from apps.enterprise_document.schemas.scheme_writer.state_projection import (
    SectionStateSchema as ProductionSectionStateSchema,
    VerificationProjectionStatus,
)
if TYPE_CHECKING:
    from apps.enterprise_document.services.scheme_writer.high_risk_claim_service import (
        ExtractedHighRiskClaim,
        HighRiskClaimExtractor,
    )
    from apps.enterprise_document.services.scheme_writer.trusted_fact_ledger import (
        TrustedFactLedgerBuilder,
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

    def __init__(
        self,
        *,
        high_risk_extractor: "HighRiskClaimExtractor | None" = None,
        trusted_fact_builder: "TrustedFactLedgerBuilder | None" = None,
    ) -> None:
        self.high_risk_extractor = high_risk_extractor
        self.trusted_fact_builder = trusted_fact_builder

    def verify_mainline(
        self,
        *,
        sections: Iterable[SchemeSectionSchema],
        section_states: Mapping[str, ProductionSectionStateSchema],
        project_input: ProjectInputSchema | None,
        mode: str = "full_logic",
        changed_section_ids: set[str] | None = None,
    ) -> DocumentVerificationResultSchema:
        """Verify canonical production Sections without a second shared state.

        The production top-level checkpoint remains authoritative.  This method
        consumes immutable Section snapshots and typed projections, then emits a
        deterministic conflict/repair plan for the graph to route serially.
        """

        if mode not in {"full_logic", "incremental_logic"}:
            raise ValueError("unsupported document verification mode")
        if self.high_risk_extractor is None:
            from apps.enterprise_document.services.scheme_writer.high_risk_claim_service import (
                HighRiskClaimExtractor,
            )

            self.high_risk_extractor = HighRiskClaimExtractor()
        if self.trusted_fact_builder is None:
            from apps.enterprise_document.services.scheme_writer.trusted_fact_ledger import (
                TrustedFactLedgerBuilder,
            )

            self.trusted_fact_builder = TrustedFactLedgerBuilder()
        state_ids = set(section_states)
        changed = set(changed_section_ids or set())
        unknown = changed - state_ids
        if unknown:
            raise ValueError(
                "incremental verification has unknown sections: "
                + ", ".join(sorted(unknown))
            )
        checked = self._checked_production_sections(
            section_states,
            mode=mode,
            changed_section_ids=changed,
        )
        section_by_id = {section.section_id: section for section in sections}
        conflicts: list[DocumentConflictSchema] = []
        extracted: dict[
            tuple[str, str, str],
            list[tuple[str, ExtractedHighRiskClaim, str]],
        ] = {}

        for section_id in sorted(checked):
            section = section_by_id.get(section_id)
            state = section_states.get(section_id)
            if section is None or state is None:
                conflicts.append(
                    self._conflict(
                        conflict_type="section_snapshot_missing",
                        section_ids=[section_id],
                        target_section_ids=[section_id],
                        claim_ids=[],
                        authoritative_fact_ref=None,
                        message="文档验证缺少章节快照",
                    )
                )
                continue
            if (
                state.verification.status
                != VerificationProjectionStatus.COMPLETED
                and section.content.strip()
            ):
                conflicts.append(
                    self._conflict(
                        conflict_type="section_verification_incomplete",
                        section_ids=[section_id],
                        target_section_ids=[section_id],
                        claim_ids=[],
                        authoritative_fact_ref=None,
                        message=f"章节“{section.section_title}”尚未完成验证",
                    )
                )
            for violation in list(
                (section.extra or {}).get("mandatory_project_fact_violations")
                or []
            ):
                raw = dict(violation) if isinstance(violation, Mapping) else {}
                claim_ref = self._mainline_claim_ref(
                    section_id=section_id,
                    fact_type=str(raw.get("fact_type") or "high_risk_fact"),
                    subject=str(raw.get("subject") or "unknown"),
                    value=raw.get("value"),
                    unit=str(raw.get("unit") or ""),
                    span_start=int(raw.get("span_start") or 0),
                )
                conflicts.append(
                    self._conflict(
                        conflict_type="authoritative_fact_conflict",
                        section_ids=[section_id],
                        target_section_ids=[section_id],
                        claim_ids=[claim_ref],
                        authoritative_fact_ref=None,
                        message=(
                            str(raw.get("reason") or "")
                            or f"章节“{section.section_title}”包含未受支持的高风险事实"
                        ),
                    )
                )
            for claim in self.high_risk_extractor.extract(section.content):
                if claim.commitment != "affirmative":
                    continue
                claim_ref = self._mainline_claim_ref(
                    section_id=section_id,
                    fact_type=claim.fact_type,
                    subject=claim.subject,
                    value=claim.value,
                    unit=claim.unit,
                    span_start=claim.span_start,
                )
                extracted.setdefault(
                    (claim.fact_type, claim.subject, claim.unit),
                    [],
                ).append((section_id, claim, claim_ref))

        trusted_by_key: dict[tuple[str, str, str], set[str]] = {}
        if project_input is not None:
            for fact in self.trusted_fact_builder.build(project_input):
                key = (fact.fact_type, fact.subject, fact.unit)
                trusted_by_key.setdefault(key, set()).add(
                    self._normalized_value(fact.value)
                )
        for key, group in sorted(extracted.items()):
            values = {
                self._normalized_value(claim.value)
                for _section_id, claim, _claim_ref in group
            }
            if len(values) <= 1:
                continue
            trusted_values = trusted_by_key.get(key, set())
            target_sections = sorted(
                {
                    section_id
                    for section_id, claim, _claim_ref in group
                    if not trusted_values
                    or self._normalized_value(claim.value) not in trusted_values
                }
            )
            if not target_sections:
                target_sections = sorted({item[0] for item in group})
            conflicts.append(
                self._conflict(
                    conflict_type="cross_section_fact_conflict",
                    section_ids=sorted({item[0] for item in group}),
                    target_section_ids=target_sections,
                    claim_ids=sorted({item[2] for item in group}),
                    authoritative_fact_ref=None,
                    message=(
                        f"跨章节 {key[1]} {key[0]} 声明不一致，"
                        "必须依据可信项目事实统一"
                    ),
                )
            )

        conflicts = self._deduplicate(conflicts)
        return DocumentVerificationResultSchema(
            mode=mode,
            checked_section_ids=sorted(checked),
            conflicts=conflicts,
            repair_plan=self._repair_plan(conflicts),
            passed=not conflicts,
        )

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
        repair_plan = self._repair_plan(conflicts)
        return DocumentVerificationResultSchema(
            mode=mode,
            checked_section_ids=sorted(checked),
            conflicts=conflicts,
            repair_plan=repair_plan,
            passed=not conflicts,
        )

    @staticmethod
    def _checked_production_sections(
        section_states: Mapping[str, ProductionSectionStateSchema],
        *,
        mode: str,
        changed_section_ids: set[str],
    ) -> set[str]:
        if mode == "full_logic":
            return set(section_states)
        checked = set(changed_section_ids)
        for section_id, section in section_states.items():
            references = set(section.hard_dependencies) | set(
                section.consistency_references
            )
            if references & changed_section_ids:
                checked.add(section_id)
            if section_id in changed_section_ids:
                checked.update(section.hard_dependencies)
        return checked

    @staticmethod
    def _mainline_claim_ref(
        *,
        section_id: str,
        fact_type: str,
        subject: str,
        value: Any,
        unit: str,
        span_start: int,
    ) -> str:
        raw = f"{section_id}:{fact_type}:{subject}:{value}:{unit}:{span_start}"
        return "claim_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def _repair_plan(
        cls,
        conflicts: Iterable[DocumentConflictSchema],
    ) -> ConflictRepairPlanSchema:
        repair_tasks: list[RepairTaskSchema] = []
        previous_repair_id = None
        for index, conflict in enumerate(
            sorted(conflicts, key=cls._conflict_priority),
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
                        if conflict.conflict_type
                        in {"claim_binding_missing", "section_snapshot_missing"}
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
        return ConflictRepairPlanSchema(repair_tasks=repair_tasks)

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
