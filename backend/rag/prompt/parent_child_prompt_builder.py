# -*- coding: utf-8 -*-
"""
rag_template/prompt/parent_child_prompt_builder.py
=================================================

P4-lite PromptBuilder for parent-child RAG.

Input:
- query
- packed_context from ContextPacker
- optional citations metadata

Output:
- prompt string
- prompt_id / prompt_version metadata

职责边界：
- 只构造 prompt，不调用 LLM。
- 不负责检索、重排、context packing、日志保存。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


DEFAULT_PARENT_CHILD_STRICT_QA_TEMPLATE = """你是一个严格基于资料回答问题的助手。
你只能使用给定资料回答问题，不能编造资料中不存在的信息。
如果资料不足，请回答“资料不足，无法确定”。

【资料】
{packed_context}

【问题】
{query}

【回答要求】
1. 先直接回答问题。
2. 只能依据【资料】作答。
3. 不要编造资料中没有的事实、数字、人物、结论。
4. 如果资料之间存在冲突，请指出冲突。
5. 回答末尾列出引用的资料编号，例如：引用：[资料 1]、[资料 2]。

【回答】
"""


@dataclass
class PromptBuildResult:
    """Prompt build result for downstream capture / LLM call."""

    prompt: str
    prompt_id: str
    prompt_version: str
    template: str
    variables: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "template": self.template,
            "variables": self.variables,
        }


class ParentChildPromptBuilder:
    """Build strict QA prompt from packed parent-child RAG context."""

    def __init__(
        self,
        *,
        prompt_id: str = "parent_child_strict_qa",
        prompt_version: str = "v1.0",
        template: Optional[str] = None,
    ):
        self.prompt_id = prompt_id
        self.prompt_version = prompt_version
        self.template = template or DEFAULT_PARENT_CHILD_STRICT_QA_TEMPLATE

    @staticmethod
    def _format_citation_ids(citations: Optional[List[Dict[str, Any]]]) -> List[str]:
        if not citations:
            return []
        ids: List[str] = []
        for citation in citations:
            rank = citation.get("context_rank")
            if rank is not None:
                ids.append(f"资料 {rank}")
        return ids

    def build(
        self,
        *,
        query: str,
        packed_context: str,
        citations: Optional[List[Dict[str, Any]]] = None,
        extra_variables: Optional[Dict[str, Any]] = None,
    ) -> PromptBuildResult:
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")
        if not packed_context or not str(packed_context).strip():
            raise ValueError("packed_context cannot be empty")

        variables: Dict[str, Any] = {
            "query": str(query),
            "packed_context": str(packed_context),
            "citation_ids": self._format_citation_ids(citations),
        }
        if extra_variables:
            variables.update(extra_variables)

        prompt = self.template.format(
            query=variables["query"],
            packed_context=variables["packed_context"],
        )

        return PromptBuildResult(
            prompt=prompt,
            prompt_id=self.prompt_id,
            prompt_version=self.prompt_version,
            template=self.template,
            variables=variables,
        )
