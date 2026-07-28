"""Shared LangChain configuration propagation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from langchain_core.runnables import RunnableConfig


@dataclass(frozen=True)
class LangChainRuntimeCapabilities:
    """Truthful capabilities of the schema-preserving Runnable facades."""

    invoke: bool = True
    ainvoke: bool = True
    batch: bool = True
    stream_final_response: bool = True
    token_streaming: bool = False
    native_async_provider: bool = False


def _current_graph_config() -> dict[str, Any]:
    """Return the active LangGraph config when called from a graph node."""

    try:
        from langgraph.config import get_config

        return dict(get_config())
    except RuntimeError:
        return {}


def merge_runnable_config(
    *,
    base: Mapping[str, Any],
    supplied: RunnableConfig | Mapping[str, Any] | None = None,
) -> RunnableConfig:
    """Merge graph, caller and facade config without losing callback context.

    Caller configuration may tune concurrency and callback delivery.  Stable
    identity metadata from ``base`` wins over caller values so one request
    cannot be accidentally attributed to another task or run.
    """

    parent = _current_graph_config()
    caller = dict(supplied or {})
    stable = dict(base)
    merged: dict[str, Any] = {
        **parent,
        **caller,
        **stable,
        "configurable": {
            **dict(parent.get("configurable", {})),
            **dict(caller.get("configurable", {})),
            **dict(stable.get("configurable", {})),
        },
        "metadata": {
            **dict(parent.get("metadata", {})),
            **dict(caller.get("metadata", {})),
            **dict(stable.get("metadata", {})),
        },
    }
    tags: list[str] = []
    for source in (parent, caller, stable):
        for tag in source.get("tags", []) or []:
            value = str(tag)
            if value not in tags:
                tags.append(value)
    if tags:
        merged["tags"] = tags
    if "callbacks" in caller:
        merged["callbacks"] = caller["callbacks"]
    elif "callbacks" in parent:
        merged["callbacks"] = parent["callbacks"]
    return RunnableConfig(**merged)
