"""定义跨运行时边界共享的规范失败类别。

失败类别只描述“发生了哪一类失败”，不直接决定是否重试、是否降级或如何交付。
具体动作由调用方结合类别、重试策略和业务上下文决定，从而避免把运行策略固化在
错误数据模型中。
"""

from __future__ import annotations

from enum import Enum


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
