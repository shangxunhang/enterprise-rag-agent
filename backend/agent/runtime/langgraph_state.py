"""Serializable LangGraph state and legacy ``GraphStateSchema`` projection."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from copy import deepcopy
import json
from typing import Any, Mapping

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from typing_extensions import TypedDict

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from schemas.agent import AgentResultSchema
from schemas.graph import GraphNodeInputSchema, GraphNodeOutputSchema


LANGGRAPH_RUNTIME_STATE_VERSION = 1


class LangGraphRuntimeState(TypedDict, total=False):
    """Checkpoint-safe state owned by LangGraph.

    Every value is a JSON-compatible scalar, mapping or sequence. Runtime
    services, synchronization objects and mutable ``GraphStateSchema`` model
    instances are deliberately excluded.
    """

    runtime_state_version: int
    task_id: str
    run_id: str
    workflow_id: str
    workflow_version: str
    workflow_namespace: str
    graph_state_payload: dict[str, Any]
    node_inputs: list[dict[str, Any]]
    node_outputs: list[dict[str, Any]]
    node_results: list[dict[str, Any]]
    transition_count: int
    retry_count: int
    recovered_failure_count: int
    workflow_complete: bool
    terminal_reason: str
    terminal_failure_status: str | None
    next_node: str


class WorkflowNamespaceCheckpointSaver(BaseCheckpointSaver[Any]):
    """Persist top-level graph checkpoints under the declared workflow namespace.

    LangGraph 1.2.x clears a non-empty top-level ``checkpoint_ns`` before it
    calls the saver. It preserves custom configurable values, so the engine
    also supplies ``workflow_namespace`` and this adapter restores that value
    to the standard ``checkpoint_ns`` key at the saver boundary. The wrapped
    saver therefore stores and exposes the standard ``(thread_id,
    checkpoint_ns)`` identity.
    """

    def __init__(self, delegate: BaseCheckpointSaver[Any]) -> None:
        super().__init__(serde=delegate.serde)
        self.delegate = delegate

    @property
    def config_specs(self) -> list:
        return self.delegate.config_specs

    @staticmethod
    def _scoped_config(config: RunnableConfig) -> RunnableConfig:
        scoped: RunnableConfig = {
            **config,
            "configurable": dict(config.get("configurable", {})),
        }
        configurable = scoped["configurable"]
        namespace = configurable.get("workflow_namespace") or configurable.get(
            "checkpoint_ns",
            "",
        )
        configurable["checkpoint_ns"] = str(namespace or "")
        return scoped

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.delegate.get_tuple(self._scoped_config(config))

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        return self.delegate.list(
            self._scoped_config(config) if config is not None else None,
            filter=filter,
            before=self._scoped_config(before) if before is not None else None,
            limit=limit,
        )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.delegate.put(
            self._scoped_config(config),
            checkpoint,
            metadata,
            new_versions,
        )

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.delegate.put_writes(
            self._scoped_config(config),
            writes,
            task_id,
            task_path,
        )

    def delete_thread(self, thread_id: str) -> None:
        self.delegate.delete_thread(thread_id)

    def get_next_version(self, current: Any, channel: None) -> Any:
        return self.delegate.get_next_version(current, channel)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await self.delegate.aget_tuple(self._scoped_config(config))

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        async for checkpoint in self.delegate.alist(
            self._scoped_config(config) if config is not None else None,
            filter=filter,
            before=self._scoped_config(before) if before is not None else None,
            limit=limit,
        ):
            yield checkpoint

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await self.delegate.aput(
            self._scoped_config(config),
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await self.delegate.aput_writes(
            self._scoped_config(config),
            writes,
            task_id,
            task_path,
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await self.delegate.adelete_thread(thread_id)


class LangGraphRuntimeStateCodec:
    """Validate the boundary between durable graph state and legacy Agents."""

    @staticmethod
    def workflow_namespace(workflow: WorkflowDefinitionSchema) -> str:
        return f"{workflow.workflow_id}:{workflow.workflow_version}"

    def checkpoint_config(
        self,
        workflow: WorkflowDefinitionSchema,
        *,
        run_id: str,
    ) -> RunnableConfig:
        """Return the canonical identity used by invoke and checkpoint queries."""

        namespace = self.workflow_namespace(workflow)
        return {
            "configurable": {
                "thread_id": run_id,
                "checkpoint_ns": namespace,
                # Compatibility carrier for LangGraph 1.2.x, which clears the
                # top-level standard key before saver calls.
                "workflow_namespace": namespace,
            }
        }

    @staticmethod
    def _json_payload(value: Any) -> Any:
        """Return a detached JSON value and fail closed on live runtime objects."""

        return json.loads(json.dumps(value, ensure_ascii=False))

    def dump_graph_state(self, state: GraphStateSchema) -> dict[str, Any]:
        payload = state.model_dump(mode="json", round_trip=True)
        return self._json_payload(payload)

    def restore_graph_state(
        self,
        runtime_state: Mapping[str, Any],
        *,
        workflow: WorkflowDefinitionSchema | None = None,
    ) -> GraphStateSchema:
        actual_version = runtime_state.get("runtime_state_version")
        if (
            type(actual_version) is not int
            or actual_version != LANGGRAPH_RUNTIME_STATE_VERSION
        ):
            raise ValueError(
                "unsupported LangGraph runtime state version: "
                f"expected={LANGGRAPH_RUNTIME_STATE_VERSION} "
                f"actual={actual_version!r}"
            )
        payload = runtime_state.get("graph_state_payload")
        if not isinstance(payload, Mapping):
            raise ValueError("LangGraph runtime state requires graph_state_payload")
        restored = GraphStateSchema.model_validate(deepcopy(dict(payload)))
        runtime_task_id = runtime_state.get("task_id")
        if runtime_task_id != restored.task_id:
            raise ValueError(
                "LangGraph runtime task_id identity mismatch: "
                f"expected={restored.task_id!r} actual={runtime_task_id!r}"
            )
        runtime_run_id = runtime_state.get("run_id")
        if runtime_run_id != restored.run_id:
            raise ValueError(
                "LangGraph runtime run_id identity mismatch: "
                f"expected={restored.run_id!r} actual={runtime_run_id!r}"
            )
        if workflow is not None:
            self.validate_workflow_identity(runtime_state, workflow)
        return restored

    def validate_workflow_identity(
        self,
        runtime_state: Mapping[str, Any],
        workflow: WorkflowDefinitionSchema,
    ) -> None:
        expected = {
            "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.workflow_version,
            "workflow_namespace": self.workflow_namespace(workflow),
        }
        for field_name, expected_value in expected.items():
            actual_value = runtime_state.get(field_name)
            if actual_value != expected_value:
                raise ValueError(
                    f"LangGraph runtime {field_name} identity mismatch: "
                    f"expected={expected_value!r} actual={actual_value!r}"
                )

    def build_initial_state(
        self,
        workflow: WorkflowDefinitionSchema,
        graph_state: GraphStateSchema,
        *,
        end_route: str = "__workflow_end__",
    ) -> LangGraphRuntimeState:
        runtime: LangGraphRuntimeState = {
            "runtime_state_version": LANGGRAPH_RUNTIME_STATE_VERSION,
            "task_id": graph_state.task_id,
            "run_id": graph_state.run_id,
            "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.workflow_version,
            "workflow_namespace": self.workflow_namespace(workflow),
            "graph_state_payload": self.dump_graph_state(graph_state),
            "node_inputs": [],
            "node_outputs": [],
            "node_results": [],
            "transition_count": 0,
            "retry_count": 0,
            "recovered_failure_count": 0,
            "workflow_complete": False,
            "terminal_reason": "not_started",
            "terminal_failure_status": None,
            "next_node": end_route,
        }
        self.assert_json_serializable(runtime)
        return runtime

    def dump_node_input(self, value: GraphNodeInputSchema) -> dict[str, Any]:
        return self._json_payload(value.model_dump(mode="json", round_trip=True))

    def dump_node_output(self, value: GraphNodeOutputSchema) -> dict[str, Any]:
        return self._json_payload(value.model_dump(mode="json", round_trip=True))

    def dump_agent_result(self, value: AgentResultSchema) -> dict[str, Any]:
        return self._json_payload(value.model_dump(mode="json", round_trip=True))

    @staticmethod
    def restore_node_inputs(
        runtime_state: Mapping[str, Any],
    ) -> list[GraphNodeInputSchema]:
        return [
            GraphNodeInputSchema.model_validate(deepcopy(item))
            for item in runtime_state.get("node_inputs", [])
        ]

    @staticmethod
    def restore_node_outputs(
        runtime_state: Mapping[str, Any],
    ) -> list[GraphNodeOutputSchema]:
        return [
            GraphNodeOutputSchema.model_validate(deepcopy(item))
            for item in runtime_state.get("node_outputs", [])
        ]

    @staticmethod
    def restore_node_results(
        runtime_state: Mapping[str, Any],
    ) -> list[AgentResultSchema]:
        return [
            AgentResultSchema.model_validate(deepcopy(item))
            for item in runtime_state.get("node_results", [])
        ]

    @staticmethod
    def project_onto(
        target: GraphStateSchema,
        source: GraphStateSchema,
    ) -> None:
        """Publish a validated final snapshot to the caller-owned compatibility model."""

        for field_name in target.__class__.model_fields:
            setattr(target, field_name, deepcopy(getattr(source, field_name)))

    @staticmethod
    def assert_json_serializable(runtime_state: Mapping[str, Any]) -> None:
        json.dumps(runtime_state, ensure_ascii=False)
