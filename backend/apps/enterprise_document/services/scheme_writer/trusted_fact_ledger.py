"""Trusted structured high-risk fact ledger.

Free-form intent fields are deliberately outside this boundary.  The ledger
accepts only explicitly typed ProjectInput fields and a small metadata
allowlist whose keys carry an unambiguous fact identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema


@dataclass(frozen=True)
class TrustedHighRiskFact:
    """One high-risk fact that may appear in a final deliverable."""

    fact_type: str
    subject: str
    value: int | float | str
    unit: str
    source_type: str
    source_id: str

    @property
    def identity(self) -> tuple[str, str, int | float | str, str]:
        return (self.fact_type, self.subject, self.value, self.unit)


_ALLOWLIST_METADATA_FACTS: dict[
    str,
    tuple[str, str, str, float],
] = {
    "server_count": ("hardware_count", "server", "count", 1.0),
    "gpu_count": ("hardware_count", "gpu", "count", 1.0),
    "cpu_count": ("hardware_count", "cpu", "count", 1.0),
    "npu_count": ("hardware_count", "npu", "count", 1.0),
    "rack_count": ("hardware_count", "rack", "count", 1.0),
    "node_count": ("hardware_count", "node", "count", 1.0),
    "staff_count": ("staff_count", "staff_total", "count", 1.0),
    "project_duration_months": ("duration", "project", "month", 1.0),
    "budget_yuan": ("finance", "budget", "yuan", 1.0),
    "budget_wan_yuan": ("finance", "budget", "yuan", 10000.0),
    "accuracy_percent": ("performance", "accuracy", "percent", 1.0),
}


def normalize_fact_value(
    value: Any,
    *,
    multiplier: float = 1.0,
) -> int | float | str | None:
    """Normalize a structured scalar without interpreting free-form prose."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value) * multiplier
        return int(number) if number.is_integer() else number

    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text) * multiplier
    except ValueError:
        return text if multiplier == 1.0 else None
    return int(number) if number.is_integer() else number


class TrustedFactLedgerBuilder:
    """Build the only structured support ledger used by the hard gate."""

    def build(
        self,
        project_input: ProjectInputSchema,
        *,
        verified_facts: Iterable[TrustedHighRiskFact] = (),
    ) -> tuple[TrustedHighRiskFact, ...]:
        facts: list[TrustedHighRiskFact] = []

        def add(
            fact_type: str,
            subject: str,
            value: Any,
            unit: str,
            *,
            source_type: str,
            source_id: str,
            multiplier: float = 1.0,
        ) -> None:
            normalized = normalize_fact_value(value, multiplier=multiplier)
            if normalized is None:
                return
            facts.append(
                TrustedHighRiskFact(
                    fact_type=fact_type,
                    subject=subject,
                    value=normalized,
                    unit=unit,
                    source_type=source_type,
                    source_id=source_id,
                )
            )

        add(
            "staff_count",
            "staff_total",
            project_input.total_staff,
            "count",
            source_type="project_input_field",
            source_id="total_staff",
        )
        add(
            "department_count",
            "functional_department",
            project_input.functional_department_count,
            "count",
            source_type="project_input_field",
            source_id="functional_department_count",
        )
        add(
            "department_count",
            "business_department",
            project_input.business_department_count,
            "count",
            source_type="project_input_field",
            source_id="business_department_count",
        )

        for index, group in enumerate(project_input.department_groups):
            group_name = str(group.group_name or "")
            if "职能" in group_name:
                subject = "functional_department"
            elif "业务" in group_name:
                subject = "business_department"
            else:
                continue
            add(
                "department_count",
                subject,
                group.department_count,
                "count",
                source_type="project_input_field",
                source_id=f"department_groups[{index}].department_count",
            )

        for index, resource in enumerate(project_input.hardware_resources):
            add(
                "hardware_count",
                "server",
                resource.server_count,
                "count",
                source_type="project_input_field",
                source_id=f"hardware_resources[{index}].server_count",
            )
            resource_text = " ".join(
                (
                    str(resource.resource_type or ""),
                    str(resource.device_model or ""),
                    str(resource.description or ""),
                )
            ).upper()
            card_subject = next(
                (
                    subject
                    for token, subject in (
                        ("GPU", "gpu"),
                        ("显卡", "gpu"),
                        ("CPU", "cpu"),
                        ("NPU", "npu"),
                    )
                    if token.upper() in resource_text
                ),
                None,
            )
            if card_subject is not None:
                add(
                    "hardware_count",
                    card_subject,
                    resource.total_cards,
                    "count",
                    source_type="project_input_field",
                    source_id=f"hardware_resources[{index}].total_cards",
                )

        for key, raw_value in dict(project_input.metadata or {}).items():
            mapping = _ALLOWLIST_METADATA_FACTS.get(str(key))
            if mapping is None:
                continue
            fact_type, subject, unit, multiplier = mapping
            add(
                fact_type,
                subject,
                raw_value,
                unit,
                source_type="allowlist_metadata",
                source_id=f"metadata.{key}",
                multiplier=multiplier,
            )

        for fact in verified_facts:
            if fact.source_type not in {
                "structured_verified_evidence",
                "verified_citation_binding",
            }:
                continue
            facts.append(fact)

        deduplicated: dict[
            tuple[str, str, int | float | str, str, str, str],
            TrustedHighRiskFact,
        ] = {}
        for fact in facts:
            deduplicated[
                (*fact.identity, fact.source_type, fact.source_id)
            ] = fact
        return tuple(deduplicated.values())

    def build_many(
        self,
        project_inputs: Iterable[ProjectInputSchema],
    ) -> tuple[TrustedHighRiskFact, ...]:
        facts = [
            fact
            for project_input in project_inputs
            for fact in self.build(project_input)
        ]
        deduplicated = {
            (*fact.identity, fact.source_type, fact.source_id): fact
            for fact in facts
        }
        return tuple(deduplicated.values())
