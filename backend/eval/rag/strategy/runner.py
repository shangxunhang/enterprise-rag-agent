"""执行静态检索规范之间的可重复 RAG 策略对比实验。

Runner 负责加载矩阵与黄金数据集、预校验组件注册和指标标签、为每个实验创建独立
运行时、执行 warmup 和正式样本、隔离逐样本失败、聚合指标并写出多格式报告。
它不实现具体检索算法，也不在运行时动态注册组件。
"""

from __future__ import annotations

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
    ExperimentConfig,
    ExperimentMatrixConfig,
    ExperimentReport,
    MatrixReport,
    RAGEvalSample,
    SampleMetricResult,
)


def _utc_now() -> str:
    """返回带时区的当前 UTC ISO 时间戳。"""

    return datetime.now(timezone.utc).isoformat()


def _aggregate(values: list[float | None]) -> float | None:
    """对非空指标值计算算术平均，全部缺失时返回 ``None``。

    ``None`` 表示指标不适用或样本失败，不能作为零分参与平均，否则不同模式之间的
    结果会因缺失指标被系统性压低。
    """

    usable = [float(value) for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def _set_seed(seed: int) -> None:
    """尽可能同步 Python、NumPy 和 PyTorch 随机种子。"""

    # 第一步：标准库随机源始终可用。
    random.seed(seed)

    # 第二步：NumPy 是可选依赖，缺失时不阻止纯 Python 实验。
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass

    # 第三步：PyTorch 可选；CUDA 可用时同步所有设备种子。
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


def _error_payload(exc: Exception) -> dict[str, Any]:
    """把异常压缩为可序列化的稳定错误摘要。"""

    # 策略报告只记录类型和消息；完整堆栈应由运行日志或 Trace 承载。
    return {
        "error_type": exc.__class__.__name__,
        "message": str(exc),
    }


class StrategyEvalRunner:
    """协调策略矩阵的预校验、执行、聚合和报告落盘。

    Attributes:
        metric_registry: 指标定义、优化方向和黄金标签校验的事实源。
        runtime_factory: 为每个实验创建隔离 ``ExperimentRuntime`` 的工厂。
    """

    def __init__(
        self,
        *,
        metric_registry: MetricRegistry | None = None,
        runtime_factory: Any | None = None,
    ) -> None:
        """注入指标注册表和实验运行时工厂。"""

        self.metric_registry = (
            metric_registry or build_default_metric_registry()
        )
        self.runtime_factory = (
            runtime_factory or DefaultExperimentRuntimeFactory()
        )

    def run_from_file(
        self,
        config_path: str | Path,
        *,
        project_root: str | Path | None = None,
        output_dir_override: str | Path | None = None,
    ) -> MatrixReport:
        """从 YAML/JSON 配置文件加载并执行完整实验矩阵。

        Args:
            config_path: 实验矩阵配置路径。
            project_root: 可选项目根目录，用于解析配置中的相对路径。
            output_dir_override: 可选输出目录覆盖，不修改配置对象本身。

        Returns:
            执行完成并已写出报告文件的矩阵报告。
        """

        # 第一步：读取严格配置，同时获得解析路径和 canonical 配置哈希。
        loader = ExperimentConfigLoader()
        matrix, resolved_config_path, config_hash = loader.load(
            config_path,
            project_root=project_root,
        )

        # 第二步：确定统一项目根目录，后续数据集、规范和输出都相对该根解析。
        root = (
            Path(project_root).resolve()
            if project_root is not None
            else StaticRetrievalSpecLoader.default_project_root()
        )

        # 第三步：把已解析对象和可重复性信息交给内存执行入口。
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
        """在不加载模型或执行样本的情况下验证实验矩阵。

        检查内容包括配置 Schema、数据集、指标名称与黄金标签、模式与指标兼容性、
        静态检索规范以及规范引用的全部组件是否已在默认注册表中注册。
        """

        # 第一步：加载矩阵并确定统一项目根目录。
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

        # 第二步：解析并校验黄金数据集。
        spec_loader = StaticRetrievalSpecLoader()
        dataset_path = spec_loader.resolve_path(
            matrix.dataset_path,
            project_root=root,
        )
        samples = load_eval_samples(dataset_path)

        # 第三步：构建生产默认组件注册表，用于验证静态规范引用可被实际装配。
        registry = build_default_component_registry()
        specs: list[dict[str, Any]] = []

        # 第四步：逐实验校验指标、模式、静态规范和组件引用。
        for experiment in matrix.experiments:
            metric_names = experiment.metrics or matrix.metrics
            definitions = self.metric_registry.require(metric_names)
            self.metric_registry.validate_samples(metric_names, samples)

            # retrieval 模式没有回答，不能选择 requires_answer 指标。
            if experiment.mode == "retrieval":
                invalid = [
                    item.name
                    for item in definitions
                    if item.requires_answer
                ]
                if invalid:
                    raise ValueError(
                        "retrieval experiment cannot use answer metrics: "
                        + ", ".join(invalid)
                    )

            # 解析并加载本实验绑定的静态检索规范。
            spec_path = spec_loader.resolve_path(
                experiment.static_retrieval_spec_file,
                project_root=root,
            )
            spec = spec_loader.load(
                spec_path,
                project_root=root,
            )

            # 将规范中全部插件引用展开为统一的 category/config 对。
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

            # 任何未注册组件都会导致真实运行失败，因此在预校验阶段一次性报告。
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

            # 保存本实验解析后的规范身份和执行口径，形成验证报告。
            specs.append(
                {
                    "experiment_id": experiment.experiment_id,
                    "static_spec_id": spec.spec_id,
                    "static_spec_version": spec.spec_version,
                    "path": str(spec_path),
                    "hash": spec.config_hash(),
                    "mode": experiment.mode,
                    "metrics": metric_names,
                    "warmup_runs": experiment.warmup_runs,
                }
            )

        # 第五步：返回不含运行副作用的验证摘要。
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

    def run(
        self,
        matrix: ExperimentMatrixConfig,
        *,
        project_root: Path,
        matrix_config_path: Path,
        matrix_config_hash: str,
        output_dir_override: str | Path | None = None,
    ) -> MatrixReport:
        """执行已校验的实验矩阵并写出矩阵级产物。

        Args:
            matrix: 严格实验矩阵配置。
            project_root: 所有相对路径的解析根目录。
            matrix_config_path: 原矩阵配置实际路径。
            matrix_config_hash: 矩阵 canonical SHA-256。
            output_dir_override: 可选输出目录覆盖。

        Returns:
            包含实验对比行和全部输出文件路径的矩阵报告。
        """

        # 第一步：建立矩阵运行身份和计时基准。
        started_at = _utc_now()
        matrix_run_id = datetime.now(timezone.utc).strftime(
            "run_%Y%m%d_%H%M%S_%f"
        )
        started = time.perf_counter()

        # 第二步：解析数据集、计算原始文件哈希并创建本次独立输出目录。
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

        # 第三步：顺序执行实验，每个实验结束后立即落盘，降低中途失败的数据损失。
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

            experiment_dir = (
                output_dir / "experiments" / experiment.experiment_id
            )
            write_json(experiment_dir / "report.json", report)
            write_experiment_samples(
                experiment_dir / "samples.jsonl",
                report,
            )

            # fail_fast 在首个含失败样本的实验后终止剩余实验。
            if matrix.fail_fast and report.failure_count:
                break

        # 第四步：基于矩阵级指标顺序构建绝对值和相对基线对比行。
        rows = BaselineManager(matrix.baseline_experiment_id).rows(
            experiment_reports,
            matrix.metrics,
        )

        # 第五步：根据所有已执行实验的样本成功/失败数决定矩阵状态。
        failures = sum(row.failure_count for row in rows)
        successes = sum(row.success_count for row in rows)
        status = (
            "failed"
            if successes == 0
            else ("partial_success" if failures else "success")
        )

        # 第六步：构建尚未填充输出文件索引的矩阵报告。
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

        # 第七步：先确定所有矩阵级产物路径，再写回报告对象。
        output_files = {
            "matrix_report_json": str(output_dir / "matrix_report.json"),
            "comparison_csv": str(output_dir / "comparison.csv"),
            "comparison_markdown": str(output_dir / "comparison.md"),
            "resolved_matrix_config": str(
                output_dir / "resolved_matrix_config.json"
            ),
        }
        report.output_files = output_files

        # 第八步：写出完整矩阵报告和实际使用的解析后配置快照。
        write_json(output_dir / "matrix_report.json", report)
        write_json(
            output_dir / "resolved_matrix_config.json",
            {
                "source": str(matrix_config_path),
                "hash": matrix_config_hash,
                "config": matrix.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
            },
        )

        # 第九步：写出适合表格分析的 CSV 和面向人工阅读的 Markdown。
        write_comparison_csv(
            output_dir / "comparison.csv",
            rows,
            matrix.metrics,
        )
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
        """执行一个静态检索策略在全部黄金样本上的实验。

        样本级异常被转换为失败记录并允许继续执行；运行时构建或 warmup 级异常会在
        尚无样本结果时把全部样本标为失败。无论成功失败，已创建的运行时都会在
        finally 中提取元数据并关闭。
        """

        # 第一步：固定实验时间、随机种子和实际指标集合。
        started_at = _utc_now()
        started = time.perf_counter()
        seed = (
            experiment.seed
            if experiment.seed is not None
            else matrix.seed
        )
        _set_seed(seed)
        metric_names = experiment.metrics or matrix.metrics
        definitions = self.metric_registry.require(metric_names)
        self.metric_registry.validate_samples(metric_names, samples)

        # 第二步：retrieval 模式禁止需要回答的指标。
        if experiment.mode == "retrieval":
            invalid = [
                item.name
                for item in definitions
                if item.requires_answer
            ]
            if invalid:
                raise ValueError(
                    "retrieval experiment cannot use answer metrics: "
                    + ", ".join(invalid)
                )

        # 第三步：预加载静态规范身份，确保即使运行时构建失败报告仍可追溯配置。
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

        # 第四步：为正式执行准备结果列表和带实际 seed 的不可变实验副本。
        sample_results: list[SampleMetricResult] = []
        runtime = None
        effective_experiment = experiment.model_copy(
            update={"seed": seed}
        )

        try:
            # 第五步：为本实验创建独立运行时。
            runtime = self.runtime_factory.build(
                project_root=project_root,
                experiment=effective_experiment,
            )

            # 第六步：可选 warmup 始终复用第一条样本，并通过私有参数标记非正式运行。
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

            # 第七步：按数据集顺序执行正式样本，并逐样本隔离异常。
            for sample in samples:
                sample_started = time.perf_counter()
                try:
                    output = runtime.run(
                        sample,
                        effective_experiment,
                    )
                    latency = (
                        time.perf_counter() - sample_started
                    ) * 1000.0

                    # 构造统一指标上下文并按配置顺序计算全部指标。
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

                    # 保存 top-k 多粒度标识及生成相关输出，供逐样本诊断。
                    retrieval_results = (
                        output.get("retrieval_results") or []
                    )
                    sample_results.append(
                        SampleMetricResult(
                            sample_id=sample.sample_id,
                            query=sample.query,
                            success=True,
                            run_id=output.get("run_id"),
                            latency_ms=latency,
                            metrics=metrics,
                            retrieved_result_count=len(
                                retrieval_results
                            ),
                            retrieved_ids=[
                                {
                                    "doc_id": item.get("doc_id"),
                                    "parent_chunk_id": item.get(
                                        "parent_chunk_id"
                                    ),
                                    "child_chunk_id": (
                                        item.get("child_chunk_id")
                                        or item.get("chunk_id")
                                    ),
                                }
                                for item in retrieval_results[
                                    : experiment.top_k
                                ]
                                if isinstance(item, dict)
                            ],
                            answer=output.get("answer"),
                            citations=(
                                output.get("citations") or []
                            ),
                            metadata={
                                "query_expansion": (
                                    output.get("query_expansion")
                                    or {}
                                ),
                                "retrieval_plan": output.get(
                                    "retrieval_plan"
                                ),
                                "evidence_quality": output.get(
                                    "evidence_quality"
                                ),
                                "self_rag": output.get("self_rag"),
                            },
                        )
                    )
                except Exception as exc:
                    # 单样本失败保留耗时和结构化错误，默认继续后续样本。
                    latency = (
                        time.perf_counter() - sample_started
                    ) * 1000.0
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
            # 运行时构建或 warmup 在首个正式结果前失败时，为全部样本生成失败记录。
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
            # 运行时存在时，用其实际元数据覆盖静态预估并释放全部资源。
            if runtime is not None:
                metadata = runtime.metadata()
                runtime.close()

        # 第八步：统计样本状态，并仅对成功样本的非空指标做宏平均。
        success_count = sum(
            1 for item in sample_results if item.success
        )
        failure_count = len(sample_results) - success_count
        aggregate = {
            name: _aggregate(
                [
                    item.metrics.get(name)
                    for item in sample_results
                    if item.success
                ]
            )
            for name in metric_names
        }

        # 第九步：根据是否存在成功与失败样本确定实验状态。
        status = (
            "failed"
            if success_count == 0
            else (
                "partial_success"
                if failure_count
                else "success"
            )
        )

        # 第十步：固化静态规范、数据、配置、环境和随机种子血缘。
        finished_at = _utc_now()
        return ExperimentReport(
            experiment_id=experiment.experiment_id,
            matrix_id=matrix.matrix_id,
            matrix_run_id=matrix_run_id,
            status=status,
            static_spec_id=str(metadata["static_spec_id"]),
            static_spec_version=str(
                metadata["static_spec_version"]
            ),
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
            duration_ms=(
                time.perf_counter() - started
            ) * 1000.0,
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
