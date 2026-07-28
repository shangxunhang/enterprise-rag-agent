"""生成章节的受约束 LLM 语义质量门禁。

语义模型只判断确定性代码难以可靠裁决的问题，例如章节跑题、建议性表述与无依据项目
承诺的语义区别。它不能替代模型/工具失败、截断、缺失章节和无效引用等客观硬检查；
Python 会重新规范化问题类型、置信度和严重度，而不直接采信模型的顶层 decision。
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


class SemanticSectionJudge:
    """让模型提供语义分类，再由代码策略限制其最终影响。

    风格、长度和轻微跑题永远不能仅凭小模型输出升级为硬失败；只有窄白名单中的高风险
    事实问题且置信度达到阈值时，才允许保留 ``hard_failure``。
    """

    def __init__(
        self,
        *,
        model_gateway: Optional[ModelGateway],
        model_name: str,
        enabled: bool = True,
        hard_confidence_threshold: float = 0.75,
    ) -> None:
        """注入模型网关、兼容模型名、启用开关和硬失败最低置信度。"""
        self.model_gateway = model_gateway
        self.model_name = model_name
        self.enabled = enabled
        self.hard_confidence_threshold = hard_confidence_threshold

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        """解析纯 JSON 或代码围栏/附加文本中的首个 JSON 对象；根节点必须为对象。"""
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

    @staticmethod
    def _compact_project_input(project_input: ProjectInputSchema) -> Dict[str, Any]:
        """只保留语义审查所需业务事实，排除 Prompt 预算等运行控制字段。"""

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

    @staticmethod
    def _compact_citations(citations: Iterable[CitationSchema]) -> List[Dict[str, Any]]:
        """最多保留 12 条引用及 320 字引文，限制语义审查 Prompt 膨胀。"""
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
        """构造严格 JSON 输出协议，并明确模型不得重复裁决客观硬检查。

        Prompt 同时提供确定性高风险候选，要求模型只补充语义分类；这些候选即使被模型
        忽略，也会在后续合并阶段重新加入。
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

    def _normalize_issue(self, raw: Dict[str, Any]) -> Optional[SemanticGateIssueSchema]:
        """清理模型问题字段，并按白名单与置信度重新约束严重度和处置动作。"""
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

    @staticmethod
    def _decision_from_issues(issues: List[SemanticGateIssueSchema]) -> str:
        """完全根据规范化 Issue 严重度重算 pass/warn/partial/fail。"""
        if any(item.severity == "hard_failure" for item in issues):
            return "fail"
        if any(item.severity == "soft_failure" for item in issues):
            return "partial"
        if issues:
            return "warn"
        return "pass"

    @staticmethod
    def _merge_deterministic_candidates(
        issues: List[SemanticGateIssueSchema],
        deterministic_candidates: List[Dict[str, Any]],
    ) -> List[SemanticGateIssueSchema]:
        """把确定性高风险候选合并回模型结果，防止客观问题被模型漏掉。

        明确数量的无依据 Claim 保留为硬失败；没有数量的资源承诺只保留为软失败，避免
        把一般性设计建议过度判死。
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

    def _fallback_result(
        self,
        *,
        deterministic_candidates: List[Dict[str, Any]],
        overlong: bool,
        error_message: Optional[str] = None,
    ) -> SemanticGateResultSchema:
        """语义模型禁用、调用失败或解析失败时，使用保守确定性候选生成降级结果。"""

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
        """执行一次受预算约束的语义审查，并返回门禁结果与原始模型响应。

        模型不可用时直接走确定性 fallback；模型成功后也必须解析 JSON、规范化每条 Issue、
        合并确定性候选并重新计算 decision。任何解析异常都不会放行未知结果，而是回退到
        可解释的保守结论。
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
