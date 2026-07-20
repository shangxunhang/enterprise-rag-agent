"""LangChain interoperability for the canonical ModelGateway boundary."""

from model_gateway.integrations.langchain.chat_model_client import LangChainChatModelClient
from model_gateway.integrations.langchain.runnable import build_model_gateway_runnable

__all__ = [
    "LangChainChatModelClient",
    "build_model_gateway_runnable",
]
