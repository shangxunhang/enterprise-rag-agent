"""Configuration-driven retrieval evidence grader plugins."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from rag.judge.rag_quality_judge import CRAGJudge
from rag.ports.quality import EvidenceCorrectionPlan, EvidenceGradeOutput


def _context(build_context: Any) -> dict[str, Any]:
    return build_context if isinstance(build_context, dict) else {}


def _dedup_queries(items: list[str], *, original_query: str = "") -> list[str]:
    original_key = str(original_query or "").strip().lower()
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = str(item or "").strip()
        text = re.sub(r"^\s*[-*•\d).、：:]+\s*", "", text).strip()
        text = text.strip(" \t\r\n'\"`，。；;")
        if len(text) < 4:
            continue
        key = text.lower()
        if key == original_key or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _coerce_query_item(item: Any) -> str:
    """Extract a query string from common LLM JSON shapes.

    Corrective-query models may return either a string array or an array of
    objects such as ``{"query": "..."}``. Treating the latter as
    ``str(dict)`` pollutes both dense and keyword retrieval with braces and
    field names, so query extraction must be structural rather than textual.
    """

    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("query", "text", "rewritten_query", "value"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""
    return "" if item is None else str(item)


def _extract_query_list(raw_text: str) -> list[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
            except Exception:
                payload = None
        else:
            payload = None
    if isinstance(payload, dict):
        value = payload.get("queries") or payload.get("rewritten_queries")
        if isinstance(value, list):
            return [
                query
                for item in value
                if (query := _coerce_query_item(item).strip())
            ]
        if isinstance(value, (str, dict)):
            query = _coerce_query_item(value).strip()
            return [query] if query else []
    if isinstance(payload, list):
        return [
            query
            for item in payload
            if (query := _coerce_query_item(item).strip())
        ]

    lines = [item for item in re.split(r"[\r\n；;]+", text) if str(item).strip()]
    return [str(item) for item in lines]


_COMMAND_PREFIX_RE = re.compile(
    r"^(?:请|请你|请根据|根据|基于)?(?:现有|已有|相关|以下|上述)?(?:资料|材料|文档|知识库)?"
    r"(?:生成|编制|撰写|输出|制定|设计|形成)?",
    flags=re.IGNORECASE,
)


def _compact_query(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "").lower(), flags=re.UNICODE)


def _subject_from_query(query: str) -> str:
    text = str(query or "").strip()
    cleaned = _COMMAND_PREFIX_RE.sub("", text).strip(" ：:，,。")
    return cleaned or text


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _planner_context(runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_context or {})
    request_context = runtime.get("request_context")
    if not isinstance(request_context, dict):
        request_context = {}
    document_context = request_context.get("document_context")
    if not isinstance(document_context, dict):
        document_context = request_context
    required_sections = _string_list(document_context.get("required_sections"))
    citation_sections = _string_list(
        document_context.get("citation_required_sections")
    )
    return {
        "task_type": str(request_context.get("task_type") or ""),
        "project_name": str(document_context.get("project_name") or ""),
        "document_title": str(document_context.get("document_title") or ""),
        "required_sections": required_sections,
        "citation_required_sections": citation_sections,
        "target_documents": _string_list(document_context.get("target_documents")),
        "target_templates": _string_list(document_context.get("target_templates")),
    }


def _query_adds_planning_facet(
    candidate: str,
    *,
    original_query: str,
    planning_context: dict[str, Any],
) -> bool:
    original_compact = _compact_query(original_query)
    candidate_compact = _compact_query(candidate)
    if not candidate_compact:
        return False
    facets = [
        *list(planning_context.get("citation_required_sections") or []),
        *list(planning_context.get("required_sections") or []),
    ]
    return any(
        _compact_query(facet)
        and _compact_query(facet) in candidate_compact
        and _compact_query(facet) not in original_compact
        for facet in facets
    )


def _is_trivial_rewrite(
    candidate: str,
    *,
    original_query: str,
    planning_context: dict[str, Any],
) -> bool:
    original_compact = _compact_query(original_query)
    candidate_compact = _compact_query(candidate)
    if not original_compact or not candidate_compact:
        return True
    if candidate_compact == original_compact:
        return True
    if _query_adds_planning_facet(
        candidate,
        original_query=original_query,
        planning_context=planning_context,
    ):
        return False
    # The failure observed in the real trace was a simple substring deletion:
    # "根据资料生成 X" -> "X". Reject that as it does not create a new
    # retrieval direction.
    if candidate_compact in original_compact or original_compact in candidate_compact:
        return True
    original_chars = set(original_compact)
    candidate_chars = set(candidate_compact)
    union = original_chars | candidate_chars
    similarity = len(original_chars & candidate_chars) / max(1, len(union))
    return similarity >= 0.86


def _section_groups(
    planning_context: dict[str, Any],
    *,
    max_groups: int,
) -> list[list[str]]:
    priority = list(planning_context.get("citation_required_sections") or [])
    remaining = [
        item
        for item in list(planning_context.get("required_sections") or [])
        if item not in priority
    ]
    sections = [*priority, *remaining]
    if not sections:
        return []
    group_count = min(max(1, int(max_groups)), len(sections))
    groups: list[list[str]] = [[] for _ in range(group_count)]
    for index, section in enumerate(sections):
        groups[index % group_count].append(section)
    return [group for group in groups if group]


class CRAGLiteEvidenceGraderPlugin:
    """Evaluate and optionally filter reranked candidates with C-RAG-lite."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        max_judge_chunks: int = 8,
        drop_irrelevant: bool = True,
        keep_at_least: int = 1,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
        noise_terms: Iterable[str] | None = None,
        ranking_policy: str = "demotion_only",
    ) -> None:
        context = _context(build_context)
        context_llm_enabled = bool(context.get("enable_quality_llm", False))
        self.use_llm = context_llm_enabled if use_llm is None else bool(use_llm)
        self.max_judge_chunks = max(1, int(max_judge_chunks))
        self.drop_irrelevant = bool(drop_irrelevant)
        self.keep_at_least = max(1, int(keep_at_least))
        self.fallback_to_deterministic = bool(fallback_to_deterministic)
        self.noise_terms = tuple(str(item) for item in (noise_terms or ()))
        self.ranking_policy = str(ranking_policy or "demotion_only").strip().lower()
        self.llm_generator = context.get("quality_llm_generator")
        self.generation_params = dict(context.get("quality_generation_params") or {})
        self.backend = CRAGJudge(
            llm_generator=self.llm_generator,
            use_llm=self.use_llm,
            generation_params=self.generation_params,
            fallback_to_deterministic=self.fallback_to_deterministic,
            noise_terms=self.noise_terms,
            ranking_policy=self.ranking_policy,
        )

    def grade(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> EvidenceGradeOutput:
        del runtime_context
        filtered, report = self.backend.evaluate_and_filter(
            query=query,
            results=list(results or []),
            max_judge_chunks=self.max_judge_chunks,
            drop_irrelevant=self.drop_irrelevant,
            keep_at_least=self.keep_at_least,
        )
        return EvidenceGradeOutput(
            results=filtered,
            report=report.to_dict(),
            correction=EvidenceCorrectionPlan(required=False),
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": "crag_lite",
            "corrective_retrieval_enabled": False,
            "max_judge_chunks": self.max_judge_chunks,
            "drop_irrelevant": self.drop_irrelevant,
            "keep_at_least": self.keep_at_least,
            "use_llm": self.use_llm,
            "llm_available": self.llm_generator is not None,
            "fallback_to_deterministic": self.fallback_to_deterministic,
            "noise_terms": list(self.noise_terms),
            "ranking_policy": self.ranking_policy,
        }


class CRAGCorrectiveEvidenceGraderPlugin(CRAGLiteEvidenceGraderPlugin):
    """C-RAG grader that can request one bounded internal re-retrieval loop.

    It judges the reranked evidence first. When confidence is below the
    configured threshold, it emits a generic ``EvidenceCorrectionPlan``. The
    retrieval pipeline then executes the same configured retrievers, fusion,
    parent-child enrichment and reranker for the proposed queries before
    grading the merged evidence again.

    This is a complete *knowledge-base internal* corrective retrieval loop. It
    intentionally does not perform external web search.
    """

    def __init__(
        self,
        *,
        build_context: Any = None,
        max_judge_chunks: int = 8,
        drop_irrelevant: bool = True,
        keep_at_least: int = 1,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
        noise_terms: Iterable[str] | None = None,
        ranking_policy: str = "demotion_only",
        confidence_threshold: float = 0.55,
        min_relevant_chunks: int = 1,
        max_correction_queries: int = 2,
        max_correction_rounds: int = 1,
        merge_original_candidates: bool = True,
        query_planner: str = "section_gap_aware_v1",
        reject_trivial_rewrites: bool = True,
    ) -> None:
        super().__init__(
            build_context=build_context,
            max_judge_chunks=max_judge_chunks,
            drop_irrelevant=drop_irrelevant,
            keep_at_least=keep_at_least,
            use_llm=use_llm,
            fallback_to_deterministic=fallback_to_deterministic,
            noise_terms=noise_terms,
            ranking_policy=ranking_policy,
        )
        self.confidence_threshold = max(0.0, min(1.0, float(confidence_threshold)))
        self.min_relevant_chunks = max(0, int(min_relevant_chunks))
        self.max_correction_queries = max(1, int(max_correction_queries))
        self.max_correction_rounds = max(0, int(max_correction_rounds))
        self.merge_original_candidates = bool(merge_original_candidates)
        normalized_planner = str(query_planner or "section_gap_aware_v1").strip().lower()
        if normalized_planner not in {"section_gap_aware_v1", "generic_v1"}:
            raise ValueError(
                "query_planner must be 'section_gap_aware_v1' or 'generic_v1'"
            )
        self.query_planner = normalized_planner
        self.reject_trivial_rewrites = bool(reject_trivial_rewrites)

    @staticmethod
    def _relevant_count(report: dict[str, Any]) -> int:
        return sum(
            1
            for item in list(report.get("chunk_judgements") or [])
            if str(item.get("relevance_label") or "") == "relevant"
        )

    def _needs_correction(self, report: dict[str, Any]) -> tuple[bool, str]:
        confidence = float(report.get("retrieval_confidence") or 0.0)
        relevant_count = self._relevant_count(report)
        reasons: list[str] = []
        if confidence < self.confidence_threshold:
            reasons.append(
                f"retrieval_confidence {confidence:.4f} below "
                f"threshold {self.confidence_threshold:.4f}"
            )
        if relevant_count < self.min_relevant_chunks:
            reasons.append(
                f"relevant_chunk_count {relevant_count} below "
                f"minimum {self.min_relevant_chunks}"
            )
        return bool(reasons), "; ".join(reasons)

    def _fallback_queries(
        self,
        query: str,
        *,
        planning_context: dict[str, Any] | None = None,
    ) -> list[str]:
        context = (
            dict(planning_context or {})
            if self.query_planner == "section_gap_aware_v1"
            else {}
        )
        subject = (
            str(context.get("document_title") or "").strip()
            or str(context.get("project_name") or "").strip()
            or _subject_from_query(query)
        )
        groups = _section_groups(
            context,
            max_groups=self.max_correction_queries,
        )
        candidates: list[str] = []
        for group in groups:
            candidates.append(
                f"{subject} {' '.join(group)} 事实依据 具体要求 实施约束"
            )
        if not candidates:
            candidates = [
                f"{subject} 关键事实 具体要求 实施依据",
                f"{subject} 技术细节 业务流程 验收标准 风险约束",
            ]
        return _dedup_queries(candidates, original_query=query)[
            : self.max_correction_queries
        ]

    def _llm_queries(
        self,
        *,
        query: str,
        report: dict[str, Any],
        planning_context: dict[str, Any] | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        if not self.use_llm or self.llm_generator is None:
            return [], {
                "method": "deterministic_fallback",
                "fallback_used": True,
                "fallback_reason": "corrective query LLM unavailable",
            }
        planning = dict(planning_context or {})
        judgements = []
        for item in list(report.get("chunk_judgements") or [])[: self.max_judge_chunks]:
            judgements.append(
                {
                    "label": item.get("relevance_label"),
                    "score": item.get("score"),
                    "reason": item.get("reason"),
                }
            )
        system_prompt = (
            "你是企业级 C-RAG 纠错检索查询生成器。只输出合法 JSON，"
            "不得回答用户问题。"
        )
        prompt = (
            "现有知识库检索证据质量不足。请根据原问题和证据评估原因，"
            "生成更具体、可检索、互补的纠错查询。不得引入原问题中不存在的"
            "具体事实。对于文档生成任务，查询必须分别覆盖不同的必需章节或"
            "引用证据缺口，禁止只删除原问题中的命令词或做同义改写。\n"
            f"输出 JSON：{{\"queries\": [最多{self.max_correction_queries}条字符串查询], "
            "\"reason\": \"简短原因\"}}。queries 必须是字符串数组，"
            "禁止输出 {{\"query\": \"...\"}} 对象。\n\n"
            f"原问题：{query}\n"
            f"文档与章节约束：{json.dumps(planning, ensure_ascii=False)}\n"
            f"当前检索置信度：{report.get('retrieval_confidence')}\n"
            f"证据判断：{json.dumps(judgements, ensure_ascii=False)}"
        )
        params = dict(self.generation_params)
        params.setdefault("max_new_tokens", max(160, self.max_correction_queries * 96))
        params.setdefault("temperature", 0.0)
        params.setdefault("top_p", 0.9)
        params.setdefault("do_sample", False)
        try:
            try:
                raw = self.llm_generator.generate(
                    prompt,
                    system_prompt=system_prompt,
                    **params,
                )
            except TypeError:
                raw = self.llm_generator.generate(
                    f"{system_prompt}\n\n{prompt}",
                    **params,
                )
            parsed = _dedup_queries(
                _extract_query_list(str(raw or "")),
                original_query=query,
            )
            rejected_trivial = (
                [
                    item
                    for item in parsed
                    if _is_trivial_rewrite(
                        item,
                        original_query=query,
                        planning_context=planning,
                    )
                ]
                if self.reject_trivial_rewrites
                else []
            )
            accepted = [item for item in parsed if item not in rejected_trivial]
            fallback = self._fallback_queries(
                query,
                planning_context=planning,
            )
            queries = _dedup_queries(
                [*accepted, *fallback],
                original_query=query,
            )[: self.max_correction_queries]
            if queries:
                return queries, {
                    "method": "llm_with_gap_completion",
                    "raw_output": str(raw or ""),
                    "fallback_used": len(queries) > len(accepted),
                    "accepted_llm_queries": accepted,
                    "rejected_trivial_queries": rejected_trivial,
                    "planner_context": planning,
                    "generated_count": len(queries),
                }
            raise ValueError("corrective query generator returned no usable queries")
        except Exception as exc:
            if not self.fallback_to_deterministic:
                raise
            return [], {
                "method": "deterministic_fallback",
                "fallback_used": True,
                "fallback_reason": f"{exc.__class__.__name__}: {exc}",
            }

    def grade(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> EvidenceGradeOutput:
        filtered, report_obj = self.backend.evaluate_and_filter(
            query=query,
            results=list(results or []),
            max_judge_chunks=self.max_judge_chunks,
            drop_irrelevant=self.drop_irrelevant,
            keep_at_least=self.keep_at_least,
        )
        report = report_obj.to_dict()
        context = dict(runtime_context or {})
        planning = _planner_context(context)
        correction_round = max(0, int(context.get("correction_round") or 0))
        allow_correction = bool(context.get("allow_correction", True))
        required, reason = self._needs_correction(report)
        can_correct = (
            required
            and allow_correction
            and correction_round < self.max_correction_rounds
            and self.max_correction_rounds > 0
        )
        queries: list[str] = []
        generation_meta: dict[str, Any] = {
            "method": "not_requested",
            "fallback_used": False,
        }
        if can_correct:
            queries, generation_meta = self._llm_queries(
                query=query,
                report=report,
                planning_context=planning,
            )
            if not queries:
                queries = self._fallback_queries(
                    query,
                    planning_context=planning,
                )
                generation_meta = {
                    **generation_meta,
                    "method": "deterministic_fallback",
                    "fallback_used": True,
                    "generated_count": len(queries),
                    "planner_context": planning,
                }
        correction = EvidenceCorrectionPlan(
            required=bool(can_correct and queries),
            queries=queries,
            reason=reason,
            max_rounds=self.max_correction_rounds,
            merge_original_candidates=self.merge_original_candidates,
            metadata={
                "correction_round": correction_round,
                "allow_correction": allow_correction,
                "confidence_threshold": self.confidence_threshold,
                "min_relevant_chunks": self.min_relevant_chunks,
                "query_generation": generation_meta,
                "planner_context": planning,
            },
        )
        report["correction_decision"] = {
            **correction.to_dict(),
            "quality_insufficient": required,
            "relevant_chunk_count": self._relevant_count(report),
        }
        return EvidenceGradeOutput(
            results=filtered,
            report=report,
            correction=correction,
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            **super().execution_metadata(),
            "mode": "crag_corrective",
            "corrective_retrieval_enabled": True,
            "confidence_threshold": self.confidence_threshold,
            "min_relevant_chunks": self.min_relevant_chunks,
            "max_correction_queries": self.max_correction_queries,
            "max_correction_rounds": self.max_correction_rounds,
            "merge_original_candidates": self.merge_original_candidates,
            "ranking_policy": self.ranking_policy,
            "query_planner": self.query_planner,
            "reject_trivial_rewrites": self.reject_trivial_rewrites,
        }


class NoOpEvidenceGraderPlugin:
    """Explicit profile-selected pass-through evidence grader."""

    def __init__(self, *, build_context: Any = None) -> None:
        del build_context

    def grade(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        runtime_context: dict[str, Any] | None = None,
    ) -> EvidenceGradeOutput:
        del query, runtime_context
        copied = [dict(item) for item in (results or [])]
        return EvidenceGradeOutput(
            results=copied,
            report=None,
            correction=EvidenceCorrectionPlan(required=False),
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "mode": "noop",
        }
