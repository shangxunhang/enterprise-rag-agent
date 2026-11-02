"""定义提示词模板元数据和单次渲染结果。

模板对象描述可版本化的提示词来源与变量契约；渲染结果保存一次实际替换后的文本和
变量快照。模型调用应记录渲染结果或其标识，不能只记录模板 ID 后再假设未来模板内容
仍与调用时一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import SchemaBase


class PromptTemplateSchema(SchemaBase):
    """保存一个可加载、可版本化提示词模板的完整定义。"""

    # 当前模板 Schema 版本。
    schema_version: str = "prompt_template_v1"

    # 提示词在注册表中的稳定标识。
    prompt_id: str
    # 面向人工阅读和管理界面的名称。
    prompt_name: str
    # 模板内容版本，用于调用复现和评测对比。
    prompt_version: str

    # 模板适用的任务类型；通用模板可为空。
    task_type: Optional[str] = None
    # 更细粒度使用场景，例如章节规划、证据判定或改写。
    scenario: Optional[str] = None

    # 模板文件相对于提示词根目录或注册来源的路径。
    template_path: str
    # 当前版本实际模板正文。
    template_text: str

    # 模板声明需要调用方提供的变量名称集合。
    variables: List[str] = Field(default_factory=list)

    # 稳定但不参与正文渲染的模板元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 尚未进入稳定模板契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)


class PromptRenderResultSchema(SchemaBase):
    """保存一次模板渲染后的可审计提示词快照。"""

    # 当前渲染结果 Schema 版本。
    schema_version: str = "prompt_render_result_v1"

    # 产生该结果的模板稳定标识。
    prompt_id: str
    # 产生该结果的模板名称。
    prompt_name: str
    # 渲染时使用的模板版本。
    prompt_version: str

    # 完成变量替换后实际发送或即将发送给模型的文本。
    rendered_text: str
    # 本次渲染使用的变量名和值快照。
    variables: Dict[str, Any] = Field(default_factory=dict)

    # 渲染过程、调用用途或来源相关元数据。
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # 尚未进入稳定渲染结果契约的扩展字段。
    extra: Dict[str, Any] = Field(default_factory=dict)
