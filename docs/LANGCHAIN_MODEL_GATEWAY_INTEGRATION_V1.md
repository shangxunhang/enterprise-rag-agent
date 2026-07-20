# LangChain ModelGateway Integration V1

## Goal

Add LangChain interoperability without replacing the project's canonical model
boundary, routing, tracing or structured error handling.

Canonical internal boundary remains:

`ModelRequestSchema -> ModelGateway -> BaseLLMClient -> ModelResponseSchema`

## Added interoperability

### A. LangChain provider inside ModelGateway

`LangChain BaseChatModel/Runnable`
`-> LangChainChatModelClient`
`-> BaseLLMClient`
`-> existing ModelGateway`

This allows provider integrations that implement LangChain's chat-model/Runnable
contract to participate in the existing registry/router/invoker/observer flow.

### B. Existing ModelGateway as LangChain Runnable

`ModelRequestSchema | dict`
`-> build_model_gateway_runnable()`
`-> existing ModelGateway`
`-> ModelResponseSchema`

This allows LangChain chains/agents to call the existing gateway without
bypassing model routing, tracing or structured error normalization.

## Deliberately unchanged

- ModelGateway
- ModelRegistry
- ModelRouter
- ModelInvoker
- ModelCallObserver
- BaseLLMClient
- LocalQwenLLMClient
- FakeLLMClient
- ModelRequestSchema / ModelResponseSchema
- existing business-agent call sites

## Failure semantics

LangChain provider exceptions are not swallowed by the adapter. They bubble to
the existing `ModelInvoker`, which converts them into the canonical structured
`MODEL_GATEWAY_CALL_FAILED` response.

## Tool-message limitation

The current `ModelRequestSchema.messages` shape only stores `role` and
`content`. A real LangChain `ToolMessage` also requires `tool_call_id`.
Therefore V1 rejects `role="tool"` instead of inventing missing identifiers.

A later tool-calling phase should first extend the canonical message schema,
then add lossless ToolMessage mapping.

## Acceptance

1. LangChain provider can be registered in existing ModelGateway.
2. Existing ModelGateway can be invoked as a LangChain Runnable.
3. Provider failures still use existing structured error normalization.
4. Existing FakeLLM behavior is unchanged.
5. Full regression suite remains green.
