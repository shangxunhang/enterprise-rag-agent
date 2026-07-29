"""StatePatchRequest 的确定性写入所有权校验。"""

from __future__ import annotations

from apps.enterprise_document.schemas.agent_rag_v1 import (
    StateActorRole,
    StatePatchRequestSchema,
)


class StateWritePermissionError(PermissionError):
    pass


class WritePermissionValidator:
    """落实规格 23.2 的域所有权，不允许 Agent 越域写入。"""

    _DOMAIN_OWNERS = {
        "execution_plan": {StateActorRole.PLANNER},
        "section.research": {StateActorRole.RESEARCH},
        "section.writing": {StateActorRole.WRITER},
        "section.claims": {StateActorRole.CLAIM_PIPELINE},
        "section.verification": {StateActorRole.VERIFIER},
        "section.execution": {StateActorRole.ROUTER},
        "document_validation": {StateActorRole.VERIFIER},
        "runtime_control": {StateActorRole.ROUTER, StateActorRole.SUPERVISOR},
        "final_result": {StateActorRole.SUPERVISOR},
        "task_context": {StateActorRole.SUPERVISOR},
    }

    def validate(self, request: StatePatchRequestSchema) -> None:
        if request.target_domain == "artifact_state":
            self._validate_artifact_state(request)
            return
        owners = self._DOMAIN_OWNERS.get(request.target_domain)
        if owners is None:
            raise StateWritePermissionError(
                f"unsupported state domain: {request.target_domain}"
            )
        if request.actor.agent_role not in owners:
            raise StateWritePermissionError(
                f"{request.actor.agent_role.value} cannot write "
                f"{request.target_domain}"
            )
        if request.target_domain == "task_context":
            unsupported = {
                operation.path
                for operation in request.operations
                if operation.path != "/task_status"
            }
            if unsupported:
                raise StateWritePermissionError(
                    "Supervisor may only patch task_context.task_status"
                )

    @staticmethod
    def _validate_artifact_state(request: StatePatchRequestSchema) -> None:
        role = request.actor.agent_role
        allowed_paths = {
            StateActorRole.ASSEMBLER: {
                "/assembly_ref",
                "/final_artifact_ref",
                "/generation_report_ref",
                "/document_version",
            },
            StateActorRole.ASSEMBLY_VALIDATOR: {
                "/assembly_validation_ref",
            },
        }.get(role)
        if allowed_paths is None:
            raise StateWritePermissionError(
                f"{role.value} cannot write artifact_state"
            )
        unsupported = {
            operation.path
            for operation in request.operations
            if operation.path not in allowed_paths
        }
        if unsupported:
            raise StateWritePermissionError(
                "artifact_state path is not owned by actor: "
                + ", ".join(sorted(unsupported))
            )
