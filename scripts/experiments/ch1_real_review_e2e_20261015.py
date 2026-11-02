"""One approved, finite real-provider Chapter-1 E2E; no retries/relaunch.

PREPARE is read-only for business sources and does not use paid providers.
EXECUTE creates one new Task/Run under E:, exclusively routes all ModelRoles
to the configured DeepSeek provider, and uses the real RAG catalog/index.
No static ProjectInput fact is generated from the user's instruction query.
No frozen Gate/Release/Claim authority is changed.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from unittest.mock import patch

ROOT = Path("D:/MyCode/rag-agent-shared").resolve()
SOURCE = Path("E:/rag-agent-evaluation/RC1_CH1_MINIMAL_REPAIR_20261013_ec10882c/input05/input_project_candidate.json")
EXPECTED_SOURCE_SHA = "36b464ed7950ba5010347f4e9ecbc3537ec2e3abe40651a907f83036efc19657"
EVAL_ROOT = Path("E:/rag-agent-evaluation/CH1_REVIEW_REAL_PROVIDER_20261015_V2")
RUNTIME = EVAL_ROOT / "runtime"
RAG_ROOT = Path("D:/MyCode/rag-agent")
CATALOG = RAG_ROOT / "data/catalog/document_profiles_v1.jsonl"
RUN_ID = "rc1-ch1-review-real-20261015-v2"
TASK_ID = "task-rc1-ch1-review-real-20261015-v2"
HELPER = Path("E:/rag-agent-tmp/post-step7-real-provider-hardening-20260913-session/observer/native_overlay_startup.py")
NATIVE_MANIFEST = Path("E:/rag-agent-tmp/post-step7-real-provider-hardening-20260913-session/native-diagnostics/faiss-1.9.0-overlay-manifest.json")

# Bound the *single* real E2E; not a promise of Quality PASS.
LIMITS = {
    "logical_model_calls": 80,
    "logical_tool_calls": 6,
    "model_tokens_total": 512000,
    "research_rounds": 1,
    "repair_attempts": 1,
    "generation_attempts": 2,
    "replan_attempts": 1,
    "true_retry_attempts": 1,
    "max_elapsed_seconds": 1800,
    "policy_ref": "budget-policy://multi-agent-v3/rc1-ch1-review-20261015-v1",
    "policy_version": "v1",
}
DEEPSEEK_MAX_OUTPUT_TOKENS = 8192
SCOPE_KBS = [
    "historical_case_kb", "policy_standard_kb", "project_fact_kb",
    "technical_reference_kb", "template_style_kb",
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_safe(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def save(name: str, value) -> None:
    dest = EVAL_ROOT / name
    if dest.exists():
        raise FileExistsError(f"will not overwrite {dest}")
    with dest.open("x", encoding="utf-8") as fp:
        json.dump(json_safe(value), fp, ensure_ascii=False, indent=2, default=str)
        fp.write("\n")
        fp.flush()
        os.fsync(fp.fileno())


def source_status() -> dict:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    rows = []
    for row in status.splitlines():
        relative = row[3:].split(" -> ")[-1].strip('"')
        path = ROOT / relative
        # Windows NUL/CON are device aliases, never read them as source files.
        if relative.upper().split(".", 1)[0] in {"NUL", "CON", "PRN", "AUX"}:
            continue
        if path.is_file():
            rows.append({"path": relative, "sha256": digest(path.read_bytes())})
    return {
        "head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True,
        ).stdout.strip(),
        "dirty_paths": len(rows),
        "dirty_manifest_sha256": digest(json.dumps(
            rows, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")),
    }


def load_source() -> dict:
    raw = SOURCE.read_bytes()
    if digest(raw) != EXPECTED_SOURCE_SHA:
        raise ValueError("previously confirmed simulated ProjectInput changed")
    project = json.loads(raw.decode("utf-8-sig"))
    if project.get("schema_version") != "project_input_v3":
        raise ValueError("this is not the authorized ProjectInput V3 fixture")
    sections = project.get("generation_requirements", {}).get("required_sections", [])
    if sections != ["项目背景"]:
        raise ValueError("chapter scope changed from authorized first chapter")
    if project.get("output_schema", {}).get("required_sections") != sections:
        raise ValueError("output required sections differ")
    if project.get("output_schema", {}).get("output_format") != "markdown":
        raise ValueError("expected existing markdown renderer")
    if not project.get("business_goal") or not project.get("user_query"):
        raise ValueError("simulated project background and query must be nonempty")
    if not (project.get("metadata") or {}).get("is_simulated"):
        raise ValueError("source material must preserve explicit simulated flag")
    project["task_id"] = TASK_ID
    # Exact business facts, including 12 NEW servers and unknown acquisition/
    # deployment state, remain byte-identical inside existing source fields.
    return project


def prepare() -> dict:
    if EVAL_ROOT.exists() and any(EVAL_ROOT.iterdir()):
        raise FileExistsError("this campaign already exists; never relaunch")
    if RUNTIME.exists():
        raise FileExistsError("new native Run root must not exist")
    project = load_source()
    if not CATALOG.is_file() or not (RAG_ROOT / "data/processed/indexes/active_index.json").is_file():
        raise FileNotFoundError("original RAG index or authorized catalog missing")
    if not HELPER.is_file() or not NATIVE_MANIFEST.is_file():
        raise FileNotFoundError("existing H7 native dependency bootstrap unavailable")
    os.environ["USE_REAL_RAG_TOOL"] = "true"
    os.environ["RAG_PROJECT_ROOT"] = str(RAG_ROOT)
    os.environ["DEFAULT_MODEL_NAME"] = "deepseek_api"
    os.environ["SUPERVISOR_MODEL_NAME"] = "deepseek_api"
    os.environ["ENABLE_LLM_ROUTING"] = "true"

    sys.path.insert(0, str(ROOT / "backend"))
    from core.config import get_settings
    from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
    from apps.enterprise_document.services.agent_rag_v1.disk_guard import DiskBudgetGuard
    import yaml
    from apps.enterprise_document.config.grounded_generation import GroundedGenerationPolicyConfig
    settings = get_settings(reload=True)
    if not settings.deepseek_api_key:
        raise RuntimeError("no configured DeepSeek credential; no paid call")
    if settings.deepseek_model_name != "deepseek_api":
        raise RuntimeError("unexpected DeepSeek route")
    # Normal RAG and canonical Artifact writes must pass unmodified SIF-13.
    import shutil
    roots = [RUNTIME, RAG_ROOT]
    # No output directory exists yet; inspect existing parent volumes without
    # touching the source index or creating a run during preflight.
    free = {
        str(RUNTIME): shutil.disk_usage(EVAL_ROOT.parent).free,
        str(RAG_ROOT): shutil.disk_usage(RAG_ROOT).free,
    }
    if free[str(RUNTIME)] < 5 * 1024 ** 3:
        raise RuntimeError("E drive cannot admit a new durable Run")
    base_policy = yaml.safe_load((
        ROOT / "backend/apps/enterprise_document/config/grounded_generation_v1.yaml"
    ).read_text(encoding="utf-8"))
    base_policy["canonical_budget"] = LIMITS
    # The legacy section-loop schema caps this field at 64; it is separate
    # from the native canonical per-run model-call cap of 80.
    base_policy["max_total_llm_calls"] = min(64, LIMITS["logical_model_calls"])
    base_policy["max_total_tokens"] = LIMITS["model_tokens_total"]
    GroundedGenerationPolicyConfig.model_validate(base_policy)
    ProjectInputSchema.model_validate(project)
    EVAL_ROOT.mkdir(parents=True, exist_ok=False)
    save("project_input.json", project)
    policy_text = yaml.safe_dump(base_policy, allow_unicode=True, sort_keys=False)
    policy_path = EVAL_ROOT / "finite_generation_policy.yaml"
    policy_path.write_text(policy_text, encoding="utf-8")
    save("preflight.json", {
        "status": "READY_FOR_SINGLE_FINITE_REAL_RUN",
        "at_utc": now(), "source": str(SOURCE), "source_sha256": digest(SOURCE.read_bytes()),
        "input_sha256": digest((EVAL_ROOT / "project_input.json").read_bytes()),
        "limits": LIMITS, "provider": settings.deepseek_model_name,
        "configured_served_model": settings.deepseek_provider_model_name,
        "thinking_enabled": settings.deepseek_thinking_enabled,
        "provider_credential_configured": True, "credential_value_persisted": False,
        "rag_root": str(RAG_ROOT), "catalog_sha256": digest(CATALOG.read_bytes()),
        "native_helper_sha256": digest(HELPER.read_bytes()),
        "native_manifest_sha256": digest(NATIVE_MANIFEST.read_bytes()),
        "disk_free_bytes": free, "source_status": source_status(),
        "allow_fake_llm": False, "allow_fake_rag": False,
        "model_roles": "all deepseek/no fallback",
        "max_provider_output_tokens": DEEPSEEK_MAX_OUTPUT_TOKENS,
        "paid_runs_authorized": 1, "automatic_reruns": 0,
    })
    return {"status": "READY", "root": str(EVAL_ROOT), "task": TASK_ID, "run": RUN_ID}


def activate_h7() -> None:
    spec = importlib.util.spec_from_file_location("ch1_native_overlay", HELPER)
    if spec is None or spec.loader is None:
        raise ImportError("native overlay loader missing")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.activate_native_overlay(NATIVE_MANIFEST)


def execute() -> int:
    p = json.loads((EVAL_ROOT / "preflight.json").read_text(encoding="utf-8"))
    if p.get("status") != "READY_FOR_SINGLE_FINITE_REAL_RUN":
        raise ValueError("exact preflight missing")
    if (EVAL_ROOT / "started.json").exists() or RUNTIME.exists():
        raise FileExistsError("run has already been attempted; no relaunch")
    if source_status()["dirty_manifest_sha256"] != p["source_status"]["dirty_manifest_sha256"]:
        raise RuntimeError("dirty source file drift since preflight")
    if digest(SOURCE.read_bytes()) != EXPECTED_SOURCE_SHA:
        raise RuntimeError("source changed after preflight")
    if digest(HELPER.read_bytes()) != p["native_helper_sha256"]:
        raise RuntimeError("native helper drift")
    if digest(NATIVE_MANIFEST.read_bytes()) != p["native_manifest_sha256"]:
        raise RuntimeError("native overlay manifest drift")

    os.environ.update({
        "USE_REAL_RAG_TOOL": "true",
        "RAG_PROJECT_ROOT": str(RAG_ROOT),
        "DEFAULT_MODEL_NAME": "deepseek_api",
        "SUPERVISOR_MODEL_NAME": "deepseek_api",
        "ENABLE_LLM_ROUTING": "true",
        "GROUNDED_GENERATION_POLICY_FILE": str(EVAL_ROOT / "finite_generation_policy.yaml"),
    })
    activate_h7()
    for directory in (ROOT / "backend", ROOT / "scripts"):
        sys.path.insert(0, str(directory))

    from application.mainline_service import MainlineApplicationService
    from bootstrap.document_catalog_adapter import DocumentProfileCatalogAdapter
    from bootstrap.model_factory import ModelGatewayFactory, build_default_model_routing
    from bootstrap.supervisor_factory import SupervisorFactory
    from core.config import get_settings
    from model_gateway.fake_llm_client import FakeLLMClient
    from model_gateway.model_contract import ModelRole, ModelRoutingConfig, RoutingPolicy
    from model_gateway.model_gateway import ModelGateway
    from model_gateway.model_router import ModelRouter
    from model_gateway.integrations.langchain.chat_model_client import LangChainChatModelClient
    from tools.fake_rag_tool import FakeRAGTool
    from schemas.rag import RetrievalAccessScopeSchema
    from langchain_core.globals import set_llm_cache

    settings = replace(
        get_settings(reload=True), default_model_name="deepseek_api",
        supervisor_model_name="deepseek_api", data_root=RUNTIME,
        deepseek_max_output_tokens=DEEPSEEK_MAX_OUTPUT_TOKENS,
        trace_enabled=True, data_capture_enabled=True,
    )
    if not settings.deepseek_api_key:
        raise RuntimeError("DeepSeek credential unavailable")
    set_llm_cache(None)

    class OneDeepSeekFactory(ModelGatewayFactory):
        def build(self, settings, trace_sink=None):
            base = build_default_model_routing(settings)
            matches = [x for x in base.profiles if x.profile_id == "deepseek" and x.selectable]
            if len(matches) != 1:
                raise RuntimeError("existing selectable DeepSeek provider absent")
            profile = matches[0]
            routing = ModelRoutingConfig(
                profiles=[profile],
                policies=[RoutingPolicy(role=role, candidates=["deepseek"],
                                        availability_fallback=False) for role in ModelRole],
            )
            gateway = ModelGateway(
                default_model_name="deepseek_api", run_trace_recorder=trace_sink,
                router=ModelRouter("deepseek_api", profiles=routing.profiles,
                                   policies=routing.policies),
            )
            provider = self.provider_factory.build(profile, settings=settings)
            if not isinstance(provider, LangChainChatModelClient):
                raise RuntimeError("real DeepSeek Chat client not available")
            if hasattr(provider.chat_model, "cache"):
                provider.chat_model.cache = False
            gateway.register_client(provider)
            return gateway

    class Catalog(DocumentProfileCatalogAdapter):
        def list_access_descriptors(self, *, project_root):
            return super().list_access_descriptors(project_root=RAG_ROOT)

    class Factory(SupervisorFactory):
        def __init__(self):
            super().__init__(model_factory=OneDeepSeekFactory())

    def fake_forbidden(*args, **kwargs):
        raise AssertionError("fake model or RAG dispatch is forbidden")

    def trace_gateway(self, request):
        selection = self.router.plan(request)
        if len(selection) != 1 or selection[0].profile_id != "deepseek":
            raise AssertionError("every role must use DeepSeek without fallback")
        return original_generate(self, request)

    original_generate = ModelGateway.generate
    scope = RetrievalAccessScopeSchema(
        tenant_id="default", authorized_kb_ids=SCOPE_KBS,
    )
    project = json.loads((EVAL_ROOT / "project_input.json").read_text(encoding="utf-8"))
    save("started.json", {"utc": now(), "task_id": TASK_ID, "run_id": RUN_ID,
                          "single_paid_run": True, "max_billed_tokens": 512000})
    RUNTIME.mkdir(parents=True, exist_ok=False)
    begin = time.monotonic()
    summary = None
    failure = None
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(FakeLLMClient, "generate", fake_forbidden))
            stack.enter_context(patch.object(FakeRAGTool, "run", fake_forbidden))
            stack.enter_context(patch.object(ModelGateway, "generate", trace_gateway))
            with (EVAL_ROOT / "console.log").open("x", encoding="utf-8") as output:
                with redirect_stdout(output), redirect_stderr(output):
                    summary = MainlineApplicationService(
                        supervisor_factory=Factory(), document_catalog=Catalog(),
                    ).run(
                        project_root=ROOT, user_input=project["user_query"],
                        task_id=TASK_ID, run_id=RUN_ID, output_root=RUNTIME,
                        clean_existing=False, settings=settings,
                        project_input=project, allow_demo_defaults=False,
                        enable_agent_self_rag=False, durable_memory_required=False,
                        retrieval_access_scope=scope,
                    )
        save("summary.json", summary)
    except BaseException as exc:
        failure = {"type": type(exc).__name__, "message": str(exc),
                   "traceback": traceback.format_exc()}
        save("exception.json", failure)
    finally:
        save("execution.json", {
            "task_id": TASK_ID, "run_id": RUN_ID, "end_utc": now(),
            "elapsed_seconds": round(time.monotonic() - begin, 3),
            "summary_status": summary.get("status") if summary else None,
            "quality_state": (summary.get("terminal_outcome") or {}).get("quality_state")
                if summary else None,
            "control_action": (summary.get("terminal_outcome") or {}).get("control_action")
                if summary else None,
            "review_draft": summary.get("target_review_draft") if summary else None,
            "exception": failure,
            "source_status_end": source_status(),
        })
    print(json.dumps({
        "event": "ONE_REAL_CH1_E2E_FINISHED", "task_id": TASK_ID, "run_id": RUN_ID,
        "successfully_returned_summary": summary is not None,
        "summary_status": summary.get("status") if summary else None,
        "quality_state": (summary.get("terminal_outcome") or {}).get("quality_state")
            if summary else None,
        "output_root": str(EVAL_ROOT),
        "error_type": failure.get("type") if failure else None,
    }), flush=True)
    return 0 if summary is not None and failure is None else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "execute"])
    args = parser.parse_args()
    if args.action == "prepare":
        print(json.dumps(prepare(), ensure_ascii=False))
    else:
        raise SystemExit(execute())
