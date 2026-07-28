"""简单工具路由 Agent 的固定回归样本。

样本覆盖知识库检索、计算器和纯模型回答三种路由。它们用于快速检查工具选择、
工具执行和最终回答关键词，不用于评估复杂文档生成工作流。
"""

# 每个样本都包含输入查询、期望工具和回答必须覆盖的字面关键词。
EVAL_CASES = [
    {
        "query": "FAISS 是什么？",
        "expected_tool": "rag",
        "expected_keywords": ["FAISS", "向量", "检索"],
    },
    {
        "query": "请根据知识库解释一下 RAG",
        "expected_tool": "rag",
        "expected_keywords": ["RAG", "检索", "生成"],
    },
    {
        "query": "计算 1 + 2 * 3",
        "expected_tool": "calculator",
        "expected_keywords": ["7"],
    },
    {
        "query": "你好，简单介绍一下你自己",
        "expected_tool": "llm",
        "expected_keywords": ["AI", "助手"],
    },
    {
        "query": "帮我算一下 10 / 2 + 3",
        "expected_tool": "calculator",
        "expected_keywords": ["8"],
    },
]
