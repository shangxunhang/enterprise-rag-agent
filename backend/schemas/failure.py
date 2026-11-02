"""定义跨运行时边界共享的规范失败类别。

失败类别只描述“发生了哪一类失败”，不直接决定是否重试、是否降级或如何交付。
具体动作由调用方结合类别、重试策略和业务上下文决定，从而避免把运行策略固化在
错误数据模型中。
"""

from __future__ import annotations

from enum import Enum


class FailureOrigin(str, Enum):
    """标识失败事实最接近的来源/authority family，而不授予任何控制权限。

    FailureCategory 回答发生了什么；FailureOrigin 只回答失败由哪一类执行或
    语义边界确认。路由、重试、Budget、Gate 和 Release 必须继续由各自 authority
    结合当前事实决定，不能仅凭本枚举取值推导动作。
    """

    PROVIDER_RUNTIME = "provider_runtime"
    TOOL_RUNTIME = "tool_runtime"
    CANONICAL_BUDGET = "canonical_budget"
    LOCAL_STORAGE = "local_storage"
    EXECUTION_LIFECYCLE = "execution_lifecycle"
    CANONICAL_COMMIT = "canonical_commit"
    PROTOCOL = "protocol"
    RECOVERY = "recovery"
    EVIDENCE = "evidence"
    POST_WRITE = "post_write"
    RELEASE = "release"
    UNKNOWN = "unknown"


class FailureCategory(str, Enum):
    """对运行时、配置、数据和业务质量失败进行稳定分类。"""

    # 外部服务、模型供应商或基础设施当前不可访问。
    AVAILABILITY = "availability"
    # 短暂网络、并发或服务抖动，通常具备稍后重试价值。
    TRANSIENT = "transient"
    # 调用或工作流步骤超过规定时间窗口。
    TIMEOUT = "timeout"
    # 缺少配置、配置值非法或依赖装配不完整。
    CONFIGURATION = "configuration"
    # 输入、输出或状态不符合结构与字段约束。
    VALIDATION = "validation"
    # 技术执行完成，但结果未达到业务质量门槛。
    BUSINESS_QUALITY = "business_quality"
    # 检索证据不足，无法在禁止臆测的约束下继续生成。
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    # 执行被人工、调用方或系统控制信号主动终止。
    CANCELLED = "cancelled"
    # 内存、磁盘、配额、令牌预算或并发资源已经耗尽。
    RESOURCE_EXHAUSTED = "resource_exhausted"
    # 当前信息不足以归入任何已知类别；应保留原始错误供后续分析。
    UNKNOWN = "unknown"
