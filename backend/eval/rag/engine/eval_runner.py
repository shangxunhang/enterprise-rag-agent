# =============================================================================
# 中文阅读说明：离线评测模块，用于执行实验、评分、对比和报告生成。
# 主要定义：load_eval_samples、normalize_retrieval_results、call_retriever、call_generator、_average、run_eval、_to_serializable、save_eval_report、run_and_save_eval。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""
eval_runner.py
==============

RAG 评估模块的统一调度入口。

本文件负责串联完整的离线评估流程：
1. 读取 eval_dataset.json 中的评估样本
2. 对每个 query 调用 retriever 执行检索
3. 调用 retrieval_eval.py 计算检索指标
4. 可选调用 generator 生成最终答案
5. 调用 generation_eval.py 计算生成指标
6. 汇总所有样本的平均结果
7. 输出 eval_report.json 评估报告

eval_runner.py 不应该直接写死具体的 FAISS、Milvus、Qdrant 或某个 LLM 实现。
它应尽量依赖统一接口，例如：
1. retriever.retrieve(query, top_k)
2. generator.generate(query, retrieved_chunks)

这样后续无论切换 chunk 策略、向量数据库、reranker、LLM 或 Agent，
评估模块都可以复用。
"""
from eval.rag.engine.generation_eval import *
from eval.rag.engine.retrieval_eval import *

"""
eval_runner.py
==============

RAG 评估模块的统一调度入口。

本文件负责串联完整的离线评估流程：
1. 读取 eval_dataset.json 中的评估样本
2. 对每个 query 调用 retriever 执行检索
3. 调用 retrieval_eval.py 计算检索指标
4. 可选调用 generator 生成最终答案
5. 调用 generation_eval.py 计算生成指标
6. 汇总所有样本的平均结果
7. 输出 eval_report.json 评估报告

eval_runner.py 不应该直接写死具体的 FAISS、Milvus、Qdrant 或某个 LLM 实现。
它应尽量依赖统一接口，例如：
1. retriever.retrieve(query, top_k)
2. generator.generate(query, retrieved_chunks)

这样后续无论切换 chunk 策略、向量数据库、reranker、LLM 或 Agent，
评估模块都可以复用。
"""

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path


# 阅读注释（函数）：加载 评测 samples。
def load_eval_samples(eval_dataset_path: str | Path) -> List[EvalSample]:
    """
    加载 eval_dataset.json，并转换为 EvalSample 列表。

    eval_dataset.json 示例：

    [
      {
        "query": "FAISS 是什么？",
        "expected_doc_ids": ["doc_003"],
        "expected_keywords": ["向量检索", "相似度搜索"],
        "answer_keywords": ["Facebook AI Research", "向量检索"]
      }
    ]

    参数:
        eval_dataset_path:
            评估数据集路径。

    返回:
        samples:
            EvalSample 对象列表。
    """
    eval_dataset_path = Path(eval_dataset_path)

    if not eval_dataset_path.exists():
        raise FileNotFoundError(f"评估数据集不存在: {eval_dataset_path}")

    with eval_dataset_path.open("r", encoding="utf-8") as f:
        raw_samples = json.load(f)

    if not isinstance(raw_samples, list):
        raise ValueError("eval_dataset.json 的顶层结构必须是 list。")

    samples: List[EvalSample] = []

    for item in raw_samples:
        if not isinstance(item, dict):
            raise ValueError("eval_dataset.json 中每条样本必须是 dict。")

        sample = EvalSample(
            query=item["query"],
            expected_doc_ids=item.get("expected_doc_ids", []),
            expected_keywords=item.get("expected_keywords", []),
            answer_keywords=item.get("answer_keywords", []),
            metadata=item.get("metadata", {}),
        )
        samples.append(sample)

    return samples


# 阅读注释（函数）：规范化 检索 结果集合。
def normalize_retrieval_results(
        raw_results: List[Any],
) -> List[RetrievalResult]:
    """
    将 retriever 返回的原始结果转换成标准 RetrievalResult。

    设计这个函数的原因：
    不同 retriever 返回的数据格式可能不一样。
    比如有的返回 dict，有的返回对象。
    eval 模块内部只使用标准 RetrievalResult。

    支持两类输入：
    1. 已经是 RetrievalResult 的对象
    2. dict 格式的检索结果

    dict 至少应包含：
        doc_id
        chunk_id
        text

    可选包含：
        rank
        score
        source
        metadata

    参数:
        raw_results:
            retriever 返回的原始检索结果。

    返回:
        normalized_results:
            标准化后的 RetrievalResult 列表。
    """
    normalized_results: List[RetrievalResult] = []

    for index, item in enumerate(raw_results):
        if isinstance(item, RetrievalResult):
            normalized_results.append(item)
            continue

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

        raise TypeError(
            f"不支持的检索结果类型: {type(item)}。"
            f"请返回 RetrievalResult 或 dict。"
        )

    return normalized_results


# 阅读注释（函数）：处理 call retriever 相关逻辑。
def call_retriever(
        retriever: Any,
        query: str,
        top_k: int,
) -> List[RetrievalResult]:
    """
    调用 retriever 执行检索，并标准化返回结果。

    retriever 需要实现以下接口之一：
        retriever.retrieve(query, top_k=top_k)
        retriever.search(query, top_k=top_k)

    参数:
        retriever:
            检索器对象。
        query:
            查询文本。
        top_k:
            检索返回数量。

    返回:
        retrieved_results:
            标准化 RetrievalResult 列表。
    """
    if hasattr(retriever, "retrieve"):
        raw_results = retriever.retrieve(query, top_k=top_k)
    elif hasattr(retriever, "search"):
        raw_results = retriever.search(query, top_k=top_k)
    else:
        raise AttributeError(
            "retriever 必须实现 retrieve(query, top_k=...) "
            "或 search(query, top_k=...) 方法。"
        )

    return normalize_retrieval_results(raw_results)


# 阅读注释（函数）：处理 call generator 相关逻辑。
def call_generator(
        generator: Any,
        query: str,
        retrieved_results: List[RetrievalResult],
) -> tuple[str, Optional[List[str]]]:
    """
    调用 generator 生成答案。

    generator 需要实现以下接口之一：
        generator.generate(query, retrieved_results)
        generator.generate(query=query, retrieved_results=retrieved_results)

    推荐返回格式：
        1. str
           只返回 answer

        2. dict
           {
               "answer": "...",
               "cited_doc_ids": ["doc_001", "doc_002"]
           }

    参数:
        generator:
            生成器对象。
        query:
            用户问题。
        retrieved_results:
            检索结果。

    返回:
        answer:
            生成答案。
        cited_doc_ids:
            答案引用的 doc_id 列表。没有则为 None。
    """
    if generator is None:
        return "", None

    if not hasattr(generator, "generate"):
        raise AttributeError("generator 必须实现 generate(...) 方法。")

    raw_output = generator.generate(
        query=query,
        retrieved_results=retrieved_results,
    )

    if isinstance(raw_output, str):
        return raw_output, None

    if isinstance(raw_output, dict):
        answer = raw_output.get("answer", "")
        cited_doc_ids = raw_output.get("cited_doc_ids")
        return answer, cited_doc_ids

    raise TypeError(
        f"不支持的 generator 输出类型: {type(raw_output)}。"
        f"请返回 str 或 dict。"
    )


# 阅读注释（函数）：处理 average 相关逻辑。
def _average(values: List[float]) -> float:
    """
    计算平均值。

    空列表返回 0.0，避免除零错误。
    """
    if not values:
        return 0.0

    return sum(values) / len(values)


# 阅读注释（函数）：执行 评测 runner 的主流程。
def run_eval(
        retriever: Any,
        eval_dataset_path: str | Path,
        top_k: int = 5,
        generator: Optional[Any] = None,
) -> EvalReport:
    """
    运行完整 RAG 离线评估流程。

    参数:
        retriever:
            检索器对象，需要实现 retrieve 或 search 方法。

        eval_dataset_path:
            eval_dataset.json 路径。

        top_k:
            参与评估的 top-k 检索结果。

        generator:
            可选生成器对象。
            如果传入，则会额外执行生成评估；
            如果不传入，则只做检索评估。

    返回:
        EvalReport:
            整体评估报告。
    """
    samples = load_eval_samples(eval_dataset_path)

    details: List[EvalResult] = []

    hit_at_k_values: List[float] = []
    recall_at_k_values: List[float] = []
    mrr_values: List[float] = []
    context_keyword_hit_values: List[float] = []

    answer_keyword_hit_values: List[float] = []
    answer_context_overlap_values: List[float] = []
    citation_hit_values: List[float] = []

    for sample in samples:
        retrieved_results = call_retriever(
            retriever=retriever,
            query=sample.query,
            top_k=top_k,
        )

        retrieval_eval = evaluate_retrieval(
            sample=sample,
            retrieved_results=retrieved_results,
            top_k=top_k,
        )

        hit_at_k_values.append(retrieval_eval.hit_at_k)
        recall_at_k_values.append(retrieval_eval.recall_at_k)
        mrr_values.append(retrieval_eval.mrr)
        context_keyword_hit_values.append(retrieval_eval.context_keyword_hit)

        generation_eval = None
        answer = None

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

        eval_result = EvalResult(
            query=sample.query,
            retrieval_eval=retrieval_eval,
            generation_eval=generation_eval,
            answer=answer,
            retrieved_results=retrieved_results,
        )

        details.append(eval_result)

    report = EvalReport(
        num_samples=len(samples),
        top_k=top_k,
        avg_hit_at_k=_average(hit_at_k_values),
        avg_recall_at_k=_average(recall_at_k_values),
        avg_mrr=_average(mrr_values),
        avg_context_keyword_hit=_average(context_keyword_hit_values),
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

    return report


# 阅读注释（函数）：把 评测 runner 转换为 serializable。
def _to_serializable(obj: Any) -> Any:
    """
    将 dataclass / Path / 普通对象转换为可 JSON 序列化对象。

    主要用于保存 EvalReport。
    """
    if is_dataclass(obj):
        return asdict(obj)

    if isinstance(obj, Path):
        return str(obj)

    return obj


# 阅读注释（函数）：保存 评测 report。
def save_eval_report(
        report: EvalReport,
        output_path: str | Path,
) -> None:
    """
    保存评估报告为 JSON 文件。

    参数:
        report:
            EvalReport 对象。
        output_path:
            输出路径，例如 data/eval/eval_report.json。
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report_dict = _to_serializable(report)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(
            report_dict,
            f,
            ensure_ascii=False,
            indent=2,
        )


# 阅读注释（函数）：执行 评测 runner 的主流程。
def run_and_save_eval(
        retriever: Any,
        eval_dataset_path: str | Path,
        output_path: str | Path,
        top_k: int = 5,
        generator: Optional[Any] = None,
) -> EvalReport:
    """
    运行评估并保存报告。

    这是 eval_runner.py 对外最常用的入口函数。

    参数:
        retriever:
            检索器对象。

        eval_dataset_path:
            评估数据集路径。

        output_path:
            评估报告输出路径。

        top_k:
            参与评估的 top-k 检索结果。

        generator:
            可选生成器对象。

    返回:
        report:
            EvalReport 对象。
    """
    report = run_eval(
        retriever=retriever,
        eval_dataset_path=eval_dataset_path,
        top_k=top_k,
        generator=generator,
    )

    save_eval_report(report, output_path)

    return report
