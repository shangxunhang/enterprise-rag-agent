"""
Agent 评估样本。

当前评估目标：
1. Planner 是否选对工具
2. Tool 是否执行成功
3. Final Answer 是否命中关键词
"""

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