"""Path resolution for local RAG resources."""

from __future__ import annotations

from pathlib import Path


def resolve_path(path: str | Path, project_root: str | Path | None = None) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    root = Path(project_root) if project_root else Path.cwd()
    return str((root / candidate).resolve())


def require_path(path: str | Path, name: str) -> None:
    if not Path(path).exists():
        raise FileNotFoundError(f"{name} not found: {path}")
