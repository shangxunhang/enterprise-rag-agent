"""
configs/LLMConfig.py
====================

RAG 项目内部 LLM 配置。

注意：
- Agent-RAG 场景通常不建议让 RAGEngine.answer() 再调用内部 LLM。
- Agent 项目更推荐调用 RAGEngine.retrieve_context()，然后由 Agent 的 Qwen Finalizer 生成最终回答。
- 这个配置主要保留给纯 RAG demo / CLI 使用。
- RAG-Fusion / HyDE 属于检索前查询生成层，本次升级后默认也复用本地 Qwen LLM。
"""

from rag.configs.BaseConfig import MODEL_ROOT_DIR


# =========================
# 本地 LLM 模型
# =========================

LLM_MODEL_DIR = (
    MODEL_ROOT_DIR
    / "llm"
    / "Qwen2.5-1.5B-Instruct"
)

LLM_MODEL_NAME = str(LLM_MODEL_DIR)

LLM_DEVICE = "cuda"
LLM_MAX_NEW_TOKENS = 256
LLM_TEMPERATURE = 0.7
LLM_TOP_P = 0.9
LLM_DO_SAMPLE = False


# =========================
# RAG-Fusion / HyDE 查询生成层
# =========================

# True：RAG-Fusion 查询改写和 HyDE 假想文档都使用本地 LLM。
# False：回退到 query_expander.py 内置的确定性模板，便于无显卡/无模型环境测试。
QUERY_EXPANSION_LLM_ENABLED = True

# 默认复用同一个 Qwen2.5-1.5B-Instruct 路径，避免额外维护第二套模型配置。
QUERY_EXPANSION_LLM_MODEL_NAME = LLM_MODEL_NAME
QUERY_EXPANSION_LLM_DEVICE = LLM_DEVICE

# 查询生成需要稳定，不追求发散，因此默认 do_sample=False、temperature 较低。
QUERY_REWRITE_MAX_NEW_TOKENS = 192
QUERY_HYDE_MAX_NEW_TOKENS = 256
QUERY_EXPANSION_TEMPERATURE = 0.1
QUERY_EXPANSION_TOP_P = 0.9
QUERY_EXPANSION_DO_SAMPLE = False


if __name__ == "__main__":
    print(f"LLM_MODEL_NAME                  = {LLM_MODEL_NAME}")
    print(f"LLM_DEVICE                      = {LLM_DEVICE}")
    print(f"QUERY_EXPANSION_LLM_ENABLED     = {QUERY_EXPANSION_LLM_ENABLED}")
    print(f"QUERY_EXPANSION_LLM_MODEL_NAME  = {QUERY_EXPANSION_LLM_MODEL_NAME}")
    print(f"QUERY_EXPANSION_LLM_DEVICE      = {QUERY_EXPANSION_LLM_DEVICE}")
