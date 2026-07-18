"""Prompt manager.

PromptManager v1:
- Load prompt templates from files.
- Render prompt templates with variables.
- Return prompt metadata for DataCapture / Eval / future prompt versioning.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas.prompt import PromptRenderResultSchema, PromptTemplateSchema


class PromptManager:
    """File-based prompt manager."""

    DEFAULT_PROMPT_REGISTRY = {
        "scheme_generation_v1": {
            "relative_path": "scheme/scheme_generation_v1.txt",
            "prompt_name": "建设方案生成 Prompt",
            "prompt_version": "v1.0",
            "task_type": "scheme_generation",
            "scenario": "scheme",
        }
,
        "scheme_section_generation_v1": {
            "relative_path": "scheme/scheme_section_generation_v1.txt",
            "prompt_name": "建设方案分章节生成 Prompt",
            "prompt_version": "v1.1",
            "task_type": "scheme_generation",
            "scenario": "scheme_section",
        }
    }

    VARIABLE_PATTERN = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")

    def __init__(
        self,
        prompt_root: str | Path = "prompts",
        prompt_registry: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> None:
        self.prompt_root = Path(prompt_root)
        self.prompt_registry = prompt_registry or self.DEFAULT_PROMPT_REGISTRY

    def load_template(self, prompt_id: str) -> PromptTemplateSchema:
        """Load prompt template by prompt_id."""

        if prompt_id not in self.prompt_registry:
            raise KeyError(f"Prompt not found: {prompt_id}")

        item = self.prompt_registry[prompt_id]


        template_path = self.prompt_root / item["relative_path"]

        if not template_path.exists():
            raise FileNotFoundError(f"Prompt template file not found: {template_path}")

        template_text = template_path.read_text(encoding="utf-8")

        variables = self.extract_variables(template_text)

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
        """Render prompt template with variables.

        Args:
            prompt_id: Prompt id.
            variables: Variables used to render prompt.
            strict: If true, missing variables raise ValueError.

        Returns:
            PromptRenderResultSchema.
        """

        template = self.load_template(prompt_id)

        missing_variables = [
            variable_name
            for variable_name in template.variables
            if variable_name not in variables
        ]

        if strict and missing_variables:
            raise ValueError(
                f"Missing prompt variables for {prompt_id}: {missing_variables}"
            )

        safe_variables = {
            variable_name: "" if value is None else str(value)
            for variable_name, value in variables.items()
        }

        rendered_text = template.template_text.format(**safe_variables)

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
        """Extract variable names from a prompt template."""

        variables = cls.VARIABLE_PATTERN.findall(template_text)
        return sorted(set(variables))

    def exists(self, prompt_id: str) -> bool:
        return prompt_id in self.prompt_registry

    def list_prompt_ids(self) -> List[str]:
        return sorted(self.prompt_registry.keys())