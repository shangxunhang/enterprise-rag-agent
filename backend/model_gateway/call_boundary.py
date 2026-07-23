"""Canonical small text-generation boundary over ModelGateway.

Both RAG internals and enterprise-document quality plugins use this adapter.
It owns request construction and lineage propagation; provider routing remains in
``ModelGateway`` and budget semantics remain injectable from the application.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from contracts.model_gateway import ModelGatewayPort
from model_gateway.model_contract import ModelRole
from schemas.model import ModelRequestSchema, ModelResponseSchema

BudgetHook = Callable[[ModelRequestSchema], None]
ResponseHook = Callable[[ModelRequestSchema, ModelResponseSchema], None]


class ModelCallBudgetExceeded(RuntimeError):
    """Base exception for hard model-call budget rejection.

    RAG internals use this gateway-owned base class to avoid converting an
    application safety fuse into a deterministic fallback success.
    """


def _coerce_role(value: ModelRole | str | None) -> ModelRole | None:
    if value is None:
        return None
    if isinstance(value, ModelRole):
        return value
    return ModelRole(str(value))


_PURPOSE_ROLE_MAP: tuple[tuple[str, ModelRole], ...] = (
    ("query_rewrite", ModelRole.QUERY_REWRITE),
    ("hyde", ModelRole.HYDE),
    ("self_rag_check", ModelRole.RETRIEVAL_JUDGE),
    ("evidence_assessment", ModelRole.RETRIEVAL_JUDGE),
    ("crag", ModelRole.RETRIEVAL_JUDGE),
    ("retrieval_judge", ModelRole.RETRIEVAL_JUDGE),
    ("corrective_query", ModelRole.CORRECTIVE_PLANNER),
    ("semantic", ModelRole.SEMANTIC_GATE),
    ("local_rewrite", ModelRole.REPAIR),
    ("validation_rewrite", ModelRole.REPAIR),
    ("compression", ModelRole.REPAIR),
    ("citation_repair", ModelRole.REPAIR),
    ("grounded_regeneration", ModelRole.REPAIR),
    ("repair", ModelRole.REPAIR),
    ("section", ModelRole.SECTION_GENERATION),
    ("generation", ModelRole.SECTION_GENERATION),
)


def infer_model_role(purpose: str, fallback: ModelRole = ModelRole.GENERAL) -> ModelRole:
    normalized = str(purpose or "").strip().lower()
    for marker, role in _PURPOSE_ROLE_MAP:
        if marker in normalized:
            return role
    return fallback


class ModelCallBoundary:
    """Translate a small ``generate(prompt, ...)`` call to ModelRequestSchema."""

    def __init__(
        self,
        *,
        model_gateway: ModelGatewayPort,
        model_role: ModelRole | str | None = None,
        model_name: str | None = None,
        runtime_context: dict[str, Any] | None = None,
        default_purpose: str = "model_generation",
        call_suffix: str = "generation",
        budget_hook: BudgetHook | None = None,
        response_hook: ResponseHook | None = None,
    ) -> None:
        self.model_gateway = model_gateway
        self.model_role = _coerce_role(model_role)
        self.model_name = str(model_name or "").strip() or None
        self.runtime_context = dict(runtime_context or {})
        self.default_purpose = str(default_purpose or "model_generation")
        self.call_suffix = str(call_suffix or "generation")
        self.budget_hook = budget_hook
        self.response_hook = response_hook
        self.last_response: ModelResponseSchema | None = None

    @staticmethod
    def _merged_context(
        base: dict[str, Any],
        runtime_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged = dict(base)
        merged.update(dict(runtime_context or {}))
        request_context = merged.get("request_context")
        if isinstance(request_context, dict):
            # RAG plugins historically wrap request context one level deep.
            merged = {**request_context, **merged}
        return merged

    @staticmethod
    def _value(context: dict[str, Any], *names: str, default: Any = None) -> Any:
        for name in names:
            value = context.get(name)
            if value is not None and str(value).strip():
                return value
        return default

    def generate_response(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        messages: Sequence[Mapping[str, str]] | None = None,
        max_new_tokens: int = 384,
        temperature: float = 0.0,
        top_p: float = 0.9,
        do_sample: bool = False,
        runtime_context: dict[str, Any] | None = None,
        call_purpose: str | None = None,
        model_role: ModelRole | str | None = None,
        model_name: str | None = None,
        model_call_id: str | None = None,
        created_at: str | None = None,
        model_extra: Mapping[str, Any] | None = None,
        **_: Any,
    ) -> ModelResponseSchema:
        """Execute one logical model call and return its canonical response.

        ``WorkflowBudget`` hooks account for this logical call once.  Concrete
        provider attempts (including availability fallback) are accounted by
        ``ModelUsageLedger`` inside ``ModelGateway`` instead.

        The optional ``model_extra`` mapping is caller-owned metadata only.
        Boundary-owned lineage keys are written afterwards so callers cannot
        accidentally replace task/run/call-purpose identity.
        """
        context = self._merged_context(self.runtime_context, runtime_context)
        task_id = str(self._value(context, "task_id", default="model_task"))
        run_id = str(
            self._value(
                context,
                "workflow_run_id",
                "run_id",
                default="model_run",
            )
        )
        section_id = str(self._value(context, "section_id", default="") or "")
        rag_run_id = str(self._value(context, "rag_run_id", default="") or "")
        retrieval_trace_id = str(
            self._value(context, "retrieval_trace_id", default="") or ""
        )
        purpose = str(call_purpose or self.default_purpose)

        if model_role is not None:
            resolved_role = _coerce_role(model_role)
            assert resolved_role is not None
        elif self.model_role is not None:
            resolved_role = self.model_role
        else:
            resolved_role = infer_model_role(purpose)

        explicit_override = str(model_name or self.model_name or "").strip() or None
        suffix = self.call_suffix.strip() or purpose
        lineage_suffix = section_id or rag_run_id or retrieval_trace_id or suffix
        call_id = str(model_call_id or "").strip() or (
            f"model_call_{run_id}_{lineage_suffix}_{uuid4().hex[:8]}"
        )
        caller_extra = {
            **dict(context.get("model_extra") or {}),
            **dict(model_extra or {}),
        }
        request_extra = {
            **caller_extra,
            "call_purpose": purpose,
            "section_id": section_id or None,
            "section_title": context.get("section_title"),
            "workflow_run_id": run_id,
            "rag_run_id": rag_run_id or None,
            "retrieval_trace_id": retrieval_trace_id or None,
            "retrieval_scope": context.get("retrieval_scope"),
            "generation_attempt": context.get("generation_attempt"),
            "generation_params": {
                "top_p": float(top_p),
                "do_sample": bool(do_sample),
            },
            "budget_semantics": "logical_model_call_v1",
        }

        request = ModelRequestSchema(
            model_call_id=call_id,
            task_id=task_id,
            run_id=run_id,
            model_role=resolved_role.value,
            model_name=explicit_override,
            caller_agent=str(
                self._value(
                    context,
                    "caller_agent",
                    "agent_name",
                    default="ModelCallBoundary",
                )
            ),
            prompt=str(prompt),
            system_prompt=system_prompt,
            messages=[dict(item) for item in (messages or [])],
            temperature=float(temperature),
            max_tokens=max(1, int(max_new_tokens)),
            created_at=(
                str(created_at).strip()
                if created_at is not None and str(created_at).strip()
                else datetime.now(timezone.utc).isoformat()
            ),
            extra=request_extra,
        )

        hook = context.get("budget_hook") or self.budget_hook
        if callable(hook):
            hook(request)
        response = self.model_gateway.generate(request)
        self.last_response = response
        response_hook = context.get("response_hook") or self.response_hook
        if callable(response_hook):
            response_hook(request, response)
        return response

    def generate(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> str:
        """Execute one logical model call and return text for TextGenerator ports."""
        response = self.generate_response(prompt, **kwargs)
        if not response.success:
            message = response.error_message or (
                response.error.message if response.error is not None else ""
            )
            raise RuntimeError(message or "model gateway call failed")
        return str(response.content or "").strip()


# Compatibility name retained for existing imports.  Both historical adapters
# now resolve to this one implementation.
ModelGatewayTextGenerator = ModelCallBoundary
