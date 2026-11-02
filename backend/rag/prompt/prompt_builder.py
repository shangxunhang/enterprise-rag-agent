"""把旧版检索结果路由到问题类型 Prompt 并渲染最终模型输入。"""

from typing import Any, Dict, List

from rag.prompt.query_type import QueryType, detect_query_type
from rag.prompt.templates import (
    BASE_SYSTEM_INSTRUCTION,
    PROMPT_TEMPLATES,
)


def format_context(
    retrieved_chunks: List[Dict[str, Any]],
) -> str:
    """把检索 Chunk 格式化为带来源身份的文本资料块。

    Args:
        retrieved_chunks: 检索或重排后真正进入 Prompt 的 Chunk 列表。

    Returns:
        以空行分隔的资料文本。每项包含 rank、chunk_id、doc_id、source 和正文。

    source 优先读取顶层字段，缺失时回退 metadata.source；本函数不截断正文，Context
    预算应在调用前由检索链或 Context Packer 控制。
    """

    context_parts: List[str] = []

    # 逐项保持当前排序；缺少 rank 时使用 1-based 输入顺序。
    for index, chunk in enumerate(retrieved_chunks):
        rank = chunk.get("rank", index + 1)
        chunk_id = chunk.get("chunk_id", "")
        doc_id = chunk.get("doc_id", "")
        source = chunk.get("source", "")
        text = chunk.get("text", "")

        # 旧记录可能把来源放在 metadata 内部。
        metadata = chunk.get("metadata", {}) or {}
        if not source:
            source = metadata.get("source", "")

        # 固定字段标签便于模型和人工日志识别来源身份。
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
    """返回问题类型对应模板，未知值回退通用模板。"""

    # 使用枚举 value 查表，使模板配置与 QueryType 的字符串协议一致。
    return PROMPT_TEMPLATES.get(
        query_type.value,
        PROMPT_TEMPLATES["default"],
    )


def build_rag_prompt(
    query: str,
    retrieved_chunks: List[Dict[str, Any]],
    query_type: QueryType | None = None,
) -> str:
    """构造旧版知识库问答 Prompt。

    Args:
        query: 用户问题。
        retrieved_chunks: 检索或重排后进入 Prompt 的 Chunk。
        query_type: 可选显式问题类型；省略时由规则分类器判断。

    Returns:
        已注入系统约束、资料上下文和查询的完整 Prompt 字符串。
    """

    # 第一步：没有显式路由结果时，按固定规则识别问题类型。
    if query_type is None:
        query_type = detect_query_type(query)

    # 第二步：分别构建上下文并选择对应回答结构模板。
    context = format_context(retrieved_chunks)
    template = get_template_by_query_type(query_type)

    # 第三步：渲染模板。模板必须声明这三个稳定变量。
    prompt = template.format(
        system_instruction=BASE_SYSTEM_INSTRUCTION,
        context=context,
        query=query,
    )

    # 第四步：打印路由决策供旧脚本调试；Prompt 正文不在这里输出。
    print("=" * 80)
    print(f"[PromptRouter] query = {query}")
    print(f"[PromptRouter] query_type = {query_type}")
    print("=" * 80)

    return prompt
