"""旧类名兼容层；实际实现已切换为 ProjectInput 规范化 Agent。"""

from .project_input_normalizer_agent import ProjectInputNormalizerAgent


class FakeTableAgent(ProjectInputNormalizerAgent):
    """保留历史注册名，不再包含任何伪造表格或业务默认值逻辑。"""

    agent_name = "FakeTableAgent"
