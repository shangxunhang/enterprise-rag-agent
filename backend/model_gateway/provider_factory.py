"""Provider-client factory for model profiles."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableLambda

from contracts.base_client import BaseLLMClient
from model_gateway.integrations.langchain.chat_model_client import LangChainChatModelClient
from model_gateway.local_qwen_client import LocalQwenLLMClient
from model_gateway.model_contract import ModelProfile


class ModelProviderFactory:
    """Build provider clients without leaking provider SDKs into business code."""

    def build(self, profile: ModelProfile, *, settings: Any) -> BaseLLMClient | None:
        if not profile.selectable:
            return None
        provider = profile.provider.strip().lower()
        if provider == "fake":
            # FakeLLMClient is registered explicitly by the composition root;
            # the profile exists only so role routing remains deterministic in
            # fake/test runtimes.
            return None
        if provider == "local_huggingface":
            if not profile.local_path:
                raise ValueError(
                    f"local model profile requires local_path: {profile.profile_id}"
                )
            return LocalQwenLLMClient(
                model_name=profile.model_name,
                model_path=profile.local_path,
                device=str(getattr(settings, "local_qwen_device", "cuda")),
                max_new_tokens=min(
                    int(getattr(settings, "local_qwen_max_new_tokens", profile.max_output_tokens)),
                    int(profile.max_output_tokens),
                ),
            )
        if provider == "openai_compatible":
            return self._build_openai_compatible(profile, settings=settings)
        raise ValueError(
            f"unsupported model provider for profile {profile.profile_id}: {profile.provider}"
        )

    @staticmethod
    def _unavailable_chat_model(reason: str) -> RunnableLambda:
        def unavailable(*_: Any, **__: Any):
            raise RuntimeError(f"provider unavailable: {reason}")

        return RunnableLambda(unavailable)

    def _build_openai_compatible(
        self,
        profile: ModelProfile,
        *,
        settings: Any,
    ) -> LangChainChatModelClient:
        """Build DeepSeek/OpenAI-compatible models through LangChain adapter.

        Missing optional dependency/API key is represented as an unavailable
        provider client.  This keeps startup deterministic and lets the gateway
        apply its normal availability-fallback policy at call time.
        """

        api_key = str(getattr(settings, "deepseek_api_key", "") or "").strip()
        base_url = str(
            getattr(settings, "deepseek_base_url", "https://api.deepseek.com")
            or "https://api.deepseek.com"
        ).strip()
        provider_model = str(
            profile.provider_model_name or profile.model_name
        ).strip()

        if not api_key:
            chat_model = self._unavailable_chat_model(
                "DEEPSEEK_API_KEY is not configured"
            )
        else:
            try:
                from langchain_openai import ChatOpenAI
            except ImportError:
                chat_model = self._unavailable_chat_model(
                    "langchain-openai is not installed"
                )
            else:
                chat_model = ChatOpenAI(
                    model=provider_model,
                    api_key=api_key,
                    base_url=base_url,
                    timeout=float(profile.timeout_seconds),
                    max_retries=0,
                )

        return LangChainChatModelClient(
            model_name=profile.model_name,
            chat_model=chat_model,
            provider_name="deepseek_openai_compatible",
        )
