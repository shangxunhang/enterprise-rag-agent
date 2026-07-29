# -*- coding: utf-8 -*-
"""对父子块 RAG 证据执行本地、确定性的质量判断。

Judge 只检查检索上下文是否“看起来足以进入生成”：结果存在性、唯一父块数、分数
归一化、头部分数、分数间隔、来源覆盖、关键词覆盖和 query/content 词法重叠。
它不调用 LLM，也不判断事实真假、声明级蕴含或最终答案忠实度。输出结构兼容早期
CRAG/C-RAG 流程，可作为“接受、补充检索或拒绝”的低成本路由信号。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence


class RagQualityJudge:
    """基于可解释规则评估候选证据质量。

    Attributes:
        accept_threshold: 综合分数达到该值时判定 ``accept``。
        retry_threshold: 低于 accept 但达到该值时判定 ``retry``。
        min_result_count: 希望至少获得的结果数量。
        min_unique_parent_count: 希望至少覆盖的唯一父块数。
        top_score_good: 头部分数达到该值视为强信号。
        top_score_min: 头部分数低于该值视为明显不足。
        min_score_margin: top1 与 top2 最小理想间隔。
        keyword_hit_good: 关键词覆盖达到该值视为良好。
        lexical_overlap_good: query/content 词法重叠达到该值视为良好。
    """

    _TOKEN_PATTERN = re.compile(
        r"[A-Za-z0-9_]+|[\u4e00-\u9fff]",
        re.UNICODE,
    )

    def __init__(
        self,
        *,
        accept_threshold: float = 0.65,
        retry_threshold: float = 0.35,
        min_result_count: int = 2,
        min_unique_parent_count: int = 1,
        top_score_good: float = 0.55,
        top_score_min: float = 0.15,
        min_score_margin: float = 0.03,
        keyword_hit_good: float = 0.5,
        lexical_overlap_good: float = 0.3,
    ) -> None:
        """保存判定阈值并规范化计数下限。

        计数参数至少为 1，避免配置为零后让“无结果”被错误视为数量充足。分数阈值
        保留调用方原值，便于实验显式调整；最终组件分数会统一裁剪到 ``[0, 1]``。
        """

        self.accept_threshold = float(accept_threshold)
        self.retry_threshold = float(retry_threshold)
        self.min_result_count = max(1, int(min_result_count))
        self.min_unique_parent_count = max(
            1,
            int(min_unique_parent_count),
        )
        self.top_score_good = float(top_score_good)
        self.top_score_min = float(top_score_min)
        self.min_score_margin = float(min_score_margin)
        self.keyword_hit_good = float(keyword_hit_good)
        self.lexical_overlap_good = float(lexical_overlap_good)

    @staticmethod
    def _clip01(value: float) -> float:
        """把数值裁剪到 ``[0, 1]``。"""

        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _safe_float(
        value: Any,
        default: Optional[float] = None,
    ) -> Optional[float]:
        """尽力把任意值转换为有限浮点数。

        ``None``、转换失败、NaN 和无穷值都返回默认值，防止异常分数污染排序和综合
        计算。
        """

        try:
            if value is None:
                return default
            converted = float(value)
            if math.isnan(converted) or math.isinf(converted):
                return default
            return converted
        except Exception:
            return default

    @classmethod
    def _tokenize(cls, text: str) -> List[str]:
        """把文本切分为英文/数字连续词和中文单字。"""

        if not text:
            return []
        return cls._TOKEN_PATTERN.findall(str(text).lower())

    @classmethod
    def _lexical_overlap(cls, query: str, text: str) -> float:
        """计算查询 Token 集合在上下文 Token 集合中的覆盖比例。"""

        # 第一步：对 query 和 context 做相同词法切分并去重。
        query_tokens = set(cls._tokenize(query))
        if not query_tokens:
            return 0.0
        text_tokens = set(cls._tokenize(text))
        if not text_tokens:
            return 0.0

        # 第二步：以查询 Token 数为分母，表达上下文覆盖了多少查询词。
        return len(query_tokens & text_tokens) / len(query_tokens)

    @staticmethod
    def _get_context_text(
        item: Dict[str, Any],
        context_text_field: str = "text",
    ) -> str:
        """按优先级提取一条结果的上下文文本。

        优先使用调用方指定字段，其次 ``parent_text``、``text``、``child_text``。
        重复字段通过集合去重，避免同一个键被检查两次。
        """

        for field in (
            context_text_field,
            "parent_text",
            "text",
            "child_text",
        ):
            if field in item and item.get(field):
                return str(item.get(field))
        return ""

    @staticmethod
    def _get_parent_id(item: Dict[str, Any]) -> str:
        """提取父块身份，缺失时回退普通 chunk_id。"""

        return str(
            item.get("parent_chunk_id")
            or item.get("chunk_id")
            or ""
        )

    @staticmethod
    def _get_doc_id(item: Dict[str, Any]) -> str:
        """提取文档身份，缺失时返回空字符串。"""

        return str(item.get("doc_id") or "")

    @classmethod
    def _resolve_raw_score(
        cls,
        item: Dict[str, Any],
    ) -> tuple[Optional[float], str]:
        """选择一条结果中最有代表性的分数字段。

        优先级：rerank_score > score > fusion_score > dense_score > keyword_score。
        Returns 中同时返回字段名，便于后续按 score_type 解释数值方向。
        """

        candidates = [
            ("rerank_score", item.get("rerank_score")),
            ("score", item.get("score")),
            ("fusion_score", item.get("fusion_score")),
            ("dense_score", item.get("dense_score")),
            ("keyword_score", item.get("keyword_score")),
        ]
        for name, value in candidates:
            converted = cls._safe_float(value)
            if converted is not None:
                return converted, name
        return None, "none"

    @classmethod
    def _normalize_score(
        cls,
        value: Optional[float],
        score_type: Optional[str],
    ) -> float:
        """把不同检索/重排分数近似映射到 ``[0, 1]``。

        规则只用于质量路由，不用于重新排序：
        - distance 类分数越小越好，使用 ``1 / (1 + max(value, 0))``；
        - 已知相似度/归一化分数直接裁剪；
        - logit 或超出区间值使用 Sigmoid；
        - 缺失值映射为 0。
        """

        if value is None:
            return 0.0

        normalized_type = str(score_type or "").lower()

        # 距离越小越好，转为单调递减的相似度代理。
        if "distance" in normalized_type:
            return cls._clip01(1.0 / (1.0 + max(0.0, value)))

        # 明确声明 similarity/normalized/probability 的值按已归一化处理。
        if any(
            marker in normalized_type
            for marker in (
                "similarity",
                "normalized",
                "probability",
                "cosine",
                "rrf",
            )
        ):
            return cls._clip01(value)

        # Cross-Encoder logit 或任意区间外值通过 Sigmoid 映射。
        if "logit" in normalized_type or value < 0 or value > 1:
            return 1.0 / (1.0 + math.exp(-value))

        return cls._clip01(value)

    def _normalized_item_score(
        self,
        item: Dict[str, Any],
    ) -> tuple[float, Optional[float], str, str]:
        """返回归一化分数、原始分数、字段名和分数类型。"""

        raw_score, score_field = self._resolve_raw_score(item)

        # rerank_score 使用 rerank_score_type，其余字段使用 score_type。
        if score_field == "rerank_score":
            score_type = str(
                item.get("rerank_score_type")
                or "cross_encoder_logit"
            )
        else:
            score_type = str(
                item.get("score_type") or score_field
            )

        normalized_score = self._normalize_score(
            raw_score,
            score_type,
        )
        return (
            normalized_score,
            raw_score,
            score_field,
            score_type,
        )

    @staticmethod
    def _keyword_hit_ratio(
        keywords: Sequence[str],
        text: str,
    ) -> float:
        """计算关键词在文本中的大小写不敏感字面覆盖率。"""

        cleaned = [
            str(keyword).strip()
            for keyword in keywords
            if str(keyword).strip()
        ]
        if not cleaned:
            return 0.0

        lowered = str(text or "").lower()
        hit_count = sum(
            1
            for keyword in cleaned
            if keyword.lower() in lowered
        )
        return hit_count / len(cleaned)

    def evaluate(
        self,
        *,
        query: str,
        results: Sequence[Dict[str, Any]],
        expected_keywords: Optional[Sequence[str]] = None,
        context_text_field: str = "text",
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        """评估候选证据并返回可解释质量报告。

        Args:
            query: 当前检索查询。
            results: 已排序候选结果。
            expected_keywords: 可选黄金关键词；存在时参与质量分数和原因码。
            context_text_field: 优先读取的上下文字段。
            top_k: 可选评估截断窗口；非正值视为不截断。

        Returns:
            包含 decision、quality_score、component_scores、reason_codes、统计、
            头部结果诊断和配置快照的字典。
        """

        # 第一步：只保留字典结果，并按 top_k 截断当前排序。
        evaluated_results = [
            item
            for item in results
            if isinstance(item, dict)
        ]
        if top_k is not None and top_k > 0:
            evaluated_results = evaluated_results[: int(top_k)]

        # 第二步：无结果是明确拒绝，不进入加权质量公式。
        if not evaluated_results:
            return {
                "schema_version": "rag_quality_report_v1",
                "decision": "reject",
                "quality_score": 0.0,
                "component_scores": {
                    "result_count": 0.0,
                    "unique_parent_count": 0.0,
                    "top_score": 0.0,
                    "score_margin": 0.0,
                    "doc_coverage": 0.0,
                    "keyword_hit": 0.0,
                    "lexical_overlap": 0.0,
                },
                "reason_codes": ["no_results"],
                "result_count": 0,
                "unique_parent_count": 0,
                "unique_doc_count": 0,
                "top_score": 0.0,
                "second_score": 0.0,
                "score_margin": 0.0,
                "keyword_hit_ratio": 0.0,
                "lexical_overlap_ratio": 0.0,
                "head_results": [],
                "judge_config": self._config_dict(),
            }

        normalized_scores: List[float] = []
        head_results: List[Dict[str, Any]] = []
        context_parts: List[str] = []
        parent_ids: List[str] = []
        doc_ids: List[str] = []

        # 第三步：逐结果提取分数、身份、上下文和头部诊断。
        for rank, item in enumerate(evaluated_results, start=1):
            (
                normalized_score,
                raw_score,
                score_field,
                score_type,
            ) = self._normalized_item_score(item)
            normalized_scores.append(normalized_score)

            parent_id = self._get_parent_id(item)
            doc_id = self._get_doc_id(item)
            context_text = self._get_context_text(
                item,
                context_text_field,
            )

            if parent_id:
                parent_ids.append(parent_id)
            if doc_id:
                doc_ids.append(doc_id)
            if context_text:
                context_parts.append(context_text)

            # 只保留前五条诊断，防止报告随候选数量无限增长。
            if rank <= 5:
                head_results.append(
                    {
                        "rank": rank,
                        "doc_id": doc_id,
                        "parent_chunk_id": parent_id,
                        "child_chunk_id": str(
                            item.get("child_chunk_id")
                            or item.get("chunk_id")
                            or ""
                        ),
                        "raw_score": raw_score,
                        "score_field": score_field,
                        "score_type": score_type,
                        "normalized_score": normalized_score,
                    }
                )

        # 第四步：计算数量、来源覆盖和文本信号。
        result_count = len(evaluated_results)
        unique_parent_count = len(set(parent_ids))
        unique_doc_count = len(set(doc_ids))
        context_text = "\n".join(context_parts)

        top_score = normalized_scores[0] if normalized_scores else 0.0
        second_score = (
            normalized_scores[1]
            if len(normalized_scores) > 1
            else 0.0
        )
        score_margin = max(0.0, top_score - second_score)

        keyword_hit_ratio = self._keyword_hit_ratio(
            expected_keywords or [],
            context_text,
        )
        lexical_overlap_ratio = self._lexical_overlap(
            query,
            context_text,
        )

        # 第五步：把各原始信号映射为统一组件分数。
        result_count_score = self._clip01(
            result_count / self.min_result_count
        )
        unique_parent_score = self._clip01(
            unique_parent_count / self.min_unique_parent_count
        )
        top_score_component = self._clip01(
            top_score / max(self.top_score_good, 1e-9)
        )
        margin_component = self._clip01(
            score_margin / max(self.min_score_margin, 1e-9)
        )

        # 文档覆盖是弱信号：至少一个文档得 0.5，两个及以上得满分。
        doc_coverage_score = self._clip01(
            unique_doc_count / 2.0
        )

        # 没有黄金关键词时，该组件使用中性 0.5，不奖励也不完全惩罚。
        keyword_component = (
            self._clip01(
                keyword_hit_ratio
                / max(self.keyword_hit_good, 1e-9)
            )
            if expected_keywords
            else 0.5
        )
        lexical_component = self._clip01(
            lexical_overlap_ratio
            / max(self.lexical_overlap_good, 1e-9)
        )

        component_scores = {
            "result_count": result_count_score,
            "unique_parent_count": unique_parent_score,
            "top_score": top_score_component,
            "score_margin": margin_component,
            "doc_coverage": doc_coverage_score,
            "keyword_hit": keyword_component,
            "lexical_overlap": lexical_component,
        }

        # 第六步：按固定权重组合质量分数。头部分数权重最高，文本相关性次之。
        quality_score = self._clip01(
            0.12 * result_count_score
            + 0.10 * unique_parent_score
            + 0.28 * top_score_component
            + 0.10 * margin_component
            + 0.08 * doc_coverage_score
            + 0.14 * keyword_component
            + 0.18 * lexical_component
        )

        # 第七步：生成可解释原因码，便于 CRAG 纠错策略决定补检索方向。
        reason_codes: List[str] = []
        if result_count < self.min_result_count:
            reason_codes.append("insufficient_result_count")
        if unique_parent_count < self.min_unique_parent_count:
            reason_codes.append("insufficient_parent_coverage")
        if top_score < self.top_score_min:
            reason_codes.append("top_score_too_low")
        elif top_score < self.top_score_good:
            reason_codes.append("top_score_weak")
        if (
            len(normalized_scores) > 1
            and score_margin < self.min_score_margin
        ):
            reason_codes.append("score_margin_small")
        if expected_keywords and keyword_hit_ratio < self.keyword_hit_good:
            reason_codes.append("keyword_coverage_low")
        if lexical_overlap_ratio < self.lexical_overlap_good:
            reason_codes.append("lexical_overlap_low")
        if unique_doc_count <= 1:
            reason_codes.append("source_diversity_low")

        # 第八步：先按综合阈值选择决策，再应用明显不足的硬降级规则。
        if quality_score >= self.accept_threshold:
            decision = "accept"
        elif quality_score >= self.retry_threshold:
            decision = "retry"
        else:
            decision = "reject"

        # 无论综合分多高，头部分数明显过低都不能直接接受。
        if top_score < self.top_score_min:
            decision = "reject"
        # 有结果但证据不足时优先 retry，而不是直接拒绝整个查询。
        elif decision == "reject" and result_count > 0:
            decision = "retry"

        # 第九步：返回完整可解释报告。
        return {
            "schema_version": "rag_quality_report_v1",
            "decision": decision,
            "quality_score": quality_score,
            "component_scores": component_scores,
            "reason_codes": reason_codes,
            "result_count": result_count,
            "unique_parent_count": unique_parent_count,
            "unique_doc_count": unique_doc_count,
            "top_score": top_score,
            "second_score": second_score,
            "score_margin": score_margin,
            "keyword_hit_ratio": keyword_hit_ratio,
            "lexical_overlap_ratio": lexical_overlap_ratio,
            "head_results": head_results,
            "judge_config": self._config_dict(),
        }

    def _config_dict(self) -> Dict[str, Any]:
        """返回当前 Judge 阈值快照，供 Trace 和评测复现。"""

        return {
            "accept_threshold": self.accept_threshold,
            "retry_threshold": self.retry_threshold,
            "min_result_count": self.min_result_count,
            "min_unique_parent_count": self.min_unique_parent_count,
            "top_score_good": self.top_score_good,
            "top_score_min": self.top_score_min,
            "min_score_margin": self.min_score_margin,
            "keyword_hit_good": self.keyword_hit_good,
            "lexical_overlap_good": self.lexical_overlap_good,
        }


# Historical import compatibility for callers that used the combined judge
# module before retrieval and generation quality responsibilities were split.
from rag.judge.crag_judge import CRAGJudge, CRAGJudgeResult
