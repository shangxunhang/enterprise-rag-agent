"""
prompt_builder.py
=================

RAG Prompt 构造模块。

本文件负责：
1. 将 retrieved_chunks 转换成 context
2. 根据 query 判断问题类型
3. 使用对应 prompt template 构造最终 prompt
"""

from typing import Any, Dict, List

from rag.prompt.query_type import detect_query_type, QueryType
from rag.prompt.templates import (
    BASE_SYSTEM_INSTRUCTION,
    PROMPT_TEMPLATES,
)


def format_context(retrieved_chunks: List[Dict[str, Any]]) -> str:
    """
    将 retrieved_chunks 格式化为 prompt context。

    每个 chunk 尽量保留：
    1. rank
    2. chunk_id
    3. doc_id
    4. source
    5. text
    """
    context_parts = []

    for index, chunk in enumerate(retrieved_chunks):
        rank = chunk.get("rank", index + 1)
        chunk_id = chunk.get("chunk_id", "")
        doc_id = chunk.get("doc_id", "")
        source = chunk.get("source", "")
        text = chunk.get("text", "")

        metadata = chunk.get("metadata", {}) or {}

        if not source:
            source = metadata.get("source", "")

        context_part = f"""
[资料 {rank}]
chunk_id: {chunk_id}
doc_id: {doc_id}
source: {source}
text:
{text}
""".strip()

        context_parts.append(context_part)

    return "\n\n".join(context_parts)


def get_template_by_query_type(query_type: QueryType) -> str:
    """
    根据 query_type 获取 prompt template。
    """
    return PROMPT_TEMPLATES.get(
        query_type.value,
        PROMPT_TEMPLATES["default"],
    )


def build_rag_prompt(
    query: str,
    retrieved_chunks: List[Dict[str, Any]],
    query_type: QueryType | None = None,
) -> str:
    """
    构造 RAG prompt。

    参数:
        query:
            用户问题。

        retrieved_chunks:
            检索 / rerank 后进入 prompt 的 chunks。

        query_type:
            可选问题类型。
            如果不传，则自动根据 query 判断。

    返回:
        prompt:
            最终传给 LLM 的 prompt。
    """
    if query_type is None:
        query_type = detect_query_type(query)

    context = format_context(retrieved_chunks)
    template = get_template_by_query_type(query_type)

    prompt = template.format(
        system_instruction=BASE_SYSTEM_INSTRUCTION,
        context=context,
        query=query,
    )
    print("=" * 80)
    print(f"[PromptRouter] query = {query}")
    print(f"[PromptRouter] query_type = {query_type}")
    print("=" * 80)
    return prompt