# =============================================================================
# 中文阅读说明：RAG 核心模块，负责查询变换、召回、融合、重排、证据评估和上下文组装。
# 主要定义：ComponentKey、ComponentMetadata、ComponentRegistry。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Version-aware component registry used by the RAG composition root."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rag.config.static_retrieval import ComponentConfig
from rag.ports.plugin_contracts import validate_plugin_contract

T = TypeVar("T")
Builder = Callable[..., T]


# 阅读注释（类）：封装 component key，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class ComponentKey:
    """封装 component key，集中封装相关状态、依赖和行为。"""
    category: str
    name: str
    version: str

    # 阅读注释（函数）：创建 ComponentKey。
    @classmethod
    def create(cls, category: str, name: str, version: str) -> "ComponentKey":
        """创建 ComponentKey。

        参数:
            category: category，具体约束请结合类型标注和调用方确认。
            name: 名称，具体约束请结合类型标注和调用方确认。
            version: 版本，具体约束请结合类型标注和调用方确认。

        返回:
            'ComponentKey'

        阅读提示:
            主要直接调用：strip, str, all, ValueError, cls。
        """
        values = [str(item or "").strip() for item in (category, name, version)]
        if not all(values):
            raise ValueError("component category/name/version cannot be blank")
        return cls(*values)


# 阅读注释（类）：封装 component 元数据，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class ComponentMetadata:
    """封装 component 元数据，集中封装相关状态、依赖和行为。"""
    category: str
    name: str
    version: str
    implementation: str

    # 阅读注释（函数）：把 ComponentMetadata 转换为 字典。
    def to_dict(self) -> dict[str, str]:
        """把 ComponentMetadata 转换为 字典。

        返回:
            dict[str, str]
        """
        return {
            "category": self.category,
            "name": self.name,
            "version": self.version,
            "implementation": self.implementation,
        }


# 阅读注释（类）：封装 component 注册表，集中封装相关状态、依赖和行为。
class ComponentRegistry(Generic[T]):
    """Map ``(category, name, version)`` to component builders.

    The registry contains no strategy branches. New plugins extend the registry
    through registration and do not require changes to the pipeline or factory.
    """

    # 阅读注释（函数）：初始化 ComponentRegistry，保存运行所需的依赖、配置或状态。
    def __init__(self) -> None:
        """初始化 ComponentRegistry，保存运行所需的依赖、配置或状态。

        返回:
            None
        """
        self._builders: dict[ComponentKey, Builder[T]] = {}

    # 阅读注释（函数）：注册 ComponentRegistry。
    def register(
        self,
        *,
        category: str,
        name: str,
        version: str,
        builder: Builder[T],
    ) -> None:
        """注册 ComponentRegistry。

        参数:
            category: category，具体约束请结合类型标注和调用方确认。
            name: 名称，具体约束请结合类型标注和调用方确认。
            version: 版本，具体约束请结合类型标注和调用方确认。
            builder: builder，具体约束请结合类型标注和调用方确认。

        返回:
            None

        阅读提示:
            主要直接调用：ComponentKey.create, ValueError, callable, TypeError。
        """
        key = ComponentKey.create(category, name, version)
        if key in self._builders:
            raise ValueError(
                "duplicate RAG component registration: "
                f"{key.category}/{key.name}@{key.version}"
            )
        if not callable(builder):
            raise TypeError("component builder must be callable")
        validate_plugin_contract(key.category, builder)
        self._builders[key] = builder

    # 阅读注释（函数）：构建 ComponentRegistry。
    def build(
        self,
        *,
        category: str,
        config: ComponentConfig,
        build_context: Any = None,
    ) -> T:
        """构建 ComponentRegistry。

        参数:
            category: category，具体约束请结合类型标注和调用方确认。
            config: 运行配置。
            build_context: build 上下文，具体约束请结合类型标注和调用方确认。

        返回:
            T

        阅读提示:
            主要直接调用：ValueError, ComponentKey.create, join, sorted, builder, dict, ComponentMetadata, setattr。
        """
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
        validate_plugin_contract(key.category, instance)
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

    # 阅读注释（函数）：处理 contains 相关逻辑。
    def contains(self, *, category: str, name: str, version: str) -> bool:
        """处理 contains 相关逻辑。

        参数:
            category: category，具体约束请结合类型标注和调用方确认。
            name: 名称，具体约束请结合类型标注和调用方确认。
            version: 版本，具体约束请结合类型标注和调用方确认。

        返回:
            bool

        阅读提示:
            主要直接调用：ComponentKey.create。
        """
        return ComponentKey.create(category, name, version) in self._builders

    # 阅读注释（函数）：列出 components。
    def list_components(self, *, category: str | None = None) -> list[ComponentKey]:
        """列出 components。

        参数:
            category: category，具体约束请结合类型标注和调用方确认。

        返回:
            list[ComponentKey]

        阅读提示:
            主要直接调用：sorted。
        """
        components = [
            key for key in self._builders if category is None or key.category == category
        ]
        return sorted(components, key=lambda item: (item.category, item.name, item.version))
