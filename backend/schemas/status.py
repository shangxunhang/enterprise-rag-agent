"""定义 Task、Workflow、Agent 和 Tool 共用的规范执行状态。

所有运行层级共享同一组状态值，避免不同模块分别使用 ``done``、``error``、
``finished`` 等近义字符串导致状态映射和统计失真。
"""

from __future__ import annotations

from enum import Enum


class ExecutionStatus(str, Enum):
    """表示一个可执行对象从创建到终止可能经历的状态。

    继承 ``str`` 使枚举值能够直接写入 JSON、日志和持久化记录，同时仍保留枚举
    类型检查能力。
    """

    # 对象已建立，但尚未提交到调度或执行队列。
    CREATED = "created"
    # 对象已进入队列，等待运行资源或调度器选取。
    QUEUED = "queued"
    # 对象已具备执行条件，但尚未真正开始处理。
    PENDING = "pending"
    # 对象正在执行，后续仍可能成功、失败、暂停或取消。
    RUNNING = "running"
    # 对象在可恢复中断点暂停，等待人工或外部输入。
    PAUSED = "paused"
    # 目标全部完成，结果满足成功契约。
    SUCCESS = "success"
    # 只完成了部分目标，但已有结果仍具备业务价值。
    PARTIAL_SUCCESS = "partial_success"
    # 执行失败，当前错误不应由同一层直接重试。
    FAILED = "failed"
    # 执行失败，但错误类别和策略允许后续重试。
    RETRYABLE_FAILED = "retryable_failed"
    # 执行被调用方、人工操作或系统控制信号主动终止。
    CANCELLED = "cancelled"
    # 根据条件判断无需执行；跳过不是失败，但属于终态。
    SKIPPED = "skipped"


# 终态一旦写入，本次执行不应再自然回到运行态；恢复通常会形成新的执行过程。
TERMINAL_STATUSES = {
    ExecutionStatus.SUCCESS,
    ExecutionStatus.PARTIAL_SUCCESS,
    ExecutionStatus.FAILED,
    ExecutionStatus.RETRYABLE_FAILED,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.SKIPPED,
}

# 失败集合用于统一失败判断；部分成功和跳过不归入失败。
FAILURE_STATUSES = {
    ExecutionStatus.FAILED,
    ExecutionStatus.RETRYABLE_FAILED,
    ExecutionStatus.CANCELLED,
}


def is_failure(status: str | ExecutionStatus) -> bool:
    """判断给定状态是否应按失败处理。

    Args:
        status: 规范状态枚举或来自持久化、接口输入的状态字符串。

    Returns:
        状态属于失败集合时返回 ``True``；未知字符串也返回 ``True``，以采用
        fail-closed 策略，避免新状态或拼写错误被误判为成功。
    """

    try:
        # 第一步：把字符串统一转换为规范枚举，再按共享失败集合判断。
        return ExecutionStatus(status) in FAILURE_STATUSES
    except ValueError:
        # 第二步：无法识别的状态按失败处理，防止调用方继续消费不可信结果。
        return True
