"""章节生成完整性、语义审查和文档硬门禁的评测协议。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import SchemaBase


class TruncationCheckSchema(SchemaBase):
    """记录模型输出是否因长度、未闭合 JSON 或句子不完整而疑似截断。"""
    schema_version: str = "truncation_check_v1"
    truncated: bool = False
    reasons: List[str] = Field(default_factory=list)
    finish_reason: Optional[str] = None
    json_closed: Optional[bool] = None
    sentence_complete: Optional[bool] = None
    output_chars: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SemanticGateIssueSchema(SchemaBase):
    """语义审查发现的一条问题及其严重度、证据和建议处置。"""
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
    """一次语义审查的决定、问题列表及模型调用血缘。"""
    schema_version: str = "semantic_gate_result_v1"
    decision: str = "pass"
    issues: List[SemanticGateIssueSchema] = Field(default_factory=list)
    summary: str = ""
    model_call_id: Optional[str] = None
    fallback_used: bool = False
    error_message: Optional[str] = None
    raw_output: Dict[str, Any] = Field(default_factory=dict)


class SectionEvalSchema(SchemaBase):
    """合并确定性检查与可选语义门禁后的章节级评测结论。"""
    schema_version: str = "section_eval_v2"
    passed: bool
    checks: Dict[str, bool] = Field(default_factory=dict)
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    semantic_gate: Optional[SemanticGateResultSchema] = None


class HardGateResultSchema(SchemaBase):
    """决定草稿能否升级为业务交付物的文档级硬门禁结果。"""
    schema_version: str = "hard_gate_result_v2"
    passed: bool
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    checks: Dict[str, bool] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
