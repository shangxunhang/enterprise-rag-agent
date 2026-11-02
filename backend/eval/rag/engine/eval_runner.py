"""协调旧版 RAG 检索与可选生成的离线评测流程。

本模块读取 JSON 黄金样本，适配具有 ``retrieve`` 或 ``search`` 接口的 Retriever，
把不同返回格式统一为 ``RetrievalResult``，计算检索指标，并在提供 Generator 时继续
计算生成指标。它不绑定 FAISS、Milvus、Qdrant 或具体 LLM，实现切换由调用方注入。

当前入口保留对旧版样本和多种简单接口的兼容，新的配置驱动策略实验应优先使用
``eval.rag.strategy.StrategyEvalRunner``。
"""

# 继续向历史调用方暴露 generation_eval/retrieval_eval 中的公开函数。
from eval.rag.engine.generation_eval import *
from eval.rag.engine.retrieval_eval import *

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, List, Optional

from rag.schema.eval_schema import (
    EvalReport,
    EvalResult,
    EvalSample,
    RetrievalResult,
)
from eval.rag.engine.rag_eval_case_schema import (
    migrate_legacy_rag_eval_case_v1_payload,
)


def _load_raw_eval_samples(
    eval_dataset_path: str | Path,
) -> list[dict[str, Any]]:
    """从 JSON 列表文件读取未经 Schema 校验的评测样本。

    Args:
        eval_dataset_path: 顶层结构必须为列表的 JSON 文件路径。

    Returns:
        按文件顺序读取的样本字典列表。

    Raises:
        FileNotFoundError: 数据集路径不存在时抛出。
        ValueError: JSON 顶层不是列表，或任一列表项不是对象时抛出。
        json.JSONDecodeError: 文件不是合法 JSON 时抛出。
    """

    # 第一步：标准化路径并拒绝不存在的输入，避免生成误导性的空报告。
    dataset_path = Path(eval_dataset_path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"评估数据集不存在: {dataset_path}")

    # 第二步：按 UTF-8 读取完整 JSON 文档。
    with dataset_path.open("r", encoding="utf-8") as handle:
        raw_samples = json.load(handle)

    # 第三步：旧版 eval_runner 只接受 JSON 列表，不隐式支持包装对象或 JSONL。
    if not isinstance(raw_samples, list):
        raise ValueError("eval_dataset.json 的顶层结构必须是 list。")

    # 第四步：逐项确认是对象，Schema 字段校验由公开加载函数负责。
    for item in raw_samples:
        if not isinstance(item, dict):
            raise ValueError("eval_dataset.json 中每条样本必须是 dict。")

    return raw_samples


def load_eval_samples(
    eval_dataset_path: str | Path,
) -> List[EvalSample]:
    """严格加载当前 canonical 评测样本。

    本入口不会自动生成 sample_id、过滤未知字段或迁移旧别名。旧数据必须显式调用
    ``load_legacy_eval_samples_v1``，使迁移行为可见。
    """

    return [
        EvalSample.model_validate(item, strict=True)
        for item in _load_raw_eval_samples(eval_dataset_path)
    ]


def load_legacy_eval_samples_v1(
    eval_dataset_path: str | Path,
) -> List[EvalSample]:
    """显式迁移缺少 canonical sample_id 的旧版评测样本。

    文件顺序用于生成 ``legacy_eval_00001`` 风格的确定性回退标识；已有 case_id 或
    sample_id 的一致性由迁移适配器校验。
    """

    return [
        EvalSample.model_validate(
            migrate_legacy_rag_eval_case_v1_payload(
                item,
                fallback_sample_id=f"legacy_eval_{index:05d}",
            ),
            strict=True,
        )
        for index, item in enumerate(
            _load_raw_eval_samples(eval_dataset_path),
            start=1,
        )
    ]


def normalize_retrieval_results(
    raw_results: List[Any],
) -> List[RetrievalResult]:
    """把 Retriever 的对象或字典结果统一为旧版评测协议。

    Args:
        raw_results: 每项必须是 ``RetrievalResult`` 或字典。字典可提供 rank、score、
            doc_id、chunk_id、text、source 和 metadata。

    Returns:
        与输入顺序一致的 ``RetrievalResult`` 列表。

    Raises:
        TypeError: 遇到不支持的结果类型时抛出。

    已经符合协议的对象保持原引用；字典缺失字段时使用旧版默认值，rank 默认采用
    1-based 输入顺序。
    """

    normalized_results: List[RetrievalResult] = []

    # 逐项标准化，不重新排序，确保 Retriever 的原始排名语义不变。
    for index, item in enumerate(raw_results):
        # 当前协议对象可直接复用，避免重复构造造成字段损失。
        if isinstance(item, RetrievalResult):
            normalized_results.append(item)
            continue

        # 字典结果映射到旧版固定字段；额外字段不会自动进入 metadata。
        if isinstance(item, dict):
            result = RetrievalResult(
                rank=item.get("rank", index + 1),
                score=float(item.get("score", 0.0)),
                doc_id=str(item.get("doc_id", "")),
                chunk_id=str(item.get("chunk_id", "")),
                text=str(item.get("text", "")),
                source=item.get("source"),
                metadata=item.get("metadata", {}),
            )
            normalized_results.append(result)
            continue

        # 其他类型无法形成稳定评测协议，显式报告实际 Python 类型。
        raise TypeError(
            f"不支持的检索结果类型: {type(item)}。"
            f"请返回 RetrievalResult 或 dict。"
        )

    return normalized_results


def call_retriever(
    retriever: Any,
    query: str,
    top_k: int,
) -> List[RetrievalResult]:
    """调用兼容 Retriever 并标准化其结果。

    Args:
        retriever: 实现 ``retrieve(query, top_k=...)`` 或
            ``search(query, top_k=...)`` 的对象。
        query: 当前评测查询。
        top_k: 请求 Retriever 返回的最大结果数。

    Returns:
        统一的 ``RetrievalResult`` 列表。

    Raises:
        AttributeError: 两种检索接口都不存在时抛出。
    """

    # 第一步：优先使用项目约定的 retrieve 接口。
    if hasattr(retriever, "retrieve"):
        raw_results = retriever.retrieve(query, top_k=top_k)

    # 第二步：旧实现可能只提供 search，作为兼容回退。
    elif hasattr(retriever, "search"):
        raw_results = retriever.search(query, top_k=top_k)
    else:
        raise AttributeError(
            "retriever 必须实现 retrieve(query, top_k=...) "
            "或 search(query, top_k=...) 方法。"
        )

    # 第三步：隔离具体 Retriever 返回格式，后续指标只处理统一协议。
    return normalize_retrieval_results(raw_results)


def call_generator(
    generator: Any,
    query: str,
    retrieved_results: List[RetrievalResult],
) -> tuple[str, Optional[List[str]]]:
    """调用兼容 Generator 并归一化答案与引用文档。

    Args:
        generator: 实现 ``generate(query=..., retrieved_results=...)`` 的对象；
            ``None`` 表示本次只做检索评测。
        query: 当前评测查询。
        retrieved_results: 标准化检索结果。

    Returns:
        ``(answer, cited_doc_ids)``。Generator 可直接返回字符串，也可返回包含
        ``answer`` 和可选 ``cited_doc_ids`` 的字典。

    Raises:
        AttributeError: 非空 Generator 没有 generate 方法时抛出。
        TypeError: generate 返回值既不是字符串也不是字典时抛出。
    """

    # 未配置生成器时返回空答案和不可用引用，调用方不会进入生成指标分支。
    if generator is None:
        return "", None

    # 生成器必须显式提供统一入口。
    if not hasattr(generator, "generate"):
        raise AttributeError("generator 必须实现 generate(...) 方法。")

    # 使用关键字参数调用，兼容 RAGGeneratorWrapper 等标准适配器。
    raw_output = generator.generate(
        query=query,
        retrieved_results=retrieved_results,
    )

    # 简单生成器可只返回答案文本。
    if isinstance(raw_output, str):
        return raw_output, None

    # 结构化生成器可同时返回引用文档列表。
    if isinstance(raw_output, dict):
        answer = raw_output.get("answer", "")
        cited_doc_ids = raw_output.get("cited_doc_ids")
        return answer, cited_doc_ids

    raise TypeError(
        f"不支持的 generator 输出类型: {type(raw_output)}。"
        f"请返回 str 或 dict。"
    )


def _average(values: List[float]) -> float:
    """计算浮点列表平均值，空列表返回 ``0.0``。"""

    # 检索指标始终有值；生成指标是否适用由报告字段是否为 None 另行表达。
    if not values:
        return 0.0
    return sum(values) / len(values)


def run_eval(
    retriever: Any,
    eval_dataset_path: str | Path,
    top_k: int = 5,
    generator: Optional[Any] = None,
) -> EvalReport:
    """运行旧版 RAG 检索与可选生成的完整离线评测。

    Args:
        retriever: 兼容 ``call_retriever`` 的检索器。
        eval_dataset_path: canonical JSON 黄金样本文件。
        top_k: 检索请求和指标计算使用的结果窗口。
        generator: 可选兼容 ``call_generator`` 的生成器；为空时只评检索。

    Returns:
        包含宏平均指标和逐样本详情的 ``EvalReport``。

    本函数按样本顺序同步执行，任何 Retriever、Generator 或指标异常都会直接终止
    整批评测；需要逐样本失败隔离时应使用配置驱动的 StrategyEvalRunner。
    """

    # 第一步：严格加载全部黄金样本。
    samples = load_eval_samples(eval_dataset_path)

    # 第二步：准备逐样本结果和各指标独立累计列表。
    details: List[EvalResult] = []
    hit_at_k_values: List[float] = []
    recall_at_k_values: List[float] = []
    mrr_values: List[float] = []
    context_keyword_hit_values: List[float] = []
    answer_keyword_hit_values: List[float] = []
    answer_context_overlap_values: List[float] = []
    citation_hit_values: List[float] = []

    # 第三步：按数据集顺序执行每条样本。
    for sample in samples:
        # 调用 Retriever 并统一结果格式。
        retrieved_results = call_retriever(
            retriever=retriever,
            query=sample.query,
            top_k=top_k,
        )

        # 计算文档级检索指标并分别累计。
        retrieval_eval = evaluate_retrieval(
            sample=sample,
            retrieved_results=retrieved_results,
            top_k=top_k,
        )
        hit_at_k_values.append(retrieval_eval.hit_at_k)
        recall_at_k_values.append(retrieval_eval.recall_at_k)
        mrr_values.append(retrieval_eval.mrr)
        context_keyword_hit_values.append(
            retrieval_eval.context_keyword_hit
        )

        # 默认没有生成结果；纯检索模式保持 generation_eval/answer 为 None。
        generation_eval = None
        answer = None

        # 第四步：配置 Generator 时生成答案并计算三项旧版生成指标。
        if generator is not None:
            answer, cited_doc_ids = call_generator(
                generator=generator,
                query=sample.query,
                retrieved_results=retrieved_results,
            )
            generation_eval = evaluate_generation(
                sample=sample,
                answer=answer,
                retrieved_results=retrieved_results,
                cited_doc_ids=cited_doc_ids,
            )
            answer_keyword_hit_values.append(
                generation_eval.answer_keyword_hit
            )
            answer_context_overlap_values.append(
                generation_eval.answer_context_overlap
            )
            citation_hit_values.append(
                generation_eval.citation_hit
            )

        # 第五步：保存本样本检索、生成和原始结果详情。
        details.append(
            EvalResult(
                query=sample.query,
                retrieval_eval=retrieval_eval,
                generation_eval=generation_eval,
                answer=answer,
                retrieved_results=retrieved_results,
            )
        )

    # 第六步：构建批次报告。未执行生成阶段的聚合字段使用 None 而不是零。
    return EvalReport(
        num_samples=len(samples),
        top_k=top_k,
        avg_hit_at_k=_average(hit_at_k_values),
        avg_recall_at_k=_average(recall_at_k_values),
        avg_mrr=_average(mrr_values),
        avg_context_keyword_hit=_average(
            context_keyword_hit_values
        ),
        avg_answer_keyword_hit=(
            _average(answer_keyword_hit_values)
            if answer_keyword_hit_values
            else None
        ),
        avg_answer_context_overlap=(
            _average(answer_context_overlap_values)
            if answer_context_overlap_values
            else None
        ),
        avg_citation_hit=(
            _average(citation_hit_values)
            if citation_hit_values
            else None
        ),
        details=details,
    )


def _to_serializable(obj: Any) -> Any:
    """把 dataclass 或 Path 转换为 JSON 可序列化对象。

    其他对象原样返回，由 ``json.dump`` 决定是否支持。当前 EvalReport 及其嵌套对象
    使用 dataclass，因此 ``asdict`` 会递归转换完整报告。
    """

    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, Path):
        return str(obj)
    return obj


def save_eval_report(
    report: EvalReport,
    output_path: str | Path,
) -> None:
    """将旧版评测报告保存为 UTF-8 JSON 文件。

    Args:
        report: ``run_eval`` 返回的报告。
        output_path: 目标 JSON 路径；父目录不存在时自动创建。
    """

    # 第一步：标准化目标路径并创建父目录。
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 第二步：把 dataclass 报告递归转换为普通 Python 结构。
    report_dict = _to_serializable(report)

    # 第三步：保留中文并使用缩进写入，便于人工审阅。
    with target.open("w", encoding="utf-8") as handle:
        json.dump(
            report_dict,
            handle,
            ensure_ascii=False,
            indent=2,
        )


def run_and_save_eval(
    retriever: Any,
    eval_dataset_path: str | Path,
    output_path: str | Path,
    top_k: int = 5,
    generator: Optional[Any] = None,
) -> EvalReport:
    """执行评测、保存报告并返回内存中的同一报告对象。

    Args:
        retriever: 兼容检索器。
        eval_dataset_path: 黄金样本 JSON 文件。
        output_path: 报告 JSON 输出路径。
        top_k: 检索和指标窗口。
        generator: 可选生成器。

    Returns:
        已持久化的 ``EvalReport``。
    """

    # 第一步：运行全部样本并生成报告。
    report = run_eval(
        retriever=retriever,
        eval_dataset_path=eval_dataset_path,
        top_k=top_k,
        generator=generator,
    )

    # 第二步：将该报告写入调用方指定路径。
    save_eval_report(report, output_path)

    # 第三步：返回同一对象，避免调用方再次读取磁盘。
    return report
