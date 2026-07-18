"""Model Gateway composition."""

from __future__ import annotations

from core.config import AppSettings
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.local_qwen_client import LocalQwenLLMClient
from model_gateway.model_gateway import ModelGateway


class ModelGatewayFactory:
    def build(self, settings: AppSettings, trace_sink=None) -> ModelGateway:
        gateway = ModelGateway(
            default_model_name=settings.default_model_name,
            run_trace_recorder=trace_sink,
        )
        gateway.register_client(FakeLLMClient())
        if settings.local_qwen_model_path.exists():
            gateway.register_client(
                LocalQwenLLMClient(
                    model_name=settings.local_qwen_model_name,
                    model_path=settings.local_qwen_model_path,
                    device=settings.local_qwen_device,
                    max_new_tokens=settings.local_qwen_max_new_tokens,
                )
            )
        elif (
            settings.default_model_name == settings.local_qwen_model_name
            or settings.supervisor_model_name == settings.local_qwen_model_name
        ):
            raise FileNotFoundError(
                f"Local Qwen model path not found: {settings.local_qwen_model_path}"
            )
        else:
            print(
                "[Model Runtime] local Qwen path unavailable; current configuration does not require it",
                flush=True,
            )
        return gateway
