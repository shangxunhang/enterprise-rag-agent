"""Shared runtime adapters for configured quality plugins.

The plugins remain independent of SchemeWriter internals.  A small adapter
translates the generic ``TextGenerator`` surface expected by the RAG judges to
``ModelGateway`` requests when an Agent-level section check or repair runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from schemas.model import ModelRequestSchema


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelGatewayTextGenerator:
    """Expose ``ModelGateway`` through the minimal ``generate`` protocol."""

    def __init__(
        self,
        *,
        model_gateway: Any,
        model_name: str,
        runtime_context: dict[str, Any] | None,
        purpose: str,
        call_suffix: str,
    ) -> None:
        self.model_gateway = model_gateway
        self.model_name = str(model_name)
        self.runtime_context = dict(runtime_context or {})
        self.purpose = str(purpose)
        self.call_suffix = str(call_suffix)

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_new_tokens: int = 384,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        task_id = str(self.runtime_context.get("task_id") or "quality_task")
        run_id = str(self.runtime_context.get("run_id") or "quality_run")
        section_id = str(self.runtime_context.get("section_id") or "section")
        section_title = str(self.runtime_context.get("section_title") or "")
        model_call_id = (
            f"model_call_{run_id}_{section_id}_{self.call_suffix}_"
            f"{uuid4().hex[:8]}"
        )
        request = ModelRequestSchema(
            model_call_id=model_call_id,
            task_id=task_id,
            run_id=run_id,
            model_name=self.model_name,
            caller_agent=str(
                self.runtime_context.get("caller_agent") or "SchemeWriterAgent"
            ),
            prompt=str(prompt),
            system_prompt=system_prompt,
            temperature=float(temperature),
            max_tokens=int(max_new_tokens),
            created_at=_now_iso(),
            extra={
                "call_purpose": self.purpose,
                "section_id": section_id,
                "section_title": section_title,
                "quality_plugin": True,
                **dict(self.runtime_context.get("model_extra") or {}),
            },
        )
        response = self.model_gateway.generate(request)
        shared_state = self.runtime_context.get("shared_state")
        if shared_state is not None and hasattr(shared_state, "generated_outputs"):
            shared_state.generated_outputs[model_call_id] = response.model_dump()
        if not response.success:
            raise RuntimeError(
                response.error_message
                or (response.error.message if response.error else "quality model call failed")
            )
        return str(response.content or "").strip()


def resolve_quality_generator(
    *,
    build_context: dict[str, Any],
    runtime_context: dict[str, Any] | None,
    purpose: str,
    call_suffix: str,
) -> Any | None:
    """Resolve an Agent-aware generator first, then the standalone RAG one."""

    model_gateway = build_context.get("model_gateway")
    model_name = build_context.get("model_name")
    if model_gateway is not None and model_name:
        return ModelGatewayTextGenerator(
            model_gateway=model_gateway,
            model_name=str(model_name),
            runtime_context=runtime_context,
            purpose=purpose,
            call_suffix=call_suffix,
        )
    return build_context.get("quality_llm_generator")
