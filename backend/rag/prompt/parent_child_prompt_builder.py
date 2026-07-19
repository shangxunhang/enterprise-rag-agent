# -*- coding: utf-8 -*-
# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：PromptBuildResult、ParentChildPromptBuilder。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
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


# 阅读注释（类）：封装 提示词 build 结果，集中封装相关状态、依赖和行为。
@dataclass
class PromptBuildResult:
    """Prompt build result for downstream capture / LLM call."""

    prompt: str
    prompt_id: str
    prompt_version: str
    template: str
    variables: Dict[str, Any]

    # 阅读注释（函数）：把 PromptBuildResult 转换为 字典。
    def to_dict(self) -> Dict[str, Any]:
        """把 PromptBuildResult 转换为 字典。

        返回:
            Dict[str, Any]
        """
        return {
            "prompt": self.prompt,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "template": self.template,
            "variables": self.variables,
        }


# 阅读注释（类）：封装 父块 子块 提示词 builder，集中封装相关状态、依赖和行为。
class ParentChildPromptBuilder:
    """Build strict QA prompt from packed parent-child RAG context."""

    # 阅读注释（函数）：初始化 ParentChildPromptBuilder，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        prompt_id: str = "parent_child_strict_qa",
        prompt_version: str = "v1.0",
        template: Optional[str] = None,
    ):
        """初始化 ParentChildPromptBuilder，保存运行所需的依赖、配置或状态。

        参数:
            prompt_id: 提示词 标识，具体约束请结合类型标注和调用方确认。
            prompt_version: 提示词 版本，具体约束请结合类型标注和调用方确认。
            template: template，具体约束请结合类型标注和调用方确认。

        返回:
            未显式标注；请结合调用方和实际返回语句理解。
        """
        self.prompt_id = prompt_id
        self.prompt_version = prompt_version
        self.template = template or DEFAULT_PARENT_CHILD_STRICT_QA_TEMPLATE

    # 阅读注释（函数）：格式化 引用 标识集合。
    @staticmethod
    def _format_citation_ids(citations: Optional[List[Dict[str, Any]]]) -> List[str]:
        """格式化 引用 标识集合。

        参数:
            citations: 引用信息集合。

        返回:
            List[str]

        阅读提示:
            主要直接调用：citation.get, ids.append。
        """
        if not citations:
            return []
        ids: List[str] = []
        for citation in citations:
            rank = citation.get("context_rank")
            if rank is not None:
                ids.append(f"资料 {rank}")
        return ids

    # 阅读注释（函数）：构建 ParentChildPromptBuilder。
    def build(
        self,
        *,
        query: str,
        packed_context: str,
        citations: Optional[List[Dict[str, Any]]] = None,
        extra_variables: Optional[Dict[str, Any]] = None,
    ) -> PromptBuildResult:
        """构建 ParentChildPromptBuilder。

        参数:
            query: 当前检索或生成查询。
            packed_context: packed 上下文，具体约束请结合类型标注和调用方确认。
            citations: 引用信息集合。
            extra_variables: extra variables，具体约束请结合类型标注和调用方确认。

        返回:
            PromptBuildResult

        阅读提示:
            主要直接调用：strip, str, ValueError, self._format_citation_ids, variables.update, self.template.format, PromptBuildResult。
        """
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
