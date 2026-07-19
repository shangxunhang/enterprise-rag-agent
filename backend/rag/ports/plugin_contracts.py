"""Runtime contract checks for registered RAG plugin categories."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Any


@dataclass(frozen=True)
class MethodContract:
    name: str
    parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginContract:
    methods: tuple[MethodContract, ...]
    attributes: tuple[str, ...] = ()


PLUGIN_CONTRACTS: dict[str, PluginContract] = {
    "chunker": PluginContract(
        methods=(
            MethodContract("chunk_records", ("records",)),
            MethodContract("execution_metadata"),
        )
    ),
    "query_transformer": PluginContract(
        methods=(MethodContract("transform", ("state",)),)
    ),
    "retriever": PluginContract(
        methods=(MethodContract("retrieve", ("request",)),),
        attributes=("source_name",),
    ),
    "source_fusion": PluginContract(
        methods=(MethodContract("fuse", ("candidate_sets",)),)
    ),
    "query_fusion": PluginContract(
        methods=(MethodContract("fuse", ("candidate_sets",)),)
    ),
    "candidate_enricher": PluginContract(
        methods=(MethodContract("enrich", ("candidate_set",)),)
    ),
    "reranker": PluginContract(
        methods=(
            MethodContract("rerank", ("query", "results")),
            MethodContract("execution_metadata"),
        )
    ),
    "evidence_assessor": PluginContract(
        methods=(
            MethodContract("assess", ("query", "results", "runtime_context")),
            MethodContract("execution_metadata"),
        )
    ),
    "corrective_retrieval_gate": PluginContract(
        methods=(
            MethodContract(
                "decide",
                (
                    "assessment",
                    "correction_budget",
                    "completed_rounds",
                    "runtime_context",
                ),
            ),
            MethodContract("execution_metadata"),
        )
    ),
    "corrective_query_planner": PluginContract(
        methods=(
            MethodContract("plan", ("query", "assessment", "runtime_context")),
            MethodContract("execution_metadata"),
        )
    ),
    "context_packer": PluginContract(
        methods=(MethodContract("pack", ("results", "token_budget", "max_items")),),
        attributes=("max_context_chars", "max_items"),
    ),
}


def validate_plugin_contract(category: str, component: Any) -> None:
    """Fail fast when a registered builder cannot satisfy its category port."""
    contract = PLUGIN_CONTRACTS.get(category)
    if contract is None:
        return

    component_name = getattr(component, "__qualname__", None) or component.__class__.__qualname__
    for attribute in contract.attributes:
        if not isinstance(component, type) and not hasattr(component, attribute):
            raise TypeError(
                f"plugin contract violation: {category}/{component_name} "
                f"is missing attribute {attribute!r}"
            )

    for method_contract in contract.methods:
        method = getattr(component, method_contract.name, None)
        if not callable(method):
            raise TypeError(
                f"plugin contract violation: {category}/{component_name} "
                f"is missing method {method_contract.name}()"
            )
        parameters = signature(method).parameters
        accepts_extra_keywords = any(
            item.kind is Parameter.VAR_KEYWORD for item in parameters.values()
        )
        missing = [
            name
            for name in method_contract.parameters
            if name not in parameters and not accepts_extra_keywords
        ]
        if missing:
            raise TypeError(
                f"plugin contract violation: {category}/{component_name}."
                f"{method_contract.name}() is missing parameters: "
                f"{', '.join(missing)}"
            )
