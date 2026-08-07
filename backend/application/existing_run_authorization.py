"""Single fail-closed authorization boundary for existing durable runs."""

from __future__ import annotations

import json
from typing import Any, Mapping

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.rag import RetrievalAccessScopeSchema
from schemas.task import TaskSchema
from schemas.task_state import (
    TaskAuthorizationIdentitySchema,
    TaskStateRecordSchema,
)


class ExistingRunAuthorizationError(PermissionError):
    """Stable authorization error that does not reveal persisted identities."""


class ExistingRunAuthorizer:
    """Authorize task metadata first, then checkpoint and immutable identity."""

    _DENIED = "existing run is not authorized"
    _DEMO_TENANTS = frozenset({"default", "demo"})

    @classmethod
    def validate_trusted_scope(
        cls,
        scope: RetrievalAccessScopeSchema | None,
    ) -> None:
        if scope is not None and not isinstance(scope, RetrievalAccessScopeSchema):
            raise TypeError(
                "retrieval_access_scope must be a trusted "
                "RetrievalAccessScopeSchema instance"
            )

    @classmethod
    def authorize_task_metadata(
        cls,
        metadata: TaskAuthorizationIdentitySchema,
        *,
        requested_task_id: str,
        requested_run_id: str,
        retrieval_access_scope: RetrievalAccessScopeSchema | None,
        allow_demo_defaults: bool,
    ) -> str:
        """Authorize only the task-store identity projection and return tenant."""

        cls.validate_trusted_scope(retrieval_access_scope)
        if (
            metadata.task_id != requested_task_id
            or metadata.run_id != requested_run_id
        ):
            raise ExistingRunAuthorizationError(cls._DENIED)

        if retrieval_access_scope is None:
            if not (
                allow_demo_defaults
                and metadata.tenant_id in cls._DEMO_TENANTS
            ):
                raise ExistingRunAuthorizationError(cls._DENIED)
            return metadata.tenant_id

        if retrieval_access_scope.tenant_id != metadata.tenant_id:
            raise ExistingRunAuthorizationError(cls._DENIED)
        return retrieval_access_scope.tenant_id

    @classmethod
    def authorize_task_state_identity(
        cls,
        *,
        task_state: TaskStateRecordSchema,
        identity: TaskAuthorizationIdentitySchema,
        authenticated_tenant: str,
    ) -> None:
        """Match the full authorized Task projection to its immutable identity."""

        if (
            task_state.task_id != identity.task_id
            or task_state.run_id != identity.run_id
            or task_state.task_type != identity.task_type
            or task_state.tenant_id != authenticated_tenant
            or task_state.tenant_id != identity.tenant_id
            or task_state.canonical_task_request_sha256
            != identity.canonical_task_request_sha256
        ):
            raise ExistingRunAuthorizationError(cls._DENIED)

    @classmethod
    def authorize_checkpoint(
        cls,
        *,
        inspection: Mapping[str, Any],
        metadata: TaskAuthorizationIdentitySchema,
        authenticated_tenant: str,
    ) -> TaskSchema:
        """Validate checkpoint task, ProjectInput and immutable request identity."""

        graph_state = inspection.get("graph_state")
        if not isinstance(graph_state, Mapping):
            raise ExistingRunAuthorizationError(cls._DENIED)
        if (
            graph_state.get("task_id") != metadata.task_id
            or graph_state.get("run_id") != metadata.run_id
            or graph_state.get("task_type") != metadata.task_type
            or graph_state.get("tenant_id") != authenticated_tenant
        ):
            raise ExistingRunAuthorizationError(cls._DENIED)

        raw_task = graph_state.get("task")
        if not isinstance(raw_task, Mapping):
            raise ExistingRunAuthorizationError(cls._DENIED)
        try:
            task = TaskSchema.model_validate_json(
                json.dumps(dict(raw_task), ensure_ascii=False)
            )
            project_input = ProjectInputSchema.model_validate_json(
                json.dumps(task.project_input, ensure_ascii=False)
            )
        except Exception as exc:
            raise ExistingRunAuthorizationError(cls._DENIED) from exc

        if (
            task.task_id != metadata.task_id
            or task.run_id != metadata.run_id
            or task.task_type != metadata.task_type
            or task.tenant_id != authenticated_tenant
            or project_input.task_id != metadata.task_id
            or project_input.tenant_id != authenticated_tenant
            or task.canonical_request_sha256()
            != metadata.canonical_task_request_sha256
        ):
            raise ExistingRunAuthorizationError(cls._DENIED)
        return task


__all__ = ["ExistingRunAuthorizationError", "ExistingRunAuthorizer"]
