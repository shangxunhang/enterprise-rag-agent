"""EXPERIMENT ONLY — force every active LLM model role to DeepSeek for one E2E run.

DO NOT MERGE this routing override into production.  The wrapper patches only the
current Python process.  Retrieval/indexing, embedding, reranking, evidence
governance, citation grounding, claim policy and document hard gates are unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
BACKEND_ROOT = PROJECT_ROOT / "backend"
for root in (SCRIPT_ROOT, BACKEND_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

import bootstrap.model_factory as model_factory
from model_gateway.model_contract import ModelRole, ModelRoutingConfig, RoutingPolicy
import run_demo


_ORIGINAL_BUILD_ROUTING = model_factory.build_default_model_routing


def _all_deepseek_routing(settings) -> ModelRoutingConfig:
    base = _ORIGINAL_BUILD_ROUTING(settings)
    deepseek_profiles = [p for p in base.profiles if p.profile_id == "deepseek"]
    if len(deepseek_profiles) != 1 or not deepseek_profiles[0].selectable:
        raise RuntimeError("deepseek model profile is not selectable")
    return ModelRoutingConfig(
        profiles=list(base.profiles),
        policies=[
            RoutingPolicy(role=role, candidates=["deepseek"])
            for role in ModelRole
        ],
    )


def main() -> None:
    if not str(os.getenv("DEEPSEEK_API_KEY") or "").strip():
        raise RuntimeError("DEEPSEEK_API_KEY is not set in this PowerShell session")

    # Patch both references used by the E2E entry point and ModelGatewayFactory.
    # This lasts only for this process and does not alter production source files.
    model_factory.build_default_model_routing = _all_deepseek_routing
    run_demo.build_default_model_routing = _all_deepseek_routing

    print("[Experiment] ALL ACTIVE LLM ROLES => deepseek", flush=True)
    print("[Experiment] production routing files are NOT modified", flush=True)
    run_demo.main()


if __name__ == "__main__":
    main()
