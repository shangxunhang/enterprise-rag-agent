"""根据应用配置装配具体轨迹与数据采集适配器。

工厂只决定是否创建 JSONL 实现以及输出目录，不参与事件构造或样本治理。关闭某项能力
时返回 ``None``，上层服务通过可选端口继续运行而不产生对应持久化副作用。
"""

from __future__ import annotations

from pathlib import Path

from core.config import AppSettings
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from data_capture.run_trace_recorder import JsonlRunTraceRecorder
from observability.pre_write_trace import PreWriteTraceEmitter
from observability.sqlite_trace_sink import SQLiteTraceSink


class ObservabilityFactory:
    """构造当前运行使用的 Trace 和数据采集输出适配器。"""

    def build(
        self,
        settings: AppSettings,
        runs_dir: Path,
        captures_dir: Path,
    ):
        """按功能开关创建 JSONL 轨迹与采集器。

        Args:
            settings: 当前不可变应用配置快照。
            runs_dir: 运行轨迹与运行级聚合产物目录。
            captures_dir: 原始交互、SFT 候选和评测样本采集目录。

        Returns:
            ``(trace_recorder, capture_recorder)`` 二元组；被配置关闭的能力对应 ``None``。
        """

        # 第一步：启用 Trace 时创建运行级 JSONL 记录器，否则显式关闭该副作用。
        trace = (
            JsonlRunTraceRecorder(output_dir=runs_dir)
            if settings.trace_enabled
            else None
        )

        # 第二步：启用数据采集时创建采集记录器，否则不生成训练/评测候选文件。
        capture = (
            JsonlDataCaptureRecorder(output_dir=captures_dir)
            if settings.data_capture_enabled
            else None
        )

        # 第三步：把两个可选端口交给上层装配，不在工厂内缓存或管理运行生命周期。
        return trace, capture

    def build_required_pre_write_trace(
        self,
        settings: AppSettings,
        runs_dir: Path,
    ) -> tuple[SQLiteTraceSink, PreWriteTraceEmitter]:
        """Build the mandatory Target Gate control Trace on the effective runs dir.

        This path is deliberately independent from the optional JSONL diagnostic
        recorder above. ``TRACE_ENABLED=false`` can suppress optional diagnostics,
        but required Gate control events are still attempted by PreWriteTraceEmitter.
        """

        sink = SQLiteTraceSink(runs_dir / "run_trace.sqlite3")
        emitter = PreWriteTraceEmitter(
            sink,
            trace_enabled=settings.trace_enabled,
        )
        return sink, emitter
