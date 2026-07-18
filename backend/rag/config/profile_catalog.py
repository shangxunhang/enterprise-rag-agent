"""Startup validation for versioned online RAG profile catalogs.

Catalog validation is intentionally lightweight: every profile is parsed with
its strict Pydantic schema and every declared component reference must exist in
the registry. Heavy resources (Milvus, embedding models, rerankers and LLMs)
are only constructed for the selected profile by the composition root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from rag.config.pipeline_config import OnlineRAGPipelineConfig, PipelineConfigLoader


_SINGLE_COMPONENT_SLOTS: tuple[tuple[str, str], ...] = (
    ("fusion", "fusion"),
    ("query_fusion", "query_fusion"),
    ("candidate_enricher", "candidate_enricher"),
    ("reranker", "reranker"),
    ("evidence_grader", "evidence_grader"),
    ("context_packer", "context_packer"),
    ("generation_checker", "generation_checker"),
    ("repair_strategy", "repair_strategy"),
)


@dataclass(frozen=True)
class ValidatedProfile:
    path: str
    profile_id: str
    profile_version: str
    schema_version: str
    config_hash: str
    component_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "schema_version": self.schema_version,
            "config_hash": self.config_hash,
            "component_count": self.component_count,
        }


@dataclass
class ProfileCatalogReport:
    profile_dir: str
    profiles: list[ValidatedProfile] = field(default_factory=list)

    @property
    def profile_count(self) -> int:
        return len(self.profiles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "success",
            "validation_level": "schema_and_registry_reference",
            "profile_dir": self.profile_dir,
            "profile_count": self.profile_count,
            "profiles": [item.to_dict() for item in self.profiles],
        }


class OnlineRAGProfileCatalogValidator:
    """Validate every profile before an online runtime starts."""

    def __init__(
        self,
        *,
        loader: PipelineConfigLoader | None = None,
    ) -> None:
        self.loader = loader or PipelineConfigLoader()

    @staticmethod
    def _profile_paths(profile_dir: Path) -> list[Path]:
        paths = sorted(
            {
                *profile_dir.glob("*.yaml"),
                *profile_dir.glob("*.yml"),
                *profile_dir.glob("*.json"),
            }
        )
        if not paths:
            raise ValueError(f"no online RAG profiles found: {profile_dir}")
        return paths

    @staticmethod
    def _component_references(
        config: OnlineRAGPipelineConfig,
    ) -> Iterable[tuple[str, Any]]:
        for item in config.query_transformers:
            if item.enabled:
                yield "query_transformer", item
        for item in config.retrievers:
            if item.enabled:
                yield "retriever", item
        for field_name, category in _SINGLE_COMPONENT_SLOTS:
            item = getattr(config, field_name)
            if item.enabled:
                yield category, item

    def validate(
        self,
        *,
        project_root: str | Path,
        registry: Any,
        profile_dir: str | Path = "backend/rag/profiles",
    ) -> ProfileCatalogReport:
        root = Path(project_root).expanduser().resolve()
        directory = Path(profile_dir).expanduser()
        if not directory.is_absolute():
            directory = root / directory
        directory = directory.resolve()
        if not directory.is_dir():
            raise FileNotFoundError(f"RAG profile directory not found: {directory}")

        seen_ids: dict[str, Path] = {}
        report = ProfileCatalogReport(profile_dir=str(directory))
        for path in self._profile_paths(directory):
            config = self.loader.load(path, project_root=root)
            previous = seen_ids.get(config.profile_id)
            if previous is not None:
                raise ValueError(
                    "duplicate online RAG profile_id "
                    f"{config.profile_id!r}: {previous.name}, {path.name}"
                )
            seen_ids[config.profile_id] = path
            if path.stem != config.profile_id:
                raise ValueError(
                    "online RAG profile filename must match profile_id: "
                    f"file={path.name!r}, profile_id={config.profile_id!r}"
                )

            component_count = 0
            for category, component in self._component_references(config):
                component_count += 1
                if not registry.contains(
                    category=category,
                    name=component.name,
                    version=component.version,
                ):
                    raise ValueError(
                        "online RAG profile references unregistered component: "
                        f"profile={config.profile_id!r}, "
                        f"component={category}/{component.name}@{component.version}"
                    )

            report.profiles.append(
                ValidatedProfile(
                    path=str(path),
                    profile_id=config.profile_id,
                    profile_version=config.profile_version,
                    schema_version=config.schema_version,
                    config_hash=config.config_hash(),
                    component_count=component_count,
                )
            )
        return report
