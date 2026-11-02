"""从文件加载、版本化并渲染提示词模板。

``PromptManager`` 使用显式注册表把 ``prompt_id`` 映射到模板文件和业务元数据，渲染时
校验变量并返回包含模板版本、实际文本和变量快照的标准结果。它不调用模型，也不负责
提示词效果评测或在线版本发布。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas.prompt import PromptRenderResultSchema, PromptTemplateSchema


class PromptManager:
    """管理基于本地文件的提示词注册、加载和变量渲染。

    Attributes:
        prompt_root: 所有模板相对路径使用的根目录。
        prompt_registry: prompt_id 到文件路径及版本元数据的映射。
    """

    # 默认提示词注册表。每个 ID 明确绑定文件、可读名称、版本、任务类型和场景，
    # 调用方不能通过任意路径参数绕过注册表直接加载未知文件。
    DEFAULT_PROMPT_REGISTRY = {
        "scheme_generation_v1": {
            "relative_path": "scheme/scheme_generation_v1.txt",
            "prompt_name": "建设方案生成 Prompt",
            "prompt_version": "v1.0",
            "task_type": "scheme_generation",
            "scenario": "scheme",
        },
        "scheme_section_generation_v1": {
            "relative_path": "scheme/scheme_section_generation_v1.txt",
            "prompt_name": "建设方案分章节生成 Prompt",
            "prompt_version": "v1.1",
            "task_type": "scheme_generation",
            "scenario": "scheme_section",
        },
        "scheme_section_generation_v2": {
            "relative_path": "scheme/scheme_section_generation_v2.txt",
            "prompt_name": "建设方案分章节生成 Prompt：可选软目标",
            "prompt_version": "v2.0",
            "task_type": "scheme_generation",
            "scenario": "scheme_section",
        },
    }

    # 只识别 Python format 风格的简单命名变量，例如 {project_name}。
    # 不支持属性访问、索引表达式或复杂格式字段，降低模板行为复杂度。
    VARIABLE_PATTERN = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")

    def __init__(
        self,
        prompt_root: str | Path = "prompts",
        prompt_registry: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> None:
        """创建提示词管理器。

        Args:
            prompt_root: 模板文件根目录。
            prompt_registry: 自定义提示词注册表；未提供时使用默认注册表。
        """

        # 第一步：统一把根目录转换为 Path，后续路径拼接不依赖调用方输入类型。
        self.prompt_root = Path(prompt_root)

        # 第二步：使用显式自定义注册表或默认注册表；当前实现保留映射对象本身。
        self.prompt_registry = prompt_registry or self.DEFAULT_PROMPT_REGISTRY

    def load_template(self, prompt_id: str) -> PromptTemplateSchema:
        """按注册标识加载并解析一个提示词模板。

        Args:
            prompt_id: 注册表中的稳定提示词标识。

        Returns:
            包含模板正文、版本、变量集合和来源路径的标准模板对象。

        Raises:
            KeyError: 提示词标识未注册时抛出。
            FileNotFoundError: 注册表指向的模板文件不存在时抛出。
        """

        # 第一步：只允许加载注册表中明确登记的模板。
        if prompt_id not in self.prompt_registry:
            raise KeyError(f"Prompt not found: {prompt_id}")

        # 第二步：取得当前提示词的文件与版本元数据。
        item = self.prompt_registry[prompt_id]

        # 第三步：以 prompt_root 为唯一基准解析模板路径。
        template_path = self.prompt_root / item["relative_path"]

        # 第四步：在读取前显式检查文件，提供比通用读取异常更清晰的诊断信息。
        if not template_path.exists():
            raise FileNotFoundError(
                f"Prompt template file not found: {template_path}"
            )

        # 第五步：按 UTF-8 读取完整模板文本，保留中文和换行格式。
        template_text = template_path.read_text(encoding="utf-8")

        # 第六步：从实际模板正文提取变量，而不是信任独立人工维护的变量清单。
        variables = self.extract_variables(template_text)

        # 第七步：构造可用于调用复现和数据采集的标准模板快照。
        return PromptTemplateSchema(
            prompt_id=prompt_id,
            prompt_name=item.get("prompt_name", prompt_id),
            prompt_version=item.get("prompt_version", "v1.0"),
            task_type=item.get("task_type"),
            scenario=item.get("scenario"),
            template_path=str(template_path),
            template_text=template_text,
            variables=variables,
            metadata={
                "relative_path": item["relative_path"],
            },
        )

    def render(
        self,
        prompt_id: str,
        variables: Dict[str, Any],
        strict: bool = True,
    ) -> PromptRenderResultSchema:
        """使用给定变量渲染一个已注册提示词模板。

        Args:
            prompt_id: 要渲染的提示词标识。
            variables: 变量名到原始值的映射。
            strict: 为 ``True`` 时，缺少任一模板变量都会失败。

        Returns:
            保存实际渲染文本、模板版本和原始变量快照的标准结果。

        Raises:
            ValueError: 严格模式下缺少模板要求变量时抛出。
            KeyError: 非严格模式仍缺少 format 所需变量时，可能由 ``str.format`` 抛出。
        """

        # 第一步：加载模板快照，保证渲染和返回元数据来自同一个版本。
        template = self.load_template(prompt_id)

        # 第二步：根据模板实际声明的变量找出调用方未提供的名称。
        missing_variables = [
            variable_name
            for variable_name in template.variables
            if variable_name not in variables
        ]

        # 第三步：严格模式在执行 format 前一次性报告全部缺失变量。
        if strict and missing_variables:
            raise ValueError(
                f"Missing prompt variables for {prompt_id}: {missing_variables}"
            )

        # 第四步：把 None 转为空字符串，其余值转为文本，避免对象 repr 之外的隐式格式行为。
        safe_variables = {
            variable_name: "" if value is None else str(value)
            for variable_name, value in variables.items()
        }

        # 第五步：使用 Python 标准 format 执行简单变量替换。
        rendered_text = template.template_text.format(**safe_variables)

        # 第六步：返回渲染快照；variables 保留调用方原始值，便于审计和重放。
        return PromptRenderResultSchema(
            prompt_id=template.prompt_id,
            prompt_name=template.prompt_name,
            prompt_version=template.prompt_version,
            rendered_text=rendered_text,
            variables=variables,
            metadata={
                "template_path": template.template_path,
                "task_type": template.task_type,
                "scenario": template.scenario,
            },
        )

    @classmethod
    def extract_variables(cls, template_text: str) -> List[str]:
        """提取模板中的简单变量名并去重排序。

        Args:
            template_text: 完整模板正文。

        Returns:
            按字典序排列的唯一变量名称列表。
        """

        # 第一步：使用受限正则找出所有合法简单变量名。
        variables = cls.VARIABLE_PATTERN.findall(template_text)

        # 第二步：去重并排序，使模板元数据和测试结果确定。
        return sorted(set(variables))

    def exists(self, prompt_id: str) -> bool:
        """判断给定提示词标识是否已注册。"""

        return prompt_id in self.prompt_registry

    def list_prompt_ids(self) -> List[str]:
        """按字典序返回全部已注册提示词标识。"""

        # 排序后返回新列表，避免调用方依赖注册表实现的插入顺序。
        return sorted(self.prompt_registry.keys())
