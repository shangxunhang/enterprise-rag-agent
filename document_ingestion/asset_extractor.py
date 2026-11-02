"""Best-effort extraction for Word images and charts without silent loss."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .safe_paths import safe_child


@dataclass(frozen=True, slots=True)
class AssetResult:
    image_uri: str | None
    flags: list[str]
    metadata: dict[str, Any]


class AssetExtractor:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self.images_root = self.output_root / "images"

    def extract(self, word_object: Any, block_id: str, *, is_chart: bool) -> AssetResult:
        metadata = _asset_metadata(word_object, is_chart=is_chart)
        if is_chart:
            asset_name = f"{block_id}.png"
            asset_path = safe_child(self.images_root, asset_name)
            try:
                self.images_root.mkdir(parents=True, exist_ok=True)
                chart = word_object.Chart
                exported = chart.Export(str(asset_path))
                if exported is not False and asset_path.is_file() and asset_path.stat().st_size > 0:
                    return AssetResult(f"images/{asset_name}", [], metadata)
                asset_path.unlink(missing_ok=True)
            except Exception as exc:
                asset_path.unlink(missing_ok=True)
                metadata["extraction_error"] = type(exc).__name__
        return AssetResult(None, ["asset_not_extracted"], metadata)


def _asset_metadata(word_object: Any, *, is_chart: bool) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "asset_kind": "chart" if is_chart else "image",
        "word_object_type": _safe_value(word_object, "Type"),
        "name": _safe_value(word_object, "Name"),
        "alternative_text": _safe_value(word_object, "AlternativeText"),
        "title": _safe_value(word_object, "Title"),
    }
    return {key: value for key, value in metadata.items() if value not in {None, ""}}


def _safe_value(value: Any, attribute: str) -> Any:
    try:
        result = getattr(value, attribute)
        if isinstance(result, (str, int, float, bool)):
            return result
    except Exception:
        pass
    return None
