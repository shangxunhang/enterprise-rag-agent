"""配置驱动的 RAG 策略实验、指标、运行时和报告公开入口。"""

from .baseline import BaselineManager
from .config import ExperimentConfigLoader
from .dataset import load_eval_samples
from .metrics import MetricRegistry, build_default_metric_registry
from .runner import StrategyEvalRunner
from .schemas import (
    ExperimentConfig,
    ExperimentMatrixConfig,
    ExperimentReport,
    GoldenDatasetManifest,
    MatrixReport,
    RAGEvalSample,
)

# 限定包级公开 API，内部报告写入器和运行时适配器由 Runner 统一协调。
__all__ = [
    "BaselineManager",
    "ExperimentConfigLoader",
    "ExperimentConfig",
    "ExperimentMatrixConfig",
    "ExperimentReport",
    "GoldenDatasetManifest",
    "MatrixReport",
    "MetricRegistry",
    "RAGEvalSample",
    "StrategyEvalRunner",
    "build_default_metric_registry",
    "load_eval_samples",
]
