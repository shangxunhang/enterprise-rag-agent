# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：SectionAdvisoryService。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generated from the stable v7.5.1 SchemeWriter behavior."""


from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Dict, Iterable, List

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


_MANDATORY_NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)"
_MANDATORY_FACT_PATTERNS = (
    (
        "hardware_count",
        "server",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:台|套)\s*"
            rf"(?:(?:GPU|CPU|NPU)\s*)?服务器",
            re.IGNORECASE,
        ),
    ),
    (
        "hardware_count",
        "server",
        "count",
        re.compile(
            rf"(?:(?:GPU|CPU|NPU)\s*)?服务器(?:数量|总数|规模)?"
            rf"(?:为|共|达到|配置|部署)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:台|套)",
            re.IGNORECASE,
        ),
    ),
    (
        "hardware_count",
        "gpu",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:块|张|卡|个|台)\s*"
            rf"(?:GPU(?!\s*服务器)|显卡)",
            re.IGNORECASE,
        ),
    ),
    (
        "hardware_count",
        "gpu",
        "count",
        re.compile(
            rf"(?:GPU|显卡)(?:数量|总数)?(?:为|共|达到|配置)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:块|张|卡|个|台)",
            re.IGNORECASE,
        ),
    ),
    (
        "hardware_count",
        "cpu",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:块|颗|片|个|台)\s*"
            rf"CPU(?!\s*服务器)",
            re.IGNORECASE,
        ),
    ),
    (
        "hardware_count",
        "npu",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:块|张|卡|个|台)\s*"
            rf"NPU(?!\s*服务器)",
            re.IGNORECASE,
        ),
    ),
    (
        "hardware_count",
        "rack",
        "count",
        re.compile(rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个|台|套)\s*机柜"),
    ),
    (
        "hardware_count",
        "rack",
        "count",
        re.compile(
            rf"机柜(?:数量|总数)?(?:为|共|达到|配置)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个|台|套)"
        ),
    ),
    (
        "hardware_count",
        "node",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个|台|套)?\s*"
            rf"(?:数据库节点|计算节点|节点)"
        ),
    ),
    (
        "hardware_count",
        "node",
        "count",
        re.compile(
            rf"(?:数据库节点|计算节点|节点)(?:数量|总数)?"
            rf"(?:为|共|达到|配置)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个|台|套)?"
        ),
    ),
    (
        "staff_count",
        "engineer",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:名|人)\s*"
            rf"(?:高级技术人员|工程师|项目经理|开发人员|测试人员|运维人员)"
        ),
    ),
    (
        "staff_count",
        "engineer",
        "count",
        re.compile(
            rf"(?:高级技术人员|工程师|项目经理|开发人员|测试人员|运维人员)"
            rf"(?:数量|总数)?(?:为|共|达到|配备)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:名|人)"
        ),
    ),
    (
        "staff_count",
        "technical_team",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:名|人)\s*"
            rf"(?:技术团队|实施团队|运维团队|项目小组)"
        ),
    ),
    (
        "staff_count",
        "staff_total",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:名|人)\s*"
            rf"(?:员工|人员)"
        ),
    ),
    (
        "staff_count",
        "staff_total",
        "count",
        re.compile(
            rf"(?:员工|人员)(?:总数|数量|规模)?(?:为|共|达到|约)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:名|人)"
        ),
    ),
    (
        "department_count",
        "functional_department",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个|类)?\s*职能部门"
        ),
    ),
    (
        "department_count",
        "functional_department",
        "count",
        re.compile(
            rf"职能部门(?:数量|总数)?(?:为|共|达到)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个|类)?"
        ),
    ),
    (
        "department_count",
        "business_department",
        "count",
        re.compile(
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个|类)?\s*业务部门"
        ),
    ),
    (
        "department_count",
        "business_department",
        "count",
        re.compile(
            rf"业务部门(?:数量|总数)?(?:为|共|达到)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个|类)?"
        ),
    ),
    (
        "duration",
        "project",
        "month",
        re.compile(
            rf"(?:项目工期|建设工期|建设期|项目周期|周期|工期)"
            rf"(?:为|共|预计|计划)?\s*"
            rf"(?P<value>{_MANDATORY_NUMBER})\s*(?:个月|月)"
        ),
    ),
)

_MANDATORY_METADATA_FACTS = {
    "server_count": ("hardware_count", "server", "count"),
    "gpu_count": ("hardware_count", "gpu", "count"),
    "rack_count": ("hardware_count", "rack", "count"),
    "staff_count": ("staff_count", "staff_total", "count"),
    "project_duration_months": ("duration", "project", "month"),
}
_MANDATORY_SCOPE_BOUNDARY_PATTERN = re.compile(
    r"(?:但是|然而|不过|(?<!不)但|同时|另外|然后|并且|"
    r"(?<!从)而(?=(?:本项目|项目|确定|新增|部署|配置|配备|建设|采购|采用|设置)))"
)
_MANDATORY_INTERROGATIVE_PATTERN = re.compile(
    r"(?:是否需要|是否部署|需不需要|要不要|是否|能否|可否|吗|[？?])"
)
_MANDATORY_NEGATION_PATTERN = re.compile(
    r"(?:不再|不要|不得|禁止|无需|不需要|不应|不能|不采用|不部署|取消|避免|"
    r"不(?=(?:新增|设置|配置|配备|建设|采购|采用|部署)))"
)
_MANDATORY_AFFIRMATIVE_VERB_PATTERN = re.compile(
    r"(?:确定|新增|设置|配置|配备|建设|采购|采用|部署)"
)
_MANDATORY_MODAL_QUALIFICATION_TERMS = {
    "建议",
    "可采用",
    "可选用",
    "可配置",
    "可部署",
    "可考虑",
    "宜采用",
    "可根据",
    "原则上",
}
_MANDATORY_ATTRIBUTE_TERMS = {
    "数量",
    "总数",
    "规模",
    "容量",
    "型号",
    "品牌",
    "厂商",
    "规格",
    "性能",
    "金额",
    "预算",
    "工期",
    "周期",
}
_MANDATORY_EXPECTED_ATTRIBUTES = {
    ("hardware_count", "count"): {"数量", "总数", "规模"},
    ("staff_count", "count"): {"数量", "总数", "规模"},
    ("department_count", "count"): {"数量", "总数", "规模"},
    ("resource_commitment", "unspecified"): {
        "数量",
        "总数",
        "规模",
        "容量",
        "型号",
        "品牌",
        "厂商",
        "规格",
        "性能",
    },
    ("duration", "month"): {"工期", "周期"},
    ("duration", "unspecified"): {"工期", "周期"},
    ("finance", "unspecified"): {"金额", "预算"},
}
_MANDATORY_SUBJECT_TERMS = {
    "server": {"服务器"},
    "gpu": {"GPU", "显卡"},
    "cpu": {"CPU"},
    "npu": {"NPU"},
    "rack": {"机柜"},
    "node": {"节点", "数据库节点", "计算节点"},
    "engineer": {"工程师", "技术人员"},
    "staff_total": {"员工", "人员"},
    "functional_department": {"职能部门"},
    "business_department": {"业务部门"},
    "project": {"项目"},
}

_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


@dataclass(frozen=True)
class _MandatoryProjectFact:
    fact_type: str
    subject: str
    value: int | float | None
    unit: str
    commitment: str
    claim: str

    @property
    def support_key(
        self,
    ) -> tuple[str, str, int | float | None, str, str]:
        return (
            self.fact_type,
            self.subject,
            self.value,
            self.unit,
            self.commitment,
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

    @staticmethod
    def _mandatory_numeric_value(value: Any) -> int | float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            number = float(value)
            return int(number) if number.is_integer() else number

        text = unicodedata.normalize("NFKC", str(value)).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            number = float(text)
            return int(number) if number.is_integer() else number
        if not text or any(
            char not in _CHINESE_DIGITS and char not in _CHINESE_UNITS
            for char in text
        ):
            return None

        total = 0
        section = 0
        digit = 0
        for char in text:
            if char in _CHINESE_DIGITS:
                digit = _CHINESE_DIGITS[char]
                continue
            unit = _CHINESE_UNITS[char]
            if unit == 10000:
                section = (section + digit) or 1
                total += section * unit
                section = 0
            else:
                section += (digit or 1) * unit
            digit = 0
        return total + section + digit

    @staticmethod
    def _normalize_mandatory_claim(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or ""))
        text = re.sub(r"\[([A-Za-z0-9_.:\-]+)\]", "", text)
        text = re.sub(r"^[\-+*\d.、（）()\s]+", "", text)
        return text.replace("**", "").strip()

    @staticmethod
    def _mandatory_qualification_matches_fact(
        clause: str,
        fact_span: tuple[int, int],
        *,
        fact_type: str,
        subject: str,
        unit: str,
    ) -> bool:
        fact_start, fact_end = fact_span
        expected_attributes = _MANDATORY_EXPECTED_ATTRIBUTES.get(
            (fact_type, unit),
            set(),
        )
        for term in _QUALIFIED_FACT_TERMS:
            start = clause.find(term)
            while start >= 0:
                end = start + len(term)
                if end <= fact_start:
                    gap = clause[end:fact_start]
                elif start >= fact_end:
                    gap = clause[fact_end:start]
                else:
                    gap = ""
                if len(gap) > 12:
                    start = clause.find(term, start + len(term))
                    continue

                if end <= fact_start:
                    owner_context = (
                        clause[max(0, start - 12):start] + gap
                    )
                elif start >= fact_end:
                    owner_context = gap
                else:
                    owner_context = clause[
                        max(0, start - 12):min(len(clause), end + 12)
                    ]
                mentioned_attributes = {
                    attribute
                    for attribute in _MANDATORY_ATTRIBUTE_TERMS
                    if attribute in owner_context
                }
                if (
                    mentioned_attributes
                    and not mentioned_attributes.intersection(
                        expected_attributes
                    )
                ):
                    start = clause.find(term, start + len(term))
                    continue

                mentioned_subjects = {
                    candidate_subject
                    for candidate_subject, terms in (
                        _MANDATORY_SUBJECT_TERMS.items()
                    )
                    if any(
                        subject_term.upper() in owner_context.upper()
                        for subject_term in terms
                    )
                }
                if (
                    mentioned_subjects
                    and subject not in mentioned_subjects
                ):
                    start = clause.find(term, start + len(term))
                    continue

                if (
                    end <= fact_start
                    and term not in _MANDATORY_MODAL_QUALIFICATION_TERMS
                    and _MANDATORY_AFFIRMATIVE_VERB_PATTERN.search(gap)
                ):
                    start = clause.find(term, start + len(term))
                    continue
                return True
        return False

    @classmethod
    def _detect_fact_commitment(
        cls,
        clause: str,
        fact_span: tuple[int, int],
        *,
        fact_type: str,
        subject: str,
        unit: str,
    ) -> str:
        fact_start, fact_end = fact_span
        local_context = clause[
            max(0, fact_start - 16):min(len(clause), fact_end + 10)
        ]
        if _MANDATORY_INTERROGATIVE_PATTERN.search(local_context):
            return "interrogative"
        if _MANDATORY_NEGATION_PATTERN.search(local_context):
            return "negated"
        if cls._mandatory_qualification_matches_fact(
            clause,
            fact_span,
            fact_type=fact_type,
            subject=subject,
            unit=unit,
        ):
            return "qualified"
        return "affirmative"

    @classmethod
    def _mandatory_atomic_clauses(cls, content: str) -> list[str]:
        clauses: list[str] = []
        scoped_content = _MANDATORY_SCOPE_BOUNDARY_PATTERN.sub(
            "\n",
            content or "",
        )
        scoped_content = re.sub(r"([？?])", r"\1\n", scoped_content)
        for item in re.split(r"[，,；;。！!\n]+", scoped_content):
            clause = cls._normalize_mandatory_claim(item)
            if clause and not clause.startswith("#"):
                clauses.append(clause)
        return clauses

    @staticmethod
    def _fallback_mandatory_subject(clause: str) -> tuple[str, str, str]:
        mappings = (
            ("服务器", "resource_commitment", "server", "unspecified"),
            ("GPU", "resource_commitment", "gpu", "unspecified"),
            ("NPU", "resource_commitment", "npu", "unspecified"),
            ("CPU", "resource_commitment", "cpu", "unspecified"),
            ("机柜", "resource_commitment", "rack", "unspecified"),
            ("节点", "resource_commitment", "node", "unspecified"),
            ("工程师", "resource_commitment", "engineer", "unspecified"),
            ("人员", "resource_commitment", "staff_total", "unspecified"),
            ("职能部门", "department_count", "functional_department", "count"),
            ("业务部门", "department_count", "business_department", "count"),
            ("并发", "performance", "concurrency", "count"),
            ("QPS", "performance", "qps", "qps"),
            ("准确率", "performance", "accuracy", "percent"),
            ("召回率", "performance", "recall", "percent"),
            ("响应时间", "performance", "response_time", "unspecified"),
            ("预算", "finance", "budget", "unspecified"),
            ("金额", "finance", "amount", "unspecified"),
            ("成本", "finance", "cost", "unspecified"),
            ("工期", "duration", "project", "unspecified"),
            ("周期", "duration", "project", "unspecified"),
        )
        normalized = clause.upper()
        for keyword, fact_type, subject, unit in mappings:
            if keyword.upper() in normalized:
                return fact_type, subject, unit
        return "high_risk_fact", "unknown", "unspecified"

    @classmethod
    def _extract_mandatory_high_risk_facts(
        cls,
        content: str,
    ) -> list[_MandatoryProjectFact]:
        facts: list[_MandatoryProjectFact] = []
        seen: set[
            tuple[str, str, int | float | None, str, str, str]
        ] = set()
        for clause in cls._mandatory_atomic_clauses(content):
            clause_facts: list[_MandatoryProjectFact] = []
            for fact_type, subject, unit, pattern in _MANDATORY_FACT_PATTERNS:
                for match in pattern.finditer(clause):
                    commitment = cls._detect_fact_commitment(
                        clause,
                        match.span(),
                        fact_type=fact_type,
                        subject=subject,
                        unit=unit,
                    )
                    fact = _MandatoryProjectFact(
                        fact_type=fact_type,
                        subject=subject,
                        value=cls._mandatory_numeric_value(match.group("value")),
                        unit=unit,
                        commitment=commitment,
                        claim=clause,
                    )
                    clause_facts.append(fact)

            if not clause_facts:
                extracted_values = [
                    cls._mandatory_numeric_value(item)
                    for item in _NUMERIC_OR_MODEL_PATTERN.findall(clause)
                    if not re.search(r"[a-z]", str(item), re.IGNORECASE)
                ]
                has_quantified_fact = bool(
                    any(value is not None for value in extracted_values)
                    and _HIGH_RISK_QUANTIFIED_PATTERN.search(clause)
                )
                has_resource_commitment = bool(
                    (
                        _HARDWARE_RESOURCE_PATTERN.search(clause)
                        and _RESOURCE_COMMITMENT_VERB_PATTERN.search(clause)
                    )
                    or (
                        _STAFF_RESOURCE_PATTERN.search(clause)
                        and _STAFF_COMMITMENT_VERB_PATTERN.search(clause)
                    )
                )
                if has_quantified_fact or has_resource_commitment:
                    fact_type, subject, unit = cls._fallback_mandatory_subject(
                        clause
                    )
                    value = next(
                        (
                            item
                            for item in extracted_values
                            if item is not None
                        ),
                        None,
                    )
                    value_match = re.search(_MANDATORY_NUMBER, clause)
                    span = value_match.span() if value_match else (0, len(clause))
                    clause_facts.append(
                        _MandatoryProjectFact(
                            fact_type=fact_type,
                            subject=subject,
                            value=value,
                            unit=unit,
                            commitment=cls._detect_fact_commitment(
                                clause,
                                span,
                                fact_type=fact_type,
                                subject=subject,
                                unit=unit,
                            ),
                            claim=clause,
                        )
                    )

            for fact in clause_facts:
                identity = (*fact.support_key, fact.claim)
                if identity in seen:
                    continue
                seen.add(identity)
                facts.append(fact)
        return facts

    @classmethod
    def _build_mandatory_project_fact_support(
        cls,
        project_input: ProjectInputSchema,
    ) -> set[tuple[str, str, int | float | None, str, str]]:
        support: set[
            tuple[str, str, int | float | None, str, str]
        ] = set()

        def add(
            fact_type: str,
            subject: str,
            value: Any,
            unit: str,
        ) -> None:
            normalized_value = cls._mandatory_numeric_value(value)
            if normalized_value is not None:
                support.add(
                    (
                        fact_type,
                        subject,
                        normalized_value,
                        unit,
                        "affirmative",
                    )
                )

        add("staff_count", "staff_total", project_input.total_staff, "count")
        add(
            "department_count",
            "functional_department",
            project_input.functional_department_count,
            "count",
        )
        add(
            "department_count",
            "business_department",
            project_input.business_department_count,
            "count",
        )
        for group in project_input.department_groups:
            group_name = str(group.group_name or "")
            if "职能" in group_name:
                add(
                    "department_count",
                    "functional_department",
                    group.department_count,
                    "count",
                )
            elif "业务" in group_name:
                add(
                    "department_count",
                    "business_department",
                    group.department_count,
                    "count",
                )
        for resource in project_input.hardware_resources:
            add(
                "hardware_count",
                "server",
                resource.server_count,
                "count",
            )
            resource_text = " ".join(
                [
                    str(resource.resource_type or ""),
                    str(resource.device_model or ""),
                    str(resource.description or ""),
                ]
            ).upper()
            for keyword, subject in (
                ("GPU", "gpu"),
                ("显卡", "gpu"),
                ("CPU", "cpu"),
                ("NPU", "npu"),
            ):
                if keyword.upper() in resource_text:
                    add(
                        "hardware_count",
                        subject,
                        resource.total_cards,
                        "count",
                    )

        for key, value in dict(project_input.metadata or {}).items():
            mapping = _MANDATORY_METADATA_FACTS.get(str(key))
            if mapping is not None:
                add(*mapping[:2], value, mapping[2])

        for source_text in (
            project_input.user_query,
            project_input.business_goal,
        ):
            support.update(
                fact.support_key
                for fact in cls._extract_mandatory_high_risk_facts(source_text)
                if fact.commitment == "affirmative"
            )
        return support

    @classmethod
    def mandatory_project_fact_violations(
        cls,
        content: str,
        project_input: ProjectInputSchema,
        *,
        grounded_claims: Iterable[str] = (),
    ) -> List[Dict[str, Any]]:
        """Validate affirmative deliverable facts against exact support slots."""

        project_support = cls._build_mandatory_project_fact_support(
            project_input
        )
        verified_claims = {
            clause
            for item in grounded_claims
            for clause in cls._mandatory_atomic_clauses(item)
        }
        violations: list[Dict[str, Any]] = []
        for fact in cls._extract_mandatory_high_risk_facts(content):
            if fact.commitment != "affirmative":
                continue
            if cls._normalize_mandatory_claim(fact.claim) in verified_claims:
                continue
            if fact.support_key in project_support:
                continue
            violations.append(
                {
                    "claim": fact.claim,
                    "reason": "mandatory_project_fact_not_supported",
                    "fact_type": fact.fact_type,
                    "subject": fact.subject,
                    "value": fact.value,
                    "unit": fact.unit,
                    "commitment": fact.commitment,
                }
            )
        return violations

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
