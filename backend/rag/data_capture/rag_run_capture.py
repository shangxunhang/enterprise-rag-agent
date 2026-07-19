# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：RagRunCapture。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（类）：封装 RAG run capture，集中封装相关状态、依赖和行为。
class RagRunCapture:
    """Capture one RAG pipeline run as one JSONL record."""

    # 阅读注释（函数）：初始化 RagRunCapture，保存运行所需的依赖、配置或状态。
    def __init__(self, output_path: str | Path = "data/runs/rag_runs.jsonl"):
        """初始化 RagRunCapture，保存运行所需的依赖、配置或状态。

        参数:
            output_path: 输出 路径，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：Path, JsonlWriter。
        """
        self.output_path = Path(output_path)
        self.writer = JsonlWriter(self.output_path)

    # 阅读注释（函数）：记录并沉淀 RagRunCapture。
    def capture(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """记录并沉淀 RagRunCapture。

        参数:
            record: 记录，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：self.writer.write, str, record.get。
        """
        path = self.writer.write(record)
        return {
            "saved": True,
            "output_path": str(path),
            "run_id": record.get("run_id"),
        }

    # 阅读注释（函数）：以可调用对象形式执行 RagRunCapture 的核心逻辑。
    def __call__(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """以可调用对象形式执行 RagRunCapture 的核心逻辑。

        参数:
            record: 记录，具体约束请结合类型标注和调用方确认。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：self.capture。
        """
        return self.capture(record)
