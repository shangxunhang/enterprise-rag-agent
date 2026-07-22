"""Composition root for the one static parent-child retrieval topology."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, TYPE_CHECKING

from rag.common.pathing import resolve_path

if TYPE_CHECKING:
    from contracts.model_gateway import ModelGatewayPort
    from rag.rag_engine.parent_child_rag_engine import ParentChildRAGEngine


class ParentChildRuntimeFactory:
    def __init__(
        self,
        *,
        model_gateway: "ModelGatewayPort | None" = None,
        model_name: str | None = None,
        model_budget_hook: Any | None = None,
    ) -> None:
        self.model_gateway = model_gateway
        # Compatibility/introspection only. Production RAG calls route by role;
        # this legacy name is no longer injected as an explicit model override.
        self.model_name = str(model_name or "").strip() or None
        self.model_budget_hook = model_budget_hook

    def resolve_config(self, config: Any, project_root: Path) -> Any:
        cfg = config.__class__(**asdict(config))
        for name in (
            "parent_file",
            "child_file",
            "db_file",
            "capture_output",
            "static_retrieval_spec_file",
            "intent_policy_file",
            "retrieval_gate_policy_file",
            "active_index_pointer",
        ):
            setattr(cfg, name, resolve_path(getattr(cfg, name), project_root))
        cfg.index_lineage = {
            "status": "legacy_paths",
            "index_version": getattr(cfg, "index_version", "legacy_unversioned_index"),
            "pointer_path": cfg.active_index_pointer,
        }
        pointer_path = Path(cfg.active_index_pointer)
        if bool(getattr(cfg, "use_active_index_manifest", True)) and pointer_path.is_file():
            from rag.offline.resolver import ActiveIndexResolver

            lineage = ActiveIndexResolver(
                verify_manifest_hash=bool(
                    getattr(cfg, "verify_active_index_manifest_hash", True)
                ),
                verify_artifacts=bool(
                    getattr(cfg, "verify_active_index_artifacts", False)
                ),
            ).resolve(pointer_path)
            if lineage.get("backend") != "milvus_lite":
                raise ValueError(
                    "active online index must use backend=milvus_lite; "
                    f"got {lineage.get('backend')}"
                )
            cfg.parent_file = str(lineage["parent_file"])
            cfg.child_file = str(lineage["child_file"])
            cfg.db_file = str(lineage["db_file"])
            cfg.collection_name = str(lineage.get("collection_name") or cfg.collection_name)
            cfg.metric_type = str(lineage.get("metric_type") or cfg.metric_type)
            cfg.embedding_model = str(lineage.get("embedding_model") or cfg.embedding_model)
            cfg.hash_embedding = bool(lineage.get("hash_embedding"))
            cfg.hash_dim = int(lineage.get("embedding_dim") or cfg.hash_dim)
            cfg.index_version = str(lineage["index_version"])
            cfg.dataset_version = str(lineage["dataset_version"])
            cfg.index_manifest_file = str(lineage["manifest_path"])
            cfg.index_manifest_hash = str(lineage["manifest_hash"])
            cfg.index_config_hash = str(lineage["config_hash"])
            cfg.index_reproducibility_hash = str(lineage["reproducibility_hash"])
            cfg.index_lineage = {"status": "active_manifest", **lineage}
        return cfg

    def build(
        self,
        config: Any,
        project_root: Path,
    ) -> tuple["ParentChildRAGEngine", Any]:
        from rag.config.retrieval_policies import (
            IntentPolicyConfig,
            RetrievalGatePolicyConfig,
            RetrievalPolicyLoader,
        )
        from rag.config.static_retrieval import StaticRetrievalSpecLoader
        from rag.context.context_gate import ContextGate, ContextRequirements
        from rag.data_capture.rag_run_capture import RagRunCapture
        from model_gateway.integrations.text_generator import (
            ModelGatewayTextGenerator,
        )
        from rag.planning.retrieval_planner import AdaptiveRetrievalPlanner
        from rag.query.query_transform_selector import QueryTransformSelector
        from rag.rag_engine.parent_child_rag_engine import ParentChildRAGEngine
        from rag.registry.default_registrations import build_default_component_registry
        from rag.runtime.resource_pool import ParentChildResourcePool

        cfg = self.resolve_config(config, project_root)
        spec = StaticRetrievalSpecLoader().load(
            cfg.static_retrieval_spec_file,
            project_root=project_root,
        )
        policy_loader = RetrievalPolicyLoader()
        intent_policy = policy_loader.load(cfg.intent_policy_file, IntentPolicyConfig)
        gate_policy = policy_loader.load(
            cfg.retrieval_gate_policy_file,
            RetrievalGatePolicyConfig,
        )
        registry = build_default_component_registry()
        resource_pool = ParentChildResourcePool(
            runtime_config=cfg,
            project_root=project_root,
        )
        retrieval_context = {
            "project_root": project_root,
            "runtime_config": cfg,
            "resource_pool": resource_pool,
        }

        retriever_configs = [item for item in spec.retrievers if item.enabled]
        retrievers = [
            registry.build(
                category="retriever",
                config=item,
                build_context=retrieval_context,
            )
            for item in retriever_configs
        ]
        source_names = [str(getattr(item, "source_name", "")) for item in retrievers]
        if any(not name for name in source_names) or len(source_names) != len(
            set(source_names)
        ):
            raise ValueError("static retrievers must expose unique non-empty source_name")

        source_fusion = registry.build(
            category="source_fusion",
            config=spec.source_fusion,
            build_context=retrieval_context,
        )
        query_fusion = registry.build(
            category="query_fusion",
            config=spec.query_fusion,
            build_context=retrieval_context,
        )
        candidate_enricher = registry.build(
            category="candidate_enricher",
            config=spec.candidate_enricher,
            build_context=retrieval_context,
        )
        reranker = registry.build(
            category="reranker",
            config=spec.reranker,
            build_context=retrieval_context,
        )

        query_llm = None
        if cfg.enable_query_expansion_llm and self.model_gateway is not None:
            query_llm = ModelGatewayTextGenerator(
                model_gateway=self.model_gateway,
                default_purpose="rag_internal_generation",
                call_suffix="rag",
                budget_hook=self.model_budget_hook,
            )
        generation_params = {
            "rewrite_max_new_tokens": cfg.query_rewrite_max_new_tokens,
            "hyde_max_new_tokens": cfg.query_hyde_max_new_tokens,
            "temperature": cfg.query_expansion_temperature,
            "top_p": cfg.query_expansion_top_p,
            "do_sample": cfg.query_expansion_do_sample,
        }
        transform_context = {
            "project_root": project_root,
            "runtime_config": cfg,
            "query_llm_generator": query_llm,
            "enable_query_expansion_llm": cfg.enable_query_expansion_llm,
            "query_expansion_generation_params": generation_params,
        }
        transformers = [
            registry.build(
                category="query_transformer",
                config=item,
                build_context=transform_context,
            )
            for item in spec.query_transformers
            if item.enabled
        ]
        transform_selector = QueryTransformSelector(
            transformers,
            spec_id=spec.spec_id,
            spec_version=spec.spec_version,
        )
        retrieval_planner = AdaptiveRetrievalPlanner(
            short_query_max_chars=intent_policy.short_query_max_chars,
            correction_budget=gate_policy.correction_budget,
        )

        quality_context = {
            "project_root": project_root,
            "runtime_config": cfg,
            "quality_llm_generator": query_llm,
            "enable_quality_llm": bool(
                cfg.enable_query_expansion_llm and query_llm is not None
            ),
            "quality_generation_params": generation_params,
        }
        evidence_assessor = registry.build(
            category="evidence_assessor",
            config=spec.evidence_assessor,
            build_context=quality_context,
        )
        correction_gate = registry.build(
            category="corrective_retrieval_gate",
            config=spec.corrective_retrieval_gate,
            build_context=quality_context,
        )
        corrective_query_planner = registry.build(
            category="corrective_query_planner",
            config=spec.corrective_query_planner,
            build_context=quality_context,
        )

        packers = {
            item.name: registry.build(
                category="context_packer",
                config=item,
                build_context={"project_root": project_root, "runtime_config": cfg},
            )
            for item in spec.context_packers
            if item.enabled
        }
        gate_config = spec.context_gate
        context_gate = ContextGate(
            default_packer=packers["default"],
            lost_in_middle_packer=packers["lost_in_middle"],
            default_requirements=ContextRequirements(
                model_context_window=gate_config.model_context_window,
                prompt_reserved_tokens=gate_config.prompt_reserved_tokens,
                section_token_budget=gate_config.section_token_budget,
                max_evidence_items=gate_config.max_evidence_items,
                max_context_chars=gate_config.max_context_chars,
            ),
            long_context_threshold_ratio=gate_config.long_context_threshold_ratio,
        )

        def component_record(instance: Any, component_config: Any) -> dict[str, Any]:
            return {
                **instance.plugin_metadata.to_dict(),
                "enabled": bool(component_config.enabled),
                "params": dict(component_config.params),
            }

        cfg.pipeline_name = spec.spec_id
        cfg.pipeline_version = spec.spec_version
        cfg.static_retrieval_spec_hash = spec.config_hash()
        cfg.static_retrieval_spec_schema_version = spec.schema_version
        cfg.static_retrieval_spec_id = spec.spec_id
        cfg.static_retrieval_spec_version = spec.spec_version
        cfg.intent_policy_id = intent_policy.policy_id
        cfg.retrieval_gate_policy_id = gate_policy.policy_id
        cfg.static_retrieval_component_metadata = {
            "query_transformers": [
                component_record(instance, item)
                for instance, item in zip(
                    transformers,
                    [item for item in spec.query_transformers if item.enabled],
                    strict=True,
                )
            ],
            "retrievers": [
                component_record(instance, item)
                for instance, item in zip(retrievers, retriever_configs, strict=True)
            ],
            "source_fusion": component_record(source_fusion, spec.source_fusion),
            "query_fusion": component_record(query_fusion, spec.query_fusion),
            "candidate_enricher": component_record(
                candidate_enricher, spec.candidate_enricher
            ),
            "reranker": component_record(reranker, spec.reranker),
            "evidence_assessor": component_record(
                evidence_assessor, spec.evidence_assessor
            ),
            "corrective_retrieval_gate": component_record(
                correction_gate, spec.corrective_retrieval_gate
            ),
            "corrective_query_planner": component_record(
                corrective_query_planner, spec.corrective_query_planner
            ),
            "context_packers": {
                item.name: component_record(packers[item.name], item)
                for item in spec.context_packers
                if item.enabled
            },
            "context_gate": context_gate.execution_metadata(),
        }

        engine = ParentChildRAGEngine(
            retrievers=retrievers,
            source_fusion=source_fusion,
            query_fusion=query_fusion,
            candidate_enricher=candidate_enricher,
            reranker=reranker,
            context_gate=context_gate,
            evidence_assessor=evidence_assessor,
            corrective_retrieval_gate=correction_gate,
            corrective_query_planner=corrective_query_planner,
            run_capture=RagRunCapture(cfg.capture_output),
            query_llm_generator=query_llm,
            query_transform_selector=transform_selector,
            retrieval_planner=retrieval_planner,
            enable_query_expansion_llm=cfg.enable_query_expansion_llm,
            query_expansion_generation_params=generation_params,
            pipeline_name=cfg.pipeline_name,
            pipeline_version=cfg.pipeline_version,
        )
        return engine, cfg
