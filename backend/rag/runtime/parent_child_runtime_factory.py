"""Composition of the parent-child RAG runtime from external configuration.

Heavy ML/vector-store dependencies are loaded lazily through a per-runtime
resource pool. The composition root resolves registered plugins; it does not
branch on concrete retrieval strategy names.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, TYPE_CHECKING, Tuple

from rag.common.pathing import resolve_path

if TYPE_CHECKING:
    from rag.rag_engine.parent_child_rag_engine import ParentChildRAGEngine


class ParentChildRuntimeFactory:
    def resolve_config(self, config: Any, project_root: Path) -> Any:
        cfg = config.__class__(**asdict(config))
        cfg.parent_file = resolve_path(cfg.parent_file, project_root)
        cfg.child_file = resolve_path(cfg.child_file, project_root)
        cfg.db_file = resolve_path(cfg.db_file, project_root)
        cfg.capture_output = resolve_path(cfg.capture_output, project_root)
        cfg.pipeline_config_file = resolve_path(cfg.pipeline_config_file, project_root)
        cfg.active_index_pointer = resolve_path(cfg.active_index_pointer, project_root)
        cfg.index_lineage = {
            "status": "legacy_paths",
            "index_version": getattr(cfg, "index_version", "legacy_unversioned_index"),
            "pointer_path": cfg.active_index_pointer,
        }
        pointer_path = Path(cfg.active_index_pointer)
        if bool(getattr(cfg, "use_active_index_manifest", True)) and pointer_path.is_file():
            from rag.offline.resolver import ActiveIndexResolver

            lineage = ActiveIndexResolver(
                verify_manifest_hash=bool(getattr(cfg, "verify_active_index_manifest_hash", True)),
                verify_artifacts=bool(getattr(cfg, "verify_active_index_artifacts", False)),
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

    def build(self, config: Any, project_root: Path) -> Tuple["ParentChildRAGEngine", Any]:
        from rag.config.pipeline_config import PipelineConfigLoader
        from rag.config.profile_catalog import OnlineRAGProfileCatalogValidator
        from rag.data_capture.rag_run_capture import RagRunCapture
        from rag.llm.local_llm import LocalLLMGenerator
        from rag.prompt.parent_child_prompt_builder import ParentChildPromptBuilder
        from rag.query.query_transform_chain import QueryTransformChain
        from rag.rag_engine.parent_child_rag_engine import ParentChildRAGEngine
        from rag.registry.default_registrations import build_default_component_registry
        from rag.runtime.resource_pool import ParentChildResourcePool

        cfg = self.resolve_config(config, project_root)
        loader = PipelineConfigLoader()
        pipeline_config = loader.load(
            cfg.pipeline_config_file,
            project_root=project_root,
        )
        component_registry = build_default_component_registry()
        catalog_report = OnlineRAGProfileCatalogValidator(
            loader=loader
        ).validate(
            project_root=project_root,
            registry=component_registry,
        )
        resource_pool = ParentChildResourcePool(
            runtime_config=cfg,
            project_root=project_root,
        )
        retrieval_build_context = {
            "project_root": project_root,
            "runtime_config": cfg,
            "resource_pool": resource_pool,
        }

        enabled_retriever_configs = [
            item for item in pipeline_config.retrievers if item.enabled
        ]
        retrievers = [
            component_registry.build(
                category="retriever",
                config=item,
                build_context=retrieval_build_context,
            )
            for item in enabled_retriever_configs
        ]
        source_names = [str(getattr(item, "source_name", "")) for item in retrievers]
        if any(not name for name in source_names):
            raise ValueError("configured retriever plugin must expose source_name")
        if len(source_names) != len(set(source_names)):
            raise ValueError(
                "configured retriever plugins expose duplicate source_name values"
            )
        fusion = component_registry.build(
            category="fusion",
            config=pipeline_config.fusion,
            build_context=retrieval_build_context,
        )
        query_fusion = component_registry.build(
            category="query_fusion",
            config=pipeline_config.query_fusion,
            build_context=retrieval_build_context,
        )
        candidate_enricher = component_registry.build(
            category="candidate_enricher",
            config=pipeline_config.candidate_enricher,
            build_context=retrieval_build_context,
        )

        reranker = component_registry.build(
            category="reranker",
            config=pipeline_config.reranker,
            build_context=retrieval_build_context,
        )
        context_packer = component_registry.build(
            category="context_packer",
            config=pipeline_config.context_packer,
            build_context={
                "project_root": project_root,
                "runtime_config": cfg,
            },
        )
        prompt_builder = ParentChildPromptBuilder()
        run_capture = RagRunCapture(cfg.capture_output)

        answer_llm = None
        query_llm = None
        model_name = None
        model_provider = None
        if cfg.enable_llm:
            answer_llm = LocalLLMGenerator(
                model_name=cfg.llm_model,
                device=cfg.llm_device,
            )
            model_name = cfg.llm_model
            model_provider = cfg.model_provider
        if cfg.enable_query_expansion_llm:
            can_reuse = (
                answer_llm is not None
                and str(cfg.query_expansion_llm_model) == str(cfg.llm_model)
                and str(cfg.query_expansion_llm_device) == str(cfg.llm_device)
            )
            query_llm = answer_llm if can_reuse else LocalLLMGenerator(
                model_name=cfg.query_expansion_llm_model,
                device=cfg.query_expansion_llm_device,
            )

        query_transform_build_context = {
            "project_root": project_root,
            "runtime_config": cfg,
            "query_llm_generator": query_llm,
            "enable_query_expansion_llm": cfg.enable_query_expansion_llm,
            "query_expansion_generation_params": {
                "rewrite_max_new_tokens": cfg.query_rewrite_max_new_tokens,
                "hyde_max_new_tokens": cfg.query_hyde_max_new_tokens,
                "temperature": cfg.query_expansion_temperature,
                "top_p": cfg.query_expansion_top_p,
                "do_sample": cfg.query_expansion_do_sample,
            },
        }
        query_transformers = [
            component_registry.build(
                category="query_transformer",
                config=item,
                build_context=query_transform_build_context,
            )
            for item in pipeline_config.query_transformers
            if item.enabled
        ]
        query_transform_chain = QueryTransformChain(
            query_transformers,
            profile_id=pipeline_config.profile_id,
            profile_version=pipeline_config.profile_version,
        )

        quality_llm = query_llm or answer_llm
        quality_build_context = {
            "project_root": project_root,
            "runtime_config": cfg,
            "quality_llm_generator": quality_llm,
            "enable_quality_llm": bool(
                cfg.enable_query_expansion_llm and quality_llm is not None
            ),
            "quality_generation_params": {
                "temperature": cfg.query_expansion_temperature,
                "top_p": cfg.query_expansion_top_p,
                "do_sample": cfg.query_expansion_do_sample,
            },
        }
        evidence_grader = component_registry.build(
            category="evidence_grader",
            config=pipeline_config.evidence_grader,
            build_context=quality_build_context,
        )
        generation_checker = component_registry.build(
            category="generation_checker",
            config=pipeline_config.generation_checker,
            build_context=quality_build_context,
        )
        repair_strategy = component_registry.build(
            category="repair_strategy",
            config=pipeline_config.repair_strategy,
            build_context=quality_build_context,
        )

        # External profile is the source of truth for migrated slots. Legacy
        # runtime fields remain available only for compatibility and audit.
        requested_legacy_rerank_top_k = int(cfg.rerank_top_k)
        cfg.legacy_reranker_overrides = {
            "skip_rerank": bool(cfg.skip_rerank),
            "rerank_top_k": requested_legacy_rerank_top_k,
            "ignored_by_configured_reranker": True,
        }
        cfg.rerank_top_k = int(getattr(reranker, "top_k", cfg.rerank_top_k))
        cfg.max_context_chars = int(context_packer.max_context_chars)
        cfg.max_context_items = int(context_packer.max_items)
        cfg.pipeline_name = pipeline_config.profile_id
        cfg.pipeline_version = pipeline_config.profile_version
        cfg.pipeline_config_hash = pipeline_config.config_hash()
        cfg.pipeline_schema_version = pipeline_config.schema_version
        cfg.pipeline_profile_id = pipeline_config.profile_id
        cfg.pipeline_profile_version = pipeline_config.profile_version
        cfg.profile_catalog_validation = catalog_report.to_dict()

        def _component_record(instance: Any, component_config: Any) -> dict[str, Any]:
            return {
                **instance.plugin_metadata.to_dict(),
                "enabled": bool(component_config.enabled),
                "params": dict(component_config.params),
            }

        cfg.pipeline_component_metadata = {
            "query_transformers": [
                _component_record(instance, component_config)
                for instance, component_config in zip(
                    query_transformers,
                    [item for item in pipeline_config.query_transformers if item.enabled],
                    strict=True,
                )
            ],
            "retrievers": [
                _component_record(instance, component_config)
                for instance, component_config in zip(
                    retrievers,
                    enabled_retriever_configs,
                    strict=True,
                )
            ],
            "fusion": _component_record(fusion, pipeline_config.fusion),
            "query_fusion": _component_record(
                query_fusion, pipeline_config.query_fusion
            ),
            "candidate_enricher": _component_record(
                candidate_enricher, pipeline_config.candidate_enricher
            ),
            "reranker": _component_record(reranker, pipeline_config.reranker),
            "evidence_grader": _component_record(
                evidence_grader, pipeline_config.evidence_grader
            ),
            "context_packer": _component_record(
                context_packer, pipeline_config.context_packer
            ),
            "generation_checker": _component_record(
                generation_checker, pipeline_config.generation_checker
            ),
            "repair_strategy": _component_record(
                repair_strategy, pipeline_config.repair_strategy
            ),
        }

        engine = ParentChildRAGEngine(
            retrievers=retrievers,
            fusion=fusion,
            query_fusion=query_fusion,
            candidate_enricher=candidate_enricher,
            reranker=reranker,
            context_packer=context_packer,
            prompt_builder=prompt_builder,
            evidence_grader=evidence_grader,
            generation_checker=generation_checker,
            repair_strategy=repair_strategy,
            run_capture=run_capture,
            llm_generator=answer_llm,
            model_name=model_name,
            model_provider=model_provider,
            query_llm_generator=query_llm,
            query_transform_chain=query_transform_chain,
            enable_query_expansion_llm=cfg.enable_query_expansion_llm,
            query_expansion_generation_params={
                "rewrite_max_new_tokens": cfg.query_rewrite_max_new_tokens,
                "hyde_max_new_tokens": cfg.query_hyde_max_new_tokens,
                "temperature": cfg.query_expansion_temperature,
                "top_p": cfg.query_expansion_top_p,
                "do_sample": cfg.query_expansion_do_sample,
            },
            pipeline_name=cfg.pipeline_name,
            pipeline_version=cfg.pipeline_version,
        )
        return engine, cfg
