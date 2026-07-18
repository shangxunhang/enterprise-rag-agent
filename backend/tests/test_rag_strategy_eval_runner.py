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


def _sample() -> RAGEvalSample:
    return RAGEvalSample(
        sample_id="sample-1",
        query="enterprise rag",
        expected_parent_chunk_ids=["parent-b"],
        expected_keywords=["RAG"],
        answer_keywords=["evidence"],
    )


def _output() -> dict[str, Any]:
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


def test_default_metrics_are_registry_driven_and_correct() -> None:
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


def test_dataset_loader_supports_jsonl_and_rejects_duplicate_ids(tmp_path) -> None:
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


def test_experiment_loader_resolves_paths_from_project_root(tmp_path) -> None:
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
                        "pipeline_config_file": "backend/rag/profiles/hybrid_v1.yaml",
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


class _FakeRuntime:
    def __init__(self, experiment: ExperimentConfig) -> None:
        self.experiment = experiment
        self.closed = False

    def run(self, sample: RAGEvalSample, experiment: ExperimentConfig) -> dict[str, Any]:
        del sample, experiment
        return _output()

    def metadata(self) -> dict[str, Any]:
        return {
            "profile_id": Path(self.experiment.pipeline_config_file).stem,
            "profile_version": "v1",
            "pipeline_config_file": self.experiment.pipeline_config_file,
            "pipeline_config_hash": "a" * 64,
            "components": {"profile_id": Path(self.experiment.pipeline_config_file).stem},
        }

    def close(self) -> None:
        self.closed = True


class _FakeRuntimeFactory:
    def __init__(self) -> None:
        self.runtimes: list[_FakeRuntime] = []

    def build(self, *, project_root: Path, experiment: ExperimentConfig) -> _FakeRuntime:
        del project_root
        runtime = _FakeRuntime(experiment)
        self.runtimes.append(runtime)
        return runtime


def test_strategy_runner_writes_reproducible_comparison_outputs(tmp_path) -> None:
    root = tmp_path / "project"
    dataset_path = root / "data/eval/eval.jsonl"
    dataset_path.parent.mkdir(parents=True)
    dataset_path.write_text(
        json.dumps(_sample().model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )
    profile_dir = root / "backend/rag/profiles"
    profile_dir.mkdir(parents=True)
    for name in ("hybrid_v1.yaml", "rag_fusion_v1.yaml"):
        (profile_dir / name).write_text(
            (PROJECT_ROOT / "backend/rag/profiles" / name).read_text(encoding="utf-8"),
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
                pipeline_config_file="backend/rag/profiles/hybrid_v1.yaml",
            ),
            ExperimentConfig(
                experiment_id="fusion",
                pipeline_config_file="backend/rag/profiles/rag_fusion_v1.yaml",
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


def test_retrieval_mode_rejects_answer_only_metric(tmp_path) -> None:
    root = tmp_path / "project"
    dataset_path = root / "data/eval/eval.jsonl"
    dataset_path.parent.mkdir(parents=True)
    dataset_path.write_text(
        json.dumps(_sample().model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )
    profile_dir = root / "backend/rag/profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "hybrid_v1.yaml").write_text(
        (PROJECT_ROOT / "backend/rag/profiles/hybrid_v1.yaml").read_text(encoding="utf-8"),
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
                pipeline_config_file="backend/rag/profiles/hybrid_v1.yaml",
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


def test_example_matrix_references_registered_profiles() -> None:
    matrix, _, _ = ExperimentConfigLoader().load(
        PROJECT_ROOT / "backend/rag/experiments/online_strategy_matrix_v1.yaml",
        project_root=PROJECT_ROOT,
    )
    assert matrix.baseline_experiment_id == "baseline_hybrid_v1"
    assert {item.experiment_id for item in matrix.experiments} == {
        "baseline_hybrid_v1",
        "rag_fusion_v1",
        "hyde_v1",
        "c_rag_v1",
    }
    for experiment in matrix.experiments:
        assert (
            PROJECT_ROOT / experiment.pipeline_config_file
        ).is_file()


def test_validate_only_checks_dataset_metrics_profiles_and_registry(tmp_path) -> None:
    root = tmp_path / "project"
    dataset_path = root / "data/eval/eval.jsonl"
    dataset_path.parent.mkdir(parents=True)
    dataset_path.write_text(
        json.dumps(_sample().model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )
    target_profiles = root / "backend/rag/profiles"
    target_profiles.parent.mkdir(parents=True)
    import shutil

    shutil.copytree(PROJECT_ROOT / "backend/rag/profiles", target_profiles)
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
                        "pipeline_config_file": "backend/rag/profiles/hybrid_v1.yaml",
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
    assert validation["profile_catalog"]["profile_count"] == 11
    assert validation["profiles"][0]["profile_id"] == "hybrid_v1"


def test_runtime_params_cannot_override_query_or_gold() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="cannot override"):
        ExperimentConfig(
            experiment_id="bad",
            pipeline_config_file="backend/rag/profiles/hybrid_v1.yaml",
            runtime_params={"query": "contaminated"},
        )


def test_metric_gold_validation_fails_before_expensive_runtime() -> None:
    registry = build_default_metric_registry()
    sample = RAGEvalSample(
        sample_id="answer-only",
        query="q",
        answer_keywords=["a"],
    )

    with pytest.raises(ValueError, match="missing retrieval_ids"):
        registry.validate_samples(["mrr"], [sample])
