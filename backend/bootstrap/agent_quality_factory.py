"""Compose Agent-level generation quality plugins from the RAG profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bootstrap.runtime_options import RuntimeOptions
from model_gateway.model_gateway import ModelGateway
from rag.config.pipeline_config import PipelineConfigLoader
from rag.config.profile_catalog import OnlineRAGProfileCatalogValidator
from rag.registry.default_registrations import build_default_component_registry
from rag.routing.runtime import AdaptiveProfileRouterRuntime
from rag.routing.schema import peek_config_schema_version


@dataclass(frozen=True)
class AgentQualityRuntime:
    generation_checker: Any
    repair_strategy: Any
    metadata: dict[str, Any]


class AgentQualityFactory:
    """Build final-section checking and repair from the same external profile."""

    def build(
        self,
        *,
        options: RuntimeOptions,
        model_gateway: ModelGateway,
        model_name: str,
    ) -> AgentQualityRuntime:
        loader = PipelineConfigLoader()
        configured_file = options.rag_pipeline_config_file
        adaptive_router_metadata: dict[str, Any] | None = None
        if (
            peek_config_schema_version(configured_file)
            == "adaptive_profile_router_config_v1"
        ):
            adaptive_runtime = AdaptiveProfileRouterRuntime(
                config_file=configured_file,
                project_root=options.rag_project_root,
            )
            configured_file = adaptive_runtime.profile_path(
                adaptive_runtime.agent_quality_profile_id
            )
            adaptive_router_metadata = {
                **adaptive_runtime.validation_report(),
                "agent_quality_selection_mode": "static_router_profile_v1",
                "selected_agent_quality_profile_id": (
                    adaptive_runtime.agent_quality_profile_id
                ),
            }
        pipeline_config = loader.load(
            configured_file,
            project_root=options.rag_project_root,
        )
        registry = build_default_component_registry()
        catalog_report = OnlineRAGProfileCatalogValidator(
            loader=loader
        ).validate(
            project_root=options.rag_project_root,
            registry=registry,
        )
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
        checker = registry.build(
            category="generation_checker",
            config=pipeline_config.generation_checker,
            build_context=build_context,
        )
        repair = registry.build(
            category="repair_strategy",
            config=pipeline_config.repair_strategy,
            build_context=build_context,
        )

        def _record(instance: Any, config: Any) -> dict[str, Any]:
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
                "profile_id": pipeline_config.profile_id,
                "profile_version": pipeline_config.profile_version,
                "pipeline_config_file": str(configured_file),
                "requested_pipeline_config_file": str(options.rag_pipeline_config_file),
                "adaptive_profile_router": adaptive_router_metadata,
                "pipeline_config_hash": pipeline_config.config_hash(),
                "profile_catalog_validation": catalog_report.to_dict(),
                "generation_checker": _record(
                    checker, pipeline_config.generation_checker
                ),
                "repair_strategy": _record(
                    repair, pipeline_config.repair_strategy
                ),
                "legacy_enable_agent_self_rag": bool(
                    options.enable_agent_self_rag
                ),
                "legacy_flag_ignored": True,
            },
        )
