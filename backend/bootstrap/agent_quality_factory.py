"""Compose application-owned generation quality plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.enterprise_document.config.grounded_generation import (
    GenerationPluginConfig,
    GroundedGenerationPolicyLoader,
)
from apps.enterprise_document.quality.ports import (
    GenerationCheckerPort,
    RepairStrategyPort,
)
from apps.enterprise_document.quality.registry import (
    build_generation_plugin_registry,
)
from bootstrap.runtime_options import RuntimeOptions
from model_gateway.model_gateway import ModelGateway


@dataclass(frozen=True)
class AgentQualityRuntime:
    generation_checker: GenerationCheckerPort
    repair_strategy: RepairStrategyPort
    metadata: dict[str, Any]


class AgentQualityFactory:
    """Build Self-RAG checking independently from retrieval configuration."""

    def build(
        self,
        *,
        options: RuntimeOptions,
        model_gateway: ModelGateway,
        model_name: str,
    ) -> AgentQualityRuntime:
        configured_file = options.grounded_generation_policy_file
        if not configured_file.is_absolute():
            configured_file = options.rag_project_root / configured_file
        policy = GroundedGenerationPolicyLoader().load(configured_file)
        registry = build_generation_plugin_registry()
        build_context = {
            "model_gateway": model_gateway,
            "model_name": model_name,
            "enable_quality_llm": True,
            "quality_generation_params": {
                "temperature": 0.0,
                "top_p": 0.9,
                "do_sample": False,
            },
        }
        checker_config = policy.generation_checker
        repair_config = policy.repair_strategy
        if not options.enable_agent_self_rag:
            checker_config = GenerationPluginConfig(name="noop_generation")
            repair_config = GenerationPluginConfig(name="noop_repair")

        checker = registry.build(
            category="generation_checker",
            config=checker_config,
            build_context=build_context,
        )
        repair = registry.build(
            category="repair_strategy",
            config=repair_config,
            build_context=build_context,
        )

        def record(instance: Any, config: GenerationPluginConfig) -> dict[str, Any]:
            return {
                **instance.plugin_metadata.to_dict(),
                "enabled": bool(config.enabled),
                "params": dict(config.params),
                "execution": dict(instance.execution_metadata() or {}),
            }

        return AgentQualityRuntime(
            generation_checker=checker,
            repair_strategy=repair,
            metadata={
                "policy_id": policy.policy_id,
                "policy_version": policy.policy_version,
                "policy_config_file": str(configured_file),
                "schema_version": policy.schema_version,
                "generation_checker": record(checker, checker_config),
                "repair_strategy": record(repair, repair_config),
                "enabled": bool(options.enable_agent_self_rag),
                "max_retrieval_rounds": policy.max_retrieval_rounds,
                "max_rewrite_rounds": policy.max_rewrite_rounds,
                "max_total_llm_calls": policy.max_total_llm_calls,
                "max_total_tokens": policy.max_total_tokens,
                "human_review_on_exhaustion": policy.human_review_on_exhaustion,
                "budget_scope": policy.budget_scope,
            },
        )
