# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：to_jsonable、JsonlWriter。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
rag_template/data_capture/jsonl_writer.py
========================================

Small JSONL writer used by P4-lite DataCapture.

职责边界：
- 只负责把 dict 追加写入 JSONL。
- 不做业务字段拼装。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict


# 阅读注释（函数）：把 jsonl writer 转换为 jsonable。
def to_jsonable(value: Any) -> Any:
    """Convert common Python objects to JSON-serializable values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(x) for x in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return to_jsonable(value.to_dict())
    return str(value)


# 阅读注释（类）：封装 jsonl writer，集中封装相关状态、依赖和行为。
class JsonlWriter:
    """Append-only JSONL writer."""

    # 阅读注释（函数）：初始化 JsonlWriter，保存运行所需的依赖、配置或状态。
    def __init__(self, path: str | Path):
        """初始化 JsonlWriter，保存运行所需的依赖、配置或状态。

        参数:
            path: 目标文件或目录路径。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。

        阅读提示:
            主要直接调用：Path, self.path.parent.mkdir。
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # 阅读注释（函数）：写入 JsonlWriter。
    def write(self, record: Dict[str, Any]) -> Path:
        """写入 JsonlWriter。

        参数:
            record: 记录，具体约束请结合类型标注和调用方确认。

        返回:
            Path

        阅读提示:
            主要直接调用：to_jsonable, self.path.open, f.write, json.dumps。
        """
        jsonable = to_jsonable(record)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(jsonable, ensure_ascii=False) + "\n")
        return self.path
