"""Build concrete trace and capture adapters."""

from __future__ import annotations

from pathlib import Path

from core.config import AppSettings
from data_capture.data_capture_recorder import JsonlDataCaptureRecorder
from data_capture.run_trace_recorder import JsonlRunTraceRecorder


class ObservabilityFactory:
    def build(self, settings: AppSettings, runs_dir: Path, captures_dir: Path):
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
