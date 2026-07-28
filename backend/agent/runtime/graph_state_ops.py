"""实现 GraphState 的输入投影、写入契约校验、差异生成和原子提交。

业务节点仍在状态副本上执行，本模块随后比较执行前后状态，只允许提交步骤声明的
物理路径。每个通过校验的业务增量都带修订号和哈希，提交器在修改真实状态前再次
验证契约和基线修订，并在提交后复核完整状态哈希。
"""

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


# 这些字段由工作流引擎维护，不属于业务节点可提交的状态差异。
# graph_revision 只由 GraphStateApplier 在受控提交时递增；节点位置和历史由执行器更新。
_GRAPH_CONTROL_FIELDS = {
    "graph_revision",
    "current_node_id",
    "completed_node_ids",
    "node_history",
    "workflow_engine_name",
    "workflow_engine_version",
    "graph_metadata",
}


# 把工作流定义中的逻辑输出键映射到真实 GraphState 写入路径。
# 同一个逻辑输出可能同时更新规范字段和迁移期兼容投影，因此映射值是路径列表。
_DEFAULT_WRITE_ALIASES: Dict[str, List[str]] = {
    # 规范化项目输入同时进入业务上下文和 legacy contexts 投影。
    "normalized_project_input": [
        "context_bundle.business.project_input",
        "contexts.project_input",
    ],
    "project_input": [
        "context_bundle.business.project_input",
        "contexts.project_input",
    ],
    # 项目输入解析 Agent 的兼容输出。
    "table_agent_output": ["contexts.table_agent_output"],
    # 规范业务上下文字段。
    "source_materials": ["context_bundle.business.source_materials"],
    "missing_information": ["context_bundle.business.missing_information"],
    "conflicting_information": [
        "context_bundle.business.conflicting_information"
    ],
    "manual_boundaries": ["context_bundle.business.manual_boundaries"],
    # 顶层迁移期结构化事实字段。
    "structured_facts": ["structured_facts"],
    # 方案生成 v1 兼容上下文。
    "scheme_writer_input": ["contexts.scheme_writer_input"],
    "scheme_writer_output": ["contexts.scheme_writer_output"],
    "rag_tool_output": ["contexts.rag_tool_output"],
    # 企业文档显式产物生命周期字段。
    "generated_draft": ["generated_draft"],
    "rejected_artifact": ["rejected_artifact"],
    "deliverable_final_result": ["deliverable_final_result"],
    # 规范证据与生成上下文。
    "evidence_context": ["context_bundle.evidence"],
    "generation_context": ["context_bundle.generation"],
    # 通用节点输出、工具结果和兼容最终结果。
    "generated_outputs": ["generated_outputs"],
    "tool_results": ["tool_results"],
    "final_result": ["final_result"],
}


class StateWriteContractViolation(ValueError):
    """表示节点修改了工作流定义未声明的 GraphState 路径。

    Attributes:
        changed_paths: 实际发生但不被允许的路径，已去重排序。
        allowed_paths: 当前步骤允许写入的路径前缀，已去重排序。
    """

    def __init__(
        self,
        *,
        changed_paths: Iterable[str],
        allowed_paths: Iterable[str],
    ) -> None:
        """规范化违规路径并构造可诊断错误信息。

        Args:
            changed_paths: 检测到的越界写入路径。
            allowed_paths: 当前步骤声明的合法路径前缀。
        """

        # 第一步：去重排序，保证错误消息和测试结果确定。
        self.changed_paths = sorted(set(changed_paths))
        self.allowed_paths = sorted(set(allowed_paths))

        # 第二步：同时展示实际越界路径和允许集合，便于直接修正工作流契约。
        message = (
            "workflow node wrote undeclared GraphState paths: "
            + ", ".join(self.changed_paths)
            + "; allowed: "
            + (", ".join(self.allowed_paths) or "<none>")
        )
        super().__init__(message)


class GraphStateWriteContract:
    """把逻辑输出解析为物理路径，并校验节点实际状态变更。"""

    def resolve_allowed_paths(
        self,
        *,
        declared_write_keys: Iterable[str],
        declared_write_paths: Iterable[str] = (),
    ) -> List[str]:
        """合并显式物理路径和逻辑输出键对应的允许路径。

        Args:
            declared_write_keys: 工作流步骤声明的逻辑 ``output_keys``。
            declared_write_paths: 工作流步骤显式声明的物理 ``write_paths``。

        Returns:
            去重排序后的 GraphState 路径前缀列表。
        """

        # 第一步：规范化显式物理路径，过滤空字符串。
        allowed = {
            str(item).strip()
            for item in declared_write_paths
            if str(item).strip()
        }

        # 第二步：取得 GraphState 顶层字段集合，用于识别直接字段声明。
        graph_fields = set(GraphStateSchema.model_fields)

        # 第三步：逐个把逻辑输出键映射到一个或多个物理路径。
        for raw_key in declared_write_keys:
            key = str(raw_key or "").strip()
            if not key:
                continue

            # 已知逻辑键使用集中别名表，包含规范路径和必要兼容投影。
            aliases = _DEFAULT_WRITE_ALIASES.get(key)
            if aliases:
                allowed.update(aliases)

            # 顶层真实字段或已经包含点号的物理路径可直接登记。
            elif key in graph_fields or "." in key:
                allowed.add(key)

            else:
                # 未登记的历史逻辑输出默认位于 contexts 下，维持旧工作流兼容。
                allowed.add(f"contexts.{key}")

        return sorted(allowed)

    @staticmethod
    def path_is_allowed(path: str, allowed_paths: Iterable[str]) -> bool:
        """判断具体变更路径是否落在任一允许路径前缀内。

        Args:
            path: 实际发生变化的叶子路径或集合路径。
            allowed_paths: 步骤允许写入的路径前缀。

        Returns:
            路径与前缀完全相等或位于其子路径时返回 ``True``。
        """

        # 点号边界防止允许 ``foo`` 时错误匹配 ``foobar``。
        return any(
            path == prefix or path.startswith(f"{prefix}.")
            for prefix in allowed_paths
        )

    def validate(
        self,
        *,
        changed_paths: Iterable[str],
        allowed_paths: Iterable[str],
    ) -> None:
        """验证全部实际变更是否符合步骤写入契约。

        Args:
            changed_paths: diff 检测出的实际变更路径。
            allowed_paths: 当前步骤允许的路径前缀。

        Raises:
            StateWriteContractViolation: 至少一个变更路径越界时抛出。
        """

        # 第一步：物化允许集合，避免一次性迭代器在多次路径判断中被耗尽。
        allowed = list(allowed_paths)

        # 第二步：收集所有不匹配任何允许前缀的真实变更。
        violations = [
            path
            for path in changed_paths
            if not self.path_is_allowed(path, allowed)
        ]

        # 第三步：一次性报告全部违规路径，避免调用方逐个修复后重复运行。
        if violations:
            raise StateWriteContractViolation(
                changed_paths=violations,
                allowed_paths=allowed,
            )


class GraphStateProjector:
    """只根据步骤声明的读取键构造确定性节点输入。"""

    def build_node_input(
        self,
        *,
        workflow: WorkflowDefinitionSchema,
        step: WorkflowStepSchema,
        state: GraphStateSchema,
    ) -> GraphNodeInputSchema:
        """把 GraphState 投影为当前节点允许读取的标准输入。

        Args:
            workflow: 当前业务工作流定义。
            step: 当前步骤定义。
            state: 执行节点前的规范图状态。

        Returns:
            带读取键、缺失键、状态修订和稳定输入哈希的节点输入对象。
        """

        # 第一步：分别保存解析值和缺失键，保持输入键顺序与步骤声明一致。
        values: Dict[str, Any] = {}
        missing: List[str] = []

        # 第二步：只解析 input_keys 声明的值，不把完整状态暴露为节点输入契约。
        for key in step.input_keys:
            found, value = self.resolve(state, key)
            if found:
                # 深拷贝阻止节点通过输入对象引用直接修改真实状态。
                values[key] = deepcopy(value)
            else:
                # 缺失键显式赋 None 并单独登记，便于节点做完整性判断。
                values[key] = None
                missing.append(key)

        # 第三步：构造只包含输入语义的哈希载荷，用于重放和审计比对。
        hash_payload = {
            "node_id": step.step_id,
            "workflow_id": workflow.workflow_id,
            "state_revision": state.graph_revision,
            "declared_read_keys": list(step.input_keys),
            "values": values,
            "missing_keys": missing,
        }

        # 第四步：创建框架无关节点输入，不携带完整 GraphState。
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

    def resolve(self, state: GraphStateSchema, key: str) -> Tuple[bool, Any]:
        """按兼容优先级解析一个工作流读取键。

        Args:
            state: 当前规范图状态。
            key: 步骤声明的逻辑读取键。

        Returns:
            ``(是否找到, 值)``。找到且值本身为 ``None`` 的顶层字段仍视为存在；
            别名值为 ``None`` 时视为未找到。
        """

        # 第一步：真实 GraphState 顶层字段优先，支持直接读取稳定 Schema 字段。
        if hasattr(state, key):
            return True, getattr(state, key)

        # 第二步：依次查询旧 contexts、通用生成输出、Agent 结果和工具结果映射。
        if key in state.contexts:
            return True, state.contexts[key]
        if key in state.generated_outputs:
            return True, state.generated_outputs[key]
        if key in state.agent_results:
            return True, state.agent_results[key]
        if key in state.tool_results:
            return True, state.tool_results[key]

        # 第三步：集中定义当前工作流仍使用的逻辑读取别名。
        aliases = {
            "project_input": state.context_bundle.business.project_input,
            "normalized_project_input": state.context_bundle.business.project_input,
            "source_materials": state.context_bundle.business.source_materials,
            "structured_facts": state.structured_facts,
            "evidence_contract": state.context_bundle.evidence.contract,
            "rag_context": state.context_bundle.evidence.context_text,
            "scheme_writer_output": state.contexts.get("scheme_writer_output"),
            "generated_draft": state.generated_draft,
            "rejected_artifact": state.rejected_artifact,
            "deliverable_final_result": state.deliverable_final_result,
            "final_result": state.final_result,
        }

        # 第四步：别名存在且当前有值时返回；空值不伪装成已准备输入。
        if key in aliases and aliases[key] is not None:
            return True, aliases[key]

        return False, None


class GraphStateDiffer:
    """比较状态副本，生成经过写入契约校验的业务增量。"""

    def __init__(
        self,
        *,
        write_contract: GraphStateWriteContract | None = None,
    ) -> None:
        """创建状态差异器。

        Args:
            write_contract: 自定义写入契约；未提供时使用默认别名规则。
        """

        self.write_contract = write_contract or GraphStateWriteContract()

    def diff(
        self,
        *,
        node_id: str,
        before: GraphStateSchema,
        after: GraphStateSchema,
        declared_write_keys: Iterable[str],
        declared_write_paths: Iterable[str] = (),
        discard_legacy_engine_owned_mutations: bool = True,
    ) -> GraphStateDeltaSchema:
        """生成并校验一个节点执行产生的 GraphState 增量。

        Args:
            node_id: 产生差异的步骤标识。
            before: 节点执行前状态快照。
            after: 节点在副本上执行后的状态。
            declared_write_keys: 步骤声明的逻辑输出键。
            declared_write_paths: 步骤显式声明的物理写路径。
            discard_legacy_engine_owned_mutations: 是否移除旧 Agent 对引擎所有字段的
                重复写入。

        Returns:
            带变更值、路径、修订号和前后状态哈希的标准增量。

        Raises:
            StateWriteContractViolation: 节点修改未声明路径时抛出。
        """

        # 第一步：把前后 Pydantic 状态转换为可比较、可哈希的 Python 数据。
        before_data = before.model_dump(mode="python")
        after_data = after.model_dump(mode="python")

        # 第二步：迁移期间丢弃旧 Agent 对引擎拥有错误字段的重复镜像写入。
        if discard_legacy_engine_owned_mutations:
            self._discard_legacy_engine_owned_mutations(before_data, after_data)

        # 第三步：按顶层根字段收集替换值，并递归计算实际叶子变更路径。
        set_values: Dict[str, Any] = {}
        changed_paths: List[str] = []
        for key in sorted(set(before_data) | set(after_data)):
            # 图修订、节点位置和历史由执行器维护，不进入业务节点 delta。
            if key in _GRAPH_CONTROL_FIELDS:
                continue

            left = before_data.get(key)
            right = after_data.get(key)
            if left != right:
                # 当前 delta 使用顶层整体替换，路径列表仍保留精细契约校验语义。
                set_values[key] = deepcopy(right)
                changed_paths.extend(self.changed_paths(left, right, key))

        # 第四步：去重排序实际路径，使契约错误、哈希和持久化结果确定。
        changed_paths = sorted(set(changed_paths))

        # 第五步：解析当前节点允许的完整物理路径集合。
        allowed_paths = self.write_contract.resolve_allowed_paths(
            declared_write_keys=declared_write_keys,
            declared_write_paths=declared_write_paths,
        )

        # 第六步：任何越界状态修改都会在创建 delta 前失败。
        self.write_contract.validate(
            changed_paths=changed_paths,
            allowed_paths=allowed_paths,
        )

        # 第七步：记录实际发生变化的顶层根，供审计和调试使用。
        observed_roots = sorted(set(set_values))

        # 第八步：计算受控提交修订；即使没有普通字段变化，提交语义仍产生下一修订。
        base_revision = before.graph_revision
        next_revision = base_revision + 1

        # 第九步：计算提交前完整状态哈希。
        state_before_hash = stable_graph_hash(before_data)

        # 第十步：在内存中模拟应用顶层替换和引擎控制字段，得到预期提交后哈希。
        simulated = deepcopy(before_data)
        simulated.update(set_values)
        simulated["graph_revision"] = next_revision
        simulated["current_node_id"] = node_id
        state_after_hash = stable_graph_hash(simulated)

        # 第十一步：构造不含 delta 自身哈希的稳定载荷。
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

        # 第十二步：创建标准增量，并对整个载荷计算独立哈希防止内容漂移。
        return GraphStateDeltaSchema(
            **payload,
            delta_sha256=stable_graph_hash(payload),
            metadata={
                "delta_mode": "validated_top_level_replace_v2",
                "compatibility_adapter": "legacy_agent_copy_diff_v1",
                "write_contract": "declared_path_prefix_v1",
            },
        )

    @staticmethod
    def _discard_legacy_engine_owned_mutations(
        before_data: Dict[str, Any],
        after_data: Dict[str, Any],
    ) -> None:
        """在业务 diff 前移除旧 Agent 对引擎错误集合的镜像修改。

        ``AgentResult.error`` 是节点错误事实源。提交决定完成后，
        ``WorkflowStateController`` 才把它写入顶层和 runtime 错误集合；若保留 Agent
        内部旧写入，会越过节点写入边界并重复同一个错误。

        Args:
            before_data: 节点执行前状态字典。
            after_data: 将被原地规范化的节点执行后状态字典。
        """

        # 第一步：强制 after 顶层错误集合恢复到执行前快照。
        after_data["errors"] = deepcopy(before_data.get("errors", []))

        # 第二步：定位前后规范 runtime 子字典；Schema 正常情况下两者都存在。
        before_runtime = (
            (before_data.get("context_bundle") or {}).get("runtime") or {}
        )
        after_runtime = (
            (after_data.get("context_bundle") or {}).get("runtime") or {}
        )

        # 第三步：同样恢复 runtime.errors，留给引擎在提交决策后统一写入。
        after_runtime["errors"] = deepcopy(before_runtime.get("errors", []))

    def restrict_delta(
        self,
        *,
        before: GraphStateSchema,
        proposed: GraphStateDeltaSchema,
        declared_write_keys: Iterable[str],
        declared_write_paths: Iterable[str],
    ) -> GraphStateDeltaSchema:
        """把拟议增量裁剪为失败场景显式允许保留的路径。

        Args:
            before: 节点执行前规范状态。
            proposed: 成功路径写入契约下已经生成的完整拟议增量。
            declared_write_keys: 失败提交允许的逻辑输出键。
            declared_write_paths: 失败提交允许的物理路径。

        Returns:
            只包含允许路径、重新计算修订和哈希的全新增量。
        """

        # 第一步：解析失败提交场景的允许路径集合。
        allowed_paths = self.write_contract.resolve_allowed_paths(
            declared_write_keys=declared_write_keys,
            declared_write_paths=declared_write_paths,
        )

        # 第二步：重建拟议提交后的数据视图和初始未修改视图。
        before_data = before.model_dump(mode="python")
        proposed_data = deepcopy(before_data)
        proposed_data.update(deepcopy(proposed.set_values))
        restricted_data = deepcopy(before_data)

        # 第三步：只复制拟议变更中命中失败允许前缀的具体路径。
        for changed_path in proposed.changed_paths:
            if self.write_contract.path_is_allowed(changed_path, allowed_paths):
                self._copy_path(proposed_data, restricted_data, changed_path)

        # 第四步：用原状态类型验证裁剪后的完整数据，确保嵌套 Schema 仍合法。
        restricted_state = before.__class__.model_validate(restricted_data)

        # 第五步：重新走标准 diff，生成与裁剪内容一致的新哈希和路径集合。
        return self.diff(
            node_id=proposed.node_id,
            before=before,
            after=restricted_state,
            declared_write_keys=declared_write_keys,
            declared_write_paths=allowed_paths,
        )

    @staticmethod
    def _copy_path(source: Dict[str, Any], target: Dict[str, Any], path: str) -> None:
        """把一个点分隔路径的值或删除状态从源数据复制到目标数据。

        Args:
            source: 代表拟议提交后状态的字典。
            target: 从执行前状态复制、将被原地修改的字典。
            path: 要保留的具体点分隔路径。
        """

        # 第一步：拆分路径并从两个根字典同步向父级移动。
        parts = path.split(".")
        source_cursor: Any = source
        target_cursor: Any = target

        for part in parts[:-1]:
            # 源路径不存在时没有可复制内容，保持目标原状。
            if not isinstance(source_cursor, dict) or part not in source_cursor:
                return
            source_cursor = source_cursor[part]

            # 目标父级不再是字典时无法安全建立子路径。
            if not isinstance(target_cursor, dict):
                return

            # 目标缺少中间字典时按路径创建，支持拟议新增嵌套对象。
            child = target_cursor.get(part)
            if not isinstance(child, dict):
                child = {}
                target_cursor[part] = child
            target_cursor = child

        # 第二步：处理叶子值；源存在则深拷贝，源缺失则表达删除。
        leaf = parts[-1]
        if isinstance(source_cursor, dict) and leaf in source_cursor:
            target_cursor[leaf] = deepcopy(source_cursor[leaf])
        elif isinstance(target_cursor, dict):
            target_cursor.pop(leaf, None)

    def changed_paths(self, before: Any, after: Any, prefix: str = "") -> List[str]:
        """确定性返回两个 JSON-like 值之间的变更路径。

        Args:
            before: 变更前值。
            after: 变更后值。
            prefix: 当前递归根路径；顶层调用可为空。

        Returns:
            排序递归产生的叶子路径列表；列表内容变化按整个列表路径记录。
        """

        # 第一步：规范化根前缀，避免输出多余点号。
        root = prefix.strip(".")

        # 第二步：顶层都是字典时逐键展开，不生成空根路径。
        if not root and isinstance(before, dict) and isinstance(after, dict):
            paths: List[str] = []
            for key in sorted(set(before) | set(after), key=str):
                child = str(key)
                if key not in before or key not in after:
                    paths.append(child)
                else:
                    paths.extend(
                        self.changed_paths(before[key], after[key], child)
                    )
            return paths

        # 第三步：非空根或非双字典场景委托内部递归实现。
        return self._changed_paths(before, after, root)

    def _changed_paths(self, before: Any, after: Any, prefix: str) -> List[str]:
        """递归计算当前非空前缀下的变化路径。

        Args:
            before: 当前路径变更前值。
            after: 当前路径变更后值。
            prefix: 当前完整点分隔路径。

        Returns:
            当前路径下的实际变化路径集合。
        """

        # 值完全相等时没有变化。
        if before == after:
            return []

        # 两侧都是字典时继续下钻到具体键。
        if isinstance(before, dict) and isinstance(after, dict):
            paths: List[str] = []
            for key in sorted(set(before) | set(after), key=str):
                child = f"{prefix}.{key}"
                if key not in before or key not in after:
                    paths.append(child)
                else:
                    paths.extend(self.changed_paths(before[key], after[key], child))

            # 理论上不相等字典至少有一个子变化；回退前缀保护异常对象语义。
            return paths or [prefix]

        # 列表不按索引细分，任何增删改都归属整个列表字段写入。
        if isinstance(before, list) and isinstance(after, list):
            return [prefix]

        # 标量、类型变化或其他 JSON-like 值按当前路径记录。
        return [prefix]


class GraphStateApplier:
    """原子校验并提交一个受控业务状态增量。

    ``graph_revision`` 只表示通过本提交器验证的业务 delta 序号，不是 GraphState 中
    每个运行时或生命周期修改的全局版本。
    """

    def __init__(
        self,
        *,
        write_contract: GraphStateWriteContract | None = None,
    ) -> None:
        """创建状态增量提交器。

        Args:
            write_contract: 自定义写入契约；未提供时使用默认实现。
        """

        self.write_contract = write_contract or GraphStateWriteContract()

    def apply(
        self,
        state: GraphStateSchema,
        delta: GraphStateDeltaSchema,
    ) -> None:
        """验证基线和写入契约后，原地提交一个完整 delta。

        Args:
            state: 将被原地更新的当前规范图状态。
            delta: 基于该状态某一修订生成的标准增量。

        Raises:
            ValueError: 当前修订与 delta 基线冲突，或提交后状态哈希与 delta 预期
                不一致时抛出。
            StateWriteContractViolation: delta 声明的实际路径超出允许路径时抛出。
        """

        # 第一步：执行乐观并发检查，旧基线增量不能覆盖更新后的真实状态。
        if state.graph_revision != delta.base_revision:
            raise ValueError(
                "graph revision conflict: "
                f"state={state.graph_revision}, delta={delta.base_revision}"
            )

        # 第二步：即使 delta 已由 differ 校验，提交边界仍独立复核写入契约。
        self.write_contract.validate(
            changed_paths=delta.changed_paths,
            allowed_paths=delta.declared_write_paths,
        )

        # 第三步：在独立字典上合并顶层替换值，不直接逐字段修改真实状态。
        merged = state.model_dump(mode="python")
        merged.update(deepcopy(delta.set_values))

        # 第四步：由提交器写入下一受控修订和当前节点标识。
        merged["graph_revision"] = delta.next_revision
        merged["current_node_id"] = delta.node_id

        # 第五步：先用完整 GraphState Schema 验证合并结果，避免半提交非法状态。
        validated = state.__class__.model_validate(merged)

        # 第六步：验证成功后才把全部字段深拷贝回原对象，保持调用方对象身份不变。
        for field_name in state.__class__.model_fields:
            setattr(state, field_name, deepcopy(getattr(validated, field_name)))

        # 第七步：对实际提交后的完整状态重新计算哈希，复核模拟结果与真实结果一致。
        actual_hash = stable_graph_hash(state.model_dump(mode="python"))
        if actual_hash != delta.state_sha256_after:
            raise ValueError("committed graph state hash does not match delta")
