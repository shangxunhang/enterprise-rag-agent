# =============================================================================
# 中文阅读说明：Agent 与 Workflow 模块，负责任务路由、状态编排、工具调用和结果协议。
# 主要定义：RoutingDecision、WorkflowCatalog、WorkflowRouter。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Workflow routing strategies separated from SupervisorAgent."""

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


# 阅读注释（类）：封装 routing decision，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class RoutingDecision:
    """封装 routing decision，集中封装相关状态、依赖和行为。"""
    task_type: str
    model_response: Optional[ModelResponseSchema]
    metadata: Dict[str, Any]


# 阅读注释（类）：封装 工作流 catalog，集中封装相关状态、依赖和行为。
class WorkflowCatalog:
    """封装 工作流 catalog，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 WorkflowCatalog，保存运行所需的依赖、配置或状态。
    def __init__(self, workflows: Mapping[str, WorkflowDefinitionSchema]) -> None:
        """初始化 WorkflowCatalog，保存运行所需的依赖、配置或状态。

        参数:
            workflows: workflows，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：dict。
        """
        self._workflows = dict(workflows)

    # 阅读注释（函数）：处理 任务 types 相关逻辑。
    @property
    def task_types(self) -> list[str]:
        """处理 任务 types 相关逻辑。

        返回:
            list[str]

        阅读提示:
            主要直接调用：list, self._workflows.keys。
        """
        return list(self._workflows.keys())

    # 阅读注释（函数）：获取 WorkflowCatalog。
    def get(self, task_type: str) -> WorkflowDefinitionSchema:
        """获取 WorkflowCatalog。

        参数:
            task_type: 任务 类型，具体约束请结合类型标注和调用方确认。

        返回:
            WorkflowDefinitionSchema

        阅读提示:
            主要直接调用：KeyError。
        """
        if task_type not in self._workflows:
            raise KeyError(f"No workflow found for task_type: {task_type}")
        return self._workflows[task_type]

    # 阅读注释（函数）：处理 len 相关逻辑。
    def __len__(self) -> int:
        """处理 len 相关逻辑。

        返回:
            int

        阅读提示:
            主要直接调用：len。
        """
        return len(self._workflows)

    # 阅读注释（函数）：处理 contains 相关逻辑。
    def contains(self, task_type: str) -> bool:
        """处理 contains 相关逻辑。

        参数:
            task_type: 任务 类型，具体约束请结合类型标注和调用方确认。

        返回:
            bool
        """
        return task_type in self._workflows


# 阅读注释（类）：封装 工作流 路由器，集中封装相关状态、依赖和行为。
class WorkflowRouter:
    """封装 工作流 路由器，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 WorkflowRouter，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        catalog: WorkflowCatalog,
        *,
        model_gateway: Optional[ModelGateway] = None,
        model_name: str = "fake_llm",
        enable_llm_routing: bool = True,
        caller_agent: str = "SupervisorAgent",
    ) -> None:
        """初始化 WorkflowRouter，保存运行所需的依赖、配置或状态。

        参数:
            catalog: catalog，具体约束请结合类型标注和调用方确认。
            model_gateway: 模型 网关，具体约束请结合类型标注和调用方确认。
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
            enable_llm_routing: enable LLM routing，具体约束请结合类型标注和调用方确认。
            caller_agent: caller Agent，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.catalog = catalog
        self.model_gateway = model_gateway
        self.model_name = model_name
        self.enable_llm_routing = enable_llm_routing
        self.caller_agent = caller_agent

    # 阅读注释（函数）：提取 JSON object。
    @staticmethod
    def extract_json_object(text: str) -> Dict[str, Any]:
        """提取 JSON object。

        参数:
            text: 待处理文本。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：text.strip, json.loads, isinstance, re.search, ValueError, match.group。
        """
        text = text.strip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in supervisor routing output")
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}

    # 阅读注释（函数）：为 WorkflowRouter 选择执行路径。
    def route(self, task: TaskSchema) -> RoutingDecision:
        """为 WorkflowRouter 选择执行路径。

        参数:
            task: 待执行的任务对象。

        返回:
            RoutingDecision

        阅读提示:
            主要直接调用：len, self.catalog.contains, metadata.update, print, RoutingDecision, ModelRequestSchema, self.model_gateway.generate, self.extract_json_object。
        """
        fallback = task.task_type
        metadata: Dict[str, Any] = {
            "routing_mode": "rule_fallback",
            "selected_task_type": fallback,
            "reason": "llm_routing_disabled_or_unavailable",
            "allowed_task_types": self.catalog.task_types,
        }
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

        if not self.enable_llm_routing or self.model_gateway is None:
            return RoutingDecision(fallback, None, metadata)

        print(
            f"[Supervisor] 开始 LLM 路由: task_id={task.task_id}, "
            f"model_role={ModelRole.SUPERVISOR_ROUTING.value}",
            flush=True,
        )
        system_prompt = (
            "你是企业级 Agent 工作流路由器。只能从允许的 task_type 中选择一个，"
            "只返回 JSON。"
        )
        prompt = (
            f"允许的 task_type：{self.catalog.task_types}\n"
            f"默认 task_type：{task.task_type}\n"
            f"用户输入：{task.user_input}\n"
            '输出：{"task_type":"scheme_generation","reason":"..."}'
        )
        call_id = f"model_call_{task.run_id}_supervisor_router"
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
        print(
            f"[Supervisor] LLM 路由结束: success={response.success}, latency_ms={response.latency_ms}",
            flush=True,
        )
        if not response.success:
            metadata.update(
                routing_mode="llm_failed_fallback",
                reason=response.error_message or "llm_call_failed",
                model_call_id=call_id,
            )
            return RoutingDecision(fallback, response, metadata)

        try:
            parsed = self.extract_json_object(response.content)
            candidate = parsed.get("task_type")
            if self.catalog.contains(candidate):
                metadata.update(
                    routing_mode="llm_json",
                    selected_task_type=candidate,
                    reason=parsed.get("reason") or "llm_selected",
                    model_call_id=call_id,
                )
                return RoutingDecision(candidate, response, metadata)
            metadata.update(
                routing_mode="llm_invalid_fallback",
                reason=f"unsupported_task_type_from_llm: {candidate}",
                model_call_id=call_id,
            )
        except Exception as exc:
            metadata.update(
                routing_mode="llm_unparseable_fallback",
                reason=str(exc),
                model_call_id=call_id,
                raw_model_output=response.content[:1000],
            )
        return RoutingDecision(fallback, response, metadata)
