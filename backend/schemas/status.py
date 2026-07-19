# =============================================================================
# 中文阅读说明：跨模块数据 Schema 定义模块。
# 主要定义：ExecutionStatus、is_failure。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Canonical execution statuses used across Task, Workflow, Agent and Tool."""

from __future__ import annotations

from enum import Enum


# 阅读注释（类）：封装 execution 状态，集中封装相关状态、依赖和行为。
class ExecutionStatus(str, Enum):
    """封装 execution 状态，集中封装相关状态、依赖和行为。"""
    CREATED = "created"
    QUEUED = "queued"
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    RETRYABLE_FAILED = "retryable_failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


TERMINAL_STATUSES = {
    ExecutionStatus.SUCCESS,
    ExecutionStatus.PARTIAL_SUCCESS,
    ExecutionStatus.FAILED,
    ExecutionStatus.RETRYABLE_FAILED,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.SKIPPED,
}

FAILURE_STATUSES = {
    ExecutionStatus.FAILED,
    ExecutionStatus.RETRYABLE_FAILED,
    ExecutionStatus.CANCELLED,
}


# 阅读注释（函数）：判断 failure。
def is_failure(status: str | ExecutionStatus) -> bool:
    """判断 failure。

    参数:
        status: 状态，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：ExecutionStatus。
    """
    try:
        return ExecutionStatus(status) in FAILURE_STATUSES
    except ValueError:
        return True
