# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SchemeWriterRuntimeSupport。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Runtime support shared by SchemeWriter services."""

import traceback
from typing import Optional

from core.error_factory import ErrorFactory
from schemas.common import ErrorSchema


# 阅读注释（类）：封装 scheme writer 运行时 support，集中封装相关状态、依赖和行为。
class SchemeWriterRuntimeSupport:
    """Compatibility helpers backed by the canonical runtime error factory."""

    _runtime_error_factory = ErrorFactory()

    # 阅读注释（函数）：处理 now iso 相关逻辑。
    @classmethod
    def now_iso(cls) -> str:
        """处理 now iso 相关逻辑。

        返回:
            str

        阅读提示:
            主要直接调用：cls._runtime_error_factory.clock.now_iso。
        """
        return cls._runtime_error_factory.clock.now_iso()

    # 阅读注释（函数）：处理 错误 相关逻辑。
    @classmethod
    def error(
        cls,
        code: str,
        exc_or_message: Exception | str,
        *,
        node: str,
        retryable: bool = False,
        user_message: Optional[str] = None,
    ) -> ErrorSchema:
        """处理 错误 相关逻辑。

        参数:
            code: code，具体约束请结合类型标注和调用方确认。
            exc_or_message: exc or 消息，具体约束请结合类型标注和调用方确认。
            node: node，具体约束请结合类型标注和调用方确认。
            retryable: retryable，具体约束请结合类型标注和调用方确认。
            user_message: user 消息，具体约束请结合类型标注和调用方确认。

        返回:
            ErrorSchema

        阅读提示:
            主要直接调用：isinstance, str, traceback.format_exc, cls._runtime_error_factory.create。
        """
        if isinstance(exc_or_message, Exception):
            message = str(exc_or_message)
            error_type = exc_or_message.__class__.__name__
            stack = traceback.format_exc()
        else:
            message = str(exc_or_message)
            error_type = "RuntimeValidationError"
            stack = None
        return cls._runtime_error_factory.create(
            error_code=code,
            error_type=error_type,
            message=message,
            user_visible_message=user_message or message,
            recoverable=True,
            retryable=retryable,
            failed_node=node,
            component="SchemeWriterAgent",
            agent_name="SchemeWriterAgent",
            step_name=node,
            stack_trace=stack,
        )
