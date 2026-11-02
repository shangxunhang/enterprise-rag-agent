"""加载并指纹化 RAG 策略实验矩阵配置。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from rag.config.static_retrieval import StaticRetrievalSpecLoader

from .schemas import ExperimentMatrixConfig


class ExperimentConfigLoader:
    """解析 YAML/JSON 实验矩阵并生成稳定配置哈希。

    路径解析复用生产静态检索配置加载器，保证相对路径、项目根目录和跨平台路径
    规则一致；内容通过严格 Schema 校验后再计算 canonical hash，避免键顺序和空白
    差异造成同一实验配置被误认为不同版本。
    """

    def __init__(self) -> None:
        """创建共享路径解析器。"""

        self._path_loader = StaticRetrievalSpecLoader()

    def resolve_path(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> Path:
        """按生产配置规则解析实验配置路径。

        Args:
            path: 绝对路径或相对项目根目录的配置路径。
            project_root: 可选显式项目根目录。

        Returns:
            解析后的 ``Path``；本方法不要求文件已经存在。
        """

        # 直接复用静态检索加载器，避免评测和生产对相对路径有不同解释。
        return self._path_loader.resolve_path(
            path,
            project_root=project_root,
        )

    def load(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> tuple[ExperimentMatrixConfig, Path, str]:
        """读取、校验并指纹化一份实验矩阵配置。

        Args:
            path: YAML、YML 或 JSON 配置路径。
            project_root: 解析相对路径时使用的可选项目根目录。

        Returns:
            三元组：严格配置对象、实际配置路径、canonical SHA-256。

        Raises:
            FileNotFoundError: 解析后路径不是文件时抛出。
            ValueError: 扩展名不受支持或配置根节点不是对象时抛出。
            pydantic.ValidationError: 配置字段不符合实验矩阵协议时抛出。
        """

        # 第一步：解析实际路径并拒绝不存在的配置。
        config_path = self.resolve_path(path, project_root=project_root)
        if not config_path.is_file():
            raise FileNotFoundError(f"experiment config not found: {config_path}")

        # 第二步：使用 utf-8-sig 兼容带 BOM 文件，并按扩展名选择解析器。
        raw = config_path.read_text(encoding="utf-8-sig")
        suffix = config_path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            payload: Any = yaml.safe_load(raw)
        elif suffix == ".json":
            payload = json.loads(raw)
        else:
            raise ValueError("experiment config must use .yaml, .yml or .json")

        # 第三步：配置根必须是对象，随后交由 Pydantic 校验全部嵌套契约。
        if not isinstance(payload, dict):
            raise ValueError("experiment config root must be an object")
        matrix = ExperimentMatrixConfig.model_validate(payload)

        # 第四步：对校验后的 JSON 投影排序并紧凑序列化，消除格式和键顺序噪声。
        canonical = json.dumps(
            matrix.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        # 第五步：返回内容哈希，供实验报告、缓存和可重复性审计使用。
        return matrix, config_path, hashlib.sha256(canonical).hexdigest()
