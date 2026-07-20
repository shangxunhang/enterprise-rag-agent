"""Adapt ModelGatewayPort to the small text-generation interface."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from contracts.model_gateway import ModelGatewayPort
from schemas.model import ModelRequestSchema


class ModelGatewayTextGenerator:
    """Translate retrieval-time text calls into canonical model requests."""

    def __init__(
        self,
        *,
        model_gateway: ModelGatewayPort,
        model_name: str,
        default_purpose: str = "rag_internal_generation",
    ) -> None:
        self.model_gateway = model_gateway
        self.model_name = str(model_name or "").strip()
        if not self.model_name:
            raise ValueError("model_name is required for RAG model gateway calls")
        self.default_purpose = str(default_purpose or "rag_internal_generation")

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_new_tokens: int = 384,
        temperature: float = 0.0,
        top_p: float = 0.9,
        do_sample: bool = False,
        runtime_context: dict[str, Any] | None = None,
        call_purpose: str | None = None,
        **_: Any,
    ) -> str:
        context = dict(runtime_context or {})
        task_id = str(context.get("task_id") or "rag_internal_task")
        run_id = str(context.get("run_id") or "rag_internal_run")
        purpose = str(call_purpose or self.default_purpose)
        call_id = f"model_call_{run_id}_{purpose}_{uuid4().hex[:8]}"
        request = ModelRequestSchema(
            model_call_id=call_id,
            task_id=task_id,
            run_id=run_id,
            model_name=self.model_name,
            caller_agent=str(context.get("caller_agent") or "RAGService"),
            prompt=str(prompt),
            system_prompt=system_prompt,
            temperature=float(temperature),
            max_tokens=max(1, int(max_new_tokens)),
            created_at=datetime.now(timezone.utc).isoformat(),
            extra={
                "call_purpose": purpose,
                "retrieval_scope": context.get("retrieval_scope"),
                "generation_params": {
                    "top_p": float(top_p),
                    "do_sample": bool(do_sample),
                },
                **dict(context.get("model_extra") or {}),
            },
        )
        response = self.model_gateway.generate(request)
        if not response.success:
            message = response.error_message or (
                response.error.message if response.error is not None else ""
            )
            raise RuntimeError(message or "RAG model gateway call failed")
        return str(response.content or "").strip()
