"""Deterministic extraction and fail-closed gating of concrete high-risk facts."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Iterable

from apps.enterprise_document.schemas.project_input_schema import ProjectInputSchema
from schemas.citation import CitationBindingSchema

from .trusted_fact_ledger import (
    TrustedFactLedgerBuilder,
    TrustedHighRiskFact,
)


_NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)"
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
_CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9_.:\-]+)\]")
_CLAUSE_BOUNDARY = re.compile(r"[。！？!?；;\n]")


@dataclass(frozen=True)
class _FactPattern:
    fact_type: str
    subject: str
    unit: str
    pattern: re.Pattern[str]
    multiplier: float = 1.0


_FACT_PATTERNS = (
    _FactPattern(
        "hardware_count",
        "server",
        "count",
        re.compile(
            rf"(?P<value>{_NUMBER})\s*(?:台|套)\s*"
            rf"(?:(?:GPU|CPU|NPU)\s*)?服务器",
            re.IGNORECASE,
        ),
    ),
    _FactPattern(
        "hardware_count",
        "server",
        "count",
        re.compile(
            rf"(?:(?:GPU|CPU|NPU)\s*)?服务器(?:数量|总数|规模)?"
            rf"(?:为|共|达到|配置|部署)?\s*"
            rf"(?P<value>{_NUMBER})\s*(?:台|套)",
            re.IGNORECASE,
        ),
    ),
    _FactPattern(
        "hardware_count",
        "gpu",
        "count",
        re.compile(
            rf"(?P<value>{_NUMBER})\s*(?:块|张|卡|个|台)\s*"
            rf"(?:GPU(?!\s*服务器)|显卡)",
            re.IGNORECASE,
        ),
    ),
    _FactPattern(
        "hardware_count",
        "gpu",
        "count",
        re.compile(
            rf"(?:GPU|显卡)(?:数量|总数)?(?:为|共|达到|配置)?\s*"
            rf"(?P<value>{_NUMBER})\s*(?:块|张|卡|个|台)",
            re.IGNORECASE,
        ),
    ),
    _FactPattern(
        "hardware_count",
        "cpu",
        "count",
        re.compile(
            rf"(?P<value>{_NUMBER})\s*(?:块|颗|片|个|台)\s*"
            rf"CPU(?!\s*服务器)",
            re.IGNORECASE,
        ),
    ),
    _FactPattern(
        "hardware_count",
        "npu",
        "count",
        re.compile(
            rf"(?P<value>{_NUMBER})\s*(?:块|张|卡|个|台)\s*"
            rf"NPU(?!\s*服务器)",
            re.IGNORECASE,
        ),
    ),
    _FactPattern(
        "hardware_count",
        "rack",
        "count",
        re.compile(rf"(?P<value>{_NUMBER})\s*(?:个|台|套)\s*机柜"),
    ),
    _FactPattern(
        "hardware_count",
        "node",
        "count",
        re.compile(
            rf"(?P<value>{_NUMBER})\s*(?:个|台|套)?\s*"
            rf"(?:数据库节点|计算节点|节点)"
        ),
    ),
    _FactPattern(
        "staff_count",
        "engineer",
        "count",
        re.compile(
            rf"(?P<value>{_NUMBER})\s*(?:名|人)\s*"
            rf"(?:高级技术人员|工程师|项目经理|开发人员|测试人员|运维人员)"
        ),
    ),
    _FactPattern(
        "staff_count",
        "staff_total",
        "count",
        re.compile(rf"(?P<value>{_NUMBER})\s*(?:名|人)\s*(?:员工|人员)"),
    ),
    _FactPattern(
        "department_count",
        "functional_department",
        "count",
        re.compile(rf"(?P<value>{_NUMBER})\s*(?:个|类)?\s*职能部门"),
    ),
    _FactPattern(
        "department_count",
        "business_department",
        "count",
        re.compile(rf"(?P<value>{_NUMBER})\s*(?:个|类)?\s*业务部门"),
    ),
    _FactPattern(
        "duration",
        "project",
        "month",
        re.compile(
            rf"(?:项目工期|建设工期|建设期|项目周期|周期|工期)"
            rf"(?:为|共|预计|计划)?\s*(?P<value>{_NUMBER})\s*(?:个月|月)"
        ),
    ),
    _FactPattern(
        "duration",
        "project",
        "month",
        re.compile(
            rf"(?P<value>{_NUMBER})\s*(?:个月|月)\s*"
            rf"(?:项目工期|建设工期|建设期|项目周期|周期|工期)"
        ),
    ),
    _FactPattern(
        "finance",
        "budget",
        "yuan",
        re.compile(
            rf"(?:预算|项目预算|金额|成本)(?:为|共|约|达到)?\s*"
            rf"(?P<value>{_NUMBER})\s*万元"
        ),
        10000.0,
    ),
    _FactPattern(
        "finance",
        "budget",
        "yuan",
        re.compile(
            rf"(?P<value>{_NUMBER})\s*万元\s*(?:预算|项目预算|金额|成本)"
        ),
        10000.0,
    ),
    _FactPattern(
        "performance",
        "accuracy",
        "percent",
        re.compile(
            rf"(?:准确率|精度)(?:为|达到|不低于)?\s*"
            rf"(?P<value>{_NUMBER})\s*%"
        ),
    ),
    _FactPattern(
        "performance",
        "accuracy",
        "percent",
        re.compile(rf"(?P<value>{_NUMBER})\s*%\s*(?:准确率|精度)"),
    ),
)

_NEGATION_SIGNAL = re.compile(
    r"(?:不再|不要|不得|禁止|无需|不需要|不应|不能|不采用|不部署|取消|避免)"
)
_INTERROGATIVE_SIGNAL = re.compile(r"(?:是否|能否|可否|需不需要|要不要)")
_QUALIFICATION_SIGNAL = re.compile(r"(?:建议|可考虑|原则上|待确认|尚未确定)")
_STRONG_AFFIRMATIVE_SIGNAL = re.compile(
    r"(?:已确认|明确确认|确定|新增|本项目(?:部署|配置|配备|建设|采购|采用|设置))"
)


@dataclass(frozen=True)
class ExtractedHighRiskClaim:
    fact_type: str
    subject: str
    value: int | float | str
    unit: str
    commitment: str
    claim_text: str
    clause_text: str
    span_start: int
    span_end: int

    @property
    def identity(self) -> tuple[str, str, int | float | str, str]:
        return (self.fact_type, self.subject, self.value, self.unit)


@dataclass(frozen=True)
class HighRiskClaimGateResult:
    claims: tuple[ExtractedHighRiskClaim, ...]
    violations: tuple[dict[str, Any], ...]

    @property
    def passed(self) -> bool:
        return not self.violations


def _numeric_value(value: str, *, multiplier: float = 1.0) -> int | float | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        number = float(text) * multiplier
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
    number = float(total + section + digit) * multiplier
    return int(number) if number.is_integer() else number


def _normalized_claim_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _CITATION_PATTERN.sub("", text)
    text = re.sub(r"^[\-+*\d.、（）()\s]+", "", text)
    text = text.replace("**", "")
    return re.sub(r"[\s。！？!?；;，,]+", "", text).lower()


class HighRiskClaimExtractor:
    """Extract concrete high-risk values without deciding whether to trust them."""

    @staticmethod
    def _clause_span(content: str, start: int, end: int) -> tuple[int, int]:
        previous = [
            match.end()
            for match in _CLAUSE_BOUNDARY.finditer(content, 0, start)
        ]
        clause_start = previous[-1] if previous else 0
        following = _CLAUSE_BOUNDARY.search(content, end)
        clause_end = following.end() if following else len(content)
        return clause_start, clause_end

    @staticmethod
    def _commitment(
        clause: str,
        fact_start_in_clause: int,
    ) -> str:
        prefix = clause[:fact_start_in_clause]
        signals: list[tuple[int, str]] = []
        signals.extend(
            (match.start(), "negated")
            for match in _NEGATION_SIGNAL.finditer(prefix)
        )
        signals.extend(
            (match.start(), "interrogative")
            for match in _INTERROGATIVE_SIGNAL.finditer(prefix)
        )
        signals.extend(
            (match.start(), "qualified")
            for match in _QUALIFICATION_SIGNAL.finditer(prefix)
        )
        signals.extend(
            (match.start(), "affirmative")
            for match in _STRONG_AFFIRMATIVE_SIGNAL.finditer(prefix)
        )
        if not signals:
            return "affirmative"

        latest_affirmative = max(
            (
                position
                for position, kind in signals
                if kind == "affirmative"
            ),
            default=-1,
        )
        active_non_affirmative = [
            (position, kind)
            for position, kind in signals
            if kind != "affirmative" and position > latest_affirmative
        ]
        active_kinds = {
            kind for _, kind in active_non_affirmative
        }
        if len(active_kinds) > 1:
            return "ambiguous"
        if active_non_affirmative:
            return max(active_non_affirmative, key=lambda item: item[0])[1]
        return "affirmative"

    def extract(self, content: str) -> tuple[ExtractedHighRiskClaim, ...]:
        normalized = unicodedata.normalize("NFKC", str(content or ""))
        claims: list[ExtractedHighRiskClaim] = []
        seen_spans: set[tuple[int, int, str, str]] = set()

        for fact_pattern in _FACT_PATTERNS:
            for match in fact_pattern.pattern.finditer(normalized):
                value = _numeric_value(
                    match.group("value"),
                    multiplier=fact_pattern.multiplier,
                )
                if value is None:
                    continue
                identity = (
                    match.start(),
                    match.end(),
                    fact_pattern.fact_type,
                    fact_pattern.subject,
                )
                if identity in seen_spans:
                    continue
                seen_spans.add(identity)
                clause_start, clause_end = self._clause_span(
                    normalized,
                    match.start(),
                    match.end(),
                )
                clause = normalized[clause_start:clause_end].strip()
                fact_start_in_clause = max(
                    0,
                    match.start() - clause_start - (
                        len(normalized[clause_start:clause_end])
                        - len(normalized[clause_start:clause_end].lstrip())
                    ),
                )
                claims.append(
                    ExtractedHighRiskClaim(
                        fact_type=fact_pattern.fact_type,
                        subject=fact_pattern.subject,
                        value=value,
                        unit=fact_pattern.unit,
                        commitment=self._commitment(
                            clause,
                            fact_start_in_clause,
                        ),
                        claim_text=match.group(0).strip(),
                        clause_text=clause,
                        span_start=match.start(),
                        span_end=match.end(),
                    )
                )
        return tuple(sorted(claims, key=lambda item: (item.span_start, item.span_end)))


class HighRiskClaimGate:
    """Require exact structured or occurrence-bound evidence for every value."""

    def __init__(
        self,
        *,
        extractor: HighRiskClaimExtractor | None = None,
        ledger_builder: TrustedFactLedgerBuilder | None = None,
    ) -> None:
        self.extractor = extractor or HighRiskClaimExtractor()
        self.ledger_builder = ledger_builder or TrustedFactLedgerBuilder()

    def _binding_supports_claim(
        self,
        binding: CitationBindingSchema,
        claim: ExtractedHighRiskClaim,
        *,
        content: str,
        target_section_id: str | None,
    ) -> bool:
        if not bool((binding.metadata or {}).get("grounding_verified")):
            return False
        if target_section_id and binding.target_section_id != target_section_id:
            return False

        binding_claim = _normalized_claim_text(binding.claim_text)
        claim_clause = _normalized_claim_text(claim.clause_text)
        claim_occurrence_matches = bool(
            binding_claim
            and claim_clause
            and (
                binding_claim == claim_clause
                or binding_claim in claim_clause
                or claim_clause in binding_claim
            )
        )
        marker_start = binding.target_marker_start
        if marker_start is not None and target_section_id:
            if marker_start < claim.span_end:
                return False
        if not claim_occurrence_matches:
            return False

        evidence_facts = self.extractor.extract(str(binding.quote_text or ""))
        return any(item.identity == claim.identity for item in evidence_facts)

    def evaluate(
        self,
        content: str,
        *,
        project_input: ProjectInputSchema | None = None,
        trusted_facts: Iterable[TrustedHighRiskFact] = (),
        bindings: Iterable[CitationBindingSchema] = (),
        target_section_id: str | None = None,
    ) -> HighRiskClaimGateResult:
        ledger = list(trusted_facts)
        if project_input is not None:
            ledger.extend(self.ledger_builder.build(project_input))
        ledger_keys = {fact.identity for fact in ledger}
        binding_list = list(bindings)

        claims = self.extractor.extract(content)
        violations: list[dict[str, Any]] = []
        for claim in claims:
            supported = claim.identity in ledger_keys or any(
                self._binding_supports_claim(
                    binding,
                    claim,
                    content=content,
                    target_section_id=target_section_id,
                )
                for binding in binding_list
            )
            reason = None
            if claim.commitment == "ambiguous":
                reason = "ambiguous_high_risk_claim_scope"
            elif not supported:
                reason = "mandatory_project_fact_not_supported"
            if reason is None:
                continue
            violations.append(
                {
                    "claim": claim.clause_text,
                    "reason": reason,
                    "fact_type": claim.fact_type,
                    "subject": claim.subject,
                    "value": claim.value,
                    "unit": claim.unit,
                    "commitment": claim.commitment,
                    "span_start": claim.span_start,
                    "span_end": claim.span_end,
                }
            )
        return HighRiskClaimGateResult(
            claims=claims,
            violations=tuple(violations),
        )
