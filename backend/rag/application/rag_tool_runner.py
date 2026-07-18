"""Execute an already-composed RAG engine and map its legacy dict response."""

from __future__ import annotations

from typing import Any, Dict

from rag.common.coercion import split_csv
from rag.common.presentation import compact_contexts


class RAGToolRunner:
    def run(self, engine: Any, cfg: Any, tool_input: Dict[str, Any], tool_name: str) -> Dict[str, Any]:
        if not isinstance(tool_input, dict):
            raise TypeError("tool_input must be a dict")
        query = str(tool_input.get("query") or "").strip()
        if not query:
            raise ValueError("tool_input['query'] cannot be empty")
        generation_params = dict(tool_input.get("generation_params") or {})
        if not generation_params:
            generation_params = {
                "max_new_tokens": int(tool_input.get("max_new_tokens", cfg.max_new_tokens)),
                "temperature": float(tool_input.get("temperature", cfg.temperature)),
                "top_p": float(tool_input.get("top_p", cfg.top_p)),
                "do_sample": bool(tool_input.get("do_sample", cfg.do_sample)),
            }
        generate_answer = tool_input.get("generate_answer", cfg.enable_llm)
        user_extra_metadata = dict(tool_input.get("extra_metadata") or {})
        result = engine.run(
            query=query,
            dense_top_k=int(tool_input.get("dense_top_k", cfg.dense_top_k)),
            keyword_top_k=int(tool_input.get("keyword_top_k", cfg.keyword_top_k)),
            candidate_top_k=int(tool_input.get("candidate_top_k", cfg.candidate_top_k)),
            rrf_k=int(tool_input.get("rrf_k", cfg.rrf_k)),
            rerank_top_k=int(tool_input.get("rerank_top_k", cfg.rerank_top_k)),
            eval_top_k=int(tool_input.get("eval_top_k", cfg.eval_top_k)),
            expected_doc_ids=split_csv(tool_input.get("expected_doc_ids")),
            expected_parent_chunk_ids=split_csv(tool_input.get("expected_parent_chunk_ids")),
            expected_child_chunk_ids=split_csv(tool_input.get("expected_child_chunk_ids")),
            expected_keywords=split_csv(tool_input.get("expected_keywords")),
            filter_expr=(str(tool_input.get("filter_expr") or "").strip() or None),
            keyword_doc_ids=split_csv(tool_input.get("keyword_doc_ids")),
            generate_answer=bool(generate_answer),
            generation_params=generation_params if bool(generate_answer) else None,
            retrieval_strategy=str(tool_input.get("retrieval_strategy", cfg.retrieval_strategy)),
            num_rewrites=int(tool_input.get("num_rewrites", cfg.num_rewrites)),
            enable_hyde=bool(tool_input.get("enable_hyde", cfg.enable_hyde)),
            enable_crag=bool(tool_input.get("enable_crag", cfg.enable_crag)),
            enable_self_rag=bool(tool_input.get("enable_self_rag", cfg.enable_self_rag)),
            crag_max_judge_chunks=int(tool_input.get("crag_max_judge_chunks", cfg.crag_max_judge_chunks)),
            crag_drop_irrelevant=bool(tool_input.get("crag_drop_irrelevant", cfg.crag_drop_irrelevant)),
            extra_metadata={
                **user_extra_metadata,
                "request_context": user_extra_metadata,
                "tool_name": tool_name,
                "tool_stage": "rag_tool_v1",
                "offline_index": dict(getattr(cfg, "index_lineage", {}) or {}),
                "pipeline_config": {
                    "path": str(getattr(cfg, "pipeline_config_file", "")),
                    "schema_version": getattr(
                        cfg, "pipeline_schema_version", None
                    ),
                    "profile_id": getattr(cfg, "pipeline_profile_id", None),
                    "profile_version": getattr(
                        cfg, "pipeline_profile_version", None
                    ),
                    "hash": getattr(cfg, "pipeline_config_hash", None),
                    "components": getattr(cfg, "pipeline_component_metadata", {}),
                    "catalog_validation": getattr(
                        cfg, "profile_catalog_validation", {}
                    ),
                },
                "legacy_reranker_config": getattr(
                    cfg, "legacy_reranker_overrides", {}
                ),
                "query_expansion_llm_config": {
                    "enabled": cfg.enable_query_expansion_llm,
                    "model": cfg.query_expansion_llm_model,
                    "device": cfg.query_expansion_llm_device,
                    "rewrite_max_new_tokens": cfg.query_rewrite_max_new_tokens,
                    "hyde_max_new_tokens": cfg.query_hyde_max_new_tokens,
                },
                "experiment": user_extra_metadata,
                "quality_control_config": {
                    "adaptive_rag_requested": str(
                        tool_input.get("retrieval_strategy", cfg.retrieval_strategy)
                    ).strip().lower().replace("-", "_")
                    in {"adaptive", "adaptive_rag", "adaptive_rag_lite"},
                    "enable_crag": bool(tool_input.get("enable_crag", cfg.enable_crag)),
                    "enable_self_rag": bool(tool_input.get("enable_self_rag", cfg.enable_self_rag)),
                    "crag_max_judge_chunks": int(tool_input.get("crag_max_judge_chunks", cfg.crag_max_judge_chunks)),
                    "crag_drop_irrelevant": bool(tool_input.get("crag_drop_irrelevant", cfg.crag_drop_irrelevant)),
                },
            },
        )
        data: Dict[str, Any] = {
            "run_id": result.get("run_id"),
            "query": result.get("query"),
            "query_expansion": result.get("query_expansion") or {},
            "adaptive_rag": result.get("adaptive_rag"),
            "c_rag": result.get("c_rag"),
            "self_rag": result.get("self_rag"),
            "answer": result.get("answer"),
            "contexts": compact_contexts(result.get("retrieval_results") or []),
            "citations": result.get("citations") or [],
            "eval_result": result.get("eval_result") or {},
            "capture_result": result.get("capture_result"),
            "model_name": result.get("model_name"),
            "model_provider": result.get("model_provider"),
        }
        if bool(tool_input.get("return_prompt", False)):
            data["prompt"] = result.get("prompt")
        if bool(tool_input.get("return_full_record", False)):
            data["run_record"] = result.get("run_record")
            data["retrieval_results"] = result.get("retrieval_results") or []
            data["context_pack"] = result.get("context_pack") or {}
        return {
            "success": True,
            "tool_name": tool_name,
            "data": data,
            "error": None,
            "metadata": {
                "pipeline_name": cfg.pipeline_name,
                "pipeline_version": cfg.pipeline_version,
                "capture_output": cfg.capture_output,
                "retrieval_strategy": result.get("query_expansion", {}).get("strategy"),
                "query_expansion": result.get("query_expansion") or {},
                "adaptive_rag": result.get("adaptive_rag"),
                "c_rag": result.get("c_rag"),
                "self_rag": result.get("self_rag"),
                "query_expansion_llm_enabled": cfg.enable_query_expansion_llm,
                "query_expansion_llm_model": cfg.query_expansion_llm_model,
                "pipeline_config_file": str(getattr(cfg, "pipeline_config_file", "")),
                "pipeline_config_hash": getattr(cfg, "pipeline_config_hash", None),
                "pipeline_components": getattr(cfg, "pipeline_component_metadata", {}),
                "offline_index": dict(getattr(cfg, "index_lineage", {}) or {}),
                "pipeline_config": {
                    "schema_version": getattr(
                        cfg, "pipeline_schema_version", None
                    ),
                    "profile_id": getattr(cfg, "pipeline_profile_id", None),
                    "profile_version": getattr(
                        cfg, "pipeline_profile_version", None
                    ),
                    "path": str(getattr(cfg, "pipeline_config_file", "")),
                    "hash": getattr(cfg, "pipeline_config_hash", None),
                    "catalog_validation": getattr(
                        cfg, "profile_catalog_validation", {}
                    ),
                },
            },
        }
