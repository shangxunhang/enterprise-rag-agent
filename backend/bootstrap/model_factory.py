"""Model Gateway composition root."""

from __future__ import annotations

from core.config import AppSettings
from model_gateway.fake_llm_client import FakeLLMClient
from model_gateway.model_contract import (
    ModelProfile,
    ModelRole,
    ModelRoutingConfig,
    ResidencyPolicy,
    RoutingPolicy,
)
from model_gateway.model_gateway import ModelGateway
from model_gateway.model_router import ModelRouter
from model_gateway.provider_factory import ModelProviderFactory


def build_default_model_routing(settings: AppSettings) -> ModelRoutingConfig:
    """Build the single-node 4070 Ti SUPER model pool and role policies."""

    if settings.default_model_name == "fake_llm":
        fake_profile = ModelProfile(
            profile_id="fake",
            model_name="fake_llm",
            provider="fake",
            residency_policy=ResidencyPolicy.PRIMARY,
            context_window=32768,
            max_output_tokens=max(2048, settings.local_qwen_max_new_tokens),
            metadata={"deterministic_test_runtime": True},
        )
        return ModelRoutingConfig(
            profiles=[fake_profile],
            policies=[
                RoutingPolicy(role=role, candidates=["fake"])
                for role in ModelRole
            ],
        )

    profiles = [
        ModelProfile(
            profile_id="local_1_5b",
            model_name=settings.local_qwen_1_5b_model_name,
            provider="local_huggingface",
            local_path=str(settings.local_qwen_1_5b_model_path),
            residency_policy=ResidencyPolicy.PRIMARY,
            context_window=32768,
            max_output_tokens=settings.local_qwen_max_new_tokens,
            metadata={"family": "Qwen2.5", "size": "1.5B"},
        ),
        ModelProfile(
            profile_id="local_3b",
            model_name=settings.local_qwen_3b_model_name,
            provider="local_huggingface",
            local_path=str(settings.local_qwen_3b_model_path),
            residency_policy=ResidencyPolicy.RESIDENT,
            context_window=32768,
            max_output_tokens=settings.local_qwen_max_new_tokens,
            metadata={"family": "Qwen2.5", "size": "3B"},
        ),
        ModelProfile(
            profile_id="deepseek",
            model_name=settings.deepseek_model_name,
            provider="openai_compatible",
            provider_model_name=settings.deepseek_provider_model_name,
            residency_policy=ResidencyPolicy.REMOTE,
            context_window=32768,
            max_output_tokens=max(2048, settings.local_qwen_max_new_tokens),
            metadata={"remote": True},
        ),
        ModelProfile(
            profile_id="local_7b",
            model_name=settings.local_qwen_7b_model_name,
            provider="local_huggingface",
            local_path=str(settings.local_qwen_7b_model_path),
            enabled=False,
            residency_policy=ResidencyPolicy.DISABLED,
            context_window=32768,
            max_output_tokens=settings.local_qwen_max_new_tokens,
            metadata={
                "family": "Qwen2.5",
                "size": "7B",
                "quantization": "GPTQ-Int4",
                "activation_policy": "explicitly enable as on_demand before use",
            },
        ),
    ]

    policies = [
        RoutingPolicy(
            role=ModelRole.GENERAL,
            candidates=["local_1_5b", "local_3b", "deepseek"],
        ),
        RoutingPolicy(
            role=ModelRole.SUPERVISOR_ROUTING,
            candidates=["local_1_5b", "local_3b", "deepseek"],
        ),
        RoutingPolicy(
            role=ModelRole.QUERY_REWRITE,
            candidates=["local_1_5b", "local_3b", "deepseek"],
        ),
        RoutingPolicy(
            role=ModelRole.HYDE,
            candidates=["local_3b", "local_1_5b", "deepseek"],
        ),
        RoutingPolicy(
            role=ModelRole.RETRIEVAL_JUDGE,
            candidates=["local_1_5b", "local_3b", "deepseek"],
        ),
        RoutingPolicy(
            role=ModelRole.CORRECTIVE_PLANNER,
            candidates=["local_1_5b", "local_3b", "deepseek"],
        ),
        RoutingPolicy(
            role=ModelRole.SECTION_GENERATION,
            candidates=["local_3b", "local_1_5b", "deepseek"],
        ),
        RoutingPolicy(
            role=ModelRole.SEMANTIC_GATE,
            candidates=["local_1_5b", "local_3b", "deepseek"],
        ),
        RoutingPolicy(
            role=ModelRole.REPAIR,
            candidates=["local_3b", "local_1_5b", "deepseek"],
        ),
    ]
    return ModelRoutingConfig(profiles=profiles, policies=policies)


class ModelGatewayFactory:
    """Build ModelGateway, routing policy, registry clients and providers."""

    def __init__(self, provider_factory: ModelProviderFactory | None = None) -> None:
        self.provider_factory = provider_factory or ModelProviderFactory()

    def build(self, settings: AppSettings, trace_sink=None) -> ModelGateway:
        routing = build_default_model_routing(settings)
        router = ModelRouter(
            settings.default_model_name,
            profiles=routing.profiles,
            policies=routing.policies,
        )
        gateway = ModelGateway(
            default_model_name=settings.default_model_name,
            run_trace_recorder=trace_sink,
            router=router,
        )
        gateway.register_client(FakeLLMClient())

        for profile in routing.profiles:
            client = self.provider_factory.build(profile, settings=settings)
            if client is not None:
                gateway.register_client(client)
        return gateway
