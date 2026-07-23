# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SemanticSectionJudge。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""LLM-assisted semantic quality gate for generated document sections.

The judge evaluates only semantic issues that deterministic runtime checks
cannot reliably decide, such as scope drift and whether a sentence is a
proposal or an unsupported project commitment.  It never replaces objective
hard checks (model/tool failure, truncation, missing required sections, invalid
citations).  The caller recomputes severity from a constrained policy instead
of trusting the model's top-level decision verbatim.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from apps.enterprise_document.quality.model_adapter import (
    reserve_current_workflow_budget,
)
from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from apps.enterprise_document.schemas.scheme_writer_schema import (
    SemanticGateIssueSchema,
    SemanticGateResultSchema,
)
from model_gateway.call_boundary import ModelCallBoundary
from model_gateway.model_contract import ModelRole
from model_gateway.model_gateway import ModelGateway
from schemas.citation import CitationSchema
from schemas.model import ModelResponseSchema


_HARD_SEMANTIC_ISSUE_TYPES = {
    "unsupported_quantitative_claim",
    "unsupported_resource_commitment",
    "fabricated_project_fact",
    "evidence_contradiction",
}
_SOFT_ONLY_ISSUE_TYPES = {
    "section_scope_drift",
    "minor_scope_drift",
    "redundancy",
    "style_issue",
    "format_issue",
    "overlong_section",
    "missing_context_qualification",
}
_VALID_SEVERITIES = {"warning", "soft_failure", "hard_failure"}
_VALID_ACTIONS = {"keep", "rewrite", "qualify", "remove", "human_review"}


# 阅读注释（类）：封装 semantic 章节 judge，集中封装相关状态、依赖和行为。
class SemanticSectionJudge:
    """Use a configured LLM as a constrained semantic reviewer.

    The LLM supplies semantic classification, while Python normalizes issue
    types, confidence and severity.  This prevents a small model from turning a
    style preference into a hard runtime failure.
    """

    # 阅读注释（函数）：初始化 SemanticSectionJudge，保存运行所需的依赖、配置或状态。
    def __init__(
        self,
        *,
        model_gateway: Optional[ModelGateway],
        model_name: str,
        enabled: bool = True,
        hard_confidence_threshold: float = 0.75,
    ) -> None:
        """初始化 SemanticSectionJudge，保存运行所需的依赖、配置或状态。

        参数:
            model_gateway: 模型 网关，具体约束请结合类型标注和调用方确认。
            model_name: 模型 名称，具体约束请结合类型标注和调用方确认。
            enabled: enabled，具体约束请结合类型标注和调用方确认。
            hard_confidence_threshold: hard 置信度 阈值，具体约束请结合类型标注和调用方确认。

        返回:
            None
        """
        self.model_gateway = model_gateway
        self.model_name = model_name
        self.enabled = enabled
        self.hard_confidence_threshold = hard_confidence_threshold

    # 阅读注释（函数）：提取 JSON object。
    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        """提取 JSON object。

        参数:
            text: 待处理文本。

        返回:
            Dict[str, Any]

        阅读提示:
            主要直接调用：strip, str, raw.startswith, re.sub, json.loads, isinstance, re.search, ValueError。
        """
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return value
        except Exception:
            pass

        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError("semantic judge output does not contain a JSON object")
        value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise ValueError("semantic judge JSON root must be an object")
        return value

    # 阅读注释（函数）：处理 compact 项目 输入 相关逻辑。
    @staticmethod
    def _compact_project_input(project_input: ProjectInputSchema) -> Dict[str, Any]:
        """Keep only business facts useful to semantic review."""

        return {
            "project_name": project_input.project_name,
            "project_type": project_input.project_type,
            "customer_type": project_input.customer_type,
            "business_goal": project_input.business_goal,
            "target_documents": project_input.target_documents,
            "total_staff": project_input.total_staff,
            "functional_department_count": project_input.functional_department_count,
            "business_department_count": project_input.business_department_count,
            "department_groups": [item.model_dump() for item in project_input.department_groups],
            "hardware_resources": [item.model_dump() for item in project_input.hardware_resources],
            "target_templates": project_input.target_templates,
            "policy_requirements": project_input.policy_requirements,
            "manual_boundaries": [item.model_dump() for item in project_input.manual_boundaries],
            "missing_information": project_input.missing_information,
            "conflicting_information": project_input.conflicting_information,
            "extra": project_input.extra,
        }

    # 阅读注释（函数）：处理 compact citations 相关逻辑。
    @staticmethod
    def _compact_citations(citations: Iterable[CitationSchema]) -> List[Dict[str, Any]]:
        """处理 compact citations 相关逻辑。

        参数:
            citations: 引用信息集合。

        返回:
            List[Dict[str, Any]]

        阅读提示:
            主要直接调用：strip, str, output.append。
        """
        output: list[Dict[str, Any]] = []
        for item in citations:
            quote = str(item.quote_text or "").strip()
            output.append(
                {
                    "citation_id": item.citation_id,
                    "title": item.title,
                    "section": item.section,
                    "quote_text": quote[:320],
                }
            )
        return output[:12]

    # 阅读注释（函数）：构建 提示词。
    def _build_prompt(
        self,
        *,
        section_title: str,
        content: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
        required_sections: List[str],
        deterministic_candidates: List[Dict[str, Any]],
        overlong: bool,
    ) -> tuple[str, str]:
        """构建 提示词。

        参数:
            section_title: 章节 title，具体约束请结合类型标注和调用方确认。
            content: 待处理内容。
            project_input: 规范化后的项目输入。
            citations: 引用信息集合。
            required_sections: required sections，具体约束请结合类型标注和调用方确认。
            deterministic_candidates: deterministic candidates，具体约束请结合类型标注和调用方确认。
            overlong: overlong，具体约束请结合类型标注和调用方确认。

        返回:
            tuple[str, str]

        阅读提示:
            主要直接调用：self._compact_project_input, self._compact_citations, json.dumps。
        """
        system_prompt = (
            "你是企业文档的语义质量评审器，不负责改写正文。只评估语义型问题，"
            "并严格输出JSON。运行时错误、截断、必填章节和引用ID真实性由代码检查，"
            "你不得重复裁决。不要因为出现AI、RAG、Docker、JWT等技术术语，或因为"
            "存在一般性建设目标、建议和原则性描述就判失败。只有在正文把未获项目输入"
            "或证据支持的数量、预算、工期、性能、采购、人力或既定项目事实写成确定承诺时，"
            "才可判为hard_failure。章节轻微跑题、冗余、风格和建议长度问题最多是"
            "warning或soft_failure。"
        )
        payload = {
            "current_section": section_title,
            "document_title": project_input.output_schema.document_title,
            "required_sections": required_sections,
            "user_query": project_input.user_query,
            "project_facts": self._compact_project_input(project_input),
            "evidence": self._compact_citations(citations),
            "deterministic_high_risk_candidates": deterministic_candidates,
            "overlong": overlong,
            "section_content": content,
        }
        prompt = (
            "请审查下面JSON中的章节。输出格式必须是：\n"
            '{"decision":"pass|warn|partial|fail","summary":"...",'
            '"issues":[{"issue_type":"section_scope_drift|unsupported_quantitative_claim|'
            'unsupported_resource_commitment|fabricated_project_fact|evidence_contradiction|'
            'missing_context_qualification|redundancy|style_issue|format_issue|overlong_section|other",'
            '"severity":"warning|soft_failure|hard_failure","claim":"原文中的具体句子",'
            '"reason":"判断理由","recommended_action":"keep|rewrite|qualify|remove|human_review",'
            '"confidence":0.0}]}\n'
            "没有问题时issues必须为空。不要输出Markdown。\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        return system_prompt, prompt

    # 阅读注释（函数）：规范化 issue。
    def _normalize_issue(self, raw: Dict[str, Any]) -> Optional[SemanticGateIssueSchema]:
        """规范化 issue。

        参数:
            raw: raw，具体约束请结合类型标注和调用方确认。

        返回:
            Optional[SemanticGateIssueSchema]

        阅读提示:
            主要直接调用：lower, strip, str, raw.get, float, max, min, SemanticGateIssueSchema。
        """
        issue_type = str(raw.get("issue_type") or "other").strip().lower()
        severity = str(raw.get("severity") or "warning").strip().lower()
        if severity not in _VALID_SEVERITIES:
            severity = "warning"
        action = str(raw.get("recommended_action") or "human_review").strip().lower()
        if action not in _VALID_ACTIONS:
            action = "human_review"
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        # Scope/style/length can never become a hard runtime failure merely
        # because the small judge model emitted "hard_failure".
        if issue_type in _SOFT_ONLY_ISSUE_TYPES and severity == "hard_failure":
            severity = "soft_failure"

        # Only a narrow allow-list may become hard, and only at high confidence.
        if severity == "hard_failure" and (
            issue_type not in _HARD_SEMANTIC_ISSUE_TYPES
            or confidence < self.hard_confidence_threshold
        ):
            severity = "soft_failure"

        claim = str(raw.get("claim") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not claim and not reason:
            return None
        return SemanticGateIssueSchema(
            issue_type=issue_type,
            severity=severity,
            claim=claim,
            reason=reason,
            recommended_action=action,
            confidence=confidence,
            source="llm_semantic_judge",
        )

    # 阅读注释（函数）：处理 decision from issues 相关逻辑。
    @staticmethod
    def _decision_from_issues(issues: List[SemanticGateIssueSchema]) -> str:
        """处理 decision from issues 相关逻辑。

        参数:
            issues: issues，具体约束请结合类型标注和调用方确认。

        返回:
            str

        阅读提示:
            主要直接调用：any。
        """
        if any(item.severity == "hard_failure" for item in issues):
            return "fail"
        if any(item.severity == "soft_failure" for item in issues):
            return "partial"
        if issues:
            return "warn"
        return "pass"

    # 阅读注释（函数）：合并 deterministic candidates。
    @staticmethod
    def _merge_deterministic_candidates(
        issues: List[SemanticGateIssueSchema],
        deterministic_candidates: List[Dict[str, Any]],
    ) -> List[SemanticGateIssueSchema]:
        """Ensure explicit unsupported commitments cannot disappear in LLM review.

        The model may distinguish semantics, but an unqualified concrete number
        already identified as unsupported is an objective high-risk condition.
        Non-quantified resource commitments are retained only as soft issues.
        """

        merged = list(issues)
        existing_claims = [item.claim for item in merged if item.claim]
        for item in deterministic_candidates:
            claim = str(item.get("claim") or "").strip()
            if not claim:
                continue
            if any(claim in existing or existing in claim for existing in existing_claims):
                continue
            has_explicit_quantity = bool(
                re.search(
                    r"\d|[一二三四五六七八九十百千万两]+(?:名|人|台|套|卡|个|节点)",
                    claim,
                )
            )
            merged.append(
                SemanticGateIssueSchema(
                    issue_type=(
                        "unsupported_quantitative_claim"
                        if has_explicit_quantity
                        else "unsupported_resource_commitment"
                    ),
                    severity="hard_failure" if has_explicit_quantity else "soft_failure",
                    claim=claim,
                    reason="deterministic unsupported high-risk claim candidate",
                    recommended_action="qualify",
                    confidence=0.95 if has_explicit_quantity else 0.7,
                    source="deterministic_guard",
                )
            )
        return merged

    # 阅读注释（函数）：处理 fallback 结果 相关逻辑。
    def _fallback_result(
        self,
        *,
        deterministic_candidates: List[Dict[str, Any]],
        overlong: bool,
        error_message: Optional[str] = None,
    ) -> SemanticGateResultSchema:
        """Conservative deterministic fallback when the semantic model fails."""

        issues: list[SemanticGateIssueSchema] = []
        for item in deterministic_candidates:
            claim = str(item.get("claim") or "").strip()
            # Explicit quantities are objective enough to remain hard. Generic
            # non-quantified resource prose becomes partial/human review rather
            # than failing the entire document.
            has_explicit_quantity = bool(re.search(r"\d|[一二三四五六七八九十百千万两]+(?:名|人|台|套|卡|个|节点)", claim))
            issues.append(
                SemanticGateIssueSchema(
                    issue_type=(
                        "unsupported_quantitative_claim"
                        if has_explicit_quantity
                        else "unsupported_resource_commitment"
                    ),
                    severity="hard_failure" if has_explicit_quantity else "soft_failure",
                    claim=claim,
                    reason=str(item.get("reason") or "deterministic high-risk candidate"),
                    recommended_action="qualify",
                    confidence=0.95 if has_explicit_quantity else 0.65,
                    source="deterministic_fallback",
                )
            )
        if overlong:
            issues.append(
                SemanticGateIssueSchema(
                    issue_type="overlong_section",
                    severity="warning",
                    claim="",
                    reason="section exceeds the recommended character budget",
                    recommended_action="rewrite",
                    confidence=1.0,
                    source="deterministic_fallback",
                )
            )
        return SemanticGateResultSchema(
            decision=self._decision_from_issues(issues),
            issues=issues,
            summary="semantic model unavailable; deterministic fallback applied",
            fallback_used=True,
            error_message=error_message,
        )

    # 阅读注释（函数）：处理 judge 相关逻辑。
    def judge(
        self,
        *,
        task_id: str,
        run_id: str,
        created_at: str,
        section_id: str,
        section_title: str,
        content: str,
        project_input: ProjectInputSchema,
        citations: List[CitationSchema],
        required_sections: List[str],
        deterministic_candidates: List[Dict[str, Any]],
        overlong: bool,
        call_suffix: str = "",
    ) -> tuple[SemanticGateResultSchema, Optional[ModelResponseSchema]]:
        """处理 judge 相关逻辑。

        参数:
            task_id: 任务唯一标识。
            run_id: 本次运行唯一标识。
            created_at: created at，具体约束请结合类型标注和调用方确认。
            section_id: 章节 标识，具体约束请结合类型标注和调用方确认。
            section_title: 章节 title，具体约束请结合类型标注和调用方确认。
            content: 待处理内容。
            project_input: 规范化后的项目输入。
            citations: 引用信息集合。
            required_sections: required sections，具体约束请结合类型标注和调用方确认。
            deterministic_candidates: deterministic candidates，具体约束请结合类型标注和调用方确认。
            overlong: overlong，具体约束请结合类型标注和调用方确认。
            call_suffix: call suffix，具体约束请结合类型标注和调用方确认。

        返回:
            tuple[SemanticGateResultSchema, Optional[ModelResponseSchema]]

        阅读提示:
            主要直接调用：self._fallback_result, self._build_prompt, ModelRequestSchema, self.model_gateway.generate, self._extract_json_object, parsed.get, isinstance, ValueError。
        """
        if not self.enabled or self.model_gateway is None:
            return (
                self._fallback_result(
                    deterministic_candidates=deterministic_candidates,
                    overlong=overlong,
                    error_message="semantic judge disabled or model gateway unavailable",
                ),
                None,
            )

        system_prompt, prompt = self._build_prompt(
            section_title=section_title,
            content=content,
            project_input=project_input,
            citations=citations,
            required_sections=required_sections,
            deterministic_candidates=deterministic_candidates,
            overlong=overlong,
        )
        call_id = f"model_call_{run_id}_{section_id}_semantic_gate{call_suffix}"
        boundary = ModelCallBoundary(
            model_gateway=self.model_gateway,
            model_role=ModelRole.SEMANTIC_GATE,
            runtime_context={
                "task_id": task_id,
                "workflow_run_id": run_id,
                "section_id": section_id,
                "section_title": section_title,
                "caller_agent": "SemanticSectionJudge",
            },
            default_purpose="semantic_section_gate",
            call_suffix=f"semantic_gate{call_suffix}",
            budget_hook=reserve_current_workflow_budget,
        )
        response = boundary.generate_response(
            prompt,
            system_prompt=system_prompt,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_new_tokens=384,
            created_at=created_at,
            model_call_id=call_id,
        )
        if not response.success:
            return (
                self._fallback_result(
                    deterministic_candidates=deterministic_candidates,
                    overlong=overlong,
                    error_message=response.error_message or "semantic model call failed",
                ),
                response,
            )

        try:
            parsed = self._extract_json_object(response.content)
            raw_issues = parsed.get("issues") or []
            if not isinstance(raw_issues, list):
                raise ValueError("semantic judge issues must be a list")
            issues = [
                issue
                for item in raw_issues
                if isinstance(item, dict)
                for issue in [self._normalize_issue(item)]
                if issue is not None
            ]
            issues = self._merge_deterministic_candidates(
                issues, deterministic_candidates
            )
            result = SemanticGateResultSchema(
                decision=self._decision_from_issues(issues),
                issues=issues,
                summary=str(parsed.get("summary") or "").strip(),
                model_call_id=response.model_call_id,
                fallback_used=False,
                raw_output=parsed,
            )
            return result, response
        except Exception as exc:
            return (
                self._fallback_result(
                    deterministic_candidates=deterministic_candidates,
                    overlong=overlong,
                    error_message=f"semantic judge JSON parse failed: {exc}",
                ),
                response,
            )
