"""Smoke-test the production DeepSeek provider through SECTION_GENERATION routing."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from bootstrap.model_factory import ModelGatewayFactory
from core.config import get_settings
from model_gateway.model_contract import ModelRole
from schemas.model import ModelRequestSchema


MARKER = "DEEPSEEK_WRITER_SMOKE_OK"


def main() -> int:
    settings = get_settings(reload=True)
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    if settings.section_generation_model_profile != "deepseek":
        raise RuntimeError(
            "set SECTION_GENERATION_MODEL_PROFILE=deepseek for writer-only smoke test"
        )

    # A default fake runtime short-circuits role routing.  For this explicit
    # production-provider smoke only, switch the composition root to the
    # registered local default without mutating process environment.
    if settings.default_model_name == "fake_llm":
        settings = replace(
            settings,
            default_model_name=settings.local_qwen_1_5b_model_name,
        )

    gateway = ModelGatewayFactory().build(settings)
    try:
        capabilities = gateway.routing_capabilities(
            model_role=ModelRole.SECTION_GENERATION.value
        )
        if capabilities["candidate_profiles"] != ["deepseek"]:
            raise AssertionError(
                "SECTION_GENERATION is not isolated to DeepSeek: "
                + repr(capabilities["candidate_profiles"])
            )

        request = ModelRequestSchema(
            model_call_id="deepseek_writer_smoke",
            task_id="deepseek_writer_smoke",
            run_id="deepseek_writer_smoke",
            model_role=ModelRole.SECTION_GENERATION.value,
            caller_agent="DeepSeekWriterSmoke",
            prompt=f"只回复这一段文本，不要添加其他内容：{MARKER}",
            temperature=0.0,
            max_tokens=64,
            created_at=datetime.now(timezone.utc).isoformat(),
            extra={"call_purpose": "deepseek_writer_smoke"},
        )
        response = gateway.generate(request)
        summary = {
            "success": response.success,
            "model_name": response.model_name,
            "provider": response.metadata.get("provider"),
            "selected_profile": response.metadata.get("selected_profile"),
            "finish_reason": response.finish_reason,
            "content": response.content.strip(),
            "token_usage": response.token_usage.model_dump(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        if not response.success:
            raise RuntimeError(response.error_message or "DeepSeek call failed")
        if MARKER not in response.content:
            raise AssertionError("DeepSeek response did not preserve smoke marker")
        return 0
    finally:
        gateway.close()


if __name__ == "__main__":
    raise SystemExit(main())
