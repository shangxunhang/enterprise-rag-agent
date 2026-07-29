"""Paragraph 完成后的 Excel 硬事实熔断与轻量告警。"""

from __future__ import annotations

import hashlib
import re

from apps.enterprise_document.schemas.agent_rag_v1 import (
    ClaimVerificationStatus,
    FactSnapshotSchema,
    ParagraphPrecheckResultSchema,
    VerifierIssueSchema,
)

from .claim_pipeline import ClaimPipeline


class ParagraphPrecheckService:
    _HARD_CLAIM_TYPES = {
        "device",
        "model",
        "specification",
        "quantity",
        "amount",
        "formula",
        "calculation_result",
        "construction_scope",
    }
    _UNKNOWN_DEVICE = re.compile(
        r"(?:部署|配置|采用|新增)\s*"
        r"(?P<name>[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9_-]{1,15})"
        r"\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>台|套|个|块)"
    )

    def __init__(self, claim_pipeline: ClaimPipeline) -> None:
        self.claim_pipeline = claim_pipeline

    def check(
        self,
        *,
        section_id: str,
        paragraph_id: str,
        content: str,
        fact_snapshot: FactSnapshotSchema,
    ) -> ParagraphPrecheckResultSchema:
        claims = self.claim_pipeline.extract(
            section_id=section_id,
            paragraph_id=paragraph_id,
            content=content,
            fact_snapshot=fact_snapshot,
        )
        issues: list[VerifierIssueSchema] = []
        for claim in claims:
            if (
                claim.claim_type in self._HARD_CLAIM_TYPES
                and claim.verification_status
                == ClaimVerificationStatus.CONFLICTED
            ):
                issues.append(
                    self._issue(
                        section_id=section_id,
                        paragraph_id=paragraph_id,
                        issue_code=(
                            "EXCEL_"
                            + claim.claim_type.upper()
                            + "_CONFLICT"
                        ),
                        severity="blocking",
                        message=(
                            f"{claim.claim_type} 与 Excel 权威事实冲突"
                        ),
                        claim_ids=[claim.claim_id],
                    )
                )
            elif (
                claim.claim_type not in self._HARD_CLAIM_TYPES
                and claim.verification_status
                in {
                    ClaimVerificationStatus.PENDING,
                    ClaimVerificationStatus.UNSUPPORTED,
                }
            ):
                issues.append(
                    self._issue(
                        section_id=section_id,
                        paragraph_id=paragraph_id,
                        issue_code="CLAIM_BINDING_PENDING",
                        severity="warning",
                        message="普通技术结论尚未完成来源绑定",
                        claim_ids=[claim.claim_id],
                    )
                )

        known_names = {
            name.casefold()
            for fact in fact_snapshot.devices
            for name in [fact.device_name, *fact.aliases]
        }
        for match in self._UNKNOWN_DEVICE.finditer(content):
            candidate = match.group("name").casefold()
            if not any(
                candidate == known or candidate.endswith(known)
                for known in known_names
            ):
                issues.append(
                    self._issue(
                        section_id=section_id,
                        paragraph_id=paragraph_id,
                        issue_code="DEVICE_NOT_IN_EXCEL",
                        severity="blocking",
                        message=(
                            f"正文使用 Excel 中不存在的设备："
                            f"{match.group('name')}"
                        ),
                        claim_ids=[],
                    )
                )

        blocking = any(issue.severity == "blocking" for issue in issues)
        return ParagraphPrecheckResultSchema(
            section_id=section_id,
            paragraph_id=paragraph_id,
            passed=not blocking,
            blocking=blocking,
            claims=claims,
            issues=issues,
        )

    @staticmethod
    def _issue(
        *,
        section_id: str,
        paragraph_id: str,
        issue_code: str,
        severity: str,
        message: str,
        claim_ids: list[str],
    ) -> VerifierIssueSchema:
        raw = f"{section_id}:{paragraph_id}:{issue_code}:{','.join(claim_ids)}"
        issue_id = "issue_" + hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()[:20]
        return VerifierIssueSchema(
            issue_id=issue_id,
            issue_code=issue_code,
            severity=severity,
            message=message,
            section_id=section_id,
            paragraph_id=paragraph_id,
            claim_ids=claim_ids,
        )
