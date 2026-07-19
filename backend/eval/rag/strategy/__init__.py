# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：以常量、Schema 导入或注册配置为主。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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
