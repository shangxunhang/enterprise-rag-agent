"""Small registry dedicated to application generation-quality plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apps.enterprise_document.config.grounded_generation import (
    GenerationPluginConfig,
)
from apps.enterprise_document.quality.plugins import (
    LocalRewriteRepairStrategyPlugin,
    NoOpGenerationCheckerPlugin,
    NoOpRepairStrategyPlugin,
    SelfRAGLiteGenerationCheckerPlugin,
)


@dataclass(frozen=True)
class GenerationPluginMetadata:
    category: str
    name: str
    version: str
    implementation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "name": self.name,
            "version": self.version,
            "implementation": self.implementation,
        }


class GenerationPluginRegistry:
    def __init__(self) -> None:
        self._builders: dict[tuple[str, str, str], Callable[..., Any]] = {}

    def register(
        self,
        *,
        category: str,
        name: str,
        version: str,
        builder: Callable[..., Any],
    ) -> None:
        key = (category.strip(), name.strip(), version.strip())
        if not all(key):
            raise ValueError("generation plugin category/name/version cannot be blank")
        if key in self._builders:
            raise ValueError(f"duplicate generation plugin: {'/'.join(key)}")
        self._builders[key] = builder

    def build(
        self,
        *,
        category: str,
        config: GenerationPluginConfig,
        build_context: Any = None,
    ) -> Any:
        if not config.enabled:
            raise ValueError("disabled generation plugin cannot be built")
        key = (category, config.name, config.version)
        try:
            builder = self._builders[key]
        except KeyError as exc:
            raise ValueError(
                f"unknown generation component {category}/{config.name}"
                f"@{config.version}"
            ) from exc
        instance = builder(build_context=build_context, **dict(config.params))
        required_method = (
            "check" if category == "generation_checker" else "repair"
        )
        if not callable(getattr(instance, required_method, None)) or not callable(
            getattr(instance, "execution_metadata", None)
        ):
            raise TypeError(
                f"invalid {category} plugin implementation: "
                f"{instance.__class__.__qualname__}"
            )
        instance.plugin_metadata = GenerationPluginMetadata(
            category=category,
            name=config.name,
            version=config.version,
            implementation=(
                f"{instance.__class__.__module__}."
                f"{instance.__class__.__qualname__}"
            ),
        )
        return instance


def build_generation_plugin_registry() -> GenerationPluginRegistry:
    registry = GenerationPluginRegistry()
    registry.register(
        category="generation_checker",
        name="self_rag_lite",
        version="v1",
        builder=SelfRAGLiteGenerationCheckerPlugin,
    )
    registry.register(
        category="generation_checker",
        name="noop_generation",
        version="v1",
        builder=NoOpGenerationCheckerPlugin,
    )
    registry.register(
        category="repair_strategy",
        name="local_rewrite",
        version="v1",
        builder=LocalRewriteRepairStrategyPlugin,
    )
    registry.register(
        category="repair_strategy",
        name="noop_repair",
        version="v1",
        builder=NoOpRepairStrategyPlugin,
    )
    return registry
