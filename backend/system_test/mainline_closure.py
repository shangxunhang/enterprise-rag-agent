"""Reusable deterministic scenarios for mainline closure acceptance."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

from core.config import get_settings
from mainline_runtime import build_project_input
from run_demo_back import run_demo


def run_fake_mainline_scenario(
    output_root: str | Path,
    *,
    run_id: str,
    rag_scenario: str,
    llm_scenario: str,
    enable_corrective_retrieval: bool,
    user_input: str = "生成一个政务云建设方案",
    citation_required_sections: list[str] | None = None,
) -> Dict[str, Any]:
    """Run the formal mainline with deterministic test doubles.

    The helper changes only opt-in fake scenario environment variables and
    always restores the caller environment and settings cache.
    """

    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    task_id = f"task_{run_id}"
    env_keys = (
        "USE_REAL_RAG_TOOL",
        "ENABLE_AGENT_SELF_RAG",
        "ENABLE_SEMANTIC_GATE",
        "FAKE_RAG_SCENARIO",
        "FAKE_LLM_SCENARIO",
        "FAKE_LLM_TARGET_SECTION",
    )
    old_env = {key: os.environ.get(key) for key in env_keys}
    try:
        os.environ["USE_REAL_RAG_TOOL"] = "false"
        os.environ["ENABLE_AGENT_SELF_RAG"] = "false"
        os.environ["ENABLE_SEMANTIC_GATE"] = "false"
        os.environ["FAKE_RAG_SCENARIO"] = rag_scenario
        os.environ["FAKE_LLM_SCENARIO"] = llm_scenario
        os.environ["FAKE_LLM_TARGET_SECTION"] = "安全设计"

        base = get_settings(reload=True)
        settings = replace(
            base,
            data_root=output_root,
            run_trace_dir=output_root / "runs",
            data_capture_dir=output_root / "captures",
            eval_output_dir=output_root / "eval_outputs",
            task_state_dir=output_root / "tasks",
            default_model_name="fake_llm",
            supervisor_model_name="fake_llm",
            enable_llm_routing=False,
            trace_enabled=True,
            data_capture_enabled=True,
        )
        project_input = build_project_input(
            task_id,
            user_input,
            allow_demo_defaults=True,
        ).model_dump()
        project_input["generation_requirements"]["extra"].update(
            {
                "enable_section_aware_retrieval": True,
                "enable_corrective_section_retrieval": enable_corrective_retrieval,
            }
        )
        if citation_required_sections is not None:
            project_input["generation_requirements"]["citation_required_sections"] = list(
                citation_required_sections
            )

        return run_demo(
            user_input=user_input,
            run_id=run_id,
            task_id=task_id,
            output_root=output_root,
            clean_existing=True,
            settings=settings,
            retrieval_strategy="hybrid",
            enable_agent_self_rag=False,
            project_input=project_input,
            allow_demo_defaults=False,
        )
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings(reload=True)
