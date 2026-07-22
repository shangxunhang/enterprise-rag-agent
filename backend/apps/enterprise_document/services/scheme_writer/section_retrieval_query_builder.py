# =============================================================================
# 中文阅读说明：章节级检索 Query Builder。
# 只负责把 ProjectInput + section title 转换为普通检索或恢复检索查询，不执行 RAG。
# =============================================================================
"""Build section-aware retrieval queries for scheme generation."""

from __future__ import annotations

import re
from typing import Any, Optional

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema


class SectionRetrievalQueryBuilder:
    """Build deterministic section/recovery queries from project context."""

    @staticmethod
    def build(
        project_input: ProjectInputSchema,
        section_title: str,
        *,
        recovery: bool = False,
    ) -> str:
        placeholder_values = {
            "",
            "unspecified",
            "unknown",
            "none",
            "n/a",
            "未指定",
            "待定",
            "默认项目",
        }

        def usable_label(value: Any) -> Optional[str]:
            text = str(value or "").strip()
            if text.lower() in placeholder_values:
                return None
            return text or None

        project_label = next(
            (
                label
                for label in (
                    usable_label(project_input.project_type),
                    usable_label(project_input.project_name),
                )
                if label
            ),
            None,
        )
        if not project_label:
            user_query = str(project_input.user_query or "").strip()
            match = re.search(
                r"(?:生成|编制|撰写|制作|输出)?(?:一个|一份)?(.{2,40}?)(?:的)?建设方案",
                user_query,
            )
            if match:
                project_label = match.group(1).strip(" ：:，,。的")
        if not project_label:
            project_label = usable_label(project_input.output_schema.document_title)
        project_label = re.sub(r"(?:建设)?方案$", "", project_label or "").strip()
        project_label = project_label or "政企项目"

        domain_hints = {
            "项目概述": "建设背景、现状、建设范围、服务对象和总体依据",
            "建设目标": "总体目标、业务目标、能力目标、预期效果和建设原则",
            "建设内容": "建设任务、平台能力、功能模块、数据治理、运维和服务内容",
            "技术方案": "总体架构、系统架构、网络架构、数据架构、接口和部署方式",
            "资源配置": "计算、存储、网络、安全、容灾、容量规划和资源配置原则",
            "安全设计": "身份认证、访问控制、最小权限、数据加密、敏感数据保护、日志审计、等级保护、输入校验和接口安全",
            "实施与验收": "实施步骤、里程碑、迁移、联调、试运行、验收指标和交付物",
            "待补充事项": "项目输入缺口、待确认参数、边界条件、风险和人工补充材料",
        }
        hints = domain_hints.get(
            section_title,
            f"与“{section_title}”直接相关的要求、措施和依据",
        )
        recovery_clause = (
            "请优先返回能够直接支撑章节确定性陈述、可绑定引用的原文证据。"
            if recovery
            else "请返回与本章节直接相关、可用于方案编写和引用的依据。"
        )
        return (
            f"{project_label}建设方案的“{section_title}”章节：{hints}。"
            f"{recovery_clause}"
        )
