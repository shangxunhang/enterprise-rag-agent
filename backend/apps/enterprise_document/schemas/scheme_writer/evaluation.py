# =============================================================================
# 中文阅读说明：企业文档生成业务模块，负责方案规划、检索、章节生成、引用和验收。
# 主要定义：TruncationCheckSchema、SemanticGateIssueSchema、SemanticGateResultSchema、SectionEvalSchema、HardGateResultSchema。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Generation integrity and evaluation schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from schemas.common import SchemaBase


# 阅读注释（类）：封装 truncation check Schema，定义跨模块传递的数据结构与字段约束。
class TruncationCheckSchema(SchemaBase):
    """封装 truncation check Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "truncation_check_v1"
    truncated: bool = False
    reasons: List[str] = Field(default_factory=list)
    finish_reason: Optional[str] = None
    json_closed: Optional[bool] = None
    sentence_complete: Optional[bool] = None
    output_chars: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 semantic gate issue Schema，定义跨模块传递的数据结构与字段约束。
class SemanticGateIssueSchema(SchemaBase):
    """封装 semantic gate issue Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "semantic_gate_issue_v1"
    issue_type: str
    severity: str
    claim: str = ""
    reason: str = ""
    recommended_action: str = "human_review"
    confidence: float = 0.0
    source: str = "llm_semantic_judge"
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 semantic gate 结果 Schema，定义跨模块传递的数据结构与字段约束。
class SemanticGateResultSchema(SchemaBase):
    """封装 semantic gate 结果 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "semantic_gate_result_v1"
    decision: str = "pass"
    issues: List[SemanticGateIssueSchema] = Field(default_factory=list)
    summary: str = ""
    model_call_id: Optional[str] = None
    fallback_used: bool = False
    error_message: Optional[str] = None
    raw_output: Dict[str, Any] = Field(default_factory=dict)


# 阅读注释（类）：封装 章节 评测 Schema，定义跨模块传递的数据结构与字段约束。
class SectionEvalSchema(SchemaBase):
    """封装 章节 评测 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "section_eval_v2"
    passed: bool
    checks: Dict[str, bool] = Field(default_factory=dict)
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    semantic_gate: Optional[SemanticGateResultSchema] = None


# 阅读注释（类）：封装 hard gate 结果 Schema，定义跨模块传递的数据结构与字段约束。
class HardGateResultSchema(SchemaBase):
    """封装 hard gate 结果 Schema，定义跨模块传递的数据结构与字段约束。"""
    schema_version: str = "hard_gate_result_v2"
    passed: bool
    failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    checks: Dict[str, bool] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
