"""通过 LangGraph 自定义流通道发送领域级进度事件。

业务节点可调用本模块发出章节、节点和进度提示，而不直接依赖 HTTP、WebSocket 或
具体展示层。函数在非 LangGraph 执行上下文中安全返回 ``False``，因此同一业务代码
也能被同步测试或其他执行器复用。
"""

from __future__ import annotations

from typing import Any


def emit_workflow_custom_event(
    event_type: str,
    *,
    node_name: str | None = None,
    section_id: str | None = None,
    status: str = "running",
    progress: float | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """尝试向当前 LangGraph 自定义流写入一个规范领域事件。

    Args:
        event_type: 事件类别，例如节点开始、章节完成或人工审核等待。
        node_name: 产生事件的工作流节点名称。
        section_id: 与事件关联的文档章节标识；非章节事件可为空。
        status: 事件所描述对象的当前状态。
        progress: 可选进度值，具体取值范围由上层事件契约解释。
        payload: 事件特有的可序列化扩展数据。

    Returns:
        成功取得活动 writer 并写入事件时返回 ``True``；当前不在 LangGraph 流式
        执行上下文或无法取得 writer 时返回 ``False``。
    """

    try:
        # 第一步：延迟导入 LangGraph 上下文函数，避免普通同步路径产生强耦合。
        from langgraph.config import get_stream_writer

        # 第二步：从当前图执行上下文取得自定义事件 writer。
        writer = get_stream_writer()
    except Exception:
        # 非图上下文、框架版本不支持或上下文尚未建立时，不影响业务主流程。
        return False

    # 第三步：构造稳定事件外壳；复制 payload，避免 writer 或消费者修改调用方字典。
    event = {
        "event_type": event_type,
        "node_name": node_name,
        "section_id": section_id,
        "status": status,
        "progress": progress,
        "payload": dict(payload or {}),
    }

    # 第四步：把事件交给 LangGraph 自定义通道，由外层流消费者决定展示和持久化。
    writer(event)
    return True
