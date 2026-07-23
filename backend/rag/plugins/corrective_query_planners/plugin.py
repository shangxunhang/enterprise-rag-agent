"""Corrective query planning performed only after the correction gate opens."""

from __future__ import annotations

import json
import re
from typing import Any

from core.runtime.execution_control import WorkflowExecutionCancelled
from model_gateway.call_boundary import ModelCallBudgetExceeded
from rag.ports.quality import CorrectiveQueryPlan, EvidenceAssessment


def _dedup(items: list[str], *, original_query: str) -> list[str]:
    original = str(original_query or "").strip().lower()
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = re.sub(r"^\s*[-*\d).、：:]+\s*", "", str(item or "")).strip()
        key = text.lower()
        if len(text) < 4 or key == original or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _extract_queries(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        try:
            payload = json.loads(match.group(0)) if match else None
        except Exception:
            payload = None
    values = payload.get("queries") if isinstance(payload, dict) else payload
    if isinstance(values, list):
        output: list[str] = []
        for item in values:
            if isinstance(item, str):
                output.append(item)
            elif isinstance(item, dict):
                output.append(
                    str(
                        item.get("query")
                        or item.get("text")
                        or item.get("rewritten_query")
                        or ""
                    )
                )
        return output
    return [line for line in re.split(r"[\r\n；;]+", text) if line.strip()]


class SectionGapCorrectiveQueryPlanner:
    """Plan bounded KB queries from assessment gaps and document context."""

    def __init__(
        self,
        *,
        build_context: Any = None,
        max_queries: int = 2,
        use_llm: bool | None = None,
        fallback_to_deterministic: bool = True,
        merge_original_candidates: bool = True,
    ) -> None:
        context = build_context if isinstance(build_context, dict) else {}
        llm_enabled = bool(context.get("enable_quality_llm", False))
        self.use_llm = llm_enabled if use_llm is None else bool(use_llm)
        self.llm_generator = context.get("quality_llm_generator")
        self.generation_params = dict(context.get("quality_generation_params") or {})
        self.max_queries = max(1, int(max_queries))
        self.fallback_to_deterministic = bool(fallback_to_deterministic)
        self.merge_original_candidates = bool(merge_original_candidates)

    @staticmethod
    def _document_context(runtime_context: dict[str, Any] | None) -> dict[str, Any]:
        runtime = dict(runtime_context or {})
        request = runtime.get("request_context")
        request = request if isinstance(request, dict) else runtime
        document = request.get("document_context")
        return document if isinstance(document, dict) else request

    def _fallback(
        self,
        query: str,
        document: dict[str, Any],
    ) -> list[str]:
        title = str(
            document.get("document_title")
            or document.get("project_name")
            or query
        ).strip()
        sections = [
            str(item).strip()
            for item in list(
                document.get("citation_required_sections")
                or document.get("required_sections")
                or []
            )
            if str(item).strip()
        ]
        if sections:
            return _dedup(
                [
                    f"{title} {' '.join(group)} 事实依据 具体要求 实施约束"
                    for group in [sections[index:: self.max_queries] for index in range(self.max_queries)]
                    if group
                ],
                original_query=query,
            )[: self.max_queries]
        return _dedup(
            [
                f"{title} 关键事实 具体要求 实施依据",
                f"{title} 技术细节 业务流程 验收标准 风险约束",
            ],
            original_query=query,
        )[: self.max_queries]

    def plan(
        self,
        *,
        query: str,
        assessment: EvidenceAssessment,
        runtime_context: dict[str, Any] | None = None,
    ) -> CorrectiveQueryPlan:
        document = self._document_context(runtime_context)
        fallback = self._fallback(query, document)
        queries: list[str] = []
        method = "deterministic"
        raw_output = ""
        if self.use_llm and self.llm_generator is not None:
            prompt = (
                "当前知识库证据不足。请只输出 JSON："
                f'{{"queries": [最多{self.max_queries}条字符串]}}。'
                "查询必须具体、互补，不得回答问题或引入新事实。\n"
                f"原问题：{query}\n"
                f"证据不足原因：{assessment.reason}\n"
                f"文档约束：{json.dumps(document, ensure_ascii=False)}"
            )
            params = dict(self.generation_params)
            params.setdefault("max_new_tokens", max(160, self.max_queries * 96))
            params.setdefault("temperature", 0.0)
            params.setdefault("do_sample", False)
            try:
                raw_output = str(
                    self.llm_generator.generate(
                        prompt,
                        call_purpose="rag_corrective_query",
                        runtime_context=runtime_context,
                        **params,
                    )
                    or ""
                )
                queries = _dedup(_extract_queries(raw_output), original_query=query)
                method = "llm_with_deterministic_completion"
            except (ModelCallBudgetExceeded, WorkflowExecutionCancelled):
                raise
            except Exception:
                if not self.fallback_to_deterministic:
                    raise
        queries = _dedup([*queries, *fallback], original_query=query)[: self.max_queries]
        return CorrectiveQueryPlan(
            queries=tuple(queries),
            reason=assessment.reason,
            merge_original_candidates=self.merge_original_candidates,
            metadata={
                "method": method,
                "raw_output": raw_output,
                "document_context": document,
            },
        )

    def execution_metadata(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": "section_gap_corrective_query_planner",
            "max_queries": self.max_queries,
            "use_llm": self.use_llm,
            "llm_available": self.llm_generator is not None,
            "fallback_to_deterministic": self.fallback_to_deterministic,
        }
