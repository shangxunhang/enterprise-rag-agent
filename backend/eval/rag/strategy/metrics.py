"""定义在线 RAG 策略实验的指标注册表和计算函数。

本模块只依赖标准化实验输出，不读取文件、不调用检索器，也不持久化结果。指标函数
通过 ``MetricContext`` 获得黄金样本、运行输出、延迟和 top_k；注册表负责校验指标
名称、优化方向以及黄金标签是否齐全。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from .schemas import RAGEvalSample


@dataclass(frozen=True)
class MetricContext:
    """封装一次样本指标计算所需的只读上下文。

    Attributes:
        sample: 当前黄金样本。
        output: 生产检索运行时返回的标准化输出。
        latency_ms: 当前样本端到端运行耗时。
        top_k: 本实验用于指标截断的结果数量。
    """

    sample: RAGEvalSample
    output: dict[str, Any]
    latency_ms: float
    top_k: int


@dataclass(frozen=True)
class MetricDefinition:
    """描述一个可注册指标的元数据和计算函数。

    Attributes:
        name: 稳定指标名称。
        direction: ``maximize``、``minimize`` 或 ``neutral``。
        requires_answer: 指标是否只适用于 ``rag_answer`` 模式。
        compute: 接收 ``MetricContext`` 并返回分数的纯函数。
        required_gold: 计算前必须在黄金样本中存在的标签类型。
    """

    name: str
    direction: str
    requires_answer: bool
    compute: Callable[[MetricContext], float | None]
    required_gold: str | None = None


def _results(context: MetricContext) -> list[dict[str, Any]]:
    """从运行输出中提取字典形式的完整召回结果列表。"""

    # 非字典项不符合指标协议，直接过滤，避免后续字段访问异常。
    raw = context.output.get("retrieval_results") or []
    return [item for item in raw if isinstance(item, dict)]


def _top_results(context: MetricContext) -> list[dict[str, Any]]:
    """按实验 top_k 截断召回结果。"""

    return _results(context)[: context.top_k]


def _matches_any(sample: RAGEvalSample, item: dict[str, Any]) -> bool:
    """判断一个召回项是否命中任一粒度的黄金标识。

    文档、父块和子块黄金标签采用 OR 关系：只要召回项在样本提供的任一粒度命中，
    即视为相关。样本未提供的粒度不会参与判断。
    """

    # 第一步：归一化召回项的多粒度标识。
    doc_id = str(item.get("doc_id") or "")
    parent_id = str(item.get("parent_chunk_id") or "")
    child_id = str(
        item.get("child_chunk_id") or item.get("chunk_id") or ""
    )

    # 第二步：分别检查样本实际提供的黄金集合。
    return bool(
        (sample.expected_doc_ids and doc_id in set(sample.expected_doc_ids))
        or (
            sample.expected_parent_chunk_ids
            and parent_id in set(sample.expected_parent_chunk_ids)
        )
        or (
            sample.expected_child_chunk_ids
            and child_id in set(sample.expected_child_chunk_ids)
        )
    )


def _primary_gold(sample: RAGEvalSample) -> tuple[str, set[str]]:
    """选择用于 recall/NDCG 去重计算的最高精度黄金粒度。

    优先级为子块 > 父块 > 文档。这样当样本同时提供多粒度标签时，不会把同一证据
    在不同粒度重复计入召回分母。
    """

    if sample.expected_child_chunk_ids:
        return "child", set(sample.expected_child_chunk_ids)
    if sample.expected_parent_chunk_ids:
        return "parent", set(sample.expected_parent_chunk_ids)
    return "doc", set(sample.expected_doc_ids)


def _result_id(item: dict[str, Any], level: str) -> str:
    """按指定黄金粒度提取召回项标识。"""

    if level == "child":
        return str(
            item.get("child_chunk_id") or item.get("chunk_id") or ""
        )
    if level == "parent":
        return str(item.get("parent_chunk_id") or "")
    return str(item.get("doc_id") or "")


def hit_at_k(context: MetricContext) -> float:
    """计算 top-k 中是否至少命中一个黄金结果。"""

    # 二值指标：任一相关项出现即为 1，否则为 0。
    return 1.0 if any(
        _matches_any(context.sample, item)
        for item in _top_results(context)
    ) else 0.0


def recall_at_k(context: MetricContext) -> float:
    """计算主黄金粒度在 top-k 中的去重召回率。"""

    # 第一步：选择最细可用黄金粒度；无黄金集合时返回零。
    level, gold = _primary_gold(context.sample)
    if not gold:
        return 0.0

    # 第二步：提取 top-k 的同粒度标识并移除空值。
    retrieved = {
        _result_id(item, level)
        for item in _top_results(context)
    }
    retrieved.discard("")

    # 第三步：使用黄金集合大小作为分母，重复召回不会重复计数。
    return len(retrieved & gold) / len(gold)


def reciprocal_rank(context: MetricContext) -> float:
    """计算第一个相关结果排名的倒数。"""

    # 从排名 1 开始扫描，首次命中后立即返回；完全未命中返回零。
    for rank, item in enumerate(_top_results(context), start=1):
        if _matches_any(context.sample, item):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(context: MetricContext) -> float:
    """计算二值相关性的 NDCG@k。

    同一黄金标识只在首次出现时产生 gain，避免重复块通过多次召回抬高 DCG。
    """

    # 第一步：选择主黄金粒度并拒绝空黄金集合。
    level, gold = _primary_gold(context.sample)
    if not gold:
        return 0.0

    # 第二步：按排名累计去重相关项的折损收益。
    seen: set[str] = set()
    dcg = 0.0
    for rank, item in enumerate(_top_results(context), start=1):
        candidate_id = _result_id(item, level)
        gain = (
            1.0
            if candidate_id in gold and candidate_id not in seen
            else 0.0
        )
        if gain:
            seen.add(candidate_id)
            dcg += gain / math.log2(rank + 1)

    # 第三步：理想排序最多容纳 min(|gold|, k) 个相关项。
    ideal_hits = min(len(gold), context.top_k)
    idcg = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, ideal_hits + 1)
    )

    return dcg / idcg if idcg else 0.0


def context_keyword_hit(context: MetricContext) -> float:
    """计算黄金关键词在 top-k 召回文本中的字面命中比例。"""

    keywords = context.sample.expected_keywords
    if not keywords:
        return 0.0

    # 第一步：每个召回项只选择首个可用文本字段，避免同一内容重复拼接。
    parts: list[str] = []
    for item in _top_results(context):
        for field in ("text", "parent_text", "child_text"):
            value = item.get(field)
            if value:
                parts.append(str(value))
                break

    # 第二步：使用 casefold 做大小写无关字面匹配。
    joined = "\n".join(parts).casefold()
    return sum(
        1
        for keyword in keywords
        if keyword.casefold() in joined
    ) / len(keywords)


def answer_keyword_hit(context: MetricContext) -> float | None:
    """计算回答对黄金回答关键词的字面覆盖比例。

    没有 answer_keywords 时返回 ``None``，表示该指标不适用，而不是零分。
    """

    keywords = context.sample.answer_keywords
    if not keywords:
        return None

    answer = str(context.output.get("answer") or "").casefold()
    return sum(
        1
        for keyword in keywords
        if keyword.casefold() in answer
    ) / len(keywords)


def citation_count(context: MetricContext) -> float:
    """返回输出中的结构化引用数量。"""

    return float(len(context.output.get("citations") or []))


def result_count(context: MetricContext) -> float:
    """返回未截断的有效召回结果数量。"""

    return float(len(_results(context)))


def latency_ms(context: MetricContext) -> float:
    """返回当前样本端到端延迟毫秒数。"""

    return float(context.latency_ms)


class MetricRegistry:
    """注册指标定义并校验实验请求与黄金样本兼容性。"""

    def __init__(self) -> None:
        """创建空指标映射。"""

        self._items: dict[str, MetricDefinition] = {}

    def register(self, definition: MetricDefinition) -> None:
        """注册一个名称唯一且方向合法的指标。"""

        # 第一步：名称重复会使配置解析结果不确定，直接拒绝。
        if definition.name in self._items:
            raise ValueError(f"duplicate metric: {definition.name}")

        # 第二步：方向只允许三种稳定枚举，供报告解释和未来排序使用。
        if definition.direction not in {
            "maximize",
            "minimize",
            "neutral",
        }:
            raise ValueError(
                f"invalid metric direction: {definition.direction}"
            )

        self._items[definition.name] = definition

    def require(self, names: list[str]) -> list[MetricDefinition]:
        """按请求顺序解析指标定义，并拒绝未知名称。"""

        missing = [name for name in names if name not in self._items]
        if missing:
            raise ValueError(f"unknown metrics: {', '.join(missing)}")
        return [self._items[name] for name in names]

    def directions(self, names: list[str]) -> dict[str, str]:
        """返回请求指标的优化方向映射。"""

        return {
            item.name: item.direction
            for item in self.require(names)
        }

    def validate_samples(
        self,
        names: list[str],
        samples: list[RAGEvalSample],
    ) -> None:
        """检查所有样本是否提供所选指标要求的黄金标签。

        Raises:
            ValueError: 任一指标缺少所需黄金标签时，汇总最多五个样本 ID 后抛出。
        """

        # 第一步：先解析指标定义，确保名称全部合法。
        definitions = self.require(names)
        errors: list[str] = []

        # 第二步：按指标声明的 required_gold 类型筛查缺失样本。
        for definition in definitions:
            if definition.required_gold == "retrieval_ids":
                missing = [
                    sample.sample_id
                    for sample in samples
                    if not (
                        sample.expected_doc_ids
                        or sample.expected_parent_chunk_ids
                        or sample.expected_child_chunk_ids
                    )
                ]
            elif definition.required_gold == "expected_keywords":
                missing = [
                    sample.sample_id
                    for sample in samples
                    if not sample.expected_keywords
                ]
            elif definition.required_gold == "answer_keywords":
                missing = [
                    sample.sample_id
                    for sample in samples
                    if not sample.answer_keywords
                ]
            else:
                missing = []

            # 第三步：错误信息只预览前五个 ID，避免大型数据集异常无限膨胀。
            if missing:
                preview = ", ".join(missing[:5])
                suffix = "..." if len(missing) > 5 else ""
                errors.append(
                    f"metric {definition.name} missing "
                    f"{definition.required_gold} for samples: "
                    f"{preview}{suffix}"
                )

        # 第四步：一次性报告全部指标缺失问题，减少反复修数据的轮次。
        if errors:
            raise ValueError("; ".join(errors))


def build_default_metric_registry() -> MetricRegistry:
    """构建项目默认策略指标注册表。"""

    registry = MetricRegistry()

    # 指标定义同时声明方向、是否需要回答以及所需黄金标签类型。
    for definition in (
        MetricDefinition(
            "hit_at_k",
            "maximize",
            False,
            hit_at_k,
            "retrieval_ids",
        ),
        MetricDefinition(
            "recall_at_k",
            "maximize",
            False,
            recall_at_k,
            "retrieval_ids",
        ),
        MetricDefinition(
            "mrr",
            "maximize",
            False,
            reciprocal_rank,
            "retrieval_ids",
        ),
        MetricDefinition(
            "ndcg_at_k",
            "maximize",
            False,
            ndcg_at_k,
            "retrieval_ids",
        ),
        MetricDefinition(
            "context_keyword_hit",
            "maximize",
            False,
            context_keyword_hit,
            "expected_keywords",
        ),
        MetricDefinition(
            "answer_keyword_hit",
            "maximize",
            True,
            answer_keyword_hit,
            "answer_keywords",
        ),
        MetricDefinition(
            "citation_count",
            "neutral",
            True,
            citation_count,
        ),
        MetricDefinition(
            "result_count",
            "neutral",
            False,
            result_count,
        ),
        MetricDefinition(
            "latency_ms",
            "minimize",
            False,
            latency_ms,
        ),
    ):
        registry.register(definition)

    return registry
