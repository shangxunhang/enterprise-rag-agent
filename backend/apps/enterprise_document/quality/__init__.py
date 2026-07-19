"""Application-owned grounded generation quality plugins."""

from apps.enterprise_document.quality.plugins import (
    LocalRewriteRepairStrategyPlugin,
    NoOpGenerationCheckerPlugin,
    NoOpRepairStrategyPlugin,
    SelfRAGLiteGenerationCheckerPlugin,
)
from apps.enterprise_document.quality.ports import (
    GenerationCheckerPort,
    RepairOutput,
    RepairStrategyPort,
)
from apps.enterprise_document.quality.registry import (
    build_generation_plugin_registry,
)

__all__ = [
    "GenerationCheckerPort",
    "LocalRewriteRepairStrategyPlugin",
    "NoOpGenerationCheckerPlugin",
    "NoOpRepairStrategyPlugin",
    "RepairOutput",
    "RepairStrategyPort",
    "SelfRAGLiteGenerationCheckerPlugin",
    "build_generation_plugin_registry",
]
