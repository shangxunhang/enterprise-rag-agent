"""Strict experiment-run manifest for reproducibility and artifact integrity."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactManifestRecord(_StrictModel):
    artifact_type: str
    path: str
    lifecycle: Literal["planned", "created", "verified"]
    verification_method: Literal["none", "exists", "sha256"] = "none"
    exists: bool
    sha256: str | None = None
    size_bytes: int | None = None
    producer: str | None = None

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "ArtifactManifestRecord":
        if self.lifecycle == "planned" and self.exists:
            raise ValueError("planned artifact cannot already exist")
        if self.lifecycle in {"created", "verified"} and not self.exists:
            raise ValueError("created/verified artifact must exist")
        if self.lifecycle == "verified" and (
            self.verification_method != "sha256" or not self.sha256
        ):
            raise ValueError("verified artifact requires sha256 verification")
        return self

    @classmethod
    def from_path(
        cls,
        *,
        artifact_type: str,
        path: str | Path,
        producer: str | None = None,
        verify: bool = True,
    ) -> "ArtifactManifestRecord":
        resolved = Path(path).expanduser().resolve()
        exists = resolved.is_file()
        digest: str | None = None
        size_bytes: int | None = None
        if exists:
            size_bytes = resolved.stat().st_size
            if verify:
                hasher = hashlib.sha256()
                with resolved.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(block)
                digest = hasher.hexdigest()
        lifecycle = "planned" if not exists else "verified" if verify else "created"
        verification_method = "none" if not exists else "sha256" if verify else "exists"
        return cls(
            artifact_type=artifact_type,
            path=str(resolved),
            lifecycle=lifecycle,
            verification_method=verification_method,
            exists=exists,
            sha256=digest,
            size_bytes=size_bytes,
            producer=producer,
        )


class ModelUsageManifestSnapshot(_StrictModel):
    """Lossless manifest projection of the canonical ModelUsageLedger contract."""

    budget_semantics: str
    token_usage_semantics: str
    logical_calls: int
    unique_logical_call_ids: int
    provider_attempts: int
    calls_by_model: dict[str, int]
    calls_by_profile: dict[str, int]
    calls_by_provider: dict[str, int]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    failures: int
    cancelled_attempts: int
    incomplete_usage_attempts: int
    availability_fallback_count: int
    quality_escalation_count: int
    cost_if_available: float | None


class ExperimentRunManifest(_StrictModel):
    schema_version: Literal["experiment_run_manifest_v1"] = (
        "experiment_run_manifest_v1"
    )
    experiment_id: str
    task_id: str
    run_id: str
    created_at: str
    code: dict[str, Any] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)
    models: list[dict[str, Any]] = Field(default_factory=list)
    model_usage: ModelUsageManifestSnapshot | None = None
    model_calls: list[dict[str, Any]] = Field(default_factory=list)
    prompts: dict[str, Any] = Field(default_factory=dict)
    rag: dict[str, Any] = Field(default_factory=dict)
    index: dict[str, Any] = Field(default_factory=dict)
    dataset: dict[str, Any] = Field(default_factory=dict)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    dependencies: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, ArtifactManifestRecord] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentRunManifestWriter:
    """Build one immutable snapshot from existing runtime truth sources."""

    _DEPENDENCIES = (
        "pydantic",
        "langchain",
        "langgraph",
        "transformers",
        "torch",
        "pymilvus",
        "sentence-transformers",
    )

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).expanduser().resolve()

    def _git(self, *args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.project_root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            return None
        return completed.stdout.strip() or None

    def _code_snapshot(self) -> dict[str, Any]:
        dirty = self._git("status", "--porcelain")
        return {
            "git_commit_sha": self._git("rev-parse", "HEAD"),
            "git_tag": self._git("describe", "--tags", "--exact-match"),
            "git_branch": self._git("rev-parse", "--abbrev-ref", "HEAD"),
            "git_dirty": bool(dirty),
        }

    @classmethod
    def _dependency_snapshot(cls) -> dict[str, str]:
        versions: dict[str, str] = {}
        for package in cls._DEPENDENCIES:
            try:
                versions[package] = importlib_metadata.version(package)
            except importlib_metadata.PackageNotFoundError:
                continue
        return versions

    @staticmethod
    def _sha256_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _file_snapshot(cls, path: Any) -> dict[str, Any]:
        if not path:
            return {"path": None, "exists": False, "sha256": None}
        resolved = Path(str(path)).expanduser().resolve()
        if not resolved.is_file():
            return {"path": str(resolved), "exists": False, "sha256": None}
        hasher = hashlib.sha256()
        with resolved.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(block)
        return {
            "path": str(resolved),
            "exists": True,
            "sha256": hasher.hexdigest(),
        }

    @classmethod
    def _prompt_snapshot(cls, summary: dict[str, Any]) -> dict[str, Any]:
        draft = dict(summary.get("generated_draft") or {})
        sections = list(draft.get("sections") or [])
        prompt_records: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for section in sections:
            extra = dict((section or {}).get("extra") or {})
            prompt_text = str((section or {}).get("prompt") or "")
            record = {
                "prompt_id": extra.get("prompt_id"),
                "prompt_version": extra.get("prompt_version"),
                "template_sha256": cls._sha256_text(prompt_text) if prompt_text else None,
                "context_sha256": extra.get("context_sha256"),
            }
            key = tuple(record.values())
            if any(record.values()) and key not in seen:
                seen.add(key)
                prompt_records.append(record)
        return {"records": prompt_records}

    @staticmethod
    def _model_call_snapshot(summary: dict[str, Any]) -> list[dict[str, Any]]:
        """Read actual provider-attempt identity and generation params from Trace v2."""

        trace_path = (summary.get("paths") or {}).get("trace")
        if not trace_path:
            return []
        path = Path(str(trace_path)).expanduser().resolve()
        if not path.is_file():
            return []

        calls: list[dict[str, Any]] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("event_type") != "model_started":
                continue
            input_summary = dict(event.get("input_summary") or {})
            metadata = dict(event.get("metadata") or {})
            calls.append(
                {
                    "model_call_id": event.get("call_id")
                    or input_summary.get("model_call_id"),
                    "selected_model": event.get("model_name")
                    or event.get("component_name")
                    or input_summary.get("model_name"),
                    "selected_profile": metadata.get("selected_profile"),
                    "provider": metadata.get("provider"),
                    "model_role": metadata.get("model_role"),
                    "call_purpose": metadata.get("call_purpose")
                    or input_summary.get("call_purpose"),
                    "temperature": input_summary.get("temperature"),
                    "max_tokens": input_summary.get("max_tokens"),
                    "top_p": input_summary.get("top_p"),
                    "do_sample": input_summary.get("do_sample"),
                    "prompt_sha256": input_summary.get("prompt_sha256"),
                    "prompt_id": metadata.get("prompt_id")
                    or input_summary.get("prompt_id"),
                    "prompt_version": metadata.get("prompt_version")
                    or input_summary.get("prompt_version"),
                    "availability_fallback_from": list(
                        metadata.get("availability_fallback_from") or []
                    ),
                }
            )
        return calls

    @staticmethod
    def _model_usage_snapshot(summary: dict[str, Any]) -> ModelUsageManifestSnapshot | None:
        usage = summary.get("model_usage") or summary.get("model_usage_snapshot")
        if not usage:
            return None
        # extra='forbid' makes future producer-contract additions fail loudly
        # until the manifest contract is deliberately updated; fields can no
        # longer disappear silently in this mapper.
        return ModelUsageManifestSnapshot.model_validate(dict(usage))

    @staticmethod
    def _model_snapshot(
        summary: dict[str, Any],
        report: dict[str, Any],
        runtime_preflight: dict[str, Any],
    ) -> list[dict[str, Any]]:
        usage = dict(
            summary.get("model_usage") or summary.get("model_usage_snapshot") or {}
        )
        calls_by_model = dict(usage.get("calls_by_model") or {})
        configured_profiles = [
            dict(item)
            for item in list(runtime_preflight.get("model_profiles") or [])
            if isinstance(item, dict)
        ]
        profiles_by_model = {
            str(item.get("model_name") or ""): item
            for item in configured_profiles
            if str(item.get("model_name") or "").strip()
        }
        routing_roles_by_profile: dict[str, list[str]] = {}
        for policy in list(runtime_preflight.get("model_routing_policies") or []):
            if not isinstance(policy, dict):
                continue
            role = str(policy.get("role") or "").strip()
            for profile_id in list(policy.get("candidates") or []):
                routing_roles_by_profile.setdefault(str(profile_id), []).append(role)

        observed_models = [
            str(item)
            for item in (
                (report.get("validation") or {}).get("model_components") or []
            )
            if str(item).strip()
        ]
        model_names = list(
            dict.fromkeys([*observed_models, *calls_by_model.keys()])
        )
        primary = str(runtime_preflight.get("model_name") or "").strip()
        if primary and primary not in model_names:
            model_names.insert(0, primary)

        snapshots: list[dict[str, Any]] = []
        for name in model_names:
            profile = profiles_by_model.get(name, {})
            profile_id = str(profile.get("profile_id") or "") or None
            snapshots.append(
                {
                    "model_name": name,
                    "primary": name == primary,
                    "provider": profile.get("provider"),
                    "profile_id": profile_id,
                    "provider_model_name": profile.get("provider_model_name"),
                    "model_path": profile.get("local_path")
                    or (runtime_preflight.get("model_path") if name == primary else None),
                    "residency_policy": profile.get("residency_policy"),
                    "routing_roles": (
                        routing_roles_by_profile.get(profile_id, [])
                        if profile_id is not None
                        else []
                    ),
                    "provider_attempts": int(calls_by_model.get(name, 0) or 0),
                }
            )
        return snapshots

    def build(
        self,
        *,
        summary: dict[str, Any],
        runtime_preflight: dict[str, Any],
        report: dict[str, Any],
        seed: int | None = None,
    ) -> ExperimentRunManifest:
        run_id = str(summary.get("run_id") or "").strip()
        task_id = str(summary.get("task_id") or "").strip()
        if not run_id or not task_id:
            raise ValueError("experiment manifest requires task_id and run_id")

        artifacts = {
            name: ArtifactManifestRecord.from_path(
                artifact_type=name,
                path=path,
                producer="run_demo",
            )
            for name, path in dict(summary.get("paths") or {}).items()
            if path and name != "experiment_manifest"
        }
        index = {
            "index_version": runtime_preflight.get("active_index_version"),
            "backend": runtime_preflight.get("active_index_backend"),
            "collection": runtime_preflight.get("active_index_collection"),
            "embedding_model": runtime_preflight.get("embedding_model"),
            "embedding_version": runtime_preflight.get("embedding_version"),
            "embedding_dim": runtime_preflight.get("embedding_dim"),
            "manifest_identity": {
                "path": runtime_preflight.get("active_index_manifest_path"),
                "sha256": runtime_preflight.get("active_index_manifest_sha256"),
                "config_sha256": runtime_preflight.get("active_index_config_sha256"),
                "reproducibility_sha256": runtime_preflight.get(
                    "active_index_reproducibility_sha256"
                ),
            },
            "manifest_sha256": runtime_preflight.get("active_index_manifest_sha256"),
        }
        static_spec_identity = self._file_snapshot(
            runtime_preflight.get("static_retrieval_spec")
        )
        rag = {
            "static_retrieval_spec": runtime_preflight.get("static_retrieval_spec"),
            "static_retrieval_spec_identity": static_spec_identity,
            "rag_call_count": (report.get("metrics") or {}).get("rag_call_count"),
        }
        evaluation = {
            "report_schema_version": report.get("schema_version"),
            "status": report.get("status"),
            "execution_integrity": report.get("execution_integrity"),
            "business_quality": report.get("business_quality"),
            "hard_gate": report.get("hard_gate"),
            "eval_set_id": runtime_preflight.get("eval_set_id"),
            "eval_set_version": runtime_preflight.get("eval_set_version"),
            "eval_set_sha256": runtime_preflight.get("eval_set_sha256"),
            "evaluation_config_sha256": runtime_preflight.get("evaluation_config_sha256"),
        }
        project_input = dict(summary.get("project_input") or {})
        dataset = {
            "source_materials": project_input.get("source_materials") or [],
            "dataset_version": runtime_preflight.get("active_index_dataset_version")
            or (
                (summary.get("rag_tool_output") or {}).get("index_lineage") or {}
            ).get("dataset_version"),
        }
        return ExperimentRunManifest(
            experiment_id=f"experiment_{run_id}",
            task_id=task_id,
            run_id=run_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            code=self._code_snapshot(),
            runtime=dict(runtime_preflight),
            models=self._model_snapshot(summary, report, runtime_preflight),
            model_usage=self._model_usage_snapshot(summary),
            model_calls=self._model_call_snapshot(summary),
            prompts=self._prompt_snapshot(summary),
            rag=rag,
            index=index,
            dataset=dataset,
            evaluation=evaluation,
            seed=seed,
            dependencies=self._dependency_snapshot(),
            artifacts=artifacts,
            metadata={
                "artifact_integrity": "lifecycle_sha256_size_v2",
                "manifest_role": "reproducibility_snapshot",
                "unavailable_values_are_explicit_null": True,
            },
        )

    def write(
        self,
        manifest: ExperimentRunManifest,
        path: str | Path,
    ) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target
