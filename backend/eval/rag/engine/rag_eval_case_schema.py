# -*- coding: utf-8 -*-
"""加载并索引旧版父子块 RAG QA 评测样本。

所有当前评测路径统一使用 ``schemas.eval_schema.RAGEvalSampleSchema``。本模块只保留
历史 ``RagEvalCase`` 名称和显式 v1 迁移适配器，不定义第二套样本事实源。输入支持
JSON 列表、``{"cases": [...]}`` 包装和 JSONL。
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from schemas.eval_schema import RAGEvalSampleSchema


# 历史公开名称直接指向当前唯一样本协议。
RagEvalCase = RAGEvalSampleSchema


def _load_raw_eval_payloads(path: str | Path) -> List[Dict[str, Any]]:
    """从 JSON 或 JSONL 文件读取未经 Schema 校验的样本对象。

    Args:
        path: 评测样本文件路径。

    Returns:
        按文件顺序读取的字典列表；空文件返回空列表。

    Raises:
        FileNotFoundError: 路径不存在时抛出。
        ValueError: JSON 根结构或任一样本不是对象时抛出。
        json.JSONDecodeError: JSON/JSONL 语法损坏时抛出。
    """

    # 第一步：标准化路径并拒绝不存在的来源文件。
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(
            f"RAG eval case file not found: {source_path}"
        )

    # 第二步：读取并去除首尾空白；空文件没有原始样本。
    text = source_path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    raw_items: List[Dict[str, Any]] = []

    # 第三步：JSON 文件允许直接列表或兼容的 cases 包装对象。
    if source_path.suffix.lower() == ".json":
        raw = json.loads(text)
        if isinstance(raw, dict) and isinstance(raw.get("cases"), list):
            raw = raw["cases"]
        if not isinstance(raw, list):
            raise ValueError(
                "JSON eval case file must be a list or "
                "{'cases': [...]} object"
            )

        # 逐项验证根元素类型，并在错误中保留 1-based 索引。
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise ValueError(
                    f"JSON eval case item {index} must be an object"
                )
            raw_items.append(item)

    # 第四步：其他扩展名按 JSONL 处理，每个非空行必须是对象。
    else:
        for line_number, line in enumerate(text.splitlines(), start=1):
            normalized = line.strip()
            if not normalized:
                continue
            item = json.loads(normalized)
            if not isinstance(item, dict):
                raise ValueError(
                    f"JSONL line {line_number} must be an object"
                )
            raw_items.append(item)

    return raw_items


def load_rag_eval_cases(path: str | Path) -> List[RagEvalCase]:
    """严格加载当前 canonical RAG 评测样本。

    不执行默认值补全之外的隐藏迁移，也不接受旧 ``case_id`` 别名；旧数据必须显式
    调用 ``load_legacy_rag_eval_cases_v1``，保证迁移路径可见和可审计。
    """

    return [
        RagEvalCase.model_validate(item, strict=True)
        for item in _load_raw_eval_payloads(path)
    ]


def migrate_legacy_rag_eval_case_v1_payload(
    payload: Mapping[str, Any],
    *,
    fallback_sample_id: str,
) -> Dict[str, Any]:
    """把旧版 ``case_id`` 显式迁移为 canonical ``sample_id``。

    Args:
        payload: 旧版样本对象。
        fallback_sample_id: 两个身份字段都缺失时使用的确定性回退标识。

    Returns:
        迁移后的新字典，不修改输入 Mapping。

    Raises:
        TypeError: 输入不是 Mapping 时抛出。
        ValueError: case_id 与 sample_id 同时存在但不一致时抛出。
    """

    # 第一步：确保迁移输入是对象，并复制为可修改字典。
    if not isinstance(payload, Mapping):
        raise TypeError(
            "legacy RAG eval migration requires an object payload"
        )
    migrated = dict(payload)

    # 第二步：移除旧别名并读取可能已存在的新身份。
    legacy_case_id = migrated.pop("case_id", None)
    explicit_sample_id = migrated.get("sample_id")

    # 第三步：同时提供两个身份时必须一致，避免静默选择其中一个。
    if (
        legacy_case_id
        and explicit_sample_id
        and str(legacy_case_id) != str(explicit_sample_id)
    ):
        raise ValueError(
            "case_id and sample_id must match when both are provided"
        )

    # 第四步：缺少 canonical 身份时优先采用旧 ID，否则使用调用方回退值。
    if "sample_id" not in migrated:
        migrated["sample_id"] = (
            legacy_case_id
            if legacy_case_id is not None
            else fallback_sample_id
        )

    return migrated


def load_legacy_rag_eval_cases_v1(
    path: str | Path,
) -> List[RagEvalCase]:
    """通过显式 v1 迁移适配器加载旧版评测样本。"""

    # 文件顺序用于生成缺失身份的确定性回退 sample_id。
    return [
        RagEvalCase.model_validate(
            migrate_legacy_rag_eval_case_v1_payload(
                item,
                fallback_sample_id=f"rag_case_{index:04d}",
            ),
            strict=True,
        )
        for index, item in enumerate(
            _load_raw_eval_payloads(path),
            start=1,
        )
    ]


def index_cases_by_query(
    cases: Iterable[RagEvalCase],
) -> Dict[str, RagEvalCase]:
    """建立查询文本到样本的索引。

    重复 query 保留第一个样本，保持输入顺序决定性；调用方若需要区分重复查询，
    应使用 sample_id 而不是本兼容索引。
    """

    index: Dict[str, RagEvalCase] = {}
    for case in cases:
        index.setdefault(case.query, case)
    return index


def match_case_for_run(
    run_record: Dict[str, Any],
    cases_by_query: Dict[str, RagEvalCase],
) -> Optional[RagEvalCase]:
    """按运行记录中的标准化查询匹配黄金样本。

    Args:
        run_record: 至少可能包含 ``query`` 的运行记录。
        cases_by_query: ``index_cases_by_query`` 构建的索引。

    Returns:
        精确查询匹配的样本；查询为空或未命中时返回 ``None``。
    """

    # 去除首尾空白，但不做大小写、分词或语义模糊匹配。
    query = str(run_record.get("query") or "").strip()
    if not query:
        return None
    return cases_by_query.get(query)
