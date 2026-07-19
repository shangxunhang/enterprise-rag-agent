# =============================================================================
# 中文阅读说明：自动化测试模块，用于验证主链、边界条件和回归行为。
# 主要定义：_sample、_output、test_default_metrics_are_registry_driven_and_correct、test_dataset_loader_supports_jsonl_and_rejects_duplicate_ids、test_experiment_loader_resolves_paths_from_project_root、_FakeRuntime、_FakeRuntimeFactory、test_strategy_runner_writes_reproducible_comparison_outputs、test_retrieval_mode_rejects_answer_only_metric、test_example_matrix_references_registered_profiles等。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from eval.rag.strategy.config import ExperimentConfigLoader
from eval.rag.strategy.dataset import load_eval_samples
from eval.rag.strategy.metrics import MetricContext, build_default_metric_registry
from eval.rag.strategy.runner import StrategyEvalRunner
from eval.rag.strategy.schemas import (
    ExperimentConfig,
    ExperimentMatrixConfig,
    RAGEvalSample,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 阅读注释（函数）：处理 sample 相关逻辑。
def _sample() -> RAGEvalSample:
    """处理 sample 相关逻辑。

    返回:
        RAGEvalSample

    阅读提示:
        主要直接调用：RAGEvalSample。
    """
    return RAGEvalSample(
        sample_id="sample-1",
        query="enterprise rag",
        expected_parent_chunk_ids=["parent-b"],
        expected_keywords=["RAG"],
        answer_keywords=["evidence"],
    )


# 阅读注释（函数）：处理 输出 相关逻辑。
def _output() -> dict[str, Any]:
    """处理 输出 相关逻辑。

    返回:
        dict[str, Any]
    """
    return {
        "run_id": "run-1",
        "retrieval_results": [
            {
                "rank": 1,
                "doc_id": "doc-a",
                "parent_chunk_id": "parent-a",
                "child_chunk_id": "child-a",
                "text": "unrelated",
            },
            {
                "rank": 2,
                "doc_id": "doc-b",
                "parent_chunk_id": "parent-b",
                "child_chunk_id": "child-b",
                "text": "RAG evidence",
            },
        ],
        "answer": "grounded evidence",
        "citations": [{"citation_id": "C1"}],
    }


# 阅读注释（函数）：处理 测试 default 指标 are 注册表 driven and correct 相关逻辑。
def test_default_metrics_are_registry_driven_and_correct() -> None:
    """处理 测试 default 指标 are 注册表 driven and correct 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_default_metric_registry, MetricContext, _sample, _output, item.compute, registry.require, pytest.approx。
    """
    registry = build_default_metric_registry()
    context = MetricContext(
        sample=_sample(),
        output=_output(),
        latency_ms=12.5,
        top_k=2,
    )
    values = {
        item.name: item.compute(context)
        for item in registry.require(
            [
                "hit_at_k",
                "recall_at_k",
                "mrr",
                "ndcg_at_k",
                "context_keyword_hit",
                "answer_keyword_hit",
                "latency_ms",
            ]
        )
    }

    assert values["hit_at_k"] == 1.0
    assert values["recall_at_k"] == 1.0
    assert values["mrr"] == 0.5
    assert values["ndcg_at_k"] == pytest.approx(1 / 1.584962500721156)
    assert values["context_keyword_hit"] == 1.0
    assert values["answer_keyword_hit"] == 1.0
    assert values["latency_ms"] == 12.5


# 阅读注释（函数）：处理 测试 数据集 loader supports jsonl and rejects duplicate 标识集合 相关逻辑。
def test_dataset_loader_supports_jsonl_and_rejects_duplicate_ids(tmp_path) -> None:
    """处理 测试 数据集 loader supports jsonl and rejects duplicate 标识集合 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：model_dump, _sample, path.write_text, json.dumps, load_eval_samples, pytest.raises。
    """
    path = tmp_path / "eval.jsonl"
    payload = _sample().model_dump(mode="json")
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert load_eval_samples(path)[0].sample_id == "sample-1"

    path.write_text(
        json.dumps(payload) + "\n" + json.dumps(payload) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate sample_id"):
        load_eval_samples(path)


# 阅读注释（函数）：处理 测试 experiment loader resolves paths from 项目 root 相关逻辑。
def test_experiment_loader_resolves_paths_from_project_root(tmp_path) -> None:
    """处理 测试 experiment loader resolves paths from 项目 root 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：config_path.parent.mkdir, config_path.write_text, yaml.safe_dump, load, ExperimentConfigLoader, config_path.resolve, len。
    """
    root = tmp_path / "project"
    config_path = root / "backend/rag/experiments/matrix.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "rag_experiment_matrix_v1",
                "matrix_id": "m1",
                "dataset_path": "data/eval/eval.jsonl",
                "dataset_version": "d1",
                "eval_set_version": "e1",
                "index_version": "i1",
                "experiments": [
                    {
                        "schema_version": "rag_experiment_config_v1",
                        "experiment_id": "exp1",
                        "static_retrieval_spec_file": (
                            "backend/rag/config/static_retrieval_v1.yaml"
                        ),
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    matrix, resolved, config_hash = ExperimentConfigLoader().load(
        "backend/rag/experiments/matrix.yaml",
        project_root=root,
    )

    assert matrix.matrix_id == "m1"
    assert resolved == config_path.resolve()
    assert len(config_hash) == 64


# 阅读注释（类）：封装 fake 运行时，负责驱动实际运行流程并维护执行状态。
class _FakeRuntime:
    """封装 fake 运行时，负责驱动实际运行流程并维护执行状态。"""
    # 阅读注释（函数）：初始化 _FakeRuntime，保存运行所需的依赖、配置或状态。
    def __init__(self, experiment: ExperimentConfig) -> None:
        """初始化 _FakeRuntime，保存运行所需的依赖、配置或状态。

        参数:
            experiment: experiment，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.experiment = experiment
        self.closed = False

    # 阅读注释（函数）：执行 _FakeRuntime 的主流程。
    def run(self, sample: RAGEvalSample, experiment: ExperimentConfig) -> dict[str, Any]:
        """执行 _FakeRuntime 的主流程。

        参数:
            sample: sample，具体约束请结合类型标注和调用方确认。
            experiment: experiment，具体约束请结合类型标注和调用方确认。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：_output。
        """
        del sample, experiment
        return _output()

    # 阅读注释（函数）：处理 元数据 相关逻辑。
    def metadata(self) -> dict[str, Any]:
        """处理 元数据 相关逻辑。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：Path。
        """
        return {
            "static_spec_id": "enterprise_parent_child_hybrid_v1",
            "static_spec_version": "v1",
            "static_retrieval_spec_file": (
                self.experiment.static_retrieval_spec_file
            ),
            "static_retrieval_spec_hash": "a" * 64,
            "components": {"static_spec_id": "enterprise_parent_child_hybrid_v1"},
        }

    # 阅读注释（函数）：释放 _FakeRuntime 持有的资源。
    def close(self) -> None:
        """释放 _FakeRuntime 持有的资源。

        返回:
            None
        """
        self.closed = True


# 阅读注释（类）：封装 fake 运行时 工厂，负责根据配置装配并返回运行实例。
class _FakeRuntimeFactory:
    """封装 fake 运行时 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：初始化 _FakeRuntimeFactory，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 _FakeRuntimeFactory，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self.runtimes: list[_FakeRuntime] = []

    # 阅读注释（函数）：构建 _FakeRuntimeFactory。
    def build(self, *, project_root: Path, experiment: ExperimentConfig) -> _FakeRuntime:
        """构建 _FakeRuntimeFactory。

        参数:
            project_root: 项目 root，具体约束请结合类型标注和调用方确认。
            experiment: experiment，具体约束请结合类型标注和调用方确认。

        返回:
            _FakeRuntime

        阅读提示:
            主要直接调用：_FakeRuntime, self.runtimes.append。
        """
        del project_root
        runtime = _FakeRuntime(experiment)
        self.runtimes.append(runtime)
        return runtime


# 阅读注释（函数）：处理 测试 strategy runner writes reproducible comparison outputs 相关逻辑。
def test_strategy_runner_writes_reproducible_comparison_outputs(tmp_path) -> None:
    """处理 测试 strategy runner writes reproducible comparison outputs 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：dataset_path.parent.mkdir, dataset_path.write_text, json.dumps, model_dump, _sample, profile_dir.mkdir, write_text, read_text。
    """
    root = tmp_path / "project"
    dataset_path = root / "data/eval/eval.jsonl"
    dataset_path.parent.mkdir(parents=True)
    dataset_path.write_text(
        json.dumps(_sample().model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )
    spec_path = root / "backend/rag/config/static_retrieval_v1.yaml"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(
        (PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    matrix = ExperimentMatrixConfig(
        matrix_id="matrix-1",
        dataset_path="data/eval/eval.jsonl",
        dataset_version="dataset-v1",
        eval_set_version="eval-v1",
        index_version="index-v1",
        output_dir="outputs",
        baseline_experiment_id="baseline",
        metrics=["hit_at_k", "mrr", "latency_ms"],
        experiments=[
            ExperimentConfig(
                experiment_id="baseline",
                static_retrieval_spec_file=(
                    "backend/rag/config/static_retrieval_v1.yaml"
                ),
            ),
            ExperimentConfig(
                experiment_id="fusion",
                static_retrieval_spec_file=(
                    "backend/rag/config/static_retrieval_v1.yaml"
                ),
                retrieval_plan_overrides={"query_transform_mode": "multi_query"},
            ),
        ],
    )
    factory = _FakeRuntimeFactory()
    report = StrategyEvalRunner(runtime_factory=factory).run(
        matrix,
        project_root=root,
        matrix_config_path=root / "matrix.yaml",
        matrix_config_hash="b" * 64,
    )

    assert report.status == "success"
    assert report.experiment_count == 2
    assert report.rows[0].metrics["hit_at_k"] == 1.0
    assert report.rows[1].baseline_deltas["hit_at_k"] == 0.0
    assert all(runtime.closed for runtime in factory.runtimes)
    output_root = root / "outputs/matrix-1" / report.matrix_run_id
    assert (output_root / "matrix_report.json").is_file()
    assert (output_root / "comparison.csv").is_file()
    assert (output_root / "comparison.md").is_file()
    assert (output_root / "experiments/baseline/report.json").is_file()
    assert (output_root / "experiments/baseline/samples.jsonl").is_file()


# 阅读注释（函数）：处理 测试 检索 mode rejects answer only 指标 相关逻辑。
def test_retrieval_mode_rejects_answer_only_metric(tmp_path) -> None:
    """处理 测试 检索 mode rejects answer only 指标 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：dataset_path.parent.mkdir, dataset_path.write_text, json.dumps, model_dump, _sample, profile_dir.mkdir, write_text, read_text。
    """
    root = tmp_path / "project"
    dataset_path = root / "data/eval/eval.jsonl"
    dataset_path.parent.mkdir(parents=True)
    dataset_path.write_text(
        json.dumps(_sample().model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )
    spec_path = root / "backend/rag/config/static_retrieval_v1.yaml"
    spec_path.parent.mkdir(parents=True)
    spec_path.write_text(
        (PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    matrix = ExperimentMatrixConfig(
        matrix_id="matrix-answer-invalid",
        dataset_path="data/eval/eval.jsonl",
        dataset_version="dataset-v1",
        eval_set_version="eval-v1",
        index_version="index-v1",
        output_dir="outputs",
        metrics=["answer_keyword_hit"],
        experiments=[
            ExperimentConfig(
                experiment_id="baseline",
                static_retrieval_spec_file=(
                    "backend/rag/config/static_retrieval_v1.yaml"
                ),
                mode="retrieval",
            )
        ],
    )

    with pytest.raises(ValueError, match="cannot use answer metrics"):
        StrategyEvalRunner(runtime_factory=_FakeRuntimeFactory()).run(
            matrix,
            project_root=root,
            matrix_config_path=root / "matrix.yaml",
            matrix_config_hash="c" * 64,
        )


# 阅读注释（函数）：处理 测试 example matrix references registered profiles 相关逻辑。
def test_example_matrix_references_one_static_spec() -> None:
    """处理 测试 example matrix references registered profiles 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：load, ExperimentConfigLoader, is_file。
    """
    matrix, _, _ = ExperimentConfigLoader().load(
        PROJECT_ROOT / "backend/rag/experiments/online_strategy_matrix_v1.yaml",
        project_root=PROJECT_ROOT,
    )
    assert matrix.baseline_experiment_id == "identity"
    assert {item.experiment_id for item in matrix.experiments} == {
        "identity",
        "multi_query",
        "hyde",
        "adaptive_correction",
    }
    for experiment in matrix.experiments:
        assert (
            PROJECT_ROOT / experiment.static_retrieval_spec_file
        ).is_file()


# 阅读注释（函数）：处理 测试 validate only checks 数据集 指标 profiles and 注册表 相关逻辑。
def test_validate_only_checks_dataset_metrics_static_spec_and_registry(tmp_path) -> None:
    """处理 测试 validate only checks 数据集 指标 profiles and 注册表 相关逻辑。

    参数:
        tmp_path: tmp 路径，具体约束请结合类型标注和调用方确认。

    返回:
        None

    阅读提示:
        主要直接调用：dataset_path.parent.mkdir, dataset_path.write_text, json.dumps, model_dump, _sample, target_profiles.parent.mkdir, shutil.copytree, config_path.parent.mkdir。
    """
    root = tmp_path / "project"
    dataset_path = root / "data/eval/eval.jsonl"
    dataset_path.parent.mkdir(parents=True)
    dataset_path.write_text(
        json.dumps(_sample().model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )
    target_spec = root / "backend/rag/config/static_retrieval_v1.yaml"
    target_spec.parent.mkdir(parents=True)
    target_spec.write_text(
        (PROJECT_ROOT / "backend/rag/config/static_retrieval_v1.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    config_path = root / "backend/rag/experiments/matrix.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "rag_experiment_matrix_v1",
                "matrix_id": "matrix-validate",
                "dataset_path": "data/eval/eval.jsonl",
                "dataset_version": "dataset-v1",
                "eval_set_version": "eval-v1",
                "index_version": "index-v1",
                "experiments": [
                    {
                        "schema_version": "rag_experiment_config_v1",
                        "experiment_id": "baseline",
                        "static_retrieval_spec_file": (
                            "backend/rag/config/static_retrieval_v1.yaml"
                        ),
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    validation = StrategyEvalRunner().validate_from_file(
        config_path,
        project_root=root,
    )

    assert validation["status"] == "success"
    assert validation["sample_count"] == 1
    assert len(validation["static_specs"]) == 1
    assert validation["static_specs"][0]["static_spec_id"] == (
        "enterprise_parent_child_hybrid_v1"
    )


# 阅读注释（函数）：处理 测试 运行时 params cannot override 查询 or gold 相关逻辑。
def test_runtime_params_cannot_override_query_or_gold() -> None:
    """处理 测试 运行时 params cannot override 查询 or gold 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：pytest.raises, ExperimentConfig。
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="cannot override"):
        ExperimentConfig(
            experiment_id="bad",
            static_retrieval_spec_file=(
                "backend/rag/config/static_retrieval_v1.yaml"
            ),
            runtime_params={"query": "contaminated"},
        )


# 阅读注释（函数）：处理 测试 指标 gold validation fails before expensive 运行时 相关逻辑。
def test_metric_gold_validation_fails_before_expensive_runtime() -> None:
    """处理 测试 指标 gold validation fails before expensive 运行时 相关逻辑。

    返回:
        None

    阅读提示:
        主要直接调用：build_default_metric_registry, RAGEvalSample, pytest.raises, registry.validate_samples。
    """
    registry = build_default_metric_registry()
    sample = RAGEvalSample(
        sample_id="answer-only",
        query="q",
        answer_keywords=["a"],
    )

    with pytest.raises(ValueError, match="missing retrieval_ids"):
        registry.validate_samples(["mrr"], [sample])
