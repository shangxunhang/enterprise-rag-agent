"""不可变文档版本号的确定性 Major/Minor 决策。"""

from __future__ import annotations

import re

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ChangeSetSchema,
    VersionDecisionSchema,
)


class VersionPolicyService:
    _VERSION_PATTERN = re.compile(r"^v(?P<major>[1-9]\d*)\.(?P<minor>\d+)$")

    def decide(
        self,
        *,
        previous_version: str | None,
        changes: ChangeSetSchema | None = None,
    ) -> VersionDecisionSchema:
        if previous_version is None:
            return VersionDecisionSchema(
                previous_version=None,
                next_version="v1.0",
                change_level="initial",
                reason_codes=["initial_publish"],
            )
        match = self._VERSION_PATTERN.fullmatch(previous_version)
        if match is None:
            raise ValueError(f"invalid document version: {previous_version}")
        major = int(match.group("major"))
        minor = int(match.group("minor"))
        change_set = changes or ChangeSetSchema()
        reasons = self._major_reasons(change_set)
        if reasons:
            return VersionDecisionSchema(
                previous_version=previous_version,
                next_version=f"v{major + 1}.0",
                change_level="major",
                reason_codes=reasons,
            )
        return VersionDecisionSchema(
            previous_version=previous_version,
            next_version=f"v{major}.{minor + 1}",
            change_level="minor",
            reason_codes=["local_revision"],
        )

    @staticmethod
    def _major_reasons(changes: ChangeSetSchema) -> list[str]:
        flags = {
            "excel_scope_changed": changes.excel_scope_changed,
            "device_categories_changed": changes.device_categories_changed,
            "core_model_or_quantity_changed": (
                changes.core_model_or_quantity_changed
            ),
            "template_structure_changed": changes.template_structure_changed,
            "project_boundary_changed": changes.project_boundary_changed,
            "broad_replan": changes.broad_replan,
        }
        return [name for name, enabled in flags.items() if enabled]
