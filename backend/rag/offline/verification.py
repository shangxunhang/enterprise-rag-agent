"""Post-build verification for a versioned offline RAG index.

Step 11.2 deliberately verifies the immutable index artifacts themselves.  It
must not activate or swap the online index pointer; that belongs to Step 11.3.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from rag.offline.manifest import (
    IndexManifest,
    effective_artifact_integrity_mode,
    milvus_semantic_fingerprint,
    sha256_path,
)
from rag.store.parent_chunk_store import load_jsonl_dicts


MilvusClientFactory = Callable[[str], Any]


def _as_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_artifact_path(manifest_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (manifest_path.parent / candidate).resolve()


def _pick_evenly_spaced_indices(size: int, sample_count: int) -> list[int]:
    if size <= 0 or sample_count <= 0:
        return []
    if sample_count >= size:
        return list(range(size))
    if sample_count == 1:
        return [0]
    last = size - 1
    return sorted({round(i * last / (sample_count - 1)) for i in range(sample_count)})


def _normalize_search_hits(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    hits = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], list) else raw
    normalized: list[dict[str, Any]] = []
    for item in hits or []:
        if not isinstance(item, dict):
            continue
        entity = item.get("entity") if isinstance(item.get("entity"), dict) else {}
        chunk_id = (
            entity.get("child_chunk_id")
            or entity.get("chunk_id")
            or item.get("id")
            or item.get("chunk_id")
        )
        normalized.append(
            {
                "child_chunk_id": str(chunk_id or ""),
                "doc_id": str(entity.get("doc_id") or item.get("doc_id") or ""),
                "text": str(entity.get("text") or item.get("text") or ""),
                "score": item.get("distance", item.get("score")),
            }
        )
    return normalized


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "details": self.details}


@dataclass(frozen=True)
class OfflineIndexVerificationResult:
    status: str
    manifest_path: str
    index_version: str | None
    checks: list[VerificationCheck]
    metrics: dict[str, Any]
    self_retrieval: list[dict[str, Any]]

    @property
    def passed(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "offline_index_verification_report_v1",
            "status": self.status,
            "manifest_path": self.manifest_path,
            "index_version": self.index_version,
            "checks": [item.to_dict() for item in self.checks],
            "metrics": self.metrics,
            "self_retrieval": self.self_retrieval,
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target


class OfflineIndexVerifier:
    """Verify lineage, artifact integrity, vector shape, and Milvus consistency."""

    REQUIRED_ARTIFACTS = (
        "parent_chunks",
        "child_chunks",
        "vectors",
        "vector_index_records",
    )

    def __init__(
        self,
        *,
        milvus_client_factory: MilvusClientFactory | None = None,
    ) -> None:
        self._milvus_client_factory = milvus_client_factory

    def _client_factory(self) -> MilvusClientFactory:
        if self._milvus_client_factory is not None:
            return self._milvus_client_factory
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError(
                "pymilvus is required to verify a milvus_lite index; "
                "install the Step 11.2 dependencies first"
            ) from exc
        return lambda uri: MilvusClient(uri)

    @staticmethod
    def _check(
        checks: list[VerificationCheck],
        *,
        name: str,
        condition: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        checks.append(
            VerificationCheck(
                name=name,
                status="passed" if condition else "failed",
                details=details or {},
            )
        )

    def verify(
        self,
        manifest_path: str | Path,
        *,
        verify_artifact_hashes: bool = True,
        verify_milvus: bool = True,
        self_retrieval_samples: int = 3,
        self_retrieval_top_k: int = 3,
    ) -> OfflineIndexVerificationResult:
        manifest_file = Path(manifest_path).expanduser().resolve()
        checks: list[VerificationCheck] = []
        metrics: dict[str, Any] = {}
        self_retrieval: list[dict[str, Any]] = []

        if not manifest_file.is_file():
            self._check(
                checks,
                name="manifest_exists",
                condition=False,
                details={"path": str(manifest_file)},
            )
            return OfflineIndexVerificationResult(
                status="failed",
                manifest_path=str(manifest_file),
                index_version=None,
                checks=checks,
                metrics=metrics,
                self_retrieval=self_retrieval,
            )

        self._check(
            checks,
            name="manifest_exists",
            condition=True,
            details={"path": str(manifest_file)},
        )
        try:
            manifest = IndexManifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
        except Exception as exc:
            self._check(
                checks,
                name="manifest_schema",
                condition=False,
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
            return OfflineIndexVerificationResult(
                status="failed",
                manifest_path=str(manifest_file),
                index_version=None,
                checks=checks,
                metrics=metrics,
                self_retrieval=self_retrieval,
            )

        self._check(
            checks,
            name="manifest_schema",
            condition=True,
            details={"schema_version": manifest.schema_version},
        )
        metrics.update(
            {
                "document_count": manifest.document_count,
                "parent_chunk_count": manifest.parent_chunk_count,
                "child_chunk_count": manifest.child_chunk_count,
                "embedding_dim": manifest.embedding.get("dim"),
                "index_backend": manifest.index.get("backend"),
            }
        )

        missing = [name for name in self.REQUIRED_ARTIFACTS if name not in manifest.artifacts]
        if manifest.index.get("backend") == "milvus_lite" and "milvus_lite" not in manifest.artifacts:
            missing.append("milvus_lite")
        self._check(
            checks,
            name="required_artifacts_declared",
            condition=not missing,
            details={"missing": missing},
        )
        if missing:
            return self._finish(manifest_file, manifest, checks, metrics, self_retrieval)

        artifact_paths = {
            name: _resolve_artifact_path(manifest_file, artifact.path)
            for name, artifact in manifest.artifacts.items()
        }
        nonexistent = {name: str(path) for name, path in artifact_paths.items() if not path.exists()}
        self._check(
            checks,
            name="artifact_files_exist",
            condition=not nonexistent,
            details={"missing": nonexistent},
        )
        if nonexistent:
            return self._finish(manifest_file, manifest, checks, metrics, self_retrieval)

        if verify_artifact_hashes:
            content_mismatches: dict[str, dict[str, str]] = {}
            semantic_mismatches: dict[str, dict[str, str]] = {}
            semantic_artifacts: list[str] = []
            legacy_semantic_inferred: list[str] = []

            for name, path in artifact_paths.items():
                artifact = manifest.artifacts[name]
                mode = effective_artifact_integrity_mode(name, artifact)
                if mode == "content_sha256":
                    actual = sha256_path(
                        path,
                        metadata_only_paths=set(artifact.metadata_only_paths),
                    )
                    if artifact.sha256 != actual:
                        content_mismatches[name] = {
                            "expected": artifact.sha256,
                            "actual": actual,
                        }
                    continue

                semantic_artifacts.append(name)
                if artifact.integrity_mode != "milvus_semantic_v1":
                    # Legacy Step 11.2 manifests used a volatile directory hash.
                    # Do not mutate the immutable manifest; infer semantic mode
                    # and rely on the Milvus checks below.
                    legacy_semantic_inferred.append(name)
                    continue

                actual = milvus_semantic_fingerprint(
                    collection_name=str(manifest.index.get("collection_name") or ""),
                    record_count=int(artifact.record_count or manifest.child_chunk_count),
                    embedding_dim=_as_int(manifest.embedding.get("dim"), default=-1),
                    metric_type=str(manifest.index.get("metric_type") or ""),
                    child_chunks_sha256=manifest.artifacts["child_chunks"].sha256,
                    vector_records_sha256=manifest.artifacts["vector_index_records"].sha256,
                )
                if artifact.sha256 != actual:
                    semantic_mismatches[name] = {
                        "expected": artifact.sha256,
                        "actual": actual,
                    }

            mismatches = {**content_mismatches, **semantic_mismatches}
            self._check(
                checks,
                name="artifact_sha256",
                condition=not mismatches,
                details={
                    "mismatches": mismatches,
                    "semantic_artifacts": semantic_artifacts,
                    "legacy_semantic_inferred": legacy_semantic_inferred,
                },
            )

        parents = load_jsonl_dicts(artifact_paths["parent_chunks"])
        children = load_jsonl_dicts(artifact_paths["child_chunks"])
        vector_records = load_jsonl_dicts(artifact_paths["vector_index_records"])
        vectors = np.load(artifact_paths["vectors"], allow_pickle=False)

        actual_counts = {
            "parent_chunks": len(parents),
            "child_chunks": len(children),
            "vector_records": len(vector_records),
            "vector_rows": int(vectors.shape[0]) if vectors.ndim >= 1 else 0,
        }
        metrics["actual_counts"] = actual_counts
        expected_counts = {
            "parent_chunks": manifest.parent_chunk_count,
            "child_chunks": manifest.child_chunk_count,
            "vector_records": manifest.child_chunk_count,
            "vector_rows": manifest.child_chunk_count,
        }
        self._check(
            checks,
            name="artifact_record_counts",
            condition=actual_counts == expected_counts,
            details={"expected": expected_counts, "actual": actual_counts},
        )

        expected_dim = _as_int(manifest.embedding.get("dim"))
        shape_ok = vectors.ndim == 2 and int(vectors.shape[1]) == expected_dim
        self._check(
            checks,
            name="vector_matrix_shape",
            condition=shape_ok,
            details={"shape": list(vectors.shape), "expected_dim": expected_dim},
        )
        finite_ok = bool(np.isfinite(vectors).all()) if vectors.size else False
        self._check(
            checks,
            name="vector_values_finite",
            condition=finite_ok,
            details={"vector_count": int(vectors.shape[0]) if vectors.ndim == 2 else 0},
        )
        if vectors.ndim == 2 and vectors.size:
            norms = np.linalg.norm(vectors.astype("float64"), axis=1)
            metrics["vector_norm"] = {
                "min": float(norms.min()),
                "max": float(norms.max()),
                "mean": float(norms.mean()),
            }
            normalized = bool(np.all(np.abs(norms - 1.0) <= 1e-3))
            self._check(
                checks,
                name="vectors_l2_normalized",
                condition=normalized,
                details=metrics["vector_norm"],
            )

        parent_ids = [str(item.get("parent_chunk_id") or "") for item in parents]
        child_ids = [str(item.get("child_chunk_id") or item.get("chunk_id") or "") for item in children]
        unique_ids_ok = (
            all(parent_ids)
            and all(child_ids)
            and len(set(parent_ids)) == len(parent_ids)
            and len(set(child_ids)) == len(child_ids)
        )
        self._check(
            checks,
            name="chunk_ids_unique",
            condition=unique_ids_ok,
            details={
                "parent_unique": len(set(parent_ids)),
                "parent_total": len(parent_ids),
                "child_unique": len(set(child_ids)),
                "child_total": len(child_ids),
            },
        )
        parent_id_set = set(parent_ids)
        orphan_children = [
            child_ids[index]
            for index, child in enumerate(children)
            if str(child.get("parent_chunk_id") or "") not in parent_id_set
        ]
        self._check(
            checks,
            name="child_parent_references",
            condition=not orphan_children,
            details={"orphan_count": len(orphan_children), "sample": orphan_children[:10]},
        )
        lineage_mismatches = [
            child_ids[index]
            for index, child in enumerate(children)
            if str((child.get("extra") or {}).get("offline_index_version") or "")
            != manifest.index_version
        ]
        self._check(
            checks,
            name="child_index_lineage",
            condition=not lineage_mismatches,
            details={"mismatch_count": len(lineage_mismatches), "sample": lineage_mismatches[:10]},
        )

        if verify_milvus and manifest.index.get("backend") == "milvus_lite":
            self._verify_milvus(
                manifest=manifest,
                artifact_paths=artifact_paths,
                children=children,
                vectors=vectors,
                checks=checks,
                metrics=metrics,
                self_retrieval=self_retrieval,
                sample_count=self_retrieval_samples,
                top_k=self_retrieval_top_k,
            )
        elif manifest.index.get("backend") != "milvus_lite":
            self._check(
                checks,
                name="milvus_verification",
                condition=True,
                details={"status": "not_applicable", "backend": manifest.index.get("backend")},
            )

        return self._finish(manifest_file, manifest, checks, metrics, self_retrieval)

    def _verify_milvus(
        self,
        *,
        manifest: IndexManifest,
        artifact_paths: dict[str, Path],
        children: list[dict[str, Any]],
        vectors: np.ndarray,
        checks: list[VerificationCheck],
        metrics: dict[str, Any],
        self_retrieval: list[dict[str, Any]],
        sample_count: int,
        top_k: int,
    ) -> None:
        db_path = artifact_paths["milvus_lite"]
        collection_name = str(manifest.index.get("collection_name") or "")
        client = None
        try:
            client = self._client_factory()(str(db_path))
            exists = bool(client.has_collection(collection_name))
            self._check(
                checks,
                name="milvus_collection_exists",
                condition=exists,
                details={"collection_name": collection_name, "db_path": str(db_path)},
            )
            if not exists:
                return
            try:
                client.load_collection(collection_name)
            except Exception:
                pass
            stats = client.get_collection_stats(collection_name=collection_name)
            row_count = _as_int((stats or {}).get("row_count"))
            metrics["milvus_row_count"] = row_count
            self._check(
                checks,
                name="milvus_entity_count",
                condition=row_count == manifest.child_chunk_count,
                details={"expected": manifest.child_chunk_count, "actual": row_count},
            )

            sample_indices = _pick_evenly_spaced_indices(len(children), sample_count)
            successful = 0
            for index in sample_indices:
                expected_id = str(
                    children[index].get("child_chunk_id")
                    or children[index].get("chunk_id")
                    or ""
                )
                raw_hits = client.search(
                    collection_name=collection_name,
                    data=[np.asarray(vectors[index], dtype=np.float32).tolist()],
                    anns_field="vector",
                    limit=max(1, int(top_k)),
                    search_params={"metric_type": str(manifest.index.get("metric_type") or "COSINE")},
                    output_fields=["chunk_id", "child_chunk_id", "doc_id", "text"],
                )
                hits = _normalize_search_hits(raw_hits)
                returned_ids = [item["child_chunk_id"] for item in hits]
                passed = expected_id in returned_ids
                successful += int(passed)
                self_retrieval.append(
                    {
                        "sample_index": index,
                        "expected_child_chunk_id": expected_id,
                        "returned_child_chunk_ids": returned_ids,
                        "passed": passed,
                    }
                )
            hit_rate = successful / len(sample_indices) if sample_indices else 0.0
            metrics["self_retrieval_hit_rate"] = hit_rate
            self._check(
                checks,
                name="milvus_self_retrieval",
                condition=bool(sample_indices) and math.isclose(hit_rate, 1.0),
                details={
                    "sample_count": len(sample_indices),
                    "successful": successful,
                    "hit_rate": hit_rate,
                    "top_k": top_k,
                },
            )
        except Exception as exc:
            self._check(
                checks,
                name="milvus_runtime",
                condition=False,
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    @staticmethod
    def _finish(
        manifest_file: Path,
        manifest: IndexManifest,
        checks: Sequence[VerificationCheck],
        metrics: dict[str, Any],
        self_retrieval: list[dict[str, Any]],
    ) -> OfflineIndexVerificationResult:
        failed = [item.name for item in checks if item.status != "passed"]
        metrics["failed_checks"] = failed
        return OfflineIndexVerificationResult(
            status="success" if not failed else "failed",
            manifest_path=str(manifest_file),
            index_version=manifest.index_version,
            checks=list(checks),
            metrics=metrics,
            self_retrieval=self_retrieval,
        )
