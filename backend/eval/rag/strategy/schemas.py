# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：StrictModel、RAGEvalSample、ExperimentConfig、ExperimentMatrixConfig、SampleMetricResult、ExperimentReport、ComparisonRow、MatrixReport。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Schemas for reproducible online RAG strategy experiments."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# 阅读注释（类）：封装 strict 模型，集中封装相关状态、依赖和行为。
class StrictModel(BaseModel):
    """封装 strict 模型，集中封装相关状态、依赖和行为。"""
    model_config = ConfigDict(extra="forbid")


# 阅读注释（类）：封装 rageval sample，集中封装相关状态、依赖和行为。
class RAGEvalSample(StrictModel):
    """封装 rageval sample，集中封装相关状态、依赖和行为。"""
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

    # 阅读注释（函数）：处理 not blank 相关逻辑。
    @field_validator("sample_id", "query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """处理 not blank 相关逻辑。

        参数:
            value: value，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：strip, str, ValueError, field_validator。
        """
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("sample_id/query cannot be blank")
        return normalized

    # 阅读注释（函数）：处理 require evaluation target 相关逻辑。
    @model_validator(mode="after")
    def _require_evaluation_target(self) -> "RAGEvalSample":
        """处理 require evaluation target 相关逻辑。

        返回:
            'RAGEvalSample'

        阅读提示:
            主要直接调用：any, ValueError, model_validator。
        """
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


# 阅读注释（类）：封装 experiment 配置，集中封装相关状态、依赖和行为。
class ExperimentConfig(StrictModel):
    """封装 experiment 配置，集中封装相关状态、依赖和行为。"""
    schema_version: Literal["rag_experiment_config_v1"] = "rag_experiment_config_v1"
    experiment_id: str
    static_retrieval_spec_file: str
    mode: Literal["retrieval", "rag_answer"] = "retrieval"
    top_k: int = Field(default=5, ge=1, le=100)
    warmup_runs: int = Field(default=0, ge=0, le=20)
    metrics: list[str] | None = None
    seed: int | None = None
    runtime_params: dict[str, Any] = Field(default_factory=dict)
    generation_params: dict[str, Any] = Field(default_factory=dict)
    retrieval_plan_overrides: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    # 阅读注释（函数）：处理 experiment fields not blank 相关逻辑。
    @field_validator("experiment_id", "static_retrieval_spec_file")
    @classmethod
    def _experiment_fields_not_blank(cls, value: str) -> str:
        """处理 experiment fields not blank 相关逻辑。

        参数:
            value: value，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：strip, str, ValueError, field_validator。
        """
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(
                "experiment_id/static_retrieval_spec_file cannot be blank"
            )
        return normalized

    # 阅读注释（函数）：处理 protect 评测 sample contract 相关逻辑。
    @field_validator("runtime_params")
    @classmethod
    def _protect_eval_sample_contract(cls, value: dict[str, Any]) -> dict[str, Any]:
        """处理 protect 评测 sample contract 相关逻辑。

        参数:
            value: value，具体约束请结合类型标注和调用方确认。

        返回:
            dict[str, Any]

        阅读提示:
            主要直接调用：sorted, set, ValueError, join, dict, field_validator。
        """
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

    @field_validator("retrieval_plan_overrides")
    @classmethod
    def _validate_retrieval_plan_overrides(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {"query_transform_mode", "correction_budget"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(
                "unknown retrieval plan overrides: " + ", ".join(unknown)
            )
        mode = value.get("query_transform_mode")
        if mode is not None and mode not in {"identity", "multi_query", "hyde"}:
            raise ValueError("invalid query_transform_mode override")
        budget = value.get("correction_budget")
        if budget is not None and not 0 <= int(budget) <= 3:
            raise ValueError("correction_budget override must be between 0 and 3")
        return dict(value)


# 阅读注释（类）：封装 experiment matrix 配置，集中封装相关状态、依赖和行为。
class ExperimentMatrixConfig(StrictModel):
    """封装 experiment matrix 配置，集中封装相关状态、依赖和行为。"""
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

    # 阅读注释（函数）：处理 matrix fields not blank 相关逻辑。
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
        """处理 matrix fields not blank 相关逻辑。

        参数:
            value: value，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：strip, str, ValueError, field_validator。
        """
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("matrix string fields cannot be blank")
        return normalized

    # 阅读注释（函数）：校验 matrix。
    @model_validator(mode="after")
    def _validate_matrix(self) -> "ExperimentMatrixConfig":
        """校验 matrix。

        返回:
            'ExperimentMatrixConfig'

        阅读提示:
            主要直接调用：ValueError, len, set, model_validator。
        """
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


# 阅读注释（类）：封装 sample 指标 结果，集中封装相关状态、依赖和行为。
class SampleMetricResult(StrictModel):
    """封装 sample 指标 结果，集中封装相关状态、依赖和行为。"""
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


# 阅读注释（类）：封装 experiment report，集中封装相关状态、依赖和行为。
class ExperimentReport(StrictModel):
    """封装 experiment report，集中封装相关状态、依赖和行为。"""
    schema_version: Literal["rag_experiment_report_v1"] = "rag_experiment_report_v1"
    experiment_id: str
    matrix_id: str
    matrix_run_id: str
    status: Literal["success", "partial_success", "failed"]
    static_spec_id: str
    static_spec_version: str
    static_retrieval_spec_file: str
    static_retrieval_spec_hash: str
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


# 阅读注释（类）：封装 comparison row，集中封装相关状态、依赖和行为。
class ComparisonRow(StrictModel):
    """封装 comparison row，集中封装相关状态、依赖和行为。"""
    experiment_id: str
    static_spec_id: str
    status: str
    sample_count: int
    success_count: int
    failure_count: int
    metrics: dict[str, float | None]
    baseline_deltas: dict[str, float | None] = Field(default_factory=dict)
    static_retrieval_spec_hash: str
    duration_ms: float


# 阅读注释（类）：封装 matrix report，集中封装相关状态、依赖和行为。
class MatrixReport(StrictModel):
    """封装 matrix report，集中封装相关状态、依赖和行为。"""
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
