"""Adaptive Profile routing package.

Runtime composition is intentionally not imported here because the global RAG
component registry imports the policy plugin during startup.
"""

from rag.routing.policy import AdaptiveProfileDecision, ExplainableRuleProfileRouterPlugin
from rag.routing.schema import (
    AdaptiveProfileRouterConfig,
    AdaptiveProfileRouterConfigLoader,
    ProfileTargetConfig,
    RoutingConditionConfig,
    RoutingRuleConfig,
    peek_config_schema_version,
)

__all__ = [
    "AdaptiveProfileDecision",
    "ExplainableRuleProfileRouterPlugin",
    "AdaptiveProfileRouterConfig",
    "AdaptiveProfileRouterConfigLoader",
    "ProfileTargetConfig",
    "RoutingConditionConfig",
    "RoutingRuleConfig",
    "peek_config_schema_version",
]
