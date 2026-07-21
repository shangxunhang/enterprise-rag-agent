# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：StateWriteContractViolation、GraphStateWriteContract、GraphStateProjector、GraphStateDiffer、GraphStateApplier。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Projection, write-contract validation, diff and commit operations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Tuple

from agent.runtime.graph_state import GraphStateSchema
from agent.runtime.workflow_schema import WorkflowDefinitionSchema, WorkflowStepSchema
from schemas.graph import (
    GraphNodeInputSchema,
    GraphStateDeltaSchema,
    stable_graph_hash,
)


_GRAPH_CONTROL_FIELDS = {
    "graph_revision",
    "current_node_id",
    "completed_node_ids",
    "node_history",
    "workflow_engine_name",
    "workflow_engine_version",
    "graph_metadata",
}


_DEFAULT_WRITE_ALIASES: Dict[str, List[str]] = {
    "normalized_project_input": [
        "context_bundle.business.project_input",
        "contexts.project_input",
    ],
    "project_input": [
        "context_bundle.business.project_input",
        "contexts.project_input",
    ],
    "table_agent_output": ["contexts.table_agent_output"],
    "source_materials": ["context_bundle.business.source_materials"],
    "missing_information": ["context_bundle.business.missing_information"],
    "conflicting_information": [
        "context_bundle.business.conflicting_information"
    ],
    "manual_boundaries": ["context_bundle.business.manual_boundaries"],
    "structured_facts": ["structured_facts"],
    "scheme_writer_input": ["contexts.scheme_writer_input"],
    "scheme_writer_output": ["contexts.scheme_writer_output"],
    "rag_tool_output": ["contexts.rag_tool_output"],
    "scheme_draft": [
        "contexts.scheme_writer_output.scheme_draft",
        "final_result.scheme_draft",
    ],
    "evidence_context": ["context_bundle.evidence"],
    "generation_context": ["context_bundle.generation"],
    "generated_outputs": ["generated_outputs"],
    "tool_results": ["tool_results"],
    "final_result": ["final_result"],
}


# 阅读注释（类）：封装 状态 write contract violation，集中封装相关状态、依赖和行为。
class StateWriteContractViolation(ValueError):
    """Raised when a node mutates GraphState outside its declared boundary."""

    # 阅读注释（函数）：初始化 StateWriteContractViolation，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        changed_paths: Iterable[str],
        allowed_paths: Iterable[str],
    ) -> None:
        """初始化 StateWriteContractViolation，保存运行所需的依赖、配置或状态。

        参数:
            changed_paths: changed paths，具体约束请结合类型标注和调用方确认。
            allowed_paths: allowed paths，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：sorted, set, join, __init__, super。
        """
        self.changed_paths = sorted(set(changed_paths))
        self.allowed_paths = sorted(set(allowed_paths))
        message = (
            "workflow node wrote undeclared GraphState paths: "
            + ", ".join(self.changed_paths)
            + "; allowed: "
            + (", ".join(self.allowed_paths) or "<none>")
        )
        super().__init__(message)


# 阅读注释（类）：封装 graph 状态 write contract，集中封装相关状态、依赖和行为。
class GraphStateWriteContract:
    """Resolve logical outputs and enforce physical GraphState write paths."""

    # 阅读注释（函数）：解析并确定 allowed paths。
    def resolve_allowed_paths(
        self,
        *,
        declared_write_keys: Iterable[str],
        declared_write_paths: Iterable[str] = (),
    ) -> List[str]:
        """解析并确定 allowed paths。

        参数:
            declared_write_keys: declared write keys，具体约束请结合类型标注和调用方确认。
            declared_write_paths: declared write paths，具体约束请结合类型标注和调用方确认。

        返回:
            List[str]

        阅读提示:
            主要直接调用：strip, str, set, _DEFAULT_WRITE_ALIASES.get, allowed.update, allowed.add, sorted。
        """
        allowed = {
            str(item).strip()
            for item in declared_write_paths
            if str(item).strip()
        }
        graph_fields = set(GraphStateSchema.model_fields)
        for raw_key in declared_write_keys:
            key = str(raw_key or "").strip()
            if not key:
                continue
            aliases = _DEFAULT_WRITE_ALIASES.get(key)
            if aliases:
                allowed.update(aliases)
            elif key in graph_fields or "." in key:
                allowed.add(key)
            else:
                # Backward-compatible logical outputs live under contexts.
                allowed.add(f"contexts.{key}")
        return sorted(allowed)

    # 阅读注释（函数）：处理 路径 is allowed 相关逻辑。
    @staticmethod
    def path_is_allowed(path: str, allowed_paths: Iterable[str]) -> bool:
        """处理 路径 is allowed 相关逻辑。

        参数:
            path: 目标文件或目录路径。
            allowed_paths: allowed paths，具体约束请结合类型标注和调用方确认。

        返回:
            bool

        阅读提示:
            主要直接调用：any, path.startswith。
        """
        return any(
            path == prefix or path.startswith(f"{prefix}.")
            for prefix in allowed_paths
        )

    # 阅读注释（函数）：校验 GraphStateWriteContract。
    def validate(
        self,
        *,
        changed_paths: Iterable[str],
        allowed_paths: Iterable[str],
    ) -> None:
        """校验 GraphStateWriteContract。

        参数:
            changed_paths: changed paths，具体约束请结合类型标注和调用方确认。
            allowed_paths: allowed paths，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：list, self.path_is_allowed, StateWriteContractViolation。
        """
        allowed = list(allowed_paths)
        violations = [
            path
            for path in changed_paths
            if not self.path_is_allowed(path, allowed)
        ]
        if violations:
            raise StateWriteContractViolation(
                changed_paths=violations,
                allowed_paths=allowed,
            )


# 阅读注释（类）：封装 graph 状态 projector，集中封装相关状态、依赖和行为。
class GraphStateProjector:
    """Resolve declared node read keys to a deterministic state projection."""

    # 阅读注释（函数）：构建 node 输入。
    def build_node_input(
        self,
        *,
        workflow: WorkflowDefinitionSchema,
        step: WorkflowStepSchema,
        state: GraphStateSchema,
    ) -> GraphNodeInputSchema:
        """构建 node 输入。

        参数:
            workflow: 工作流，具体约束请结合类型标注和调用方确认。
            step: step，具体约束请结合类型标注和调用方确认。
            state: 工作流共享状态。

        返回:
            GraphNodeInputSchema

        阅读提示:
            主要直接调用：self.resolve, deepcopy, missing.append, list, GraphNodeInputSchema, stable_graph_hash。
        """
        values: Dict[str, Any] = {}
        missing: List[str] = []
        for key in step.input_keys:
            found, value = self.resolve(state, key)
            if found:
                values[key] = deepcopy(value)
            else:
                values[key] = None
                missing.append(key)

        hash_payload = {
            "node_id": step.step_id,
            "workflow_id": workflow.workflow_id,
            "state_revision": state.graph_revision,
            "declared_read_keys": list(step.input_keys),
            "values": values,
            "missing_keys": missing,
        }
        return GraphNodeInputSchema(
            node_id=step.step_id,
            node_name=step.step_name,
            node_type=step.step_type,
            target_name=step.target_name,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.workflow_version,
            task_id=state.task_id,
            run_id=state.run_id,
            state_revision=state.graph_revision,
            declared_read_keys=list(step.input_keys),
            values=values,
            missing_keys=missing,
            input_sha256=stable_graph_hash(hash_payload),
            metadata={
                "projection_mode": "declared_keys_v1",
                "state_schema_version": state.schema_version,
            },
        )

    # 阅读注释（函数）：解析并确定 GraphStateProjector。
    def resolve(self, state: GraphStateSchema, key: str) -> Tuple[bool, Any]:
        """Resolve current workflow aliases without exposing the whole state."""

        if hasattr(state, key):
            return True, getattr(state, key)

        if key in state.contexts:
            return True, state.contexts[key]
        if key in state.generated_outputs:
            return True, state.generated_outputs[key]
        if key in state.agent_results:
            return True, state.agent_results[key]
        if key in state.tool_results:
            return True, state.tool_results[key]

        aliases = {
            "project_input": state.context_bundle.business.project_input,
            "normalized_project_input": state.context_bundle.business.project_input,
            "source_materials": state.context_bundle.business.source_materials,
            "structured_facts": state.structured_facts,
            "evidence_contract": state.context_bundle.evidence.contract,
            "rag_context": state.context_bundle.evidence.context_text,
            "scheme_writer_output": state.contexts.get("scheme_writer_output"),
            "scheme_draft": (
                (state.contexts.get("scheme_writer_output") or {}).get("scheme_draft")
                or (state.final_result or {}).get("scheme_draft")
            ),
            "final_result": state.final_result,
        }
        if key in aliases and aliases[key] is not None:
            return True, aliases[key]
        return False, None


# 阅读注释（类）：封装 graph 状态 differ，集中封装相关状态、依赖和行为。
class GraphStateDiffer:
    """Create validated deltas and support path-restricted failure commits."""

    # 阅读注释（函数）：初始化 GraphStateDiffer，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        write_contract: GraphStateWriteContract | None = None,
    ) -> None:
        """初始化 GraphStateDiffer，保存运行所需的依赖、配置或状态。

        参数:
            write_contract: write contract，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：GraphStateWriteContract。
        """
        self.write_contract = write_contract or GraphStateWriteContract()

    # 阅读注释（函数）：处理 diff 相关逻辑。
    def diff(
        self,
        *,
        node_id: str,
        before: GraphStateSchema,
        after: GraphStateSchema,
        declared_write_keys: Iterable[str],
        declared_write_paths: Iterable[str] = (),
    ) -> GraphStateDeltaSchema:
        """处理 diff 相关逻辑。

        参数:
            node_id: node 标识，具体约束请结合类型标注和调用方确认。
            before: before，具体约束请结合类型标注和调用方确认。
            after: after，具体约束请结合类型标注和调用方确认。
            declared_write_keys: declared write keys，具体约束请结合类型标注和调用方确认。
            declared_write_paths: declared write paths，具体约束请结合类型标注和调用方确认。

        返回:
            GraphStateDeltaSchema

        阅读提示:
            主要直接调用：before.model_dump, after.model_dump, self._discard_legacy_engine_owned_mutations, sorted, set, before_data.get, after_data.get, deepcopy。
        """
        before_data = before.model_dump(mode="python")
        after_data = after.model_dump(mode="python")
        self._discard_legacy_engine_owned_mutations(before_data, after_data)

        set_values: Dict[str, Any] = {}
        changed_paths: List[str] = []
        for key in sorted(set(before_data) | set(after_data)):
            if key in _GRAPH_CONTROL_FIELDS:
                continue
            left = before_data.get(key)
            right = after_data.get(key)
            if left != right:
                set_values[key] = deepcopy(right)
                changed_paths.extend(self._changed_paths(left, right, key))

        changed_paths = sorted(set(changed_paths))
        allowed_paths = self.write_contract.resolve_allowed_paths(
            declared_write_keys=declared_write_keys,
            declared_write_paths=declared_write_paths,
        )
        self.write_contract.validate(
            changed_paths=changed_paths,
            allowed_paths=allowed_paths,
        )

        observed_roots = sorted(set(set_values))
        base_revision = before.graph_revision
        next_revision = base_revision + 1
        state_before_hash = stable_graph_hash(before_data)
        simulated = deepcopy(before_data)
        simulated.update(set_values)
        simulated["graph_revision"] = next_revision
        simulated["current_node_id"] = node_id
        state_after_hash = stable_graph_hash(simulated)

        payload = {
            "node_id": node_id,
            "base_revision": base_revision,
            "next_revision": next_revision,
            "set_values": set_values,
            "changed_paths": changed_paths,
            "declared_write_keys": list(declared_write_keys),
            "declared_write_paths": allowed_paths,
            "observed_write_roots": observed_roots,
            "state_sha256_before": state_before_hash,
            "state_sha256_after": state_after_hash,
        }
        return GraphStateDeltaSchema(
            **payload,
            delta_sha256=stable_graph_hash(payload),
            metadata={
                "delta_mode": "validated_top_level_replace_v2",
                "compatibility_adapter": "legacy_agent_copy_diff_v1",
                "write_contract": "declared_path_prefix_v1",
            },
        )

    # 阅读注释（函数）：处理 discard legacy engine owned mutations 相关逻辑。
    @staticmethod
    def _discard_legacy_engine_owned_mutations(
        before_data: Dict[str, Any],
        after_data: Dict[str, Any],
    ) -> None:
        """Drop legacy Agent error mirroring before business-state diffing.

        AgentResult.error is the canonical node error. WorkflowStateController
        writes it to ``errors`` and ``context_bundle.runtime.errors`` after the
        commit decision. Retaining the legacy in-Agent write would both cross
        the node boundary and duplicate the same error.
        """

        after_data["errors"] = deepcopy(before_data.get("errors", []))
        before_runtime = (
            (before_data.get("context_bundle") or {}).get("runtime") or {}
        )
        after_runtime = (
            (after_data.get("context_bundle") or {}).get("runtime") or {}
        )
        after_runtime["errors"] = deepcopy(before_runtime.get("errors", []))

    # 阅读注释（函数）：处理 restrict delta 相关逻辑。
    def restrict_delta(
        self,
        *,
        before: GraphStateSchema,
        proposed: GraphStateDeltaSchema,
        declared_write_keys: Iterable[str],
        declared_write_paths: Iterable[str],
    ) -> GraphStateDeltaSchema:
        """Keep only explicitly allowed changed paths from a proposed delta."""

        allowed_paths = self.write_contract.resolve_allowed_paths(
            declared_write_keys=declared_write_keys,
            declared_write_paths=declared_write_paths,
        )
        before_data = before.model_dump(mode="python")
        proposed_data = deepcopy(before_data)
        proposed_data.update(deepcopy(proposed.set_values))
        restricted_data = deepcopy(before_data)

        for changed_path in proposed.changed_paths:
            if self.write_contract.path_is_allowed(changed_path, allowed_paths):
                self._copy_path(proposed_data, restricted_data, changed_path)

        restricted_state = before.__class__.model_validate(restricted_data)
        return self.diff(
            node_id=proposed.node_id,
            before=before,
            after=restricted_state,
            declared_write_keys=declared_write_keys,
            declared_write_paths=allowed_paths,
        )

    # 阅读注释（函数）：处理 copy 路径 相关逻辑。
    @staticmethod
    def _copy_path(source: Dict[str, Any], target: Dict[str, Any], path: str) -> None:
        """处理 copy 路径 相关逻辑。

        参数:
            source: source，具体约束请结合类型标注和调用方确认。
            target: target，具体约束请结合类型标注和调用方确认。
            path: 目标文件或目录路径。

        返回:
            None

        阅读提示:
            主要直接调用：path.split, isinstance, target_cursor.get, deepcopy, target_cursor.pop。
        """
        parts = path.split(".")
        source_cursor: Any = source
        target_cursor: Any = target
        for part in parts[:-1]:
            if not isinstance(source_cursor, dict) or part not in source_cursor:
                return
            source_cursor = source_cursor[part]
            if not isinstance(target_cursor, dict):
                return
            child = target_cursor.get(part)
            if not isinstance(child, dict):
                child = {}
                target_cursor[part] = child
            target_cursor = child

        leaf = parts[-1]
        if isinstance(source_cursor, dict) and leaf in source_cursor:
            target_cursor[leaf] = deepcopy(source_cursor[leaf])
        elif isinstance(target_cursor, dict):
            target_cursor.pop(leaf, None)

    # 阅读注释（函数）：处理 changed paths 相关逻辑。
    def _changed_paths(self, before: Any, after: Any, prefix: str) -> List[str]:
        """处理 changed paths 相关逻辑。

        参数:
            before: before，具体约束请结合类型标注和调用方确认。
            after: after，具体约束请结合类型标注和调用方确认。
            prefix: prefix，具体约束请结合类型标注和调用方确认。

        返回:
            List[str]

        阅读提示:
            主要直接调用：isinstance, sorted, set, paths.append, paths.extend, self._changed_paths。
        """
        if before == after:
            return []
        if isinstance(before, dict) and isinstance(after, dict):
            paths: List[str] = []
            for key in sorted(set(before) | set(after), key=str):
                child = f"{prefix}.{key}"
                if key not in before or key not in after:
                    paths.append(child)
                else:
                    paths.extend(self._changed_paths(before[key], after[key], child))
            return paths or [prefix]
        if isinstance(before, list) and isinstance(after, list):
            return [prefix]
        return [prefix]


# 阅读注释（类）：封装 graph 状态 applier，集中封装相关状态、依赖和行为。
class GraphStateApplier:
    """Atomically validate and commit one controlled business-state delta.

    ``graph_revision`` is the sequence number of these validated commits, not a
    global version for every runtime/lifecycle mutation stored on GraphState.
    """

    # 阅读注释（函数）：初始化 GraphStateApplier，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        write_contract: GraphStateWriteContract | None = None,
    ) -> None:
        """初始化 GraphStateApplier，保存运行所需的依赖、配置或状态。

        参数:
            write_contract: write contract，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：GraphStateWriteContract。
        """
        self.write_contract = write_contract or GraphStateWriteContract()

    # 阅读注释（函数）：应用 GraphStateApplier。
    def apply(
        self,
        state: GraphStateSchema,
        delta: GraphStateDeltaSchema,
    ) -> None:
        """应用 GraphStateApplier。

        参数:
            state: 工作流共享状态。
            delta: delta，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ValueError, self.write_contract.validate, state.model_dump, merged.update, deepcopy, state.__class__.model_validate, setattr, getattr。
        """
        if state.graph_revision != delta.base_revision:
            raise ValueError(
                "graph revision conflict: "
                f"state={state.graph_revision}, delta={delta.base_revision}"
            )

        self.write_contract.validate(
            changed_paths=delta.changed_paths,
            allowed_paths=delta.declared_write_paths,
        )

        merged = state.model_dump(mode="python")
        merged.update(deepcopy(delta.set_values))
        merged["graph_revision"] = delta.next_revision
        merged["current_node_id"] = delta.node_id

        validated = state.__class__.model_validate(merged)
        for field_name in state.__class__.model_fields:
            setattr(state, field_name, deepcopy(getattr(validated, field_name)))

        actual_hash = stable_graph_hash(state.model_dump(mode="python"))
        if actual_hash != delta.state_sha256_after:
            raise ValueError("committed graph state hash does not match delta")
