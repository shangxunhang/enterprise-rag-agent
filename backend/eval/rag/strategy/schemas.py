"""Schemas for reproducible online RAG strategy experiments."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RAGEvalSample(StrictModel):
    schema_version: Literal["rag_strategy_eval_sample_v1"] = "rag_strategy_eval_sample_v1"
    sample_id: str
    query: str
    expected_doc_ids: list[str] = Field(default_factory=list)
    expected_parent_chunk_ids: list[str] = Field(default_factory=list)
    expected_child_chunk_ids: list[str] = Field(default_factory=list)
    expected_keywords: list[str] = Field(default_factory=list)
    answer_keywords: list[str] = Field(default_factory=list)
    filter_expr: str | None = None
    keyword_doc_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sample_id", "query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("sample_id/query cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _require_evaluation_target(self) -> "RAGEvalSample":
        if not any(
            (
                self.expected_doc_ids,
                self.expected_parent_chunk_ids,
                self.expected_child_chunk_ids,
                self.expected_keywords,
                self.answer_keywords,
            )
        ):
            raise ValueError(
                "eval sample must provide at least one expected id, keyword, "
                "or answer keyword"
            )
        return self


class ExperimentConfig(StrictModel):
    schema_version: Literal["rag_experiment_config_v1"] = "rag_experiment_config_v1"
    experiment_id: str
    pipeline_config_file: str
    mode: Literal["retrieval", "rag_answer"] = "retrieval"
    top_k: int = Field(default=5, ge=1, le=100)
    warmup_runs: int = Field(default=0, ge=0, le=20)
    metrics: list[str] | None = None
    seed: int | None = None
    runtime_params: dict[str, Any] = Field(default_factory=dict)
    generation_params: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("experiment_id", "pipeline_config_file")
    @classmethod
    def _experiment_fields_not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("experiment_id/pipeline_config_file cannot be blank")
        return normalized

    @field_validator("runtime_params")
    @classmethod
    def _protect_eval_sample_contract(cls, value: dict[str, Any]) -> dict[str, Any]:
        protected = {
            "query",
            "expected_doc_ids",
            "expected_parent_chunk_ids",
            "expected_child_chunk_ids",
            "expected_keywords",
            "answer_keywords",
            "generate_answer",
            "generation_params",
            "return_full_record",
            "extra_metadata",
        }
        conflicts = sorted(protected & set(value))
        if conflicts:
            raise ValueError(
                "runtime_params cannot override eval sample/control fields: "
                + ", ".join(conflicts)
            )
        return dict(value)


class ExperimentMatrixConfig(StrictModel):
    schema_version: Literal["rag_experiment_matrix_v1"] = "rag_experiment_matrix_v1"
    matrix_id: str
    dataset_path: str
    dataset_version: str
    eval_set_version: str
    index_version: str
    output_dir: str = "data/eval_outputs/strategy_eval"
    baseline_experiment_id: str | None = None
    metrics: list[str] = Field(
        default_factory=lambda: [
            "hit_at_k",
            "recall_at_k",
            "mrr",
            "ndcg_at_k",
            "context_keyword_hit",
            "latency_ms",
        ]
    )
    seed: int = 42
    fail_fast: bool = False
    experiments: list[ExperimentConfig]
    notes: str | None = None

    @field_validator(
        "matrix_id",
        "dataset_path",
        "dataset_version",
        "eval_set_version",
        "index_version",
        "output_dir",
    )
    @classmethod
    def _matrix_fields_not_blank(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("matrix string fields cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_matrix(self) -> "ExperimentMatrixConfig":
        if not self.experiments:
            raise ValueError("experiment matrix requires at least one experiment")
        ids = [item.experiment_id for item in self.experiments]
        if len(ids) != len(set(ids)):
            raise ValueError("experiment_id values must be unique")
        if self.baseline_experiment_id and self.baseline_experiment_id not in ids:
            raise ValueError("baseline_experiment_id must reference an experiment")
        if not self.metrics:
            raise ValueError("experiment matrix requires at least one metric")
        return self


class SampleMetricResult(StrictModel):
    sample_id: str
    query: str
    success: bool
    run_id: str | None = None
    latency_ms: float = 0.0
    metrics: dict[str, float | None] = Field(default_factory=dict)
    retrieved_result_count: int = 0
    retrieved_ids: list[dict[str, str | None]] = Field(default_factory=list)
    answer: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentReport(StrictModel):
    schema_version: Literal["rag_experiment_report_v1"] = "rag_experiment_report_v1"
    experiment_id: str
    matrix_id: str
    matrix_run_id: str
    status: Literal["success", "partial_success", "failed"]
    profile_id: str
    profile_version: str
    pipeline_config_file: str
    pipeline_config_hash: str
    component_metadata: dict[str, Any]
    mode: Literal["retrieval", "rag_answer"]
    top_k: int
    seed: int
    sample_count: int
    success_count: int
    failure_count: int
    aggregate_metrics: dict[str, float | None]
    samples: list[SampleMetricResult]
    dataset_hash: str
    matrix_config_hash: str
    started_at: str
    finished_at: str
    duration_ms: float
    reproducibility: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class ComparisonRow(StrictModel):
    experiment_id: str
    profile_id: str
    status: str
    sample_count: int
    success_count: int
    failure_count: int
    metrics: dict[str, float | None]
    baseline_deltas: dict[str, float | None] = Field(default_factory=dict)
    pipeline_config_hash: str
    duration_ms: float


class MatrixReport(StrictModel):
    schema_version: Literal["rag_experiment_matrix_report_v1"] = (
        "rag_experiment_matrix_report_v1"
    )
    matrix_id: str
    matrix_run_id: str
    status: Literal["success", "partial_success", "failed"]
    dataset_path: str
    dataset_version: str
    eval_set_version: str
    index_version: str
    dataset_hash: str
    matrix_config_hash: str
    baseline_experiment_id: str | None = None
    metrics: list[str]
    experiment_count: int
    rows: list[ComparisonRow]
    output_files: dict[str, str] = Field(default_factory=dict)
    started_at: str
    finished_at: str
    duration_ms: float
    notes: str | None = None
