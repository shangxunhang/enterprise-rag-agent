"""Config-driven RAG strategy evaluation."""

from .baseline import BaselineManager
from .config import ExperimentConfigLoader
from .dataset import load_eval_samples
from .metrics import MetricRegistry, build_default_metric_registry
from .runner import StrategyEvalRunner
from .schemas import (
    ExperimentConfig,
    ExperimentMatrixConfig,
    ExperimentReport,
    MatrixReport,
    RAGEvalSample,
)

__all__ = [
    "BaselineManager",
    "ExperimentConfigLoader",
    "ExperimentConfig",
    "ExperimentMatrixConfig",
    "ExperimentReport",
    "MatrixReport",
    "MetricRegistry",
    "RAGEvalSample",
    "StrategyEvalRunner",
    "build_default_metric_registry",
    "load_eval_samples",
]
