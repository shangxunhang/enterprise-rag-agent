"""旧类名兼容层；实际实现已切换为分章节方案生成 Agent。"""

from .scheme_writer_agent import SchemeWriterAgent


class FakeSchemeWriterAgent(SchemeWriterAgent):
    """保留历史注册名，业务能力完全继承真实 ``SchemeWriterAgent``。"""

    agent_name = "FakeSchemeWriterAgent"
