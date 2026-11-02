"""模型调用上下文规划与预算控制包。

该包把长期共享 ``ContextBundle`` 中的候选事实转换为一次调用可消费的有界
``LLMContextPackage``，并记录排序、截断、丢弃和 Token 估算决定。
"""

from .manager import LLMContextManager
from .policies import SectionGenerationContextPolicy

__all__ = [
    "LLMContextManager",
    "SectionGenerationContextPolicy",
]
