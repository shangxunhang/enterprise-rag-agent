"""Run the same eval set against multiple configured online RAG profiles."""
from __future__ import annotations

import json
import platform
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag.config.pipeline_config import PipelineConfigLoader
from rag.config.profile_catalog import OnlineRAGProfileCatalogValidator
from rag.registry.default_registrations import build_default_component_registry

from .baseline import BaselineManager
from .config import ExperimentConfigLoader
from .dataset import file_sha256, load_eval_samples
from .metrics import MetricContext, MetricRegistry, build_default_metric_registry
from .reporting import (
    write_comparison_csv,
    write_comparison_markdown,
    write_experiment_samples,
    write_json,
)
from .runtime import DefaultExperimentRuntimeFactory
from .schemas import (
    ComparisonRow,
    ExperimentConfig,
    ExperimentMatrixConfig,
    ExperimentReport,
    MatrixReport,
    RAGEvalSample,
    SampleMetricResult,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aggregate(values: list[float | None]) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def _set_seed(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


def _error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "error_type": exc.__class__.__name__,
        "message": str(exc),
    }


class StrategyEvalRunner:
    def __init__(
        self,
        *,
        metric_registry: MetricRegistry | None = None,
        runtime_factory: Any | None = None,
    ) -> None:
        self.metric_registry = metric_registry or build_default_metric_registry()
        self.runtime_factory = runtime_factory or DefaultExperimentRuntimeFactory()

    def run_from_file(
        self,
        config_path: str | Path,
        *,
        project_root: str | Path | None = None,
        output_dir_override: str | Path | None = None,
    ) -> MatrixReport:
        loader = ExperimentConfigLoader()
        matrix, resolved_config_path, config_hash = loader.load(
            config_path,
            project_root=project_root,
        )
        root = (
            Path(project_root).resolve()
            if project_root is not None
            else PipelineConfigLoader.default_project_root()
        )
        return self.run(
            matrix,
            project_root=root,
            matrix_config_path=resolved_config_path,
            matrix_config_hash=config_hash,
            output_dir_override=output_dir_override,
        )

    def validate_from_file(
        self,
        config_path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> dict[str, Any]:
        loader = ExperimentConfigLoader()
        matrix, resolved_config_path, config_hash = loader.load(
            config_path,
            project_root=project_root,
        )
        root = (
            Path(project_root).resolve()
            if project_root is not None
            else PipelineConfigLoader.default_project_root()
        )
        dataset_path = PipelineConfigLoader().resolve_path(
            matrix.dataset_path,
            project_root=root,
        )
        samples = load_eval_samples(dataset_path)
        catalog = OnlineRAGProfileCatalogValidator().validate(
            project_root=root,
            registry=build_default_component_registry(),
        )
        profiles: list[dict[str, Any]] = []
        for experiment in matrix.experiments:
            names = experiment.metrics or matrix.metrics
            definitions = self.metric_registry.require(names)
            self.metric_registry.validate_samples(names, samples)
            if experiment.mode == "retrieval":
                invalid = [item.name for item in definitions if item.requires_answer]
                if invalid:
                    raise ValueError(
                        "retrieval experiment cannot use answer metrics: "
                        + ", ".join(invalid)
                    )
            profile_path = PipelineConfigLoader().resolve_path(
                experiment.pipeline_config_file,
                project_root=root,
            )
            profile = PipelineConfigLoader().load(
                profile_path,
                project_root=root,
            )
            profiles.append(
                {
                    "experiment_id": experiment.experiment_id,
                    "profile_id": profile.profile_id,
                    "profile_version": profile.profile_version,
                    "path": str(profile_path),
                    "hash": profile.config_hash(),
                    "mode": experiment.mode,
                    "metrics": names,
                    "warmup_runs": experiment.warmup_runs,
                }
            )
        return {
            "status": "success",
            "matrix_id": matrix.matrix_id,
            "matrix_config_file": str(resolved_config_path),
            "matrix_config_hash": config_hash,
            "dataset_path": str(dataset_path),
            "dataset_hash": file_sha256(dataset_path),
            "sample_count": len(samples),
            "profile_catalog": catalog.to_dict(),
            "profiles": profiles,
        }

    def run(
        self,
        matrix: ExperimentMatrixConfig,
        *,
        project_root: Path,
        matrix_config_path: Path,
        matrix_config_hash: str,
        output_dir_override: str | Path | None = None,
    ) -> MatrixReport:
        started_at = _utc_now()
        matrix_run_id = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S_%f")
        started = time.perf_counter()
        dataset_path = PipelineConfigLoader().resolve_path(
            matrix.dataset_path,
            project_root=project_root,
        )
        samples = load_eval_samples(dataset_path)
        dataset_hash = file_sha256(dataset_path)
        output_dir = PipelineConfigLoader().resolve_path(
            output_dir_override or matrix.output_dir,
            project_root=project_root,
        ) / matrix.matrix_id / matrix_run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        experiment_reports: list[ExperimentReport] = []
        for experiment in matrix.experiments:
            report = self._run_experiment(
                matrix=matrix,
                experiment=experiment,
                samples=samples,
                project_root=project_root,
                dataset_hash=dataset_hash,
                matrix_config_hash=matrix_config_hash,
                matrix_run_id=matrix_run_id,
            )
            experiment_reports.append(report)
            experiment_dir = output_dir / "experiments" / experiment.experiment_id
            write_json(experiment_dir / "report.json", report)
            write_experiment_samples(experiment_dir / "samples.jsonl", report)
            if matrix.fail_fast and report.failure_count:
                break

        rows = BaselineManager(matrix.baseline_experiment_id).rows(
            experiment_reports,
            matrix.metrics,
        )

        failures = sum(row.failure_count for row in rows)
        successes = sum(row.success_count for row in rows)
        status = "failed" if successes == 0 else ("partial_success" if failures else "success")
        finished_at = _utc_now()
        report = MatrixReport(
            matrix_id=matrix.matrix_id,
            matrix_run_id=matrix_run_id,
            status=status,
            dataset_path=str(dataset_path),
            dataset_version=matrix.dataset_version,
            eval_set_version=matrix.eval_set_version,
            index_version=matrix.index_version,
            dataset_hash=dataset_hash,
            matrix_config_hash=matrix_config_hash,
            baseline_experiment_id=matrix.baseline_experiment_id,
            metrics=matrix.metrics,
            experiment_count=len(rows),
            rows=rows,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            notes=matrix.notes,
        )
        output_files = {
            "matrix_report_json": str(output_dir / "matrix_report.json"),
            "comparison_csv": str(output_dir / "comparison.csv"),
            "comparison_markdown": str(output_dir / "comparison.md"),
            "resolved_matrix_config": str(output_dir / "resolved_matrix_config.json"),
        }
        report.output_files = output_files
        write_json(output_dir / "matrix_report.json", report)
        write_json(
            output_dir / "resolved_matrix_config.json",
            {
                "source": str(matrix_config_path),
                "hash": matrix_config_hash,
                "config": matrix.model_dump(mode="json", exclude_none=True),
            },
        )
        write_comparison_csv(output_dir / "comparison.csv", rows, matrix.metrics)
        write_comparison_markdown(
            output_dir / "comparison.md",
            report,
            self.metric_registry.directions(matrix.metrics),
        )
        return report

    def _run_experiment(
        self,
        *,
        matrix: ExperimentMatrixConfig,
        experiment: ExperimentConfig,
        samples: list[RAGEvalSample],
        project_root: Path,
        dataset_hash: str,
        matrix_config_hash: str,
        matrix_run_id: str,
    ) -> ExperimentReport:
        started_at = _utc_now()
        started = time.perf_counter()
        seed = experiment.seed if experiment.seed is not None else matrix.seed
        _set_seed(seed)
        metric_names = experiment.metrics or matrix.metrics
        definitions = self.metric_registry.require(metric_names)
        self.metric_registry.validate_samples(metric_names, samples)
        if experiment.mode == "retrieval":
            invalid = [item.name for item in definitions if item.requires_answer]
            if invalid:
                raise ValueError(
                    "retrieval experiment cannot use answer metrics: "
                    + ", ".join(invalid)
                )

        profile_path = PipelineConfigLoader().resolve_path(
            experiment.pipeline_config_file,
            project_root=project_root,
        )
        profile = PipelineConfigLoader().load(
            profile_path,
            project_root=project_root,
        )
        metadata: dict[str, Any] = {
            "profile_id": profile.profile_id,
            "profile_version": profile.profile_version,
            "pipeline_config_file": str(profile_path),
            "pipeline_config_hash": profile.config_hash(),
            "components": profile.canonical_dict(),
        }
        sample_results: list[SampleMetricResult] = []
        runtime = None
        effective_experiment = experiment.model_copy(update={"seed": seed})
        try:
            runtime = self.runtime_factory.build(
                project_root=project_root,
                experiment=effective_experiment,
            )
            if experiment.warmup_runs and samples:
                warmup_experiment = effective_experiment.model_copy(
                    update={
                        "runtime_params": {
                            **effective_experiment.runtime_params,
                            "_experiment_warmup": True,
                        }
                    }
                )
                for _ in range(experiment.warmup_runs):
                    runtime.run(samples[0], warmup_experiment)
            for sample in samples:
                sample_started = time.perf_counter()
                try:
                    output = runtime.run(sample, effective_experiment)
                    latency = (time.perf_counter() - sample_started) * 1000.0
                    context = MetricContext(
                        sample=sample,
                        output=output,
                        latency_ms=latency,
                        top_k=experiment.top_k,
                    )
                    metrics = {
                        definition.name: definition.compute(context)
                        for definition in definitions
                    }
                    retrieval_results = output.get("retrieval_results") or []
                    sample_results.append(
                        SampleMetricResult(
                            sample_id=sample.sample_id,
                            query=sample.query,
                            success=True,
                            run_id=output.get("run_id"),
                            latency_ms=latency,
                            metrics=metrics,
                            retrieved_result_count=len(retrieval_results),
                            retrieved_ids=[
                                {
                                    "doc_id": item.get("doc_id"),
                                    "parent_chunk_id": item.get("parent_chunk_id"),
                                    "child_chunk_id": item.get("child_chunk_id")
                                    or item.get("chunk_id"),
                                }
                                for item in retrieval_results[: experiment.top_k]
                                if isinstance(item, dict)
                            ],
                            answer=output.get("answer"),
                            citations=output.get("citations") or [],
                            metadata={
                                "query_expansion": output.get("query_expansion") or {},
                                "adaptive_rag": output.get("adaptive_rag"),
                                "c_rag": output.get("c_rag"),
                                "self_rag": output.get("self_rag"),
                            },
                        )
                    )
                except Exception as exc:
                    latency = (time.perf_counter() - sample_started) * 1000.0
                    sample_results.append(
                        SampleMetricResult(
                            sample_id=sample.sample_id,
                            query=sample.query,
                            success=False,
                            latency_ms=latency,
                            error=_error_payload(exc),
                        )
                    )
                    if matrix.fail_fast:
                        break
        except Exception as exc:
            if not sample_results:
                sample_results = [
                    SampleMetricResult(
                        sample_id=sample.sample_id,
                        query=sample.query,
                        success=False,
                        error=_error_payload(exc),
                    )
                    for sample in samples
                ]
        finally:
            if runtime is not None:
                metadata = runtime.metadata()
                runtime.close()

        success_count = sum(1 for item in sample_results if item.success)
        failure_count = len(sample_results) - success_count
        aggregate = {
            name: _aggregate(
                [item.metrics.get(name) for item in sample_results if item.success]
            )
            for name in metric_names
        }
        status = (
            "failed"
            if success_count == 0
            else ("partial_success" if failure_count else "success")
        )
        finished_at = _utc_now()
        return ExperimentReport(
            experiment_id=experiment.experiment_id,
            matrix_id=matrix.matrix_id,
            matrix_run_id=matrix_run_id,
            status=status,
            profile_id=str(metadata["profile_id"]),
            profile_version=str(metadata["profile_version"]),
            pipeline_config_file=str(metadata["pipeline_config_file"]),
            pipeline_config_hash=str(metadata["pipeline_config_hash"]),
            component_metadata=dict(metadata["components"]),
            mode=experiment.mode,
            top_k=experiment.top_k,
            seed=seed,
            sample_count=len(sample_results),
            success_count=success_count,
            failure_count=failure_count,
            aggregate_metrics=aggregate,
            samples=sample_results,
            dataset_hash=dataset_hash,
            matrix_config_hash=matrix_config_hash,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            reproducibility={
                "python_version": sys.version,
                "platform": platform.platform(),
                "seed": seed,
                "dataset_version": matrix.dataset_version,
                "eval_set_version": matrix.eval_set_version,
                "index_version": matrix.index_version,
                "warmup_runs": experiment.warmup_runs,
            },
            notes=experiment.notes,
        )
