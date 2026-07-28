"""定义可重复 RAG 策略实验的配置、样本和报告协议。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.eval_schema import RAGEvalSampleSchema


class StrictModel(BaseModel):
    """为策略评测协议提供严格类型和禁止未知字段的共同基类。"""

    # strict=True 阻止隐式类型转换，extra='forbid' 阻止拼写错误被静默保存。
    model_config = ConfigDict(extra="forbid", strict=True)


# 黄金样本的唯一事实源位于 schemas.eval_schema；这里只保留历史公开别名。
RAGEvalSample = RAGEvalSampleSchema


class GoldenDatasetManifest(StrictModel):
    """描述一版黄金数据集的身份和样本清单，不重复定义样本字段。"""

    schema_version: Literal["rag_golden_dataset_manifest_v1"] = (
        "rag_golden_dataset_manifest_v1"
    )

    # 业务数据集身份和版本，用于跨文件、报告和实验矩阵关联。
    golden_set_id: str
    golden_set_version: str

    # 明确本清单引用的样本协议版本，避免不同代样本被混用。
    sample_schema_version: Literal["rag_strategy_eval_sample_v1"] = (
        "rag_strategy_eval_sample_v1"
    )

    # 清单按稳定 sample_id 引用样本；source_hash 可绑定原始数据文件版本。
    sample_ids: list[str] = Field(default_factory=list)
    source_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentConfig(StrictModel):
    """定义实验矩阵中的一个静态检索策略变体。

    一条实验配置绑定一个静态检索规范，可选择 retrieval 或 rag_answer 模式，并
    允许传入受控运行参数。黄金样本字段和评测控制字段受保护，不能由 runtime_params
    覆盖，否则实验会篡改自己的评测输入。
    """

    schema_version: Literal["rag_experiment_config_v1"] = (
        "rag_experiment_config_v1"
    )

    # 实验身份和静态检索规范文件共同决定本实验的主要策略版本。
    experiment_id: str
    static_retrieval_spec_file: str

    # retrieval 只评检索；rag_answer 还允许生成回答并评估生成指标。
    mode: Literal["retrieval", "rag_answer"] = "retrieval"

    # top_k 约束最终评测截断；warmup_runs 用于排除首次加载抖动。
    top_k: int = Field(default=5, ge=1, le=100)
    warmup_runs: int = Field(default=0, ge=0, le=20)

    # metrics 为空时由矩阵级配置决定；seed 用于可重复随机过程。
    metrics: list[str] | None = None
    seed: int | None = None

    # runtime_params 下传检索运行时；generation_params 仅服务回答生成阶段。
    runtime_params: dict[str, Any] = Field(default_factory=dict)
    generation_params: dict[str, Any] = Field(default_factory=dict)

    # 仅允许覆盖少量动态检索计划参数，不能替换静态组件图。
    retrieval_plan_overrides: dict[str, Any] = Field(default_factory=dict)

    # 标签和备注只用于组织、筛选和报告，不参与策略执行。
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("experiment_id", "static_retrieval_spec_file")
    @classmethod
    def _experiment_fields_not_blank(cls, value: str) -> str:
        """规范化实验身份字段并拒绝空白值。"""

        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(
                "experiment_id/static_retrieval_spec_file cannot be blank"
            )
        return normalized

    @field_validator("runtime_params")
    @classmethod
    def _protect_eval_sample_contract(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """阻止运行参数覆盖黄金样本或评测控制字段。

        这些字段由 Runner 根据样本和实验协议生成。如果允许 runtime_params 覆盖，
        实验就可以修改查询、黄金标签、生成开关或完整记录开关，导致结果不可比较。
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

        # 返回副本，避免调用方继续修改校验后的原始字典引用。
        return dict(value)

    @field_validator("retrieval_plan_overrides")
    @classmethod
    def _validate_retrieval_plan_overrides(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """校验允许在实验级动态覆盖的检索计划字段。"""

        # 第一步：拒绝未声明字段，避免动态覆盖逐渐演化成第二套配置系统。
        allowed = {"query_transform_mode", "correction_budget"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(
                "unknown retrieval plan overrides: " + ", ".join(unknown)
            )

        # 第二步：查询变换模式只能选择运行时已实现的三种策略。
        mode = value.get("query_transform_mode")
        if mode is not None and mode not in {"identity", "multi_query", "hyde"}:
            raise ValueError("invalid query_transform_mode override")

        # 第三步：纠错预算限制在 0 到 3，避免评测配置制造失控循环。
        budget = value.get("correction_budget")
        if budget is not None and not 0 <= int(budget) <= 3:
            raise ValueError(
                "correction_budget override must be between 0 and 3"
            )

        return dict(value)


class ExperimentMatrixConfig(StrictModel):
    """定义一批共享数据集和指标口径的策略实验矩阵。"""

    schema_version: Literal["rag_experiment_matrix_v1"] = (
        "rag_experiment_matrix_v1"
    )

    # 矩阵和数据版本字段形成报告的主要可重复性身份。
    matrix_id: str
    dataset_path: str
    dataset_version: str
    eval_set_version: str
    index_version: str

    # output_dir 相对项目根目录解析；baseline 可选引用某个 experiment_id。
    output_dir: str = "data/eval_outputs/strategy_eval"
    baseline_experiment_id: str | None = None

    # 默认指标同时覆盖命中、排序、上下文词法覆盖和延迟。
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

    # 矩阵 seed 作为实验未单独指定 seed 时的回退值。
    seed: int = 42

    # fail_fast 决定单实验/单样本失败是否立即终止整个矩阵。
    fail_fast: bool = False

    # 至少包含一个实验；每个 experiment_id 必须唯一。
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
        """去除矩阵字符串字段首尾空白并拒绝空值。"""

        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("matrix string fields cannot be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_matrix(self) -> "ExperimentMatrixConfig":
        """校验实验集合、基线引用和指标集合的交叉约束。"""

        # 第一步：空矩阵没有执行意义。
        if not self.experiments:
            raise ValueError(
                "experiment matrix requires at least one experiment"
            )

        # 第二步：实验身份必须唯一，保证报告路径和基线选择无歧义。
        experiment_ids = [item.experiment_id for item in self.experiments]
        if len(experiment_ids) != len(set(experiment_ids)):
            raise ValueError("experiment_id values must be unique")

        # 第三步：基线必须引用本矩阵内部实验，不能依赖外部隐式报告。
        if (
            self.baseline_experiment_id
            and self.baseline_experiment_id not in experiment_ids
        ):
            raise ValueError(
                "baseline_experiment_id must reference an experiment"
            )

        # 第四步：至少一个指标用于形成可比较结果。
        if not self.metrics:
            raise ValueError(
                "experiment matrix requires at least one metric"
            )

        return self


class SampleMetricResult(StrictModel):
    """保存一个实验对一条黄金样本的执行与指标结果。"""

    # 样本身份、查询和成功标记用于失败索引和报告筛选。
    sample_id: str
    query: str
    success: bool

    # run_id 来自生产检索运行记录；latency_ms 记录端到端样本耗时。
    run_id: str | None = None
    latency_ms: float = 0.0

    # 指标允许 None，表示该模式或失败状态下无法计算，而不是零分。
    metrics: dict[str, float | None] = Field(default_factory=dict)

    # 召回数量和标准化多粒度标识支持逐样本检索诊断。
    retrieved_result_count: int = 0
    retrieved_ids: list[dict[str, str | None]] = Field(default_factory=list)

    # rag_answer 模式可附带回答和引用；retrieval 模式通常为空。
    answer: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)

    # error 保存结构化失败；metadata 保存运行时组件和评测上下文。
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentReport(StrictModel):
    """汇总一个静态检索策略在全部黄金样本上的结果。"""

    schema_version: Literal["rag_experiment_report_v1"] = (
        "rag_experiment_report_v1"
    )

    # 实验与所属矩阵身份。
    experiment_id: str
    matrix_id: str
    matrix_run_id: str
    status: Literal["success", "partial_success", "failed"]

    # 静态检索规范身份、路径、哈希和组件解析结果。
    static_spec_id: str
    static_spec_version: str
    static_retrieval_spec_file: str
    static_retrieval_spec_hash: str
    component_metadata: dict[str, Any]

    # 实验执行口径。
    mode: Literal["retrieval", "rag_answer"]
    top_k: int
    seed: int

    # 样本执行统计和宏平均指标。
    sample_count: int
    success_count: int
    failure_count: int
    aggregate_metrics: dict[str, float | None]
    samples: list[SampleMetricResult]

    # 数据、配置和时间血缘共同支持结果复现。
    dataset_hash: str
    matrix_config_hash: str
    started_at: str
    finished_at: str
    duration_ms: float
    reproducibility: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class ComparisonRow(StrictModel):
    """表示一个实验在矩阵对比表中的扁平记录。"""

    experiment_id: str
    static_spec_id: str
    status: str
    sample_count: int
    success_count: int
    failure_count: int

    # metrics 保存绝对指标；baseline_deltas 保存相对基线的差值。
    metrics: dict[str, float | None]
    baseline_deltas: dict[str, float | None] = Field(default_factory=dict)

    # 静态规范哈希和耗时用于排查配置漂移和性能差异。
    static_retrieval_spec_hash: str
    duration_ms: float


class MatrixReport(StrictModel):
    """汇总整个实验矩阵的对比结果和输出文件索引。"""

    schema_version: Literal["rag_experiment_matrix_report_v1"] = (
        "rag_experiment_matrix_report_v1"
    )

    # 矩阵运行身份和整体状态。
    matrix_id: str
    matrix_run_id: str
    status: Literal["success", "partial_success", "failed"]

    # 数据集、评测集和索引版本必须随报告固化。
    dataset_path: str
    dataset_version: str
    eval_set_version: str
    index_version: str
    dataset_hash: str
    matrix_config_hash: str

    # 基线和指标顺序决定对比表的解释方式。
    baseline_experiment_id: str | None = None
    metrics: list[str]

    # rows 与实验数对应；output_files 在持久化后填充实际产物路径。
    experiment_count: int
    rows: list[ComparisonRow]
    output_files: dict[str, str] = Field(default_factory=dict)

    # 矩阵级时间范围和耗时。
    started_at: str
    finished_at: str
    duration_ms: float
    notes: str | None = None
