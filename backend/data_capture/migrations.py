"""Explicit versioned migrations for historical DataCapture records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def migrate_data_capture_record_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate v1 readiness/output ambiguity into the canonical v2 contract."""

    if not isinstance(payload, Mapping):
        raise TypeError("v1 DataCapture migration requires an object payload")
    migrated = dict(payload)
    if migrated.get("schema_version") != "data_capture_record_v1":
        raise ValueError("v1 DataCapture migration requires data_capture_record_v1")

    raw_metadata = migrated.get("metadata")
    if raw_metadata is None:
        metadata: dict[str, Any] = {}
    elif isinstance(raw_metadata, dict):
        metadata = dict(raw_metadata)
    else:
        raise TypeError("legacy DataCapture metadata must be an object or null")

    if "final_output" in migrated:
        if (
            migrated.get("generated_draft_output") is not None
            or migrated.get("deliverable_output") is not None
        ):
            raise ValueError(
                "legacy final_output conflicts with canonical artifact fields"
            )
        migrated["generated_draft_output"] = migrated.pop("final_output")
        metadata["legacy_artifact_migration"] = {
            "migration": "final_output_to_generated_draft_v1",
            "deliverable_not_inferred": True,
        }

    stage = migrated.get("capture_stage")
    if stage is None:
        stage = "candidate"
    legacy_auto_ready = bool(
        stage == "candidate"
        and metadata.get("quality_decision") is None
        and (
            migrated.get("is_usable_for_training") is True
            or migrated.get("is_usable_for_eval") is True
            or migrated.get("quality_score") is not None
        )
    )
    if legacy_auto_ready:
        metadata["legacy_capture_migration"] = {
            "migration": "reset_pre_day2_auto_readiness_v1",
            "previous_quality_score": migrated.get("quality_score"),
            "previous_is_usable_for_training": bool(
                migrated.get("is_usable_for_training")
            ),
            "previous_is_usable_for_eval": bool(
                migrated.get("is_usable_for_eval")
            ),
        }
        metadata["quality_decision"] = "pending_evaluation"
        migrated.update(
            {
                "capture_stage": "candidate",
                "quality_score": None,
                "is_usable_for_training": False,
                "is_usable_for_eval": False,
            }
        )

    migrated["schema_version"] = "data_capture_record_v2"
    migrated["metadata"] = metadata
    return migrated
