"""RAG 组合根使用的版本感知组件注册表。

注册键由组件类别、名称和版本共同确定；Builder 在注册和实例化后都执行协议校验。Pipeline
只读取配置并请求构造，不包含按具体策略名称分支，因此新增插件无需修改主检索链。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rag.config.static_retrieval import ComponentConfig
from rag.ports.plugin_contracts import validate_plugin_contract

T = TypeVar("T")
Builder = Callable[..., T]


@dataclass(frozen=True)
class ComponentKey:
    """唯一定位一个插件实现的 ``category/name@version`` 不可变键。"""
    category: str
    name: str
    version: str

    @classmethod
    def create(cls, category: str, name: str, version: str) -> "ComponentKey":
        """清理三个键字段并拒绝任一空值。"""
        values = [str(item or "").strip() for item in (category, name, version)]
        if not all(values):
            raise ValueError("component category/name/version cannot be blank")
        return cls(*values)


@dataclass(frozen=True)
class ComponentMetadata:
    """实例实际插件身份和 Python 实现路径的运行快照。"""
    category: str
    name: str
    version: str
    implementation: str

    def to_dict(self) -> dict[str, str]:
        """转换为适合 Manifest、Trace 和结果 metadata 的普通字典。"""
        return {
            "category": self.category,
            "name": self.name,
            "version": self.version,
            "implementation": self.implementation,
        }


class ComponentRegistry(Generic[T]):
    """把版本化组件键映射到构造函数，不承载任何策略分支。"""

    def __init__(self) -> None:
        """初始化空 Builder 映射。"""
        self._builders: dict[ComponentKey, Builder[T]] = {}

    def register(
        self,
        *,
        category: str,
        name: str,
        version: str,
        builder: Builder[T],
    ) -> None:
        """注册唯一 Builder；重复键、不可调用对象或不满足类别协议都会立即失败。"""
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

    def build(
        self,
        *,
        category: str,
        config: ComponentConfig,
        build_context: Any = None,
    ) -> T:
        """根据启用配置构造实例、二次校验协议并附加 ``plugin_metadata``。

        未知组件错误会列出同类别可用 ``name@version``，便于定位配置拼写或版本不匹配。
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

    def contains(self, *, category: str, name: str, version: str) -> bool:
        """检查完整版本化键是否已注册。"""
        return ComponentKey.create(category, name, version) in self._builders

    def list_components(self, *, category: str | None = None) -> list[ComponentKey]:
        """按类别、名称和版本稳定排序返回注册键快照。"""
        components = [
            key for key in self._builders if category is None or key.category == category
        ]
        return sorted(components, key=lambda item: (item.category, item.name, item.version))
