# =============================================================================
# 中文阅读说明：依赖装配与运行时构建模块。
# 主要定义：ObservabilityFactory。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Build concrete trace and capture adapters."""

from __future__ import annotations

from pathlib import Path

from core.config import AppSettings
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from data_capture.run_trace_recorder import JsonlRunTraceRecorder


# 阅读注释（类）：封装 observability 工厂，负责根据配置装配并返回运行实例。
class ObservabilityFactory:
    """封装 observability 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：构建 ObservabilityFactory。
    def build(self, settings: AppSettings, runs_dir: Path, captures_dir: Path):
        """构建 ObservabilityFactory。

        参数:
            settings: settings，具体约束请结合类型标注和调用方确认。
            runs_dir: runs dir，具体约束请结合类型标注和调用方确认。
            captures_dir: captures dir，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：JsonlRunTraceRecorder, JsonlDataCaptureRecorder。
        """
        trace = (
            JsonlRunTraceRecorder(output_dir=runs_dir)
            if settings.trace_enabled
            else None
        )
        capture = (
            JsonlDataCaptureRecorder(output_dir=captures_dir)
            if settings.data_capture_enabled
            else None
        )
        return trace, capture
