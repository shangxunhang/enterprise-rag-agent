"""Version-aware component registry used by the RAG composition root."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rag.config.pipeline_config import ComponentConfig

T = TypeVar("T")
Builder = Callable[..., T]


@dataclass(frozen=True)
class ComponentKey:
    category: str
    name: str
    version: str

    @classmethod
    def create(cls, category: str, name: str, version: str) -> "ComponentKey":
        values = [str(item or "").strip() for item in (category, name, version)]
        if not all(values):
            raise ValueError("component category/name/version cannot be blank")
        return cls(*values)


@dataclass(frozen=True)
class ComponentMetadata:
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


class ComponentRegistry(Generic[T]):
    """Map ``(category, name, version)`` to component builders.

    The registry contains no strategy branches. New plugins extend the registry
    through registration and do not require changes to the pipeline or factory.
    """

    def __init__(self) -> None:
        self._builders: dict[ComponentKey, Builder[T]] = {}

    def register(
        self,
        *,
        category: str,
        name: str,
        version: str,
        builder: Builder[T],
    ) -> None:
        key = ComponentKey.create(category, name, version)
        if key in self._builders:
            raise ValueError(
                "duplicate RAG component registration: "
                f"{key.category}/{key.name}@{key.version}"
            )
        if not callable(builder):
            raise TypeError("component builder must be callable")
        self._builders[key] = builder

    def build(
        self,
        *,
        category: str,
        config: ComponentConfig,
        build_context: Any = None,
    ) -> T:
        if not config.enabled:
            raise ValueError(
                f"disabled component cannot be built directly: {category}/{config.name}"
            )
        key = ComponentKey.create(category, config.name, config.version)
        try:
            builder = self._builders[key]
        except KeyError as exc:
            available = ", ".join(
                f"{item.name}@{item.version}"
                for item in sorted(
                    (k for k in self._builders if k.category == key.category),
                    key=lambda item: (item.name, item.version),
                )
            ) or "<none>"
            raise ValueError(
                f"unknown RAG component {key.category}/{key.name}@{key.version}; "
                f"available: {available}"
            ) from exc

        instance = builder(build_context=build_context, **dict(config.params))
        metadata = ComponentMetadata(
            category=key.category,
            name=key.name,
            version=key.version,
            implementation=(
                f"{instance.__class__.__module__}.{instance.__class__.__qualname__}"
            ),
        )
        setattr(instance, "plugin_metadata", metadata)
        return instance

    def contains(self, *, category: str, name: str, version: str) -> bool:
        return ComponentKey.create(category, name, version) in self._builders

    def list_components(self, *, category: str | None = None) -> list[ComponentKey]:
        components = [
            key for key in self._builders if category is None or key.category == category
        ]
        return sorted(components, key=lambda item: (item.category, item.name, item.version))
