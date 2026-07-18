"""Generation integrity and evaluation schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import SchemaBase


class TruncationCheckSchema(SchemaBase):
    schema_version: str = "truncation_check_v1"
    truncated: bool = False
    reasons: List[str] = Field(default_factory=list)
    finish_reason: Optional[str] = None
    json_closed: Optional[bool] = None
    sentence_complete: Optional[bool] = None
    output_chars: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticGateIssueSchema(SchemaBase):
    schema_version: str = "semantic_gate_issue_v1"
    issue_type: str
    severity: str
    claim: str = ""
    reason: str = ""
    recommended_action: str = "human_review"
    confidence: float = 0.0
    source: str = "llm_semantic_judge"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticGateResultSchema(SchemaBase):
    schema_version: str = "semantic_gate_result_v1"
    decision: str = "pass"
    issues: List[SemanticGateIssueSchema] = Field(default_factory=list)
    summary: str = ""
    model_call_id: Optional[str] = None
    fallback_used: bool = False
    error_message: Optional[str] = None
    raw_output: Dict[str, Any] = Field(default_factory=dict)


class SectionEvalSchema(SchemaBase):
    schema_version: str = "section_eval_v2"
    passed: bool
    checks: Dict[str, bool] = Field(default_factory=dict)
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    semantic_gate: Optional[SemanticGateResultSchema] = None


class HardGateResultSchema(SchemaBase):
    schema_version: str = "hard_gate_result_v2"
    passed: bool
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    checks: Dict[str, bool] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
