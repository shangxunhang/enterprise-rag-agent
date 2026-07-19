# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：_utc_now、_aggregate、_set_seed、_error_payload、StrategyEvalRunner。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Run one eval set against static retrieval specs and request policies."""
from __future__ import annotations

import json
import platform
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag.config.static_retrieval import StaticRetrievalSpecLoader
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


# 阅读注释（函数）：处理 utc now 相关逻辑。
def _utc_now() -> str:
    """处理 utc now 相关逻辑。

    返回:
        str

    阅读提示:
        主要直接调用：isoformat, datetime.now。
    """
    return datetime.now(timezone.utc).isoformat()


# 阅读注释（函数）：处理 aggregate 相关逻辑。
def _aggregate(values: list[float | None]) -> float | None:
    """处理 aggregate 相关逻辑。

    参数:
        values: values，具体约束请结合类型标注和调用方确认。

    返回:
        float | None

    阅读提示:
        主要直接调用：float, sum, len。
    """
    usable = [float(value) for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


# 阅读注释（函数）：设置 seed。
def _set_seed(seed: int) -> None:
    """设置 seed。

    参数:
        seed: seed，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：random.seed, np.random.seed, torch.manual_seed, torch.cuda.is_available, torch.cuda.manual_seed_all。
    """
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


# 阅读注释（函数）：处理 错误 载荷 相关逻辑。
def _error_payload(exc: Exception) -> dict[str, Any]:
    """处理 错误 载荷 相关逻辑。

    参数:
        exc: exc，具体约束请结合类型标注和调用方确认。

    返回:
        dict[str, Any]

    阅读提示:
        主要直接调用：str。
    """
    return {
        "error_type": exc.__class__.__name__,
        "message": str(exc),
    }


# 阅读注释（类）：封装 strategy 评测 runner，集中封装相关状态、依赖和行为。
class StrategyEvalRunner:
    """封装 strategy 评测 runner，集中封装相关状态、依赖和行为。"""
    # 阅读注释（函数）：初始化 StrategyEvalRunner，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        metric_registry: MetricRegistry | None = None,
        runtime_factory: Any | None = None,
    ) -> None:
        """初始化 StrategyEvalRunner，保存运行所需的依赖、配置或状态。

        参数:
            metric_registry: 指标 注册表，具体约束请结合类型标注和调用方确认。
            runtime_factory: 运行时 工厂，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：build_default_metric_registry, DefaultExperimentRuntimeFactory。
        """
        self.metric_registry = metric_registry or build_default_metric_registry()
        self.runtime_factory = runtime_factory or DefaultExperimentRuntimeFactory()

    # 阅读注释（函数）：执行 StrategyEvalRunner 的主流程。
    def run_from_file(
        self,
        config_path: str | Path,
        *,
        project_root: str | Path | None = None,
        output_dir_override: str | Path | None = None,
    ) -> MatrixReport:
        """执行 StrategyEvalRunner 的主流程。

        参数:
            config_path: 配置 路径，具体约束请结合类型标注和调用方确认。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。
            output_dir_override: 输出 dir override，具体约束请结合类型标注和调用方确认。

        返回:
            MatrixReport

        阅读提示:
            主要直接调用：ExperimentConfigLoader, loader.load, resolve, Path, PipelineConfigLoader.default_project_root, self.run。
        """
        loader = ExperimentConfigLoader()
        matrix, resolved_config_path, config_hash = loader.load(
            config_path,
            project_root=project_root,
        )
        root = (
            Path(project_root).resolve()
            if project_root is not None
            else StaticRetrievalSpecLoader.default_project_root()
        )
        return self.run(
            matrix,
            project_root=root,
            matrix_config_path=resolved_config_path,
            matrix_config_hash=config_hash,
            output_dir_override=output_dir_override,
        )

    # 阅读注释（函数）：校验 from 文件。
    def validate_from_file(
        self,
        config_path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> dict[str, Any]:
        """校验 from 文件。

        参数:
            config_path: 配置 路径，具体约束请结合类型标注和调用方确认。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：ExperimentConfigLoader, loader.load, resolve, Path, PipelineConfigLoader.default_project_root, resolve_path, PipelineConfigLoader, load_eval_samples。
        """
        loader = ExperimentConfigLoader()
        matrix, resolved_config_path, config_hash = loader.load(
            config_path,
            project_root=project_root,
        )
        root = (
            Path(project_root).resolve()
            if project_root is not None
            else StaticRetrievalSpecLoader.default_project_root()
        )
        spec_loader = StaticRetrievalSpecLoader()
        dataset_path = spec_loader.resolve_path(
            matrix.dataset_path,
            project_root=root,
        )
        samples = load_eval_samples(dataset_path)
        registry = build_default_component_registry()
        specs: list[dict[str, Any]] = []
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
            spec_path = spec_loader.resolve_path(
                experiment.static_retrieval_spec_file,
                project_root=root,
            )
            spec = spec_loader.load(
                spec_path,
                project_root=root,
            )
            references = [
                *(("query_transformer", item) for item in spec.query_transformers),
                *(("retriever", item) for item in spec.retrievers),
                ("source_fusion", spec.source_fusion),
                ("query_fusion", spec.query_fusion),
                ("candidate_enricher", spec.candidate_enricher),
                ("reranker", spec.reranker),
                ("evidence_assessor", spec.evidence_assessor),
                (
                    "corrective_retrieval_gate",
                    spec.corrective_retrieval_gate,
                ),
                ("corrective_query_planner", spec.corrective_query_planner),
                *(("context_packer", item) for item in spec.context_packers),
            ]
            missing = [
                f"{category}/{config.name}@{config.version}"
                for category, config in references
                if not registry.contains(
                    category=category,
                    name=config.name,
                    version=config.version,
                )
            ]
            if missing:
                raise ValueError(
                    "static retrieval spec references unregistered components: "
                    + ", ".join(missing)
                )
            specs.append(
                {
                    "experiment_id": experiment.experiment_id,
                    "static_spec_id": spec.spec_id,
                    "static_spec_version": spec.spec_version,
                    "path": str(spec_path),
                    "hash": spec.config_hash(),
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
            "static_specs": specs,
        }

    # 阅读注释（函数）：执行 StrategyEvalRunner 的主流程。
    def run(
        self,
        matrix: ExperimentMatrixConfig,
        *,
        project_root: Path,
        matrix_config_path: Path,
        matrix_config_hash: str,
        output_dir_override: str | Path | None = None,
    ) -> MatrixReport:
        """执行 StrategyEvalRunner 的主流程。

        参数:
            matrix: matrix，具体约束请结合类型标注和调用方确认。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。
            matrix_config_path: matrix 配置 路径，具体约束请结合类型标注和调用方确认。
            matrix_config_hash: matrix 配置 hash，具体约束请结合类型标注和调用方确认。
            output_dir_override: 输出 dir override，具体约束请结合类型标注和调用方确认。

        返回:
            MatrixReport

        阅读提示:
            主要直接调用：_utc_now, strftime, datetime.now, time.perf_counter, resolve_path, PipelineConfigLoader, load_eval_samples, file_sha256。
        """
        started_at = _utc_now()
        matrix_run_id = datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S_%f")
        started = time.perf_counter()
        path_loader = StaticRetrievalSpecLoader()
        dataset_path = path_loader.resolve_path(
            matrix.dataset_path,
            project_root=project_root,
        )
        samples = load_eval_samples(dataset_path)
        dataset_hash = file_sha256(dataset_path)
        output_dir = path_loader.resolve_path(
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

    # 阅读注释（函数）：执行 StrategyEvalRunner 的主流程。
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
        """执行 StrategyEvalRunner 的主流程。

        参数:
            matrix: matrix，具体约束请结合类型标注和调用方确认。
            experiment: experiment，具体约束请结合类型标注和调用方确认。
            samples: samples，具体约束请结合类型标注和调用方确认。
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。
            dataset_hash: 数据集 hash，具体约束请结合类型标注和调用方确认。
            matrix_config_hash: matrix 配置 hash，具体约束请结合类型标注和调用方确认。
            matrix_run_id: matrix run 标识，具体约束请结合类型标注和调用方确认。

        返回:
            ExperimentReport

        阅读提示:
            主要直接调用：_utc_now, time.perf_counter, _set_seed, self.metric_registry.require, self.metric_registry.validate_samples, ValueError, join, resolve_path。
        """
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

        spec_loader = StaticRetrievalSpecLoader()
        spec_path = spec_loader.resolve_path(
            experiment.static_retrieval_spec_file,
            project_root=project_root,
        )
        spec = spec_loader.load(
            spec_path,
            project_root=project_root,
        )
        metadata: dict[str, Any] = {
            "static_spec_id": spec.spec_id,
            "static_spec_version": spec.spec_version,
            "static_retrieval_spec_file": str(spec_path),
            "static_retrieval_spec_hash": spec.config_hash(),
            "components": spec.canonical_dict(),
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
                                "retrieval_plan": output.get("retrieval_plan"),
                                "evidence_quality": output.get("evidence_quality"),
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
            static_spec_id=str(metadata["static_spec_id"]),
            static_spec_version=str(metadata["static_spec_version"]),
            static_retrieval_spec_file=str(
                metadata["static_retrieval_spec_file"]
            ),
            static_retrieval_spec_hash=str(
                metadata["static_retrieval_spec_hash"]
            ),
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
