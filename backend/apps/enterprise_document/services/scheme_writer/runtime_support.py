"""SchemeWriter 拆分服务共享的时间与结构化错误兼容入口。"""

import traceback
from typing import Optional

from core.error_factory import ErrorFactory
from schemas.common import ErrorSchema
from schemas.failure import FailureCategory


class SchemeWriterRuntimeSupport:
    """以统一 ErrorFactory 替代旧 Agent 内部散落的时间和异常构造代码。"""

    _runtime_error_factory = ErrorFactory()

    @classmethod
    def now_iso(cls) -> str:
        """返回与错误工厂共用时钟的 ISO 时间。"""
        return cls._runtime_error_factory.clock.now_iso()

    @classmethod
    def error(
        cls,
        code: str,
        exc_or_message: Exception | str,
        *,
        node: str,
        retryable: bool = False,
        failure_category: FailureCategory | str | None = None,
        requires_human: bool = False,
        requires_new_input: bool = False,
        user_message: Optional[str] = None,
    ) -> ErrorSchema:
        """把异常或校验消息转换为 SchemeWriter 来源的结构化错误。

        Exception 会保留真实类型和当前堆栈；纯字符串按运行时校验错误处理。失败类别仍需由
        最近事实源明确提供，避免在兼容工具中根据错误文案猜测。
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
            failure_category=failure_category,
            requires_human=requires_human,
            requires_new_input=requires_new_input,
            failed_node=node,
            component="SchemeWriterAgent",
            agent_name="SchemeWriterAgent",
            step_name=node,
            stack_trace=stack,
        )
