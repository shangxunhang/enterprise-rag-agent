"""把工作流目录和路由策略从 ``SupervisorAgent`` 中分离。

路由优先使用确定性规则：只有一个匹配工作流时直接选择；LLM 路由被禁用、不可用、
调用失败、输出不可解析或返回未注册类型时，都回退到任务原始 ``task_type``。模型
只能从目录允许集合中选择，不能动态创建工作流名称。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from model_gateway.call_boundary import ModelCallBoundary
from model_gateway.model_contract import ModelRole
from model_gateway.model_gateway import ModelGateway
from schemas.model import ModelResponseSchema
from schemas.task import TaskSchema


@dataclass(frozen=True)
class RoutingDecision:
    """保存一次工作流路由的最终选择及可审计上下文。

    Attributes:
        task_type: 最终选择的已注册任务类型。
        model_response: 使用 LLM 路由时的标准模型响应；纯规则路由时为 ``None``。
        metadata: 路由模式、原因、允许集合和模型调用标识等审计信息。
    """

    task_type: str
    model_response: Optional[ModelResponseSchema]
    metadata: Dict[str, Any]


class WorkflowCatalog:
    """维护任务类型到工作流定义的只读运行时目录。

    Attributes:
        _workflows: 构造时复制的任务类型与工作流定义映射。
    """

    def __init__(self, workflows: Mapping[str, WorkflowDefinitionSchema]) -> None:
        """复制调用方提供的工作流映射。

        Args:
            workflows: 以可路由 ``task_type`` 为键的工作流定义集合。
        """

        # 复制映射，避免调用方随后修改原对象导致目录内容静默变化。
        self._workflows = dict(workflows)

    @property
    def task_types(self) -> list[str]:
        """按目录插入顺序返回所有允许路由的任务类型。"""

        return list(self._workflows.keys())

    def get(self, task_type: str) -> WorkflowDefinitionSchema:
        """取得指定任务类型的工作流定义。

        Args:
            task_type: 已选择的任务类型。

        Returns:
            对应工作流定义。

        Raises:
            KeyError: 目录不存在该任务类型时抛出。
        """

        if task_type not in self._workflows:
            raise KeyError(f"No workflow found for task_type: {task_type}")
        return self._workflows[task_type]

    def __len__(self) -> int:
        """返回目录中的工作流数量。"""

        return len(self._workflows)

    def contains(self, task_type: str) -> bool:
        """判断目录是否包含指定任务类型。"""

        return task_type in self._workflows


class WorkflowRouter:
    """在确定性规则和受约束 LLM 路由之间选择工作流。

    Attributes:
        catalog: 当前可用工作流目录。
        model_gateway: 可选模型网关；为空时只能使用规则和回退。
        model_name: 历史兼容模型名称字段，实际角色路由由模型网关处理。
        enable_llm_routing: 是否允许在多工作流场景调用模型。
        caller_agent: 记录到模型调用上下文中的上游 Agent 名称。
    """

    def __init__(
        self,
        catalog: WorkflowCatalog,
        *,
        model_gateway: Optional[ModelGateway] = None,
        model_name: str = "fake_llm",
        enable_llm_routing: bool = True,
        caller_agent: str = "SupervisorAgent",
    ) -> None:
        """创建工作流路由器并保存路由依赖。

        Args:
            catalog: 已注册工作流目录。
            model_gateway: 可选模型网关。
            model_name: 兼容配置中的模型名称。
            enable_llm_routing: 是否启用模型路由。
            caller_agent: 发起路由调用的 Agent 名称。
        """

        self.catalog = catalog
        self.model_gateway = model_gateway
        self.model_name = model_name
        self.enable_llm_routing = enable_llm_routing
        self.caller_agent = caller_agent

    @staticmethod
    def extract_json_object(text: str) -> Dict[str, Any]:
        """从模型文本中解析一个 JSON 对象。

        Args:
            text: 模型返回的原始文本。

        Returns:
            解析出的字典；合法 JSON 但顶层不是对象时返回空字典。

        Raises:
            ValueError: 文本既不是完整 JSON，也不包含可提取的花括号对象时抛出。
            json.JSONDecodeError: 提取到的候选对象不是合法 JSON 时抛出。
        """

        # 第一步：优先把整个输出按 JSON 解析，严格遵守“只返回 JSON”的提示约束。
        text = text.strip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            # 完整解析失败后再进入兼容提取路径。
            pass

        # 第二步：兼容模型在 JSON 前后附加说明文本的情况，提取最外层对象候选。
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in supervisor routing output")

        # 第三步：再次使用标准 JSON 解析器校验候选内容，不执行 eval 等代码解释。
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}

    def route(self, task: TaskSchema) -> RoutingDecision:
        """为任务选择一个已注册工作流类型。

        Args:
            task: 包含默认 ``task_type`` 和用户请求的标准任务。

        Returns:
            始终包含最终任务类型和路由元数据的 ``RoutingDecision``。
        """

        # 第一步：把任务声明类型作为所有异常情况下的确定性回退值。
        fallback = task.task_type

        # 第二步：预建规则回退元数据，使每条提前返回路径都可审计。
        metadata: Dict[str, Any] = {
            "routing_mode": "rule_fallback",
            "selected_task_type": fallback,
            "reason": "llm_routing_disabled_or_unavailable",
            "allowed_task_types": self.catalog.task_types,
        }

        # 第三步：目录只有一个且与任务类型匹配时直接选择，避免无意义模型调用。
        if len(self.catalog) == 1 and self.catalog.contains(fallback):
            metadata.update(
                routing_mode="single_workflow_direct",
                selected_task_type=fallback,
                reason="only_one_workflow_registered",
            )
            print(
                f"[Supervisor] 单工作流直连: task_type={fallback}; 跳过 LLM 路由",
                flush=True,
            )
            return RoutingDecision(fallback, None, metadata)

        # 第四步：模型路由未启用或网关未装配时，保留任务原始类型。
        if not self.enable_llm_routing or self.model_gateway is None:
            return RoutingDecision(fallback, None, metadata)

        # 第五步：记录即将发生的 Supervisor 路由模型调用。
        print(
            f"[Supervisor] 开始 LLM 路由: task_id={task.task_id}, "
            f"model_role={ModelRole.SUPERVISOR_ROUTING.value}",
            flush=True,
        )

        # 第六步：系统提示限制模型只能从允许集合中选择且只能返回 JSON。
        system_prompt = (
            "你是企业级 Agent 工作流路由器。只能从允许的 task_type 中选择一个，"
            "只返回 JSON。"
        )

        # 第七步：将允许集合、默认值和用户请求组成最小路由输入。
        prompt = (
            f"允许的 task_type：{self.catalog.task_types}\n"
            f"默认 task_type：{task.task_type}\n"
            f"用户输入：{task.user_input}\n"
            '输出：{"task_type":"scheme_generation","reason":"..."}'
        )

        # 第八步：构造可重复推导的调用标识，便于轨迹和用量归因。
        call_id = f"model_call_{task.run_id}_supervisor_router"

        # 第九步：通过统一调用边界绑定模型角色、任务身份和默认调用用途。
        boundary = ModelCallBoundary(
            model_gateway=self.model_gateway,
            model_role=ModelRole.SUPERVISOR_ROUTING,
            runtime_context={
                "task_id": task.task_id,
                "workflow_run_id": task.run_id,
                "caller_agent": self.caller_agent,
            },
            default_purpose="workflow_routing",
            call_suffix="supervisor_router",
        )

        # 第十步：使用零温度和较小输出上限执行结构化路由生成。
        response = boundary.generate_response(
            prompt,
            system_prompt=system_prompt,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_new_tokens=256,
            created_at=task.created_at,
            model_call_id=call_id,
            model_extra={
                "allowed_task_types": self.catalog.task_types,
            },
        )

        # 第十一步：输出调用完成状态，便于本地 demo 直接观察路由延迟。
        print(
            f"[Supervisor] LLM 路由结束: success={response.success}, latency_ms={response.latency_ms}",
            flush=True,
        )

        # 第十二步：模型调用失败时保留默认类型，并记录真实错误原因和调用标识。
        if not response.success:
            metadata.update(
                routing_mode="llm_failed_fallback",
                reason=response.error_message or "llm_call_failed",
                model_call_id=call_id,
            )
            return RoutingDecision(fallback, response, metadata)

        try:
            # 第十三步：解析模型 JSON，并读取候选 task_type。
            parsed = self.extract_json_object(response.content)
            candidate = parsed.get("task_type")

            # 第十四步：只有目录真实包含候选类型时才接受模型选择。
            if self.catalog.contains(candidate):
                metadata.update(
                    routing_mode="llm_json",
                    selected_task_type=candidate,
                    reason=parsed.get("reason") or "llm_selected",
                    model_call_id=call_id,
                )
                return RoutingDecision(candidate, response, metadata)

            # 第十五步：模型返回未注册类型时按非法选择回退，不动态创建工作流。
            metadata.update(
                routing_mode="llm_invalid_fallback",
                reason=f"unsupported_task_type_from_llm: {candidate}",
                model_call_id=call_id,
            )
        except Exception as exc:
            # 第十六步：解析异常时保留截断原始输出供诊断，同时避免无限写入轨迹。
            metadata.update(
                routing_mode="llm_unparseable_fallback",
                reason=str(exc),
                model_call_id=call_id,
                raw_model_output=response.content[:1000],
            )

        # 第十七步：所有无效模型结果最终都返回任务原始类型和完整模型响应。
        return RoutingDecision(fallback, response, metadata)
