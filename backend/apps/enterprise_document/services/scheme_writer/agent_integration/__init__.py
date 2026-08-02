"""Production-owned adapters that connect stateless business Agents to the mainline."""

from .planner_adapter import (
    MainlinePlannerAdapter,
    MainlinePlannerFailure,
    MainlinePlannerOutcome,
    build_mainline_planner_adapter,
)
from .section_agent_adapter import (
    MainlineResearchOutcome,
    MainlineSectionAgentAdapter,
    MainlineSectionAgentFailure,
    MainlineVerifierOutcome,
    MainlineWriterOutcome,
    build_round4_fake_section_agent_adapter,
    build_round5_section_agent_adapter,
)

__all__ = [
    "MainlinePlannerAdapter",
    "MainlinePlannerFailure",
    "MainlinePlannerOutcome",
    "build_mainline_planner_adapter",
    "MainlineResearchOutcome",
    "MainlineSectionAgentAdapter",
    "MainlineSectionAgentFailure",
    "MainlineVerifierOutcome",
    "MainlineWriterOutcome",
    "build_round4_fake_section_agent_adapter",
    "build_round5_section_agent_adapter",
]
