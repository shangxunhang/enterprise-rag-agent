# -*- coding: utf-8 -*-
"""为父子块 RAG 的已打包上下文构造严格问答 Prompt。

输入是用户查询、Context Packer 产出的最终上下文和可选引用 metadata；输出同时包含
Prompt 正文、模板身份、版本和渲染变量，供模型调用、Trace 和数据采集复用。本模块
不调用 LLM，也不负责检索、重排或上下文预算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# 默认模板要求模型只依据资料回答，并在证据不足或冲突时显式说明。
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
    """保存一次 Prompt 渲染的正文和可重复性 metadata。

    Attributes:
        prompt: 最终发送给模型的文本。
        prompt_id: 稳定模板逻辑身份。
        prompt_version: 模板内容版本。
        template: 本次实际使用的原始模板。
        variables: 渲染时使用的完整变量快照。
    """

    prompt: str
    prompt_id: str
    prompt_version: str
    template: str
    variables: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """返回可直接写入 JSON/Trace 的普通字典。"""

        # 显式列出字段，保持历史序列化键顺序和公开协议稳定。
        return {
            "prompt": self.prompt,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "template": self.template,
            "variables": self.variables,
        }


class ParentChildPromptBuilder:
    """从最终父级上下文渲染严格问答 Prompt。"""

    def __init__(
        self,
        *,
        prompt_id: str = "parent_child_strict_qa",
        prompt_version: str = "v1.0",
        template: Optional[str] = None,
    ) -> None:
        """初始化模板身份、版本和可选自定义模板。

        Args:
            prompt_id: 进入模型和采集血缘的稳定 Prompt ID。
            prompt_version: 模板版本字符串。
            template: 可选自定义模板；省略时使用默认严格问答模板。
        """

        self.prompt_id = prompt_id
        self.prompt_version = prompt_version
        self.template = (
            template or DEFAULT_PARENT_CHILD_STRICT_QA_TEMPLATE
        )

    @staticmethod
    def _format_citation_ids(
        citations: Optional[List[Dict[str, Any]]],
    ) -> List[str]:
        """把引用的 context_rank 转换为人类可读资料编号。

        Args:
            citations: Context Packer 或证据层产生的引用字典。

        Returns:
            ``资料 1`` 风格的列表。没有引用或引用缺少 context_rank 时忽略。

        当前默认模板没有直接使用 ``citation_ids`` 变量，但该字段保留在变量快照中，
        供自定义模板和后续引用验证使用。
        """

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
        """校验输入、渲染模板并返回完整构建结果。

        Args:
            query: 当前用户查询。
            packed_context: Context Packer 最终选择并渲染的证据文本。
            citations: 可选引用 metadata。
            extra_variables: 要写入变量快照的额外字段；同名字段会覆盖默认值。

        Returns:
            包含 Prompt 正文和模板血缘的 ``PromptBuildResult``。

        Raises:
            ValueError: 查询或打包上下文为空白时抛出。
            KeyError: 自定义模板要求了未提供变量时由 ``str.format`` 抛出。
        """

        # 第一步：拒绝空查询，避免生成与用户意图无关的回答。
        if not query or not str(query).strip():
            raise ValueError("query cannot be empty")

        # 第二步：严格 RAG Prompt 必须有证据上下文，不能退化为无依据生成。
        if not packed_context or not str(packed_context).strip():
            raise ValueError("packed_context cannot be empty")

        # 第三步：构造标准变量快照，包括尚未必被默认模板消费的 citation_ids。
        variables: Dict[str, Any] = {
            "query": str(query),
            "packed_context": str(packed_context),
            "citation_ids": self._format_citation_ids(citations),
        }

        # 第四步：额外变量显式覆盖同名值，支持实验模板扩展。
        if extra_variables:
            variables.update(extra_variables)

        # 第五步：当前默认模板只渲染 query 和 packed_context，保持历史输出不变。
        prompt = self.template.format(
            query=variables["query"],
            packed_context=variables["packed_context"],
        )

        # 第六步：返回正文和完整可重复性 metadata。
        return PromptBuildResult(
            prompt=prompt,
            prompt_id=self.prompt_id,
            prompt_version=self.prompt_version,
            template=self.template,
            variables=variables,
        )
