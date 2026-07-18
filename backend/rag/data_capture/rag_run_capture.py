# -*- coding: utf-8 -*-
"""
rag_template/data_capture/rag_run_capture.py
===========================================

P4-lite RAG run capture.

It writes a full single-query RAG trace into JSONL for later:
- debugging
- eval replay
- SFT candidate construction
- DPO / preference candidate construction
- reranker dataset construction

职责边界：
- 只保存运行轨迹，不筛选训练样本。
- 后续 DatasetBuilder 再从 runs 中构造 SFT/DPO/RAG eval 数据。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from rag.data_capture.jsonl_writer import JsonlWriter


class RagRunCapture:
    """Capture one RAG pipeline run as one JSONL record."""

    def __init__(self, output_path: str | Path = "data/runs/rag_runs.jsonl"):
        self.output_path = Path(output_path)
        self.writer = JsonlWriter(self.output_path)

    def capture(self, record: Dict[str, Any]) -> Dict[str, Any]:
        path = self.writer.write(record)
        return {
            "saved": True,
            "output_path": str(path),
            "run_id": record.get("run_id"),
        }

    def __call__(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return self.capture(record)
