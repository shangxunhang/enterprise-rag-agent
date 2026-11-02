"""Fact/Claim/证据/依赖变化的确定性章节影响分析。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ChangeSetSchema,
    ImpactAnalysisSchema,
    SectionBindingSnapshotSchema,
    SectionImpactAction,
    SectionImpactSchema,
)


class ImpactAnalyzer:
    def __init__(self, *, full_regeneration_affected_ratio: float = 0.8) -> None:
        if not 0 < full_regeneration_affected_ratio <= 1:
            raise ValueError(
                "full_regeneration_affected_ratio must be in (0, 1]"
            )
        self.threshold = full_regeneration_affected_ratio

    def analyze(
        self,
        changes: ChangeSetSchema,
        bindings: list[SectionBindingSnapshotSchema],
    ) -> ImpactAnalysisSchema:
        by_id = {binding.section_id: binding for binding in bindings}
        if len(by_id) != len(bindings):
            raise ValueError("section binding ids must be unique")
        actions = {
            section_id: SectionImpactAction.REUSE for section_id in by_id
        }
        reasons: dict[str, list[str]] = {
            section_id: [] for section_id in by_id
        }
        major = any(
            (
                changes.excel_scope_changed,
                changes.device_categories_changed,
                changes.core_model_or_quantity_changed,
                changes.template_structure_changed,
                changes.project_boundary_changed,
                changes.broad_replan,
            )
        )
        global_regeneration_reason = None
        if changes.template_structure_changed or changes.broad_replan:
            global_regeneration_reason = "plan_structure_changed"
        elif (
            changes.excel_scope_changed
            or changes.device_categories_changed
            or changes.project_boundary_changed
        ):
            global_regeneration_reason = "global_project_scope_changed"
        elif (
            changes.core_model_or_quantity_changed
            and not changes.changed_fact_refs
        ):
            global_regeneration_reason = "unscoped_core_fact_change"

        for section_id, binding in by_id.items():
            if global_regeneration_reason is not None:
                actions[section_id] = SectionImpactAction.REGENERATE
                reasons[section_id].append(global_regeneration_reason)
                continue
            if set(binding.fact_refs) & set(changes.changed_fact_refs):
                actions[section_id] = SectionImpactAction.REGENERATE
                reasons[section_id].append("bound_fact_changed")
            elif set(binding.evidence_topics) & set(
                changes.changed_evidence_topics
            ):
                actions[section_id] = SectionImpactAction.RESEARCH_AGAIN
                reasons[section_id].append("evidence_topic_changed")
            elif set(binding.claim_refs) & set(changes.changed_claim_refs):
                actions[section_id] = SectionImpactAction.REVERIFY_ONLY
                reasons[section_id].append("bound_claim_changed")

        changed = True
        while changed:
            changed = False
            affected = {
                section_id
                for section_id, action in actions.items()
                if action != SectionImpactAction.REUSE
            }
            for section_id, binding in by_id.items():
                if actions[section_id] != SectionImpactAction.REUSE:
                    continue
                references = set(binding.hard_dependencies) | set(
                    binding.consistency_references
                )
                if references & affected:
                    actions[section_id] = SectionImpactAction.REVERIFY_ONLY
                    reasons[section_id].append("dependency_or_reference_changed")
                    changed = True

        affected_count = sum(
            action != SectionImpactAction.REUSE for action in actions.values()
        )
        ratio = affected_count / len(bindings) if bindings else 0.0
        full_regeneration = bool(bindings and ratio >= self.threshold)
        if full_regeneration:
            for section_id, action in list(actions.items()):
                if action == SectionImpactAction.REUSE:
                    actions[section_id] = SectionImpactAction.REGENERATE
                    reasons[section_id].append("full_regeneration_threshold")
            ratio = 1.0

        return ImpactAnalysisSchema(
            section_impacts=[
                SectionImpactSchema(
                    section_id=section_id,
                    action=actions[section_id],
                    reason_codes=reasons[section_id],
                )
                for section_id in sorted(by_id)
            ],
            affected_ratio=ratio,
            full_regeneration=full_regeneration,
            major_change=major,
        )
