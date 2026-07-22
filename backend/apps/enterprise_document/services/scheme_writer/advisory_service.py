# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SectionAdvisoryService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated from the stable v7.5.1 SchemeWriter behavior."""


import re
import unicodedata
from typing import Any, Dict, List

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationSchema

from .citation_service import CitationService
from .constants import (
    HARDWARE_RESOURCE_PATTERN as _HARDWARE_RESOURCE_PATTERN,
    HIGH_RISK_QUANTIFIED_PATTERN as _HIGH_RISK_QUANTIFIED_PATTERN,
    NUMERIC_OR_MODEL_PATTERN as _NUMERIC_OR_MODEL_PATTERN,
    QUALIFIED_FACT_TERMS as _QUALIFIED_FACT_TERMS,
    RESOURCE_COMMITMENT_VERB_PATTERN as _RESOURCE_COMMITMENT_VERB_PATTERN,
    STAFF_COMMITMENT_VERB_PATTERN as _STAFF_COMMITMENT_VERB_PATTERN,
    STAFF_RESOURCE_PATTERN as _STAFF_RESOURCE_PATTERN,
)


# 阅读注释（类）：封装 章节 advisory 服务，封装一组可复用的业务能力。
class SectionAdvisoryService:
    """封装 章节 advisory 服务，封装一组可复用的业务能力。"""
    # 阅读注释（函数）：处理 项目 fact corpus 相关逻辑。
    @staticmethod
    def _project_fact_corpus(project_input: ProjectInputSchema) -> str:
        """Return only caller-supplied fact values, never schema field names."""

        values: list[str] = []

        # 阅读注释（函数）：收集 _project_fact_corpus。
        def collect(value: Any) -> None:
            """收集 _project_fact_corpus。

            参数:
                value: value，具体约束请结合类型标注和调用方确认。

            返回:
                None

            阅读提示:
                主要直接调用：isinstance, value.strip, values.append, str, value.values, collect。
            """
            if value is None or value is False:
                return
            if isinstance(value, str):
                text = value.strip()
                if text and text not in {"unspecified", "unknown", "default"}:
                    values.append(text)
                return
            if isinstance(value, (int, float)):
                values.append(str(value))
                return
            if isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
                return
            if isinstance(value, (list, tuple, set)):
                for nested in value:
                    collect(nested)

        # Runtime controls such as min chars, token budgets and chapter names
        # are not project facts, so do not include the entire model dump.
        collect(project_input.project_name)
        collect(project_input.user_query)
        collect(project_input.project_type)
        collect(project_input.customer_type)
        collect(project_input.business_goal)
        collect(project_input.target_documents)
        collect(project_input.total_staff)
        collect(project_input.functional_department_count)
        collect(project_input.business_department_count)
        collect([item.model_dump() for item in project_input.department_groups])
        collect([item.model_dump() for item in project_input.hardware_resources])
        collect(project_input.target_templates)
        collect(project_input.policy_requirements)
        collect([item.model_dump() for item in project_input.manual_boundaries])
        collect([item.model_dump() for item in project_input.source_materials])
        collect(project_input.missing_information)
        collect(project_input.conflicting_information)
        collect(project_input.metadata)
        collect(project_input.extra)
        return "\n".join(values)

    # 阅读注释（函数）：处理 项目 fact violations 相关逻辑。
    @classmethod
    def project_fact_violations(
        cls,
        content: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
    ) -> List[Dict[str, Any]]:
        """Detect unsupported project-specific assertions.

        The validator intentionally targets high-risk concrete claims (counts,
        models, named technologies and resource commitments). Generic prose is
        not treated as a project fact. A claim is accepted when it is qualified
        as pending confirmation or receives sufficient lexical support from
        caller-supplied ProjectInput values or retrieved child evidence.
        """

        support_text = "\n".join(
            [
                cls._project_fact_corpus(project_input),
                *(
                    str(item.quote_text or "")
                    for item in citations
                    if str(item.quote_text or "").strip()
                ),
            ]
        )
        support_tokens = CitationService.citation_match_tokens(support_text)
        support_plain = unicodedata.normalize("NFKC", support_text).lower()
        violations: list[Dict[str, Any]] = []

        claims = [
            item.strip()
            for item in re.split(r"(?<=[。！？!?；;])|\n", content or "")
            if item.strip() and not item.lstrip().startswith("#")
        ]
        for claim in claims:
            plain_claim = re.sub(r"^[\-+*\d.、（）()\s]+", "", claim)
            plain_claim = plain_claim.replace("**", "").strip()
            if len(plain_claim) < 6:
                continue
            if any(term in plain_claim for term in _QUALIFIED_FACT_TERMS):
                continue

            extracted_values = [
                unicodedata.normalize("NFKC", item).lower()
                for item in _NUMERIC_OR_MODEL_PATTERN.findall(plain_claim)
                if str(item).strip()
            ]
            # Bare English technology names (AI/RAG/JWT/Docker/HTTP, etc.)
            # describe a technical design and are not, by themselves, caller-
            # supplied project facts.  Technical grounding is handled by the
            # citation pipeline.  This boundary validator focuses on concrete
            # project commitments: quantities, performance measures and
            # committed people/hardware resources.
            numeric_values = [
                item for item in extracted_values if not re.search(r"[a-z]", item)
            ]
            has_quantified_fact = bool(
                numeric_values and _HIGH_RISK_QUANTIFIED_PATTERN.search(plain_claim)
            )
            has_hardware_commitment = bool(
                _HARDWARE_RESOURCE_PATTERN.search(plain_claim)
                and _RESOURCE_COMMITMENT_VERB_PATTERN.search(plain_claim)
            )
            has_staff_commitment = bool(
                _STAFF_RESOURCE_PATTERN.search(plain_claim)
                and _STAFF_COMMITMENT_VERB_PATTERN.search(plain_claim)
            )
            if not (
                has_quantified_fact
                or has_hardware_commitment
                or has_staff_commitment
            ):
                continue

            # Preserve model/resource identifiers for exact-support checks only
            # after the claim has already been classified as high risk.
            specific_values = extracted_values

            claim_normalized = unicodedata.normalize("NFKC", plain_claim).lower()
            if len(claim_normalized) >= 8 and claim_normalized in support_plain:
                continue
            if specific_values and all(value in support_plain for value in specific_values):
                continue

            claim_tokens = CitationService.citation_match_tokens(plain_claim)
            overlap = claim_tokens & support_tokens
            coverage = len(overlap) / max(1, len(claim_tokens))
            long_overlap = sum(1 for token in overlap if len(token) >= 4)
            supported = coverage >= 0.35 and len(overlap) >= 5 and long_overlap >= 1
            if supported:
                continue

            violations.append(
                {
                    "claim": plain_claim,
                    "reason": "project_specific_fact_not_supported",
                    "support_score": round(coverage, 4),
                }
            )

        return violations
