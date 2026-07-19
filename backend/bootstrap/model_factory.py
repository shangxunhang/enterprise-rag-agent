# =============================================================================
# 中文阅读说明：依赖装配与运行时构建模块。
# 主要定义：ModelGatewayFactory。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Model Gateway composition."""

from __future__ import annotations

from core.config import AppSettings
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.local_qwen_client import LocalQwenLLMClient
from model_gateway.model_gateway import ModelGateway


# 阅读注释（类）：封装 模型 网关 工厂，负责根据配置装配并返回运行实例。
class ModelGatewayFactory:
    """封装 模型 网关 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：构建 ModelGatewayFactory。
    def build(self, settings: AppSettings, trace_sink=None) -> ModelGateway:
        """构建 ModelGatewayFactory。

        参数:
            settings: settings，具体约束请结合类型标注和调用方确认。
            trace_sink: Trace sink，具体约束请结合类型标注和调用方确认。

        返回:
            ModelGateway

        阅读提示:
            主要直接调用：ModelGateway, gateway.register_client, FakeLLMClient, settings.local_qwen_model_path.exists, LocalQwenLLMClient, FileNotFoundError, print。
        """
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
