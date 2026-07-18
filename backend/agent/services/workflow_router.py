"""Workflow routing strategies separated from SupervisorAgent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from agent.runtime.workflow_schema import WorkflowDefinitionSchema
from model_gateway.model_gateway import ModelGateway
from schemas.model import ModelRequestSchema, ModelResponseSchema
from schemas.task import TaskSchema


@dataclass(frozen=True)
class RoutingDecision:
    task_type: str
    model_response: Optional[ModelResponseSchema]
    metadata: Dict[str, Any]


class WorkflowCatalog:
    def __init__(self, workflows: Mapping[str, WorkflowDefinitionSchema]) -> None:
        self._workflows = dict(workflows)

    @property
    def task_types(self) -> list[str]:
        return list(self._workflows.keys())

    def get(self, task_type: str) -> WorkflowDefinitionSchema:
        if task_type not in self._workflows:
            raise KeyError(f"No workflow found for task_type: {task_type}")
        return self._workflows[task_type]

    def __len__(self) -> int:
        return len(self._workflows)

    def contains(self, task_type: str) -> bool:
        return task_type in self._workflows


class WorkflowRouter:
    def __init__(
        self,
        catalog: WorkflowCatalog,
        *,
        model_gateway: Optional[ModelGateway] = None,
        model_name: str = "fake_llm",
        enable_llm_routing: bool = True,
        caller_agent: str = "SupervisorAgent",
    ) -> None:
        self.catalog = catalog
        self.model_gateway = model_gateway
        self.model_name = model_name
        self.enable_llm_routing = enable_llm_routing
        self.caller_agent = caller_agent

    @staticmethod
    def extract_json_object(text: str) -> Dict[str, Any]:
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

    def route(self, task: TaskSchema) -> RoutingDecision:
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
            f"[Supervisor] 开始 LLM 路由: task_id={task.task_id}, model={self.model_name}",
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
        request = ModelRequestSchema(
            model_call_id=f"model_call_{task.run_id}_supervisor_router",
            task_id=task.task_id,
            run_id=task.run_id,
            model_name=self.model_name,
            caller_agent=self.caller_agent,
            system_prompt=system_prompt,
            prompt=prompt,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=256,
            created_at=task.created_at,
            extra={
                "call_purpose": "workflow_routing",
                "allowed_task_types": self.catalog.task_types,
            },
        )
        response = self.model_gateway.generate(request)
        print(
            f"[Supervisor] LLM 路由结束: success={response.success}, latency_ms={response.latency_ms}",
            flush=True,
        )
        if not response.success:
            metadata.update(
                routing_mode="llm_failed_fallback",
                reason=response.error_message or "llm_call_failed",
                model_call_id=request.model_call_id,
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
                    model_call_id=request.model_call_id,
                )
                return RoutingDecision(candidate, response, metadata)
            metadata.update(
                routing_mode="llm_invalid_fallback",
                reason=f"unsupported_task_type_from_llm: {candidate}",
                model_call_id=request.model_call_id,
            )
        except Exception as exc:
            metadata.update(
                routing_mode="llm_unparseable_fallback",
                reason=str(exc),
                model_call_id=request.model_call_id,
                raw_model_output=response.content[:1000],
            )
        return RoutingDecision(fallback, response, metadata)
