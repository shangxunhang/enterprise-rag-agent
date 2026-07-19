# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：ErrorFactory。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Central construction of structured runtime errors."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.runtime.clock import Clock, SystemClock
from schemas.common import ErrorSchema, ErrorSourceSchema


# 阅读注释（类）：封装 错误 工厂，负责根据配置装配并返回运行实例。
class ErrorFactory:
    """封装 错误 工厂，负责根据配置装配并返回运行实例。"""
    # 阅读注释（函数）：初始化 ErrorFactory，保存运行所需的依赖、配置或状态。
    def __init__(self, clock: Optional[Clock] = None) -> None:
        """初始化 ErrorFactory，保存运行所需的依赖、配置或状态。

        参数:
            clock: clock，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：SystemClock。
        """
        self.clock = clock or SystemClock()

    # 阅读注释（函数）：创建 ErrorFactory。
    def create(
        self,
        *,
        error_code: str,
        error_type: str,
        message: str,
        component: str,
        user_visible_message: Optional[str] = None,
        recoverable: bool = False,
        retryable: bool = False,
        failed_node: Optional[str] = None,
        agent_name: Optional[str] = None,
        tool_name: Optional[str] = None,
        step_name: Optional[str] = None,
        stack_trace: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        debug_info: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ErrorSchema:
        """创建 ErrorFactory。

        参数:
            error_code: 错误 code，具体约束请结合类型标注和调用方确认。
            error_type: 错误 类型，具体约束请结合类型标注和调用方确认。
            message: 消息，具体约束请结合类型标注和调用方确认。
            component: component，具体约束请结合类型标注和调用方确认。
            user_visible_message: user visible 消息，具体约束请结合类型标注和调用方确认。
            recoverable: recoverable，具体约束请结合类型标注和调用方确认。
            retryable: retryable，具体约束请结合类型标注和调用方确认。
            failed_node: failed node，具体约束请结合类型标注和调用方确认。
            agent_name: Agent 名称，具体约束请结合类型标注和调用方确认。
            tool_name: 工具 名称，具体约束请结合类型标注和调用方确认。
            step_name: step 名称，具体约束请结合类型标注和调用方确认。
            stack_trace: stack Trace，具体约束请结合类型标注和调用方确认。
            details: details，具体约束请结合类型标注和调用方确认。
            debug_info: debug info，具体约束请结合类型标注和调用方确认。
            extra: extra，具体约束请结合类型标注和调用方确认。

        返回:
            ErrorSchema

        阅读提示:
            主要直接调用：ErrorSchema, ErrorSourceSchema, dict, self.clock.now_iso。
        """
        return ErrorSchema(
            error_code=error_code,
            error_type=error_type,
            message=message,
            user_visible_message=user_visible_message,
            recoverable=recoverable,
            retryable=retryable,
            failed_node=failed_node,
            source=ErrorSourceSchema(
                component=component,
                agent_name=agent_name,
                tool_name=tool_name,
                step_name=step_name,
            ),
            details=dict(details or {}),
            debug_info=dict(debug_info or {}),
            stack_trace=stack_trace,
            created_at=self.clock.now_iso(),
            extra=dict(extra or {}),
        )
