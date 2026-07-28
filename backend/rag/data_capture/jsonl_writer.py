# -*- coding: utf-8 -*-
"""为 RAG 运行采集提供崩溃安全的追加式 JSONL 写入器。

本模块只负责把业务层已组装好的记录转换为 JSON 兼容值并追加落盘，不解释字段
语义。底层使用 ``CrashSafeJsonlRecordSink``，同时支持普通追加和按稳定身份幂等
追加。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict

from core.persistence.crash_safe_jsonl import CrashSafeJsonlRecordSink


def to_jsonable(value: Any) -> Any:
    """递归把常见 Python 对象转换为 JSON 可序列化结构。

    支持标量、日期时间、Path、dataclass、字典、序列和提供 ``to_dict`` 的对象。
    未知对象最终转为字符串，保证采集不会因调试扩展字段阻塞主流程。
    """

    # 原生 JSON 标量保持不变。
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    # 日期和时间使用 ISO 格式，保留时区信息（如对象本身携带）。
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    # 文件系统路径转换为跨序列化边界的字符串。
    if isinstance(value, Path):
        return str(value)

    # dataclass 先递归展开，再处理内部日期、Path 等非 JSON 值。
    if is_dataclass(value):
        return to_jsonable(asdict(value))

    # 字典键统一转字符串，值递归转换。
    if isinstance(value, dict):
        return {
            str(key): to_jsonable(item)
            for key, item in value.items()
        }

    # 列表、元组和集合统一转列表；集合顺序由调用方输入决定，不保证跨运行稳定。
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]

    # 兼容项目内提供轻量序列化接口的对象。
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return to_jsonable(value.to_dict())

    # 最后回退字符串，避免未知调试对象导致采集失败。
    return str(value)


class JsonlWriter:
    """封装一个追加式 JSONL 目标文件。

    Attributes:
        path: 实际目标路径。
        _sink: 提供崩溃安全追加和幂等校验的底层记录 Sink。
    """

    def __init__(self, path: str | Path) -> None:
        """创建父目录并初始化崩溃安全 Sink。"""

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sink = CrashSafeJsonlRecordSink(self.path)

    def write(self, record: Dict[str, Any]) -> Path:
        """无幂等检查地追加一条 JSONL 记录。

        Args:
            record: 已由业务层组装的记录字典。

        Returns:
            目标文件路径。
        """

        # 第一步：递归转换日期、Path 和 dataclass 等值。
        jsonable = to_jsonable(record)

        # 第二步：使用紧凑 JSON 追加；Sink 负责崩溃安全写入。
        self._sink.append_record(
            json.dumps(
                jsonable,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

        return self.path

    def write_once(
        self,
        record: Dict[str, Any],
        *,
        id_field: str,
        semantic_projection: Callable[[dict[str, Any]], Any] | None = None,
        semantic_fingerprint: str | None = None,
    ) -> Path:
        """按记录中的稳定身份幂等追加。

        Args:
            record: 待写入记录。
            id_field: 作为幂等身份的字符串字段名。
            semantic_projection: 可选业务语义投影，用于区分同 ID 同义重试和冲突写入。
            semantic_fingerprint: 可选预计算语义哈希。

        Raises:
            ValueError: id_field 缺失、非字符串或为空时抛出。
        """

        # 第一步：先完成 JSON 兼容转换，幂等字段以最终落盘结构为准。
        jsonable = to_jsonable(record)
        identity = jsonable.get(id_field)
        if not isinstance(identity, str) or not identity:
            raise ValueError(f"{id_field} must be a non-empty string")

        # 第二步：Sink 负责检测重复身份及可选语义冲突。
        self._sink.append_record_once(
            json.dumps(
                jsonable,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            id_field=id_field,
            identity=identity,
            semantic_projection=semantic_projection,
            semantic_fingerprint=semantic_fingerprint,
        )

        return self.path
