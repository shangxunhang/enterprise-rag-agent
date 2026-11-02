# =============================================================================
# 中文阅读说明：端到端演示入口：装配配置和依赖，构造示例 ProjectInput，运行 Agent-RAG 主链并输出结果。
# 主要定义：_normalize_status_value、_effective_runtime_error、_is_business_gate_failure、_resolve_console_question、_configure_runtime、_preflight_real_runtime、_load_trace_events、_validate_end_to_end、_write_answer_file、_section_report_rows等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Run the Agent-RAG end-to-end console demonstration.

PyCharm users can run this file directly with no parameters. The script then
prompts for one user requirement and executes the complete native mainline:

user input -> ProjectInput -> Supervisor -> WorkflowEngine -> Agent nodes
-> real RAG -> Context Manager -> local LLM -> quality gates -> final document
-> Trace/DataCapture artifacts.

``run_pipeline.py`` remains the production-style non-interactive entry point.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
BACKEND_ROOT = PROJECT_ROOT / 'backend'

import importlib.util as _source_util
import sys as _source_sys
from pathlib import Path as _SourcePath

_source_file = PROJECT_ROOT / 'source_bootstrap.py'
_source_name = 'source_bootstrap'
if _source_name in _source_sys.modules:
    _source_module = _source_sys.modules[_source_name]
    try:
        _source_values = vars(_source_module)
        _source_spec = _source_values.get('__spec__')
        if (
            _source_spec is None
            or _source_values.get('__path__') is not None
            or _source_spec.submodule_search_locations is not None
            or _SourcePath(_source_values['__file__']).resolve() != _source_file
            or _SourcePath(_source_spec.origin).resolve() != _source_file
            or not callable(_source_values.get('bootstrap_source_root'))
        ):
            raise ImportError('SOURCE_ROOT_REJECTED: cached trusted helper')
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as _source_error:
        raise ImportError('SOURCE_ROOT_REJECTED: malformed cached trusted helper') from _source_error
else:
    _source_spec = _source_util.spec_from_file_location(_source_name, _source_file)
    if _source_spec is None or _source_spec.loader is None:
        raise ImportError('SOURCE_ROOT_REJECTED: unavailable trusted helper')
    _source_module = _source_util.module_from_spec(_source_spec)
    _source_sys.modules[_source_name] = _source_module
    # Preserve the cached module if execution/initialization fails; never evict.
    _source_spec.loader.exec_module(_source_module)
_source_contract = _source_module.bootstrap_source_root(PROJECT_ROOT)

from bootstrap.model_factory import build_default_model_routing
from core.config import AppSettings, get_settings
from experiments.manifest import ExperimentRunManifestWriter
from mainline_runtime import (
    MAINLINE_RUNTIME_VERSION,
    build_project_input,
    build_supervisor,
    build_task,
    run_mainline,
    resolve_project_path,
)
from rag.offline.resolver import ActiveIndexResolver
from schemas.rag import RetrievalAccessScopeSchema

_source_module.assert_loaded_project_origins()


RUN_DEMO_VERSION = f"{MAINLINE_RUNTIME_VERSION}+e2e-console-v5"
DEFAULT_USER_INPUT = "根据资料生成企业级 RAG-Agent 系统建设方案"
REAL_MODEL_NAME = "local_qwen2_5_1_5b"
SUCCESS_LIKE_STATUSES = {"success", "partial_success"}
RUN_DEMO_LOCK_PATH = PROJECT_ROOT / "data" / "runtime" / "run_demo.lock"


def _configured_experiment_seed() -> Optional[int]:
    raw = os.getenv("EXPERIMENT_SEED")
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError("EXPERIMENT_SEED must be an integer") from exc


def _apply_experiment_seed(seed: Optional[int]) -> Dict[str, bool]:
    """Apply the manifest seed to actual runtime RNGs before execution."""

    applied = {"python_random": False, "numpy": False, "torch": False}
    if seed is None:
        return applied

    import random

    random.seed(seed)
    applied["python_random"] = True
    try:
        import numpy as np

        np.random.seed(seed)
        applied["numpy"] = True
    except ImportError:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        applied["torch"] = True
    except ImportError:
        pass
    return applied


class RunDemoAlreadyRunningError(RuntimeError):
    """Raised when another direct run_demo.py process already owns the demo lock."""


@contextmanager
def _single_instance_lock(lock_path: Path = RUN_DEMO_LOCK_PATH):
    """Hold one non-blocking OS file lock for the lifetime of a direct demo run."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    acquired = False
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RunDemoAlreadyRunningError(
                "Another scripts/run_demo.py instance is already running. "
                f"Lock: {lock_path}"
            ) from exc

        acquired = True
        yield
    finally:
        if acquired:
            try:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        lock_file.close()


# 阅读注释（函数）：规范化 状态 value。
def _normalize_status_value(value: Any) -> str:
    """Return a canonical lower-case execution status from enums or strings."""

    if hasattr(value, "value"):
        value = value.value
    text = str(value or "").strip().lower()
    if text.startswith("executionstatus."):
        text = text.split(".", 1)[1]
    return text


# 阅读注释（函数）：处理 effective 运行时 错误 相关逻辑。
def _effective_runtime_error(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Return an error only when the final runtime status is actually failed."""

    final_status = _normalize_status_value(summary.get("status"))
    if final_status in SUCCESS_LIKE_STATUSES:
        return {}

    supervisor_result = summary.get("supervisor_result") or {}
    error = supervisor_result.get("error") or {}
    if error:
        return error

    sub_results = (supervisor_result.get("result") or {}).get("sub_agent_results") or []
    failed_sub_results = [
        item
        for item in sub_results
        if _normalize_status_value(item.get("status")) not in SUCCESS_LIKE_STATUSES
    ]
    if not failed_sub_results:
        return {}

    first_failed = failed_sub_results[0]
    return first_failed.get("error") or {
        "error_code": "SUB_AGENT_FAILED",
        "error_type": "AgentFailure",
        "message": first_failed.get("error_message") or "子 Agent 执行失败。",
        "failed_node": first_failed.get("agent_name"),
        "retryable": False,
    }




# 阅读注释（函数）：判断 business gate failure。 判断业务门控是否失败
def _is_business_gate_failure(summary: Dict[str, Any]) -> bool:
    """判断 business gate failure。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：summary.get, output.get, _effective_runtime_error, hard_gate.get, str, error.get。
    """
    output = summary.get("scheme_writer_output") or {}
    hard_gate = output.get("hard_gate") or {}
    error = output.get("error") or _effective_runtime_error(summary)
    return (
        hard_gate.get("passed") is False
        or str(error.get("error_code") or "") == "DOCUMENT_HARD_GATE_FAILED"
    )

def _read_target_release(summary: Dict[str, Any]):
    """Verify a Target publication without opening mutable business stores.

    A status/compatibility flag is not publication authority. Reuse the exact
    immutable manifest/proof/payload reader and the caller's terminal receipt.
    This is a diagnostic export, not a new Release or human approval.
    """
    raw = summary.get("target_release_publication")
    outcome = summary.get("terminal_outcome") or {}
    extra = (summary.get("scheme_writer_output") or {}).get("extra") or {}
    target_marked = (
        outcome.get("publication_exists") is True
        or outcome.get("publication_ref") is not None
        or extra.get("source_mode") == "verified_target"
    )
    if raw is None:
        if target_marked and (
            outcome.get("publication_exists") is True
            or summary.get("deliverable_final_result") is not None
        ):
            raise ValueError("Target publication evidence is missing")
        return None

    from application.reading_export.source import load_published_markdown
    from apps.enterprise_document.schemas.post_write.release import (
        PostWritePublishedReleaseSchema,
    )

    publication = PostWritePublishedReleaseSchema.model_validate(raw)
    manifest = publication.manifest
    if (manifest.task_id, manifest.run_id) != (
        summary.get("task_id"), summary.get("run_id")
    ) or publication.verified_after_write is not True:
        raise ValueError("Target publication identity/verification differs")
    trace_path = (summary.get("paths") or {}).get("trace")
    if not trace_path:
        raise ValueError("Target publication requires the Run trace path")
    package = load_published_markdown(
        target_release_root=Path(trace_path).parent / "target_release",
        release_id=manifest.release_id,
        terminal_outcome=summary.get("terminal_outcome"),
    )
    if (package.manifest != manifest
            or package.release_manifest_artifact != publication.manifest_artifact):
        raise ValueError("Target publication differs from persisted release")
    if _normalize_status_value(summary.get("status")) != package.status_view["execution_status"]:
        raise ValueError("Target execution status differs from terminal receipt")
    gate = (summary.get("scheme_writer_output") or {}).get("hard_gate") or {}
    if gate.get("passed") is not (manifest.enforcement_action_passthrough.value == "OUTPUT"):
        raise ValueError("Target compatibility Gate differs from native Control")
    if summary.get("rejected_artifact") is not None:
        raise ValueError("Published Target cannot also expose a rejected artifact")
    return package


def _target_release_report(package) -> Dict[str, Any]:
    """Expose independent owner facts; BEST_EFFORT never becomes quality PASS."""
    return {
        "release_id": package.manifest.release_id,
        "release_mode": package.manifest.release_mode.value,
        "quality_state": package.manifest.quality_state_passthrough.value,
        "control_action": package.manifest.enforcement_action_passthrough.value,
        "run_completion_result": package.manifest.run_completion_assessment_ref.aggregate_result.value,
        "payload_sha256": package.manifest.release_payload_sha256,
        "terminal_outcome_source": package.terminal_outcome_source,
        "status_view": dict(package.status_view),
    }


# 阅读注释（函数）：解析并确定 console question。   处理用户输入
def _resolve_console_question(cli_value: Optional[str]) -> str:
    """Resolve one non-empty question from CLI or an interactive console."""

    question = str(cli_value or "").strip()
    if not question:
        question = input("\n请输入建设方案生成需求：\n> ").strip()
    if not question:
        raise ValueError("用户输入不能为空")
    return question


# 阅读注释（函数）：处理 configure 运行时 相关逻辑。
def _configure_runtime(*, fake_runtime: bool) -> None:
    """Make the console entry explicit about real versus fake dependencies.

    Environment variables outrank ``.env`` in ``core.config``. Therefore the
    direct PyCharm run path is deterministic and cannot silently use FakeRAG or
    FakeLLM when the user expects an end-to-end real run.
    """

    project_root = resolve_project_path(".")
    os.environ["RAG_PROJECT_ROOT"] = str(project_root)
    os.environ["ENABLE_LLM_ROUTING"] = "false"

    if fake_runtime:
        os.environ["USE_REAL_RAG_TOOL"] = "false"
        os.environ["DEFAULT_MODEL_NAME"] = "fake_llm"
        os.environ["SUPERVISOR_MODEL_NAME"] = "fake_llm"
        return

    os.environ["USE_REAL_RAG_TOOL"] = "true"
    os.environ["DEFAULT_MODEL_NAME"] = REAL_MODEL_NAME
    os.environ["SUPERVISOR_MODEL_NAME"] = REAL_MODEL_NAME


def _model_routing_snapshot(settings: AppSettings) -> Dict[str, Any]:
    """Expose the actual configured model profiles and routing policies."""

    routing = build_default_model_routing(settings)
    return {
        "model_profiles": [
            profile.model_dump(mode="json") for profile in routing.profiles
        ],
        "model_routing_policies": [
            policy.model_dump(mode="json") for policy in routing.policies
        ],
    }


def _resolve_validation_model_name(
    runtime_preflight: Dict[str, Any],
    *,
    fallback_model_name: str,
) -> str:
    """Resolve the trace model expected by the E2E validator from active routing.

    An experiment may override every model role through ModelGateway routing while
    leaving the application's default model configuration unchanged.  When all
    routing policies share one primary profile, that routed profile is the
    authoritative expected trace model.  Mixed routing falls back to the
    application default because a single expected model is not authoritative.
    """

    profiles = {
        str(item.get("profile_id") or ""): item
        for item in (runtime_preflight.get("model_profiles") or [])
        if isinstance(item, dict) and str(item.get("profile_id") or "").strip()
    }
    primary_profile_ids = {
        str(candidates[0])
        for item in (runtime_preflight.get("model_routing_policies") or [])
        if isinstance(item, dict)
        for candidates in [item.get("candidates") or []]
        if candidates
    }
    if len(primary_profile_ids) == 1:
        profile = profiles.get(next(iter(primary_profile_ids))) or {}
        routed_model_name = str(profile.get("model_name") or "").strip()
        if routed_model_name:
            return routed_model_name
    return str(fallback_model_name or "").strip()


# 阅读注释（函数）：处理 preflight real 运行时 相关逻辑。
def _preflight_real_runtime(settings: AppSettings) -> Dict[str, Any]:
    """Fail before model loading when the real runtime is not deployable."""

    if settings.default_model_name != settings.local_qwen_model_name:
        raise RuntimeError(
            "端到端测试要求DEFAULT_MODEL_NAME与LOCAL_QWEN_MODEL_NAME一致："
            f"default={settings.default_model_name!r}, "
            f"local={settings.local_qwen_model_name!r}"
        )
    if not settings.local_qwen_model_path.is_dir():
        raise FileNotFoundError(
            f"本地LLM目录不存在：{settings.local_qwen_model_path}"
        )

    rag_runtime_root = Path(
        os.getenv("RAG_PROJECT_ROOT", str(settings.project_root))
    ).expanduser().resolve()
    pointer_path = rag_runtime_root / "data/processed/indexes/active_index.json"
    index_info = ActiveIndexResolver(
        verify_manifest_hash=True,
        verify_artifacts=True,
    ).resolve(pointer_path)

    static_spec = Path(
        os.getenv(
            "RAG_STATIC_RETRIEVAL_SPEC_FILE",
            "backend/rag/config/static_retrieval_v1.yaml",
        )
    ).expanduser()
    if not static_spec.is_absolute():
        static_spec = settings.project_root / static_spec
    static_spec = static_spec.resolve()
    if not static_spec.is_file():
        raise FileNotFoundError(f"RAG 静态检索规格不存在：{static_spec}")

    return {
        "mode": "real",
        "model_name": settings.default_model_name,
        "model_path": str(settings.local_qwen_model_path),
        "model_device": settings.local_qwen_device,
        **_model_routing_snapshot(settings),
        "rag_project_root": str(rag_runtime_root),
        "active_index_version": index_info["index_version"],
        "active_index_backend": index_info["backend"],
        "active_index_collection": index_info["collection_name"],
        "active_index_manifest_path": index_info["manifest_path"],
        "active_index_manifest_sha256": index_info["manifest_hash"],
        "active_index_dataset_version": index_info["dataset_version"],
        "active_index_config_sha256": index_info["config_hash"],
        "active_index_reproducibility_sha256": index_info["reproducibility_hash"],
        "embedding_model": index_info["embedding_model"],
        "embedding_version": index_info.get("embedding_version"),
        "embedding_dim": index_info.get("embedding_dim"),
        "static_retrieval_spec": str(static_spec),
    }


# 阅读注释（函数）：加载 Trace events。
def _load_trace_events(trace_path: str | Path) -> list[Dict[str, Any]]:
    """加载 Trace events。

    参数:
        trace_path: Trace 路径，具体约束请结合类型标注和调用方确认。

    返回:
        list[Dict[str, Any]]

    阅读提示:
        主要直接调用：Path, path.is_file, FileNotFoundError, enumerate, splitlines, path.read_text, raw_line.strip, json.loads。
    """
    path = Path(trace_path)
    if not path.is_file():
        raise FileNotFoundError(f"Trace文件不存在：{path}")
    events: list[Dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Trace第{line_number}行不是合法JSON") from exc
        if isinstance(payload, dict):
            events.append(payload)
    return events


# 阅读注释（函数）：校验 end to end。
def _validate_end_to_end(
    summary: Dict[str, Any],
    *,
    expected_model_name: str,
    expected_real_rag: bool,
    allow_business_failure: bool = False,
) -> Dict[str, Any]:
    """Validate that the public entry actually traversed the whole mainline."""

    status = _normalize_status_value(summary.get("status"))
    target_release = _read_target_release(summary)
    business_gate_failure = (
        target_release.manifest.enforcement_action_passthrough.value != "OUTPUT"
        if target_release is not None else _is_business_gate_failure(summary)
    )
    # Workflow completion and business Gate outcome are independent axes.
    # In shadow mode the workflow may finish successfully while the final
    # business Gate explicitly fails and enforcement continues for observation.
    business_gate_passed = (
        status in SUCCESS_LIKE_STATUSES and not business_gate_failure
    )
    if not business_gate_passed:
        if not allow_business_failure or not business_gate_failure:
            error = _effective_runtime_error(summary)
            error_type = str(error.get("error_type") or "RuntimeFailure")
            message = str(
                error.get("message")
                or error.get("error_message")
                or f"主链终态不是成功：{status}"
            )
            failed_node = str(error.get("failed_node") or "unknown")
            raise RuntimeError(
                f"主链技术失败：{error_type}: {message} (failed_node={failed_node})"
            )

    generated_draft = summary.get("generated_draft") or {}
    rejected_artifact = summary.get("rejected_artifact")
    deliverable = summary.get("deliverable_final_result")
    if target_release is not None:
        # Publication is verified independently of legacy deliverable aliases.
        # The persisted, content-bound payload is the only Target export source.
        artifact_draft = {"content": target_release.payload_bytes.decode("utf-8")}
    elif business_gate_passed:
        if not isinstance(deliverable, dict):
            raise RuntimeError("Hard Gate passed but deliverable_final_result is absent")
        if rejected_artifact is not None:
            raise RuntimeError("Passed result cannot also expose rejected_artifact")
        artifact_draft = deliverable.get("draft") or {}
    else:
        if deliverable is not None:
            raise RuntimeError("Hard Gate failure must not expose a deliverable")
        if not isinstance(rejected_artifact, dict):
            raise RuntimeError("Hard Gate failure must expose rejected_artifact")
        artifact_draft = generated_draft
    content = str(
        artifact_draft.get("full_text")
        or artifact_draft.get("content")
        or ""
    ).strip()
    if not content:
        raise RuntimeError("主链已执行但最终方案正文为空")

    paths = summary.get("paths") or {}
    for key in ("trace", "task_state"):
        value = paths.get(key)
        if not value or not Path(value).is_file():
            raise FileNotFoundError(f"端到端产物缺失：{key}={value}")

    events = _load_trace_events(paths["trace"])
    event_types = {str(event.get("event_type") or "") for event in events}
    required_event_types = {
        "run_started",
        "run_finished",
        "workflow_started",
        "workflow_finished",
        "agent_started",
        "agent_finished",
        "rag_started",
        "rag_finished",
        "model_started",
        "model_finished",
    }
    missing_event_types = sorted(required_event_types - event_types)
    if missing_event_types:
        raise RuntimeError(
            "Trace没有覆盖完整主链事件：" + ", ".join(missing_event_types)
        )

    tool_components = {
        str(event.get("component_name") or event.get("tool_name") or "")
        for event in events
        if event.get("component_type") == "tool"
    }
    rag_components = {
        str(event.get("component_name") or "")
        for event in events
        if event.get("component_type") == "rag"
    }
    if expected_real_rag and "RAGService" not in rag_components:
        raise RuntimeError(
            "端到端测试期望RAGService，实际RAG组件："
            + ", ".join(sorted(rag_components))
        )
    if not expected_real_rag and "FakeRAGService" not in rag_components:
        raise RuntimeError(
            "Fake测试期望FakeRAGService，实际RAG组件："
            + ", ".join(sorted(rag_components))
        )

    model_components = {
        str(
            event.get("model_name")
            or event.get("component_name")
            or ""
        )
        for event in events
        if event.get("component_type") == "model"
    }
    if expected_model_name not in model_components:
        raise RuntimeError(
            f"端到端测试未观察到模型{expected_model_name!r}，实际模型："
            + ", ".join(sorted(model_components))
        )

    graph_events = [
        event
        for event in events
        if (event.get("input_summary") or {}).get("graph_state_schema")
        == "graph_state_v1"
    ]
    if not graph_events:
        raise RuntimeError("Trace中没有观察到graph_state_v1节点输入")

    context_managed_model_calls = [
        event
        for event in events
        if event.get("event_type") == "model_started"
        and (
            (event.get("input_summary") or {}).get("context_package_id")
            or (event.get("input_summary") or {}).get("context_package")
            or (event.get("metadata") or {}).get("context_package_id")
        )
    ]
    # Step 14's compatibility policy annotates every model call, but older
    # payload layouts may place the identifier one level deeper. Only require
    # one explicit managed call here; the Step 14 acceptance remains stricter.
    if not context_managed_model_calls:
        for event in events:
            if event.get("event_type") != "model_started":
                continue
            serialized = json.dumps(event, ensure_ascii=False)
            if "context_package" in serialized:
                context_managed_model_calls.append(event)
    if not context_managed_model_calls:
        raise RuntimeError("Trace中没有观察到Context Package关联的模型调用")

    return {
        "status": status,
        "task_status": status,
        "execution_integrity": "passed",
        "business_quality": (
            target_release.status_view["business_quality_status"]
            if target_release is not None
            else "passed" if business_gate_passed else "failed"
        ),
        "target_release": (
            _target_release_report(target_release) if target_release is not None else None
        ),
        "business_gate_passed": business_gate_passed,
        "business_gate_failure": business_gate_failure,
        "artifact_lifecycle": {
            "generated_draft": bool(generated_draft),
            "rejected_artifact": rejected_artifact is not None,
            "deliverable_final_result": deliverable is not None,
        },
        "content_chars": len(content),
        "trace_event_count": len(events),
        "tool_components": sorted(tool_components),
        "rag_components": sorted(rag_components),
        "model_components": sorted(model_components),
        "graph_node_event_count": len(graph_events),
        "context_managed_model_call_count": len(context_managed_model_calls),
    }


# 阅读注释（函数）：写入具有明确生命周期名称的 Markdown 产物。
def _write_markdown_artifact(
    summary: Dict[str, Any],
    content: str | bytes,
    *,
    artifact_suffix: str,
) -> Path:
    """Write one explicitly named markdown artifact."""
    trace_path = Path(summary["paths"]["trace"])
    artifact_path = trace_path.with_name(
        f"{summary['run_id']}_{artifact_suffix}.md"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        artifact_path.write_bytes(content)  # Preserve exact published payload bytes.
    else:
        artifact_path.write_text(content, encoding="utf-8")
    return artifact_path


def _write_json_artifact(
    summary: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    artifact_suffix: str,
) -> Path:
    """Write an explicitly named, user-readable diagnostic artifact."""
    trace_path = Path(summary["paths"]["trace"])
    artifact_path = trace_path.with_name(
        f"{summary['run_id']}_{artifact_suffix}.json"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n",
    )
    return artifact_path


# 阅读注释（函数）：处理 章节 report rows 相关逻辑。
def _section_report_rows(summary: Dict[str, Any]) -> list[Dict[str, Any]]:
    """处理 章节 report rows 相关逻辑。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。

    返回:
        list[Dict[str, Any]]

    阅读提示:
        主要直接调用：summary.get, str, item.get, output.get, isinstance, draft.get, section.get, bundles.get。
    """
    draft = summary.get("generated_draft") or {}
    output = summary.get("scheme_writer_output") or {}
    bundles = {
        str(item.get("section_title") or ""): item
        for item in (output.get("section_evidence") or [])
        if isinstance(item, dict)
    }
    rows: list[Dict[str, Any]] = []
    for section in draft.get("sections") or []:
        title = str(section.get("section_title") or "")
        bundle = bundles.get(title) or {}
        eval_result = section.get("eval_result") or {}
        rows.append(
            {
                "section_id": section.get("section_id"),
                "section_title": title,
                "status": _normalize_status_value(section.get("status")),
                "content_chars": len(str(section.get("content") or "")),
                "citation_count": len(section.get("citation_bindings") or []),
                "citation_ids": list(section.get("citation_ids") or []),
                "evidence_scope": (section.get("extra") or {}).get("evidence_scope")
                or bundle.get("retrieval_scope"),
                "evidence_query": (section.get("extra") or {}).get("evidence_query")
                or bundle.get("query"),
                "recovery_count": int(bundle.get("recovery_count") or 0),
                "tool_call_ids": list(bundle.get("tool_call_ids") or []),
                "failures": list(eval_result.get("failures") or []),
                "warnings": list(eval_result.get("warnings") or []),
            }
        )
    return rows


# 阅读注释（函数）：构建 e2e report。
def _build_e2e_report(
    summary: Dict[str, Any],
    *,
    runtime_preflight: Dict[str, Any],
    validation: Optional[Dict[str, Any]],
    validation_error: Optional[Exception] = None,
) -> Dict[str, Any]:
    """构建 e2e report。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。
        runtime_preflight: 运行时 preflight，具体约束请结合类型标注和调用方确认。
        validation: validation，具体约束请结合类型标注和调用方确认。
        validation_error: validation 错误，具体约束请结合类型标注和调用方确认。

    返回:
        Dict[str, Any]

    阅读提示:
        主要直接调用：summary.get, output.get, _section_report_rows, get, is_file, Path, _load_trace_events, str。
    """
    output = summary.get("scheme_writer_output") or {}
    hard_gate = output.get("hard_gate") or {}
    sections = _section_report_rows(summary)
    trace_events: list[Dict[str, Any]] = []
    trace_path = (summary.get("paths") or {}).get("trace")
    if trace_path and Path(trace_path).is_file():
        trace_events = _load_trace_events(trace_path)
    event_counts: Dict[str, int] = {}
    for event in trace_events:
        event_type = str(event.get("event_type") or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

    error = _effective_runtime_error(summary)
    execution_integrity = (
        str((validation or {}).get("execution_integrity") or "")
        or ("failed" if validation_error is not None else "unknown")
    )
    business_quality = (
        str((validation or {}).get("business_quality") or "")
        or (
            "failed"
            if _is_business_gate_failure(summary)
            else "unknown"
        )
    )
    return {
        "schema_version": "mainline_e2e_report_v1",
        "run_demo_version": RUN_DEMO_VERSION,
        "task_id": summary.get("task_id"),
        "run_id": summary.get("run_id"),
        "status": _normalize_status_value(summary.get("status")),
        "task_status": _normalize_status_value(summary.get("status")),
        "execution_integrity": execution_integrity,
        "business_quality": business_quality,
        "business_gate_failure": _is_business_gate_failure(summary),
        "target_release": (validation or {}).get("target_release"),
        "artifact_lifecycle": {
            "generated_draft": bool(summary.get("generated_draft")),
            "rejected_artifact": summary.get("rejected_artifact") is not None,
            "deliverable_final_result": (
                summary.get("deliverable_final_result") is not None
            ),
        },
        "runtime_preflight": runtime_preflight,
        "validation": validation,
        "validation_error": (
            {
                "type": validation_error.__class__.__name__,
                "message": str(validation_error),
            }
            if validation_error is not None
            else None
        ),
        "hard_gate": hard_gate,
        "error": error or None,
        "metrics": {
            "section_count": len(sections),
            "successful_section_count": sum(
                1 for item in sections if item["status"] == "success"
            ),
            "partial_section_count": sum(
                1 for item in sections if item["status"] == "partial_success"
            ),
            "failed_section_count": sum(
                1
                for item in sections
                if item["status"] not in SUCCESS_LIKE_STATUSES
            ),
            "citation_count": len(output.get("citations") or []),
            "section_evidence_bundle_count": len(output.get("section_evidence") or []),
            "corrective_retrieval_count": sum(
                int(item.get("recovery_count") or 0)
                for item in (output.get("section_evidence") or [])
            ),
            "trace_event_count": len(trace_events),
            "rag_call_count": event_counts.get("rag_started", 0),
            "model_call_count": event_counts.get("model_started", 0),
            "error_event_count": sum(
                1
                for item in trace_events
                if str(item.get("phase") or "") == "error"
            ),
        },
        "event_type_counts": event_counts,
        "sections": sections,
        "paths": dict(summary.get("paths") or {}),
    }


# 阅读注释（函数）：写入 e2e report。
def _write_e2e_report(
    summary: Dict[str, Any],
    report: Dict[str, Any],
    *,
    explicit_path: Optional[str | Path] = None,
) -> Path:
    """写入 e2e report。

    参数:
        summary: summary，具体约束请结合类型标注和调用方确认。
        report: report，具体约束请结合类型标注和调用方确认。
        explicit_path: explicit 路径，具体约束请结合类型标注和调用方确认。

    返回:
        Path

    阅读提示:
        主要直接调用：resolve, expanduser, Path, trace_path.with_name, report_path.parent.mkdir, report_path.write_text, json.dumps。
    """
    if explicit_path is not None:
        report_path = Path(explicit_path).expanduser().resolve()
    else:
        trace_path = Path(summary["paths"]["trace"])
        report_path = trace_path.with_name(f"{summary['run_id']}_e2e_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path



def _review_artifact_path(workspace, suffix: str) -> Path:
    """Derive a review path only from the caller's pinned Run workspace."""
    from core.persistence.safe_paths import SafePathResolver

    return SafePathResolver(workspace.output_root).path_for_identifier(
        workspace.run_id, field_name="run_id", directory=("runs",), suffix=suffix,
    )


def _project_pwa004_explanations(review, rows, sections, workspace):
    """Validate Stage's bounded explanation against its native Section receipt.

    The trust boundary remains the controlled Mainline result and workspace used
    by D12. Content hashes detect changed projections; they grant no Source read
    permission. This function neither opens a store nor evaluates a Claim.
    """
    import hashlib
    import re
    from core.hashing import canonical_sha256
    from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimBindingFactSchema

    if "rejection_explanations" not in review:
        if any("pwa004_rejection_explanations" in (section.get("extra") or {})
               for section in sections.values()):
            raise ValueError("Review lost its Stage explanation summary")
        # Old summaries/checkpoints retain their body and exact native refusals.
        return {"rejection_explanations": [],
                "rejection_explanations_status": "UNAVAILABLE_LEGACY"}
    explanations = review["rejection_explanations"]
    if not isinstance(explanations, list) or len(explanations) != len(rows):
        raise ValueError("Review explanation membership differs")
    extra_keys = {
        "task_id", "run_id", "input_ref", "input_content_hash", "claim_text",
        "paragraph_text", "text_span_hash", "start_offset", "end_offset",
        "binding_facts", "shared_support", "evidence", "candidate_verdict",
        "verification_result_committed", "explanation", "limitations", "explanation_sha256",
    }
    evidence_keys = {"exact_source_ref", "evidence_id", "doc_id", "title",
                     "context_text", "context_text_sha256"}
    limitations = [
        "未产生该候选的正式 VerificationResult。",
        "证据可见或被引用不等于已证明陈述成立。",
        "本说明不授予正式发布或人工签发权限。",
    ]

    def exact_object(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys):
            raise ValueError("Review explanation fields differ")

    def digest(value, *, prefixed=False):
        pattern = r"sha256:[0-9a-f]{64}" if prefixed else r"[0-9a-f]{64}"
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise ValueError("Review explanation digest is invalid")

    def visible_text(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Review explanation text is invalid")
        # Do not expose an injected configuration credential or local path in a
        # field intended for Writer-visible prose. Unknown objects are rejected
        # separately, including raw prompts/settings/debug envelopes.
        if (re.search(r"(?i)(?:api[_ -]?key|access[_ -]?token|authorization)\s*[:=]", value)
                or re.search(r"(?i)\bbearer\s+\S+", value)
                or re.search(r"(?:^|\s)[A-Za-z]:[\\/]", value)
                or value.startswith(("\\\\", "file://"))):
            raise ValueError("Review explanation contains an unsafe field")
        return value

    projected = []
    for row, value in zip(rows, explanations):
        exact_object(value, set(row) | extra_keys)
        if any(value[key] != item for key, item in row.items()):
            raise ValueError("Review explanation differs from native refusal")
        if (value["task_id"], value["run_id"]) != (workspace.task_id, workspace.run_id):
            raise ValueError("Review explanation belongs to another Run")
        if value["input_ref"] != row["candidate_ref"].removesuffix("/candidate") + "/input":
            raise ValueError("Review explanation input differs")
        digest(value["input_content_hash"], prefixed=True)
        digest(value["explanation_sha256"])
        if canonical_sha256({key: item for key, item in value.items()
                             if key != "explanation_sha256"}) != value["explanation_sha256"]:
            raise ValueError("Review explanation content changed")
        occurrence = row["claim_occurrence_ref"]
        paragraph = visible_text(value["paragraph_text"])
        quote = visible_text(value["claim_text"])
        start, end = value["start_offset"], value["end_offset"]
        if (type(start) is not int or type(end) is not int
                or (start, end) != (occurrence["start_offset"], occurrence["end_offset"])
                or not 0 <= start < end <= len(paragraph)
                or paragraph[start:end] != quote
                or value["text_span_hash"] != occurrence["text_span_hash"]
                or hashlib.sha256(quote.encode("utf-8")).hexdigest() != value["text_span_hash"]
                or paragraph not in sections[row["section_id"]].get("content", "")):
            raise ValueError("Review explanation Claim location differs")
        if (value["candidate_verdict"] != "VERIFIED"
                or value["verification_result_committed"] is not False
                or value["limitations"] != limitations
                or value["explanation"] != (
                    "原生 Candidate 提出了 VERIFIED；SharedSupport 为 " + row["support_relation"]
                    + "，M6 规则 VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT 拒绝该候选。"
                    "该拒绝不证明陈述为假，也不构成正式质量 FAIL。")):
            raise ValueError("Review explanation changes native semantics")
        support = value["shared_support"]
        exact_object(support, {"relation", "supporting_refs", "contradicting_refs"})
        members = set(row["support_ref"]["evidence_target_set_ref"]["evidence_member_refs"])
        if support["relation"] != row["support_relation"]:
            raise ValueError("Review explanation Support relation differs")
        for key in ("supporting_refs", "contradicting_refs"):
            refs = support[key]
            if (not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs)
                    or len(refs) != len(set(refs)) or not set(refs) <= members):
                raise ValueError("Review explanation Support scope differs")
        if not isinstance(value["binding_facts"], list):
            raise ValueError("Review explanation Binding facts are invalid")
        facts = []
        for fact in value["binding_facts"]:
            typed = ClaimBindingFactSchema.model_validate(fact).model_dump(mode="json")
            if (typed != fact or typed["source_kind"] not in {
                    "EVIDENCE", "EVIDENCE_PACKAGE", "FACT_SNAPSHOT", "FACT"}
                    or typed["source_scope_ref"] != "section:" + row["section_id"]
                    or re.fullmatch(r"[a-z][a-z0-9-]*://sha256/[0-9a-f]{64}",
                                    typed["exact_source_ref"]) is None
                    or re.fullmatch(r"claim-source-current://sha256/[0-9a-f]{64}",
                                    typed["source_currentness_proof_ref"]) is None):
                raise ValueError("Review explanation Binding scope differs")
            facts.append(typed)
        evidence_refs = {fact["exact_source_ref"] for fact in facts if fact["source_kind"] == "EVIDENCE"}
        if not isinstance(value["evidence"], list):
            raise ValueError("Review explanation Evidence is invalid")
        seen = set()
        evidence = []
        for item in value["evidence"]:
            exact_object(item, evidence_keys)
            ref = item["exact_source_ref"]
            if ref not in members or ref not in evidence_refs or ref in seen:
                raise ValueError("Review explanation Evidence scope differs")
            seen.add(ref)
            for key in ("evidence_id", "doc_id", "title", "context_text"):
                visible_text(item[key])
            digest(item["context_text_sha256"])
            if canonical_sha256(item["context_text"]) != item["context_text_sha256"]:
                raise ValueError("Review explanation Evidence content changed")
            evidence.append(dict(item))
        # The Stage writes this independent receipt into the already validated
        # native Section. A nonempty external explanation is never sufficient.
        section_receipts = (sections[row["section_id"]].get("extra") or {}).get(
            "pwa004_rejection_explanations")
        if not isinstance(section_receipts, list) or value not in section_receipts:
            raise ValueError("Review explanation lacks its native Section receipt")
        projected.append(dict(value, binding_facts=facts, evidence=evidence,
                              shared_support=dict(support)))
    for sid in {row["section_id"] for row in rows}:
        expected = [value for value in projected if value["section_id"] == sid]
        if sections[sid]["extra"]["pwa004_rejection_explanations"] != expected:
            raise ValueError("Review explanation Section membership differs")
    return {"rejection_explanations": projected, "rejection_explanations_status": "AVAILABLE"}


def _project_pwa004_review(summary: Dict[str, Any], workspace) -> Dict[str, Any]:
    """Project native review receipts; this does not authorize a Release.

    The current Mainline caller supplies the workspace. Typed refs retain their
    existing contracts, including historical Evidence members; no store is
    reopened and no source/ACL decision is replaced here.
    """
    from application.run_workspace import RunWorkspace
    from core.persistence.safe_paths import SafeExternalIdentifier
    from apps.enterprise_document.schemas.agent_rag_v1.artifacts import ArtifactVersionRefSchema
    from apps.enterprise_document.schemas.agent_rag_v1.claims import ClaimRefSchema, ClaimOccurrenceRefSchema
    from apps.enterprise_document.schemas.agent_rag_v1.claim_support import ClaimEvidenceSupportRefSchema
    from apps.enterprise_document.schemas.agent_rag_v1.verification import ClaimBindingFactsRefSchema
    from core.hashing import canonical_sha256

    if not isinstance(workspace, RunWorkspace):
        raise ValueError("Review requires a trusted Run workspace")
    identity = (workspace.task_id, workspace.run_id)
    draft = summary["generated_draft"]
    if ((summary.get("task_id"), summary.get("run_id")) != identity
            or (draft.get("task_id"), draft.get("run_id")) != identity):
        raise ValueError("Review identity differs")
    if Path(summary["paths"]["trace"]).resolve() != workspace.trace_path:
        raise ValueError("Review trace differs from trusted workspace")
    outcome = summary.get("terminal_outcome") or {}
    if ((outcome.get("task_id"), outcome.get("run_id")) != identity
            or outcome.get("phase") != "evaluate_document_gate"
            or not any(d.get("code") == "VERIFIER_ADMISSION_INCOMPLETE"
                       for d in outcome.get("diagnostics", []) if isinstance(d, dict))):
        raise ValueError("Review lacks native refusal outcome")
    review = draft["extra"]["pwa004_review_draft"]
    if (review.get("formal_post_write_assessed") is not False
            or review.get("formal_release_authorized") is not False):
        raise ValueError("Review authority flags differ")

    def strings(value):
        if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
            raise ValueError("Review requires a string list")
        return list(value)

    sections = {s["section_id"]: s for s in draft["sections"]}
    if len(sections) != len(draft["sections"]):
        raise ValueError("Review has duplicate Sections")
    for sid, section in sections.items():
        SafeExternalIdentifier.validate(sid, field_name="section_id")
        if not isinstance(section.get("section_title"), str) or not section["section_title"]:
            raise ValueError("Review Section title is invalid")
    ref_schemas = {
        "claim_ref": ClaimRefSchema, "claim_occurrence_ref": ClaimOccurrenceRefSchema,
        "paragraph_version_ref": ArtifactVersionRefSchema,
        "binding_facts_ref": ClaimBindingFactsRefSchema,
        "support_ref": ClaimEvidenceSupportRefSchema,
    }
    scalar_fields = (
        "rule_code", "support_relation", "candidate_ref", "candidate_sha256",
        "verifier_semantic_input_sha256", "attempt_id", "input_snapshot_version",
    )

    def native_row(row, sid):
        result = {key: row[key] for key in scalar_fields}
        if any(not isinstance(v, str) or not v for v in result.values()):
            raise ValueError("Review requires native scalar fields")
        if (result["rule_code"] != "VERIFIER_VERIFIED_FORBIDDEN_BY_SHARED_SUPPORT"
                or result["support_relation"] not in {"INDETERMINATE", "CONTRADICTED"}):
            raise ValueError("Review refusal rule differs")
        for key in ("candidate_sha256", "verifier_semantic_input_sha256"):
            value = result[key]
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Review fingerprint is invalid")
        SafeExternalIdentifier.validate(result["attempt_id"], field_name="attempt_id")
        refs = {key: schema.model_validate(row[key]) for key, schema in ref_schemas.items()}
        claim, occurrence = refs["claim_ref"], refs["claim_occurrence_ref"]
        paragraph, support = refs["paragraph_version_ref"], refs["support_ref"]
        if (claim.claim_occurrence_ref != occurrence
                or occurrence.run_id != workspace.run_id or occurrence.section_id != sid
                or occurrence.paragraph_version_ref != paragraph
                or refs["binding_facts_ref"].claim_ref != claim
                or paragraph.producer_attempt_id != result["attempt_id"]
                or paragraph.producer_attempt_scope_ref != f"section:{sid}"):
            raise ValueError("Review refs differ from current Run/Attempt")
        subject = support.claim_support_subject_ref
        if (subject.authority_kind.value != "SIF07_CLAIM"
                or subject.subject_ref != claim.claim_id
                or support.evidence_target_set_ref.claim_support_subject_ref != subject):
            raise ValueError("Review Support subject differs")
        # Evidence members are authorized source refs, not necessarily this Run.
        for key, ref in refs.items():
            result[key] = ref.model_dump(mode="json")
            if result[key] != row[key]:
                raise ValueError("Review ref cannot be normalized")
        physical = canonical_sha256({
            "contract": "verifier-physical-occurrence-v2",
            "verifier_semantic_input_sha256": result["verifier_semantic_input_sha256"],
        })
        if result["candidate_ref"] != f"artifact://canonical-verifier-v2/{physical}/candidate":
            raise ValueError("Review Candidate ref differs")
        return result

    rows = review.get("rejections")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Review lacks native rejections")
    projected = []
    for row in rows:
        sid = row["section_id"]
        section = sections[sid]
        extra = section.get("extra") or {}
        subject = extra.get("writer_subject_ref") or {}
        if (row["section_title"] != section["section_title"]
                or extra.get("canonical_claim_verification_incomplete") is not True
                or extra.get("pwa004_review_only_not_accepted") is not True
                or subject != {"document_id": draft["document_id"],
                               "run_id": workspace.run_id, "section_id": sid}
                or extra.get("write_instance_id") != row["attempt_id"]):
            raise ValueError("Review does not match native Section")
        receipt = native_row(row, sid)
        native = [native_row(r, sid) for r in extra.get("pwa004_verifier_rejections", [])]
        if receipt not in native:
            raise ValueError("Review differs from native Section refusal")
        projected.append(dict(receipt, section_id=sid, section_title=section["section_title"]))

    ids = {key: strings(review[key]) for key in (
        "review_only_section_ids", "generated_non_review_only_section_ids", "accepted_section_ids",
    )}
    refused = {r["section_id"] for r in projected}
    if (set(ids["review_only_section_ids"]) != refused
            or set(ids["generated_non_review_only_section_ids"]) != set(sections) - refused
            or not set(ids["accepted_section_ids"]) <= set(sections) - refused):
        raise ValueError("Review Section membership differs")
    # Do not drop or duplicate a native rejection when projecting the Stage list.
    for sid in refused:
        actual = [native_row(r, sid) for r in sections[sid]["extra"]["pwa004_verifier_rejections"]]
        exported = [{k: v for k, v in r.items() if k not in {"section_id", "section_title"}}
                    for r in projected if r["section_id"] == sid]
        if actual != exported:
            raise ValueError("Review refusal membership differs")
    missing = strings(draft["missing_sections"])  # Titles, never Section IDs.
    blocked = []
    for row in review["hard_dependency_blocked"]:
        sid = SafeExternalIdentifier.validate(row["section_id"], field_name="section_id")
        title, reason = row["section_title"], row["reason_code"]
        dependencies = strings(row["blocked_by"])
        if (not isinstance(title, str) or title not in missing or sid in sections
                or reason != "PWA004_HARD_DEPENDENCY_UNVERIFIED"):
            raise ValueError("Review blocked Section differs")
        for dependency in dependencies:
            SafeExternalIdentifier.validate(dependency, field_name="blocked_by")
        blocked.append(dict(section_id=sid, section_title=title,
                            reason_code=reason, blocked_by=dependencies))
    return dict(task_id=workspace.task_id, run_id=workspace.run_id,
                missing_sections=missing, **ids, hard_dependency_blocked=blocked,
                rejections=projected,
                **_project_pwa004_explanations(review, projected, sections, workspace),
                formal_post_write_assessed=False,
                formal_release_authorized=False)


# 阅读注释（函数）：处理 persist end to end artifacts 相关逻辑。
def persist_end_to_end_artifacts(
    summary: Dict[str, Any],
    *,
    runtime_preflight: Dict[str, Any],
    expected_model_name: str,
    expected_real_rag: bool,
    report_path: Optional[str | Path] = None,
    raise_on_validation_error: bool = True,
    trusted_workspace=None,
) -> Dict[str, Any]:
    """Export verified Target output or legacy artifacts, then a diagnostic report.

    Target BEST_EFFORT is written under an explicit best_effort filename; its
    quality remains unchanged. No new publication or approval is performed.
    """

    generated_draft = summary.get("generated_draft") or {}
    generated_extra = generated_draft.get("extra") or {}
    pwa004_review = generated_extra.get("pwa004_review_draft")
    deliverable = summary.get("deliverable_final_result")
    rejected_artifact = summary.get("rejected_artifact")
    deliverable_path: Path | None = None
    rejected_draft_path: Path | None = None
    best_effort_path: Path | None = None
    review_draft_path: Path | None = None
    review_findings_path: Path | None = None
    review_body_available = False
    review_export_attempted = False
    validation: Optional[Dict[str, Any]] = None
    validation_error: Optional[Exception] = None
    target_release = None
    try:
        target_release = _read_target_release(summary)
    except Exception as exc:
        # Never export an unverified Target under a legacy deliverable alias.
        # Still preserve the original error in the diagnostic report below.
        validation_error = exc
    if target_release is not None:
        best_effort = target_release.manifest.release_mode.value == "BEST_EFFORT"
        path = _write_markdown_artifact(
            summary, target_release.payload_bytes,
            artifact_suffix="best_effort" if best_effort else "deliverable",
        )
        summary.setdefault("paths", {})[
            "best_effort_markdown" if best_effort else "deliverable_markdown"
        ] = str(path)
        if best_effort:
            best_effort_path = path
        else:
            deliverable_path = path
    elif validation_error is None and isinstance(deliverable, dict):
        deliverable_draft = deliverable.get("draft") or {}
        content = str(
            deliverable_draft.get("full_text")
            or deliverable_draft.get("content")
            or ""
        )
        deliverable_path = _write_markdown_artifact(
            summary,
            content,
            artifact_suffix="deliverable",
        )
        summary.setdefault("paths", {})["deliverable_markdown"] = str(
            deliverable_path
        )
    elif validation_error is None and isinstance(rejected_artifact, dict):
        content = str(
            generated_draft.get("full_text")
            or generated_draft.get("content")
            or ""
        )
        rejected_draft_path = _write_markdown_artifact(
            summary,
            content,
            artifact_suffix="rejected_draft",
        )
        summary.setdefault("paths", {})["rejected_draft_markdown"] = str(
            rejected_draft_path
        )
    elif (
        validation_error is None
        and isinstance(pwa004_review, dict)
        and pwa004_review
    ):
        try:
            findings = _project_pwa004_review(summary, trusted_workspace)
        except Exception:
            # Invalid source/path: do not let the diagnostic writer use its paths.
            if raise_on_validation_error:
                raise ValueError("PWA004 review delivery rejected") from None
            return {
                "validation": {}, "validation_error": {
                    "type": "ReviewExportRejected", "message": "PWA004 review delivery rejected",
                }, "report": {}, "deliverable_path": None, "rejected_draft_path": None,
                "best_effort_path": None, "review_draft_path": None,
                "review_findings_path": None, "review_body_available": False, "report_path": None,
            }
        review_export_attempted = True
        content = generated_draft.get("full_text") or generated_draft.get("content") or ""
        review_body_available = isinstance(content, str) and bool(content.strip())
        # Keep the existing writers; feed only workspace-derived review paths.
        artifact_summary = dict(summary, paths={"trace": str(trusted_workspace.trace_path)})
        for suffix, payload in (("_review_draft.md", content), ("_review_findings.json", findings)):
            if suffix.endswith(".md") and not review_body_available:
                continue
            try:
                expected_path = _review_artifact_path(trusted_workspace, suffix)
                if suffix.endswith(".md"):
                    expected_bytes = payload.encode("utf-8")
                    path = _write_markdown_artifact(
                        artifact_summary, expected_bytes, artifact_suffix="review_draft")
                else:
                    expected_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                    path = _write_json_artifact(
                        artifact_summary, payload, artifact_suffix="review_findings")
                if path.resolve() != expected_path or path.read_bytes() != expected_bytes:
                    raise ValueError("Review artifact readback differs")
                if suffix.endswith(".md"):
                    review_draft_path = path
                    summary.setdefault("paths", {})["review_draft_markdown"] = str(path)
                else:
                    review_findings_path = path
                    summary.setdefault("paths", {})["review_findings_json"] = str(path)
            except Exception:
                # Preserve any independently completed file; never expose IO details.
                validation_error = ValueError("PWA004 review artifact delivery failed")

    if validation_error is None:
        try:
            validation = _validate_end_to_end(
                summary,
                expected_model_name=expected_model_name,
                expected_real_rag=expected_real_rag,
                allow_business_failure=True,
            )
        except Exception as exc:  # report first, then preserve technical failure
            validation_error = exc
    report = {}
    persisted_report_path = None
    try:
        # 建立端到端结果报告
        report = _build_e2e_report(
            summary,
            runtime_preflight=runtime_preflight,
            validation=validation,
            validation_error=validation_error,
        )
        # 写入端到端结果
        persisted_report_path = _write_e2e_report(
            summary,
            report,
            explicit_path=report_path,
        )
        summary["paths"]["e2e_report"] = str(persisted_report_path)
        manifest_path = persisted_report_path.with_name(
            f"{summary['run_id']}_experiment_manifest.json"
        )
        # Register the planned manifest path before the final report write so the
        # report becomes immutable before its hash is captured by the manifest.
        summary["paths"]["experiment_manifest"] = str(manifest_path)
        report["paths"] = dict(summary["paths"])

        _write_e2e_report(
            summary,
            report,
            explicit_path=persisted_report_path,
        )

        manifest_writer = ExperimentRunManifestWriter(PROJECT_ROOT)
        experiment_seed = summary.get("experiment_seed")
        if experiment_seed is None:
            experiment_seed = _configured_experiment_seed()
        seed_application = dict(summary.get("experiment_seed_application") or {})
        manifest_runtime_preflight = {
            **dict(runtime_preflight),
            "experiment_seed": experiment_seed,
            "experiment_seed_application": seed_application,
            "experiment_seed_applied": bool(
                experiment_seed is not None and any(seed_application.values())
            ),
        }
        manifest = manifest_writer.build(
            summary=summary,
            runtime_preflight=manifest_runtime_preflight,
            report=report,
            seed=experiment_seed,
        )
        manifest_writer.write(manifest, manifest_path)

    except Exception:
        if not review_export_attempted:
            raise
        # Review files already delivered remain visible even if diagnostics fail.
        validation_error = ValueError("PWA004 review diagnostics delivery failed")

    if validation_error is not None and raise_on_validation_error:
        raise validation_error
    return {
        "validation": validation or {},
        "validation_error": (
            {
                "type": validation_error.__class__.__name__,
                "message": str(validation_error),
            }
            if validation_error is not None
            else None
        ),
        "report": report,
        "deliverable_path": (
            str(deliverable_path) if deliverable_path is not None else None
        ),
        "rejected_draft_path": (
            str(rejected_draft_path)
            if rejected_draft_path is not None
            else None
        ),
        "best_effort_path": str(best_effort_path) if best_effort_path is not None else None,
        "review_draft_path": str(review_draft_path) if review_draft_path is not None else None,
        "review_findings_path": str(review_findings_path) if review_findings_path is not None else None,
        "review_body_available": review_body_available,
        "report_path": str(persisted_report_path) if persisted_report_path is not None else None,
    }


# 阅读注释（函数）：执行 run 演示 的主流程。
def run_demo(
    user_input: str = DEFAULT_USER_INPUT,      #
    run_id: Optional[str] = None,
    task_id: Optional[str] = None,
    output_root: Optional[str | Path] = None,
    clean_existing: bool = False,
    settings: Optional[AppSettings] = None,
    enable_agent_self_rag: Optional[bool] = None,
    project_input: Optional[Dict[str, Any]] = None,
    allow_demo_defaults: bool = True,
    retrieval_access_scope: RetrievalAccessScopeSchema | None = None,
) -> Dict[str, Any]:
    """Run the demo adapter while delegating execution to the shared mainline."""

    experiment_seed = _configured_experiment_seed()
    seed_application = _apply_experiment_seed(experiment_seed)
    summary = run_mainline(
        user_input=user_input,
        run_id=run_id,
        task_id=task_id,
        output_root=output_root,
        clean_existing=clean_existing,
        settings=settings,
        enable_agent_self_rag=enable_agent_self_rag,
        project_input=project_input,
        allow_demo_defaults=allow_demo_defaults,
        retrieval_access_scope=retrieval_access_scope,
    )
    summary["experiment_seed"] = experiment_seed
    summary["experiment_seed_application"] = seed_application
    return summary


# 阅读注释（函数）：处理 main 相关逻辑。
def main() -> None:
    """处理 main 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：argparse.ArgumentParser, parser.add_argument, parser.parse_args, _resolve_console_question, _configure_runtime, str, resolve_project_path, get_settings。
    """
    ## 创建一个命令行解析参数
    parser = argparse.ArgumentParser(
        description="Run one complete Agent-RAG console request."
    )
    parser.add_argument(
        "--user-input",
        type=str,
        default=None,
        help="User requirement. When omitted, prompt in the console.",
    )
    parser.add_argument(
        "--fake-runtime",
        action="store_true",
        help="Use FakeRAGTool and FakeLLM for a fast smoke test.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Optional fixed run_id. If omitted, a timestamp-based run_id is generated.",
    )
    parser.add_argument(
        "--output-root",
        type=str,
        default=None,
        help="Output root directory. Default: PROJECT_ROOT/data.",
    )
    parser.add_argument(
        "--clean-existing",
        action="store_true",
        help="Delete existing output files for the same run_id before running.",
    )
    parser.add_argument(
        "--disable-agent-self-rag",
        action="store_true",
        help="Disable Agent-level Self-RAG check after SchemeWriter generation.",
    )
    parser.add_argument(
        "--rag-static-spec",
        type=str,
        default=None,
        help=(
            "YAML/JSON static retrieval specification. "
            "Overrides RAG_STATIC_RETRIEVAL_SPEC_FILE for this process."
        ),
    )
    parser.add_argument(
        "--rag-project-root",
        type=str,
        default=None,
        help=(
            "Optional online RAG runtime root containing "
            "data/processed/indexes/active_index.json. Code and policy files "
            "remain resolved from the repository."
        ),
    )
    parser.add_argument(
        "--project-input-file",
        type=str,
        default=None,
        help=(
            "Optional JSON file containing a complete ProjectInput. "
            "When omitted, the CLI builds a minimal validated ProjectInput "
            "from the console question."
        ),
    )
    parser.add_argument(
        "--authorized-kb-id",
        action="append",
        default=[],
        help=(
            "Trusted KB identifier authorized for this run. Repeat the option "
            "for multiple KBs. Required by formal real-RAG runs."
        ),
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=0,
        help="Print only the first N answer characters; 0 prints the full answer.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Treat a business hard-gate failure as process exit code 1. "
            "Without this flag, interactive mode still prints and saves the partial result."
        ),
    )
    parser.add_argument(
        "--e2e-report-path",
        type=str,
        default=None,
        help="Optional explicit path for the structured E2E JSON report.",
    )

    args = parser.parse_args()

    project_input = None
    if args.project_input_file:
        project_input_path = Path(args.project_input_file).expanduser().resolve()
        if not project_input_path.is_file():
            raise FileNotFoundError(
                f"ProjectInput file not found: {project_input_path}"
            )
        with project_input_path.open("r", encoding="utf-8") as file:
            project_input = json.load(file)
        if not isinstance(project_input, dict):
            raise ValueError(
                "ProjectInput file must contain one JSON object at the top level."
            )

    canonical_file_query = str(
        (project_input or {}).get("user_query") or ""
    ).strip()
    # Explicit ProjectInput is the canonical source of the formal run query.
    # This avoids terminal encoding/transcription drift at the CLI boundary.
    user_input = _resolve_console_question(
        args.user_input if args.user_input is not None else canonical_file_query
    )
    # 创建运行时配置
    _configure_runtime(fake_runtime=args.fake_runtime)
    if args.rag_project_root:
        os.environ["RAG_PROJECT_ROOT"] = str(
            Path(args.rag_project_root).expanduser().resolve()
        )

    # 接下用户自己上传的rag配置
    if args.rag_static_spec:
        os.environ["RAG_STATIC_RETRIEVAL_SPEC_FILE"] = str(
            resolve_project_path(args.rag_static_spec)
        )

    settings = get_settings(reload=True)

    runtime_preflight: Dict[str, Any]
    # 调用fake 不使用真实rag
    if args.fake_runtime:
        runtime_preflight = {
            "mode": "fake",
            "model_name": settings.default_model_name,
            "rag_tool": "FakeRAGTool",
            **_model_routing_snapshot(settings),
        }
    else:
        runtime_preflight = _preflight_real_runtime(settings)

    retrieval_access_scope = None
    if args.authorized_kb_id:
        tenant_id = str((project_input or {}).get("tenant_id") or "default")
        retrieval_access_scope = RetrievalAccessScopeSchema(
            tenant_id=tenant_id,
            authorized_kb_ids=list(dict.fromkeys(args.authorized_kb_id)),
        )

    # 调用run_demo函数并返回结果
    # Internal operator details remain in persisted task/trace/report artifacts.
    with open(os.devnull, "w") as private_console, redirect_stdout(private_console), redirect_stderr(private_console):
        summary = run_demo(
            user_input=user_input,
            run_id=args.run_id,
            output_root=args.output_root,
            clean_existing=args.clean_existing,
            settings=settings,
            enable_agent_self_rag=not args.disable_agent_self_rag,
            project_input=project_input,
            allow_demo_defaults=project_input is None,
            retrieval_access_scope=retrieval_access_scope,
        )
    # 生成主链的草稿
    generated_draft = summary.get("generated_draft") or {}
    deliverable = summary.get("deliverable_final_result") or {}
    display_draft = deliverable.get("draft") or generated_draft

    content = str(
        display_draft.get("full_text")
        or display_draft.get("content")
        or ""
    )
    sections = generated_draft.get("sections") or []
    first_section = sections[0] if sections else {}
    first_section_extra = first_section.get("extra") or {}
    #主链已经执行完后，保存产物并验证这次运行是否真的走完了端到端链路
    expected_validation_model_name = _resolve_validation_model_name(
        runtime_preflight,
        fallback_model_name=settings.default_model_name,
    )
    runtime_preflight["validation_expected_model_name"] = (
        expected_validation_model_name
    )
    # Pin the root from this controlled invocation, never from summary.paths.
    from application.run_workspace import RunWorkspace
    try:
        trusted_workspace = RunWorkspace(
            output_root=Path(args.output_root) if args.output_root is not None else settings.data_root,
            task_id=summary["task_id"], run_id=summary["run_id"],
        )
        if args.run_id is not None and args.run_id != trusted_workspace.run_id:
            trusted_workspace = None
    except (TypeError, ValueError):
        trusted_workspace = None
    # Internal operator details remain in persisted task/trace/report artifacts.
    with open(os.devnull, "w") as private_console, redirect_stdout(private_console), redirect_stderr(private_console):
        persisted = persist_end_to_end_artifacts(
            summary,
            runtime_preflight=runtime_preflight,
            expected_model_name=expected_validation_model_name,
            expected_real_rag=not args.fake_runtime,
            report_path=args.e2e_report_path,
            raise_on_validation_error=args.strict and summary.get("terminal_outcome") is None,
            trusted_workspace=trusted_workspace,
        )
    # 证明的是这次请求是否真实走过了完整主链，而不是只返回了一个伪造结果
    validation = persisted["validation"]
    # 端到端验证过程中是否发现了技术性错误。
    validation_error = persisted.get("validation_error")

    if summary.get("terminal_outcome") is not None:
        from application.terminal_outcome_projector import public_terminal_result
        public = public_terminal_result(summary)
        # Only this call's receipts can advertise review delivery.
        review_artifacts = {}
        for key, receipt_key, suffix in (
            ("review_draft_markdown", "review_draft_path", "_review_draft.md"),
            ("review_findings_json", "review_findings_path", "_review_findings.json"),
        ):
            path_value = persisted.get(receipt_key)
            if path_value and trusted_workspace is not None:
                try:
                    path = Path(path_value)
                    if (path.resolve() == _review_artifact_path(trusted_workspace, suffix)
                            and path.is_file() and path.read_bytes()):
                        review_artifacts[key] = str(path)
                except (OSError, ValueError):
                    pass
        if review_artifacts:
            public["review_artifacts"] = review_artifacts
        if (generated_draft.get("extra") or {}).get("pwa004_review_draft") is not None:
            public["review_delivery"] = {
                "body_available": bool(persisted.get("review_body_available")),
                "body_delivered": "review_draft_markdown" in review_artifacts,
                "findings_delivered": "review_findings_json" in review_artifacts,
                "e2e_validation_failed": validation_error is not None,
            }
        print(json.dumps(public, ensure_ascii=False, indent=2))
        if args.strict and (validation_error is not None or not public["terminal_outcome"]["publication_exists"]):
            raise SystemExit(1)
        return

    print("\n" + "=" * 80)
    print("Agent-RAG端到端运行完成")
    print("=" * 80)
    print(f"Status: {summary['status']}")
    print(f"Task ID: {summary['task_id']}")
    print(f"Run ID: {summary['run_id']}")

    print("\nEnd-to-end validation:")
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    if validation_error:
        print("\nValidation failure captured (artifacts were preserved):")
        print(json.dumps(validation_error, ensure_ascii=False, indent=2))

    print("\nOutput files:")
    for name, path in summary["paths"].items():
        print(f"- {name}: {path}")

    print("\nPrompt:")
    print(
        json.dumps(
            {
                "prompt_id": (
                    generated_draft.get("prompt_id")
                    or first_section_extra.get("prompt_id")
                ),
                "prompt_name": generated_draft.get("prompt_name"),
                "prompt_version": (
                    generated_draft.get("prompt_version")
                    or first_section_extra.get("prompt_version")
                ),
                "first_section": first_section.get("section_title"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n" + "=" * 80)
    print("最终可交付结果" if deliverable else "被拒绝草稿（不可交付）")
    print("=" * 80)
    if args.preview_chars > 0:
        print(content[: args.preview_chars])
    else:
        print(content)

    error = _effective_runtime_error(summary)
    if error:
        print("\nFailure details:")
        print(
            json.dumps(
                {
                    "error_code": error.get("error_code"),
                    "error_type": error.get("error_type"),
                    "message": error.get("message"),
                    "failed_node": error.get("failed_node"),
                    "retryable": error.get("retryable"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    print("\nJSON summary:")
    print(
        json.dumps(
            {
                "task_id": summary["task_id"],
                "run_id": summary["run_id"],
                "status": summary["status"],
                "paths": summary["paths"],
                "runtime_preflight": runtime_preflight,
                "end_to_end_validation": validation,
                "validation_error": validation_error,
                "error": error or None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if (
        args.strict
        and _normalize_status_value(summary["status"]) not in SUCCESS_LIKE_STATUSES
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        with _single_instance_lock():
            main()
    except RunDemoAlreadyRunningError as exc:
        print(f"[run_demo] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
