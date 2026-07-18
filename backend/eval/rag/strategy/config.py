"""Load strict YAML/JSON experiment matrices relative to the project root."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from rag.config.pipeline_config import PipelineConfigLoader

from .schemas import ExperimentMatrixConfig


class ExperimentConfigLoader:
    def __init__(self) -> None:
        self._path_loader = PipelineConfigLoader()

    def resolve_path(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> Path:
        return self._path_loader.resolve_path(path, project_root=project_root)

    def load(
        self,
        path: str | Path,
        *,
        project_root: str | Path | None = None,
    ) -> tuple[ExperimentMatrixConfig, Path, str]:
        config_path = self.resolve_path(path, project_root=project_root)
        if not config_path.is_file():
            raise FileNotFoundError(f"experiment config not found: {config_path}")
        raw = config_path.read_text(encoding="utf-8-sig")
        if config_path.suffix.lower() in {".yaml", ".yml"}:
            payload: Any = yaml.safe_load(raw)
        elif config_path.suffix.lower() == ".json":
            payload = json.loads(raw)
        else:
            raise ValueError("experiment config must use .yaml, .yml or .json")
        if not isinstance(payload, dict):
            raise ValueError("experiment config root must be an object")
        matrix = ExperimentMatrixConfig.model_validate(payload)
        canonical = json.dumps(
            matrix.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return matrix, config_path, hashlib.sha256(canonical).hexdigest()
