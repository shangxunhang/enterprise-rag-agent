"""加载 RAG 策略黄金样本并计算原始数据集文件哈希。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .schemas import RAGEvalSample


def file_sha256(path: str | Path) -> str:
    """以流式方式计算文件 SHA-256。

    Args:
        path: 待读取文件路径。

    Returns:
        文件原始字节内容的十六进制 SHA-256。

    使用 1 MiB 分块避免大数据集一次性读入内存；该哈希反映原始文件版本，和配置
    Loader 对规范化结构计算的 canonical hash 含义不同。
    """

    # 第一步：创建增量摘要对象。
    digest = hashlib.sha256()

    # 第二步：以二进制模式分块读取，保留换行、空白和编码字节差异。
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    # 第三步：返回稳定十六进制字符串供报告和可重复性校验使用。
    return digest.hexdigest()


def load_eval_samples(path: str | Path) -> list[RAGEvalSample]:
    """从 JSON 或 JSONL 文件加载严格 RAG 策略评测样本。

    Args:
        path: 扩展名为 ``.json`` 或 ``.jsonl`` 的数据集文件。

    Returns:
        按文件顺序校验后的非空样本列表。

    Raises:
        FileNotFoundError: 路径不是文件时抛出。
        ValueError: 格式不受支持、JSON 根不是列表、样本为空或 sample_id 重复时抛出。
        pydantic.ValidationError: 任一样本不符合 ``RAGEvalSample`` 契约时抛出。
    """

    # 第一步：标准化路径并拒绝不存在的数据集。
    dataset_path = Path(path)
    if not dataset_path.is_file():
        raise FileNotFoundError(
            f"RAG strategy eval dataset not found: {dataset_path}"
        )

    # 第二步：按扩展名解析；JSONL 会保留行号以提供精确错误位置。
    suffix = dataset_path.suffix.lower()
    if suffix == ".jsonl":
        payloads = []
        with dataset_path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSONL at line {line_number}: {dataset_path}"
                    ) from exc
                payloads.append(payload)
    elif suffix == ".json":
        payloads = json.loads(
            dataset_path.read_text(encoding="utf-8-sig")
        )
        if not isinstance(payloads, list):
            raise ValueError("JSON eval dataset root must be a list")
    else:
        raise ValueError("eval dataset must use .json or .jsonl")

    # 第三步：逐样本执行严格协议校验，避免评测逻辑接收自由字典。
    samples = [RAGEvalSample.model_validate(item) for item in payloads]

    # 第四步：空数据集没有可比较意义，显式拒绝而不是生成全零报告。
    if not samples:
        raise ValueError("eval dataset cannot be empty")

    # 第五步：sample_id 必须唯一，保证逐样本结果、重放和失败索引无歧义。
    sample_ids = [item.sample_id for item in samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("eval dataset contains duplicate sample_id values")

    return samples
